#!/usr/bin/env python3
"""Curate raw ADS publications and render the LaTeX fragments used by the CV."""

from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

import yaml

from .ads import load_snapshot
from .paths import (
    ADVISING_TEX_PATH,
    COAUTHOR_TEX_PATH,
    CURATED_PUBLICATIONS_PATH,
    FIRST_AUTHOR_TEX_PATH,
    ORCID_AUDIT_PATH,
    ORCID_WORKS_PATH,
    PUBLICATIONS_AUDIT_MARKDOWN_PATH,
    PUBLICATIONS_AUDIT_PATH,
    SECOND_AUTHOR_TEX_PATH,
    SUMMARY_TEX_PATH,
)

HIGHLY_CITED_THRESHOLD = 100

RAW_TEX_MAP = {
    "–": "--",
    "—": "---",
    "─": "--",
    "−": "-",
    "α": "$\\alpha$",
    "β": "$\\beta$",
    "γ": "$\\gamma$",
    "∼": "$\\sim$",
    "≈": "$\\approx$",
    "≤": "$\\leq$",
    "≥": "$\\geq$",
}

ESCAPE_MAP = {
    "&": "\\&",
    "%": "\\%",
    "$": "\\$",
    "#": "\\#",
    "_": "\\_",
    "{": "\\{",
    "}": "\\}",
    "~": "\\textasciitilde{}",
    "^": "\\^{}",
}

JOURNAL_MAP = [
    ("astrophysical journal letters", "\\apjl"),
    ("astrophysical journal supplement", "\\apjs"),
    ("monthly notices of the royal astronomical society: letters", "\\mnrasl"),
    ("mnras letters", "\\mnrasl"),
    ("astrophysical journal", "\\apj"),
    ("monthly notices of the royal astronomical society", "\\mnras"),
    ("astronomy and astrophysics", "\\aanda"),
    ("the astronomical journal", "\\aj"),
    ("astronomical journal", "\\aj"),
    ("a&a", "\\aanda"),
    ("apj", "\\apj"),
    ("mnras", "\\mnras"),
    ("nature", "\\nature"),
]

DOCTYPE_PRIORITY = {
    "article": 0,
    "inproceedings": 1,
    "eprint": 2,
    "software": 3,
    "proposal": 4,
}

ORCID_DOCTYPE_MAP = {
    "journal-article": "article",
    "working-paper": "eprint",
    "preprint": "eprint",
    "conference-paper": "inproceedings",
    "research-technique": "software",
    "conference-abstract": "conference-abstract",
    "dissertation-thesis": "dissertation-thesis",
}

def load_data(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        if path.suffix in {".yaml", ".yml"}:
            return yaml.safe_load(handle)
        return json.load(handle)


def load_advisee_manifest(path: Path) -> dict:
    payload = load_data(path)
    if not isinstance(payload, dict):
        raise ValueError("data/advisees.yaml must contain a top-level object.")

    categories = payload.get("categories")
    advisees = payload.get("advisees")
    if not isinstance(categories, dict) or not categories:
        raise ValueError("data/advisees.yaml must define a non-empty `categories` object.")
    if not isinstance(advisees, list):
        raise ValueError("data/advisees.yaml must define an `advisees` list.")

    return payload


def fetch_json(url: str) -> object:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "dbf-cv build",
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def fetch_text(url: str, accept: str) -> str:
    request = Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "dbf-cv build",
        },
    )
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8")


def derive_bibcode(record: dict) -> str | None:
    bibcode = record.get("bibcode")
    if bibcode:
        return bibcode

    url = record.get("url")
    if isinstance(url, str) and "/" in url:
        return url.rsplit("/", 1)[-1]

    return None


def to_int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_title(text: str) -> str:
    return " ".join((text or "").casefold().split())


def normalize_publication(text: str) -> str:
    return " ".join((text or "").casefold().split())


def normalize_author_name(text: str | None) -> str:
    collapsed = " ".join((text or "").casefold().split())
    return re.sub(r"[^a-z0-9]+", "", collapsed)


def normalize_doi(value: str | None) -> str:
    return (value or "").strip().casefold()


def normalize_arxiv(value: str | None) -> str:
    normalized = (value or "").strip()
    if normalized.casefold().startswith("arxiv:"):
        normalized = normalized.split(":", 1)[1]
    return normalized.casefold()


def latex_text(text: str | None) -> str:
    if text is None:
        return ""

    rendered = []
    for char in str(text):
        if char in RAW_TEX_MAP:
            rendered.append(RAW_TEX_MAP[char])
        elif char in ESCAPE_MAP:
            rendered.append(ESCAPE_MAP[char])
        else:
            rendered.append(char)
    return "".join(rendered)


def format_display_date(raw: str | None) -> str:
    if not raw:
        return "unknown"

    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return raw
    return f"{parsed.day} {parsed.strftime('%b %Y')}"


def journal_macro(publication_name: str | None) -> str:
    if not publication_name:
        return ""

    normalized = publication_name.casefold()
    for key, macro in JOURNAL_MAP:
        if key in normalized:
            return macro
    return latex_text(publication_name)


def strip_markup(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = " ".join(re.sub(r"<[^>]+>", "", html.unescape(text)).split())
    return re.sub(r"\s+([:;,.!?])", r"\1", stripped)


def invert_name(name: str) -> str:
    if "," in name:
        return name
    parts = name.split()
    if len(parts) < 2:
        return name
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def clean_author_text(text: str) -> str:
    cleaned = re.sub(r"[\u3400-\u9fff]+", "", text)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    return " ".join(cleaned.split())


def dedupe_key(record: dict) -> str:
    arxiv = normalize_arxiv(record.get("arxiv"))
    if arxiv:
        return f"arxiv:{arxiv}"

    doi = normalize_doi(record.get("doi"))
    if doi:
        return f"doi:{doi}"

    return f"title:{normalize_title(record.get('title', ''))}"


def normalize_record(record: dict, source: str) -> dict:
    normalized = dict(record)
    normalized["bibcode"] = derive_bibcode(normalized)
    normalized["citations"] = to_int(normalized.get("citations")) or 0
    normalized["year"] = str(normalized.get("year") or "")
    normalized["record_source"] = source
    return normalized


def build_identifier_index(records: Iterable[dict]) -> dict[str, set[str]]:
    index = {
        "bibcode": set(),
        "doi": set(),
        "arxiv": set(),
        "title": set(),
    }
    for record in records:
        bibcode = record.get("bibcode")
        if bibcode:
            index["bibcode"].add(bibcode)

        doi = normalize_doi(record.get("doi"))
        if doi:
            index["doi"].add(doi)

        arxiv = normalize_arxiv(record.get("arxiv"))
        if arxiv:
            index["arxiv"].add(arxiv)

        title = normalize_title(record.get("title"))
        if title:
            index["title"].add(title)
    return index


def orcid_external_ids(payload: dict) -> dict[str, list[str]]:
    identifiers = defaultdict(list)
    for entry in payload.get("external-ids", {}).get("external-id", []):
        kind = entry.get("external-id-type")
        value = entry.get("external-id-value")
        if kind and value:
            identifiers[kind].append(value)
    return {kind: values for kind, values in identifiers.items()}


def summarize_orcid_group(group: dict) -> dict:
    summary = (group.get("work-summary") or [{}])[0]
    identifiers = orcid_external_ids(group)
    return {
        "put_code": summary.get("put-code"),
        "title": (
            summary.get("title", {})
            .get("title", {})
            .get("value")
        ),
        "type": summary.get("type"),
        "source": (
            summary.get("source", {})
            .get("source-name", {})
            .get("value")
        ),
        "bibcodes": identifiers.get("bibcode", []),
        "dois": [normalize_doi(value) for value in identifiers.get("doi", [])],
        "arxiv": [normalize_arxiv(value) for value in identifiers.get("arxiv", [])],
    }


def matches_identifier(summary: dict, index: dict[str, set[str]]) -> bool:
    if any(value in index["bibcode"] for value in summary.get("bibcodes", [])):
        return True
    if any(value in index["doi"] for value in summary.get("dois", [])):
        return True
    if any(value in index["arxiv"] for value in summary.get("arxiv", [])):
        return True

    title = normalize_title(summary.get("title"))
    return bool(title and title in index["title"])


def matches_raw_snapshot(summary: dict, index: dict[str, set[str]]) -> bool:
    if any(value in index["bibcode"] for value in summary.get("bibcodes", [])):
        return True
    if any(value in index["doi"] for value in summary.get("dois", [])):
        return True

    mapped_doctype = ORCID_DOCTYPE_MAP.get(summary.get("type"), summary.get("type"))
    if mapped_doctype != "article" and any(value in index["arxiv"] for value in summary.get("arxiv", [])):
        return True

    title = normalize_title(summary.get("title"))
    return bool(title and title in index["title"])


def orcid_pubdate(payload: dict) -> str:
    publication_date = payload.get("publication-date") or {}
    year = (publication_date.get("year") or {}).get("value") or "0000"
    month = (publication_date.get("month") or {}).get("value") or "00"
    day = (publication_date.get("day") or {}).get("value") or "00"
    return f"{year}-{month}-{day}"


def crossref_pubdate(message: dict) -> tuple[str, str]:
    for field in ("published-print", "published-online", "issued", "created"):
        date_parts = (message.get(field) or {}).get("date-parts") or []
        if not date_parts:
            continue
        parts = [str(part) for part in date_parts[0]]
        year = parts[0]
        month = parts[1].zfill(2) if len(parts) > 1 else "00"
        day = parts[2].zfill(2) if len(parts) > 2 else "00"
        return f"{year}-{month}-{day}", year
    return "0000-00-00", ""


def fetch_crossref_fields(doi: str) -> dict:
    payload = fetch_json(f"https://api.crossref.org/works/{quote(doi, safe='')}")
    message = payload["message"]
    pubdate, year = crossref_pubdate(message)
    authors = []
    for author in message.get("author", []):
        family = author.get("family")
        given = author.get("given")
        if family and given:
            authors.append(clean_author_text(f"{family}, {given}"))
        elif family:
            authors.append(clean_author_text(family))
        elif given:
            authors.append(clean_author_text(given))

    return {
        "authors": authors,
        "page": message.get("page"),
        "pub": (message.get("container-title") or [None])[0],
        "pubdate": pubdate,
        "title": strip_markup((message.get("title") or [None])[0]),
        "volume": message.get("volume"),
        "year": year,
    }


def fetch_arxiv_fields(arxiv_id: str) -> dict:
    xml_text = fetch_text(
        f"https://export.arxiv.org/api/query?id_list={quote(arxiv_id, safe='')}",
        accept="application/atom+xml",
    )
    root = ET.fromstring(xml_text)
    namespaces = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", namespaces)
    if entry is None:
        return {}

    published = entry.findtext("atom:published", default="", namespaces=namespaces)
    pubdate = published[:10] if published else "0000-00-00"
    authors = [
        invert_name(author.findtext("atom:name", default="", namespaces=namespaces))
        for author in entry.findall("atom:author", namespaces)
    ]
    return {
        "authors": [author for author in authors if author],
        "pubdate": pubdate,
        "title": " ".join(
            (entry.findtext("atom:title", default="", namespaces=namespaces) or "").split()
        ),
        "year": pubdate[:4] if pubdate else "",
    }


def orcid_work_to_record(orcid: str, summary: dict) -> dict | None:
    put_code = summary.get("put_code")
    if put_code is None:
        return None

    detail = fetch_json(f"https://pub.orcid.org/v3.0/{orcid}/work/{put_code}")
    identifiers = orcid_external_ids(detail)
    bibcode = (identifiers.get("bibcode") or [None])[0]
    doi = (identifiers.get("doi") or [None])[0]
    arxiv = normalize_arxiv((identifiers.get("arxiv") or [None])[0])
    doctype = ORCID_DOCTYPE_MAP.get(detail.get("type"), detail.get("type"))
    contributors = []
    for contributor in (detail.get("contributors") or {}).get("contributor", []):
        name = (contributor.get("credit-name") or {}).get("value")
        if name:
            contributors.append(name)

    record = {
        "arxiv": arxiv or None,
        "authors": contributors,
        "bibcode": bibcode,
        "citations": 0,
        "doctype": doctype,
        "doi": doi,
        "page": None,
        "pub": (detail.get("journal-title") or {}).get("value"),
        "pubdate": orcid_pubdate(detail),
        "title": (
            detail.get("title", {})
            .get("title", {})
            .get("value")
        ),
        "url": f"http://adsabs.harvard.edu/abs/{bibcode}" if bibcode else None,
        "volume": None,
        "year": (detail.get("publication-date", {}).get("year") or {}).get("value") or "",
    }

    try:
        if doi and doctype == "article":
            record.update({key: value for key, value in fetch_crossref_fields(doi).items() if value})
        elif arxiv and doctype == "eprint":
            record.update({key: value for key, value in fetch_arxiv_fields(arxiv).items() if value})
    except (KeyError, URLError, TimeoutError, ET.ParseError, ValueError):
        pass

    return record


def merge_record_sources(*record_groups: tuple[str, list[dict]]) -> list[dict]:
    merged_by_bibcode: dict[str, dict] = {}
    without_bibcode = []

    for source, records in record_groups:
        for source_record in records:
            record = normalize_record(source_record, source)
            bibcode = record.get("bibcode")
            if bibcode:
                existing = merged_by_bibcode.get(bibcode, {})
                merged_by_bibcode[bibcode] = {**existing, **record}
            else:
                without_bibcode.append(record)

    return list(merged_by_bibcode.values()) + without_bibcode


def record_priority(record: dict) -> tuple[int, int, str]:
    doctype = record.get("doctype") or ""
    citations = to_int(record.get("citations")) or 0
    title = record.get("title") or ""
    return (
        DOCTYPE_PRIORITY.get(doctype, 99),
        -citations,
        title.casefold(),
    )


def merge_dedupe_group(group: list[dict]) -> dict:
    chosen = dict(sorted(group, key=record_priority)[0])
    chosen["citations"] = max(to_int(record.get("citations")) or 0 for record in group)

    for field in ("arxiv", "doi", "page", "pub", "pubdate", "title", "url", "volume", "year"):
        if chosen.get(field):
            continue
        for record in group:
            value = record.get(field)
            if value not in (None, "", []):
                chosen[field] = value
                break

    return chosen


def author_position(record: dict, config: dict, include_override: dict | None) -> int | None:
    if include_override and "author_position" in include_override:
        return int(include_override["author_position"]) - 1

    aliases = {
        normalize_author_name(alias)
        for alias in config["author_aliases"]["position_match"]
    }
    for index, author in enumerate(record.get("authors", [])):
        if normalize_author_name(author) in aliases:
            return index
    return None


def format_author_list(record: dict, my_position: int | None) -> str:
    authors = record.get("authors", [])
    if not authors:
        return "Unknown Authors"

    formatted = []
    for index, author in enumerate(authors):
        if my_position is not None and index == my_position:
            formatted.append("\\textbf{Fielding, Drummond B.}")
        else:
            formatted.append(latex_text(author))

    if len(formatted) <= 4:
        return "; ".join(formatted)

    preview = "; ".join(formatted[:4])
    if my_position is not None and my_position >= 4:
        return preview + "~\\textit{et al.} (incl. \\textbf{DBF})"
    return preview + "~\\textit{et al.}"


def href_with_resume(url: str, text: str, highlighted: bool) -> str:
    del highlighted
    return f"\\href{{{url}}}{{{text}}}"


def build_advisee_data(manifest: dict) -> tuple[dict, list[dict], dict[str, dict]]:
    categories = manifest["categories"]
    advisees = manifest["advisees"]
    visible_advisees = []
    led_papers = {}

    for category, metadata in categories.items():
        if not isinstance(metadata, dict):
            raise ValueError(f"Category `{category}` in data/advisees.yaml must map to an object.")
        if not isinstance(metadata.get("symbol"), str) or not metadata["symbol"].strip():
            raise ValueError(
                f"Category `{category}` in data/advisees.yaml is missing a TeX `symbol`."
            )
        if not isinstance(metadata.get("legend"), str) or not metadata["legend"].strip():
            raise ValueError(
                f"Category `{category}` in data/advisees.yaml is missing a `legend` label."
            )

    for advisee in advisees:
        if not isinstance(advisee, dict):
            raise ValueError("Each advisee in data/advisees.yaml must be an object.")

        name = advisee.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each advisee in data/advisees.yaml must have a non-empty `name`.")

        category = advisee.get("category")
        if category not in categories:
            raise ValueError(
                f"Advisee `{name}` uses unknown category `{category}` in data/advisees.yaml."
            )

        show_in_advising = advisee.get("show_in_advising", True)
        if show_in_advising:
            for field in ("role", "affiliation"):
                value = advisee.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"Advisee `{name}` must define `{field}` when show_in_advising is true."
                    )
            visible_advisees.append(advisee)

        led_entries = advisee.get("led_papers", [])
        if not isinstance(led_entries, list):
            raise ValueError(f"Advisee `{name}` has a non-list `led_papers` field.")

        for bibcode in led_entries:
            if not isinstance(bibcode, str) or not bibcode.strip():
                raise ValueError(f"Advisee `{name}` has an invalid bibcode in `led_papers`.")
            if bibcode in led_papers:
                raise ValueError(
                    f"Bibcode `{bibcode}` is assigned to both `{led_papers[bibcode]['name']}` "
                    f"and `{name}` in data/advisees.yaml."
                )

            led_papers[bibcode] = {
                "name": name,
                "category": category,
                "symbol": categories[category]["symbol"],
                "legend": categories[category]["legend"],
            }

    return categories, visible_advisees, led_papers


def render_advisee_legend(categories: dict) -> str:
    parts = [r"${}^\star$ indicates $\geq$ 100 citations"]
    for metadata in categories.values():
        parts.append(
            f"${{}}^{{{metadata['symbol']}}}$ {latex_text(metadata['legend'])}"
        )
    return " --- ".join(parts)


def render_advising_entry(advisee: dict) -> str:
    name_tex = latex_text(advisee["name"])
    if advisee.get("url"):
        name_tex = f"\\href{{{advisee['url']}}}{{\\textit{{{name_tex}}}}}"
    else:
        name_tex = f"\\textit{{{name_tex}}}"

    line = (
        f"\\item {name_tex} --- {latex_text(advisee['role'])} --- "
        f"{latex_text(advisee['affiliation'])}"
    )

    outcome = advisee.get("outcome")
    if outcome:
        line += f" $\\rightarrow$ {latex_text(outcome)}"

    return line


def render_advising(entries: list[dict]) -> str:
    if not entries:
        return "% No advising entries configured.\n"
    return "\n".join(render_advising_entry(entry) for entry in entries) + "\n"


def format_publication(record: dict, index: int) -> str:
    label_symbols = []
    citations = to_int(record.get("citations")) or 0
    if citations >= HIGHLY_CITED_THRESHOLD:
        label_symbols.append("\\star")
    if record.get("advisee_symbol"):
        label_symbols.append(record["advisee_symbol"])

    label_tex = f"${''.join(label_symbols)}$" if label_symbols else ""
    item = f"\\item[{{\\color{{deemph}}\\scriptsize{label_tex}{index}}}]"
    highlighted = False

    content = format_author_list(record, record.get("author_position"))
    title = latex_text(record.get("title"))
    doi = record.get("doi")
    arxiv = record.get("arxiv")
    url = record.get("url")
    title_link = None
    if doi:
        title_link = f"https://doi.org/{doi}"
    elif arxiv:
        title_link = f"https://arxiv.org/abs/{arxiv}"
    elif url:
        title_link = url

    if title_link:
        content += f", {href_with_resume(title_link, title, highlighted)}"
    else:
        content += f", {title}"

    publication = journal_macro(record.get("pub"))
    if publication:
        content += f", {publication}"

    volume = record.get("volume")
    if volume:
        content += f", {{{latex_text(str(volume))}}}"

    page = record.get("page")
    if page is not None and str(page):
        content += f", {latex_text(str(page))}"

    year = record.get("year")
    if year:
        content += f", {year}"

    if arxiv:
        content += (
            " ("
            + href_with_resume(
                f"https://arxiv.org/abs/{arxiv}",
                f"arXiv:{latex_text(arxiv)}",
                highlighted,
            )
            + ")"
        )

    if citations > 0:
        citation_text = f"{citations} citations"
        if citations >= HIGHLY_CITED_THRESHOLD:
            citation_text = f"\\textbf{{{citations}}} citations"

        if url:
            content += f" [{href_with_resume(url, citation_text, highlighted)}]"
        else:
            content += f" [{citation_text}]"

    return item + content


def compute_h_index(citations: Iterable[int]) -> int:
    ordered = sorted((int(value) for value in citations), reverse=True)
    return sum(value >= index + 1 for index, value in enumerate(ordered))


def render_summary(ads_metrics: dict, scholar_metrics: dict | None, advisee_categories: dict) -> str:
    lines = [
        f"total publication count: {ads_metrics['total_papers']} --- "
        f"citations: {ads_metrics['total_citations']} --- "
        f"h-index: {ads_metrics['h_index']} "
        f"(\\textit{{{format_display_date(ads_metrics['updated_on'])}}})\\\\"
    ]

    if scholar_metrics:
        scholar_profile_url = scholar_metrics.get("profile_url")
        scholar_label = (
            f"\\href{{{scholar_profile_url}}}{{Google Scholar}}"
            if scholar_profile_url
            else "Google Scholar"
        )
        lines.append(
            f"{scholar_label}: citations: {scholar_metrics['citations']} --- "
            f"h-index: {scholar_metrics['h_index']} "
            f"(\\textit{{{format_display_date(scholar_metrics['updated_on'])}}})\\\\"
        )

    lines.append(render_advisee_legend(advisee_categories))
    return "\n".join(lines) + "\n"


def reasoned_entry(record: dict, reason: str) -> dict:
    return {
        "bibcode": record.get("bibcode"),
        "title": record.get("title"),
        "doctype": record.get("doctype"),
        "pub": record.get("pub"),
        "year": record.get("year"),
        "citations": record.get("citations"),
        "reason": reason,
    }


def generate_publication_artifacts(
    publications_path: Path,
    rules_path: Path,
    advisees_path: Path,
) -> dict:
    ads_snapshot = load_snapshot(publications_path)
    publications = ads_snapshot.records
    overrides = load_data(rules_path)
    advisee_manifest = load_advisee_manifest(advisees_path)
    advisee_categories, advising_entries, led_papers = build_advisee_data(advisee_manifest)

    auto_aliases = set(overrides["author_aliases"]["auto_include"])
    allowed_doctypes = set(overrides["allowed_doctypes"])
    excluded_publications = {
        normalize_publication(name)
        for name in overrides.get("excluded_publications", [])
    }
    include_overrides = overrides.get("include_overrides", {})
    exclude_overrides = overrides.get("exclude_overrides", {})
    manual_records = overrides.get("manual_records", [])
    promoted_ml = overrides.get("promoted_ml_conference_papers", {})
    orcid = overrides.get("orcid")

    orcid_summaries = []
    supplemental_orcid_records = []
    orcid_audit = {
        "status": "not_configured",
        "orcid": orcid,
        "orcid_total_works": 0,
        "missing_from_raw_snapshot": [],
        "supplemented_records": [],
        "remaining_missing_from_curated": [],
        "error": None,
    }

    if orcid:
        try:
            works_payload = fetch_json(f"https://pub.orcid.org/v3.0/{orcid}/works")
            orcid_summaries = [summarize_orcid_group(group) for group in works_payload.get("group", [])]
            raw_index = build_identifier_index(publications)
            missing_from_raw = [
                summary
                for summary in orcid_summaries
                if not matches_raw_snapshot(summary, raw_index)
            ]

            for summary in missing_from_raw:
                mapped_doctype = ORCID_DOCTYPE_MAP.get(summary.get("type"), summary.get("type"))
                has_syncable_identifier = bool(summary.get("dois") or summary.get("arxiv"))
                if mapped_doctype not in allowed_doctypes or not has_syncable_identifier:
                    continue

                record = orcid_work_to_record(orcid, summary)
                if record is not None:
                    supplemental_orcid_records.append(record)

            orcid_audit.update(
                {
                    "status": "ok",
                    "orcid_total_works": len(orcid_summaries),
                    "missing_from_raw_snapshot": missing_from_raw,
                    "supplemented_records": [
                        {
                            "bibcode": record.get("bibcode"),
                            "title": record.get("title"),
                            "doctype": record.get("doctype"),
                            "doi": record.get("doi"),
                            "arxiv": record.get("arxiv"),
                        }
                        for record in supplemental_orcid_records
                    ],
                }
            )
            ORCID_WORKS_PATH.write_text(
                json.dumps(orcid_summaries, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except (KeyError, URLError, TimeoutError, ValueError) as exc:
            orcid_audit.update(
                {
                    "status": "error",
                    "error": str(exc),
                }
            )

    enriched = merge_record_sources(
        ("raw", publications),
        ("manual", manual_records),
        ("orcid", supplemental_orcid_records),
    )

    kept_candidates = []
    excluded = []

    for record in enriched:
        bibcode = record.get("bibcode")
        if not bibcode:
            excluded.append(reasoned_entry(record, "missing bibcode"))
            continue

        if bibcode in exclude_overrides:
            excluded.append(
                reasoned_entry(record, exclude_overrides[bibcode].get("reason", "excluded by override"))
            )
            continue

        include_override = include_overrides.get(bibcode)
        ml_override = promoted_ml.get(bibcode)
        matches_alias = (
            record.get("record_source") in {"manual", "orcid"}
            or any(author in auto_aliases for author in record.get("authors", []))
        )

        if not matches_alias and include_override is None and ml_override is None:
            excluded.append(reasoned_entry(record, "not matched to configured author aliases"))
            continue

        publication_name = normalize_publication(record.get("pub"))
        force_include = include_override is not None or ml_override is not None

        if record.get("doctype") not in allowed_doctypes and ml_override is None:
            excluded.append(
                reasoned_entry(
                    record,
                    f"excluded doctype `{record.get('doctype')}`",
                )
            )
            continue

        if publication_name in excluded_publications and not force_include:
            excluded.append(
                reasoned_entry(record, f"excluded publication venue `{record.get('pub')}`")
            )
            continue

        include_metadata = include_override or ml_override or {}
        record["selection_reason"] = include_metadata.get(
            "reason",
            "supplemented from ORCID public record"
            if record.get("record_source") == "orcid"
            else "matched configured author aliases",
        )
        record["author_position"] = author_position(record, overrides, include_override)
        if record["author_position"] is None:
            excluded.append(reasoned_entry(record, "unable to determine author position"))
            continue

        kept_candidates.append(record)

    dedupe_groups: dict[str, list[dict]] = defaultdict(list)
    for record in kept_candidates:
        dedupe_groups[dedupe_key(record)].append(record)

    curated = []
    for _, group in dedupe_groups.items():
        chosen = merge_dedupe_group(group)
        curated.append(chosen)
        for duplicate in group:
            if duplicate["bibcode"] != chosen["bibcode"]:
                excluded.append(
                    reasoned_entry(
                        duplicate,
                        f"deduped in favor of `{chosen['bibcode']}`",
                    )
                )

    curated.sort(
        key=lambda item: (item.get("pubdate") or "", item.get("bibcode") or ""),
        reverse=True,
    )

    sections = {
        "first_author": [],
        "second_author": [],
        "coauthor": [],
    }

    for record in curated:
        bibcode = record["bibcode"]
        advisee_metadata = led_papers.get(bibcode)
        if advisee_metadata:
            record["advisee_led"] = True
            record["advisee_name"] = advisee_metadata["name"]
            record["advisee_category"] = advisee_metadata["category"]
            record["advisee_symbol"] = advisee_metadata["symbol"]
        else:
            record["advisee_led"] = False
            record["advisee_name"] = None
            record["advisee_category"] = None
            record["advisee_symbol"] = None

        position = record["author_position"]
        if position == 0:
            record["section"] = "first_author"
            sections["first_author"].append(record)
        elif position == 1:
            record["section"] = "second_author"
            sections["second_author"].append(record)
        else:
            record["section"] = "coauthor"
            sections["coauthor"].append(record)

    ads_metrics = {
        "total_papers": len(curated),
        "total_citations": sum(record["citations"] for record in curated),
        "h_index": compute_h_index(record["citations"] for record in curated),
        "updated_on": ads_snapshot.fetched_at.date().isoformat(),
    }

    scholar_metrics = overrides.get("google_scholar")
    if scholar_metrics:
        scholar_metrics = dict(scholar_metrics)
        if "profile_id" in scholar_metrics and "profile_url" not in scholar_metrics:
            scholar_metrics["profile_url"] = (
                "https://scholar.google.com/citations?user="
                f"{scholar_metrics['profile_id']}&hl=en"
            )

    if orcid_summaries:
        curated_index = build_identifier_index(curated)
        orcid_audit["remaining_missing_from_curated"] = [
            summary
            for summary in orcid_summaries
            if not matches_identifier(summary, curated_index)
        ]

    summary_tex = render_summary(ads_metrics, scholar_metrics, advisee_categories)
    SUMMARY_TEX_PATH.write_text(summary_tex, encoding="utf-8")
    ADVISING_TEX_PATH.write_text(
        render_advising(advising_entries),
        encoding="utf-8",
    )

    section_paths = {
        "first_author": FIRST_AUTHOR_TEX_PATH,
        "second_author": SECOND_AUTHOR_TEX_PATH,
        "coauthor": COAUTHOR_TEX_PATH,
    }
    for name, records in sections.items():
        path = section_paths[name]
        rendered = []
        total = len(records)
        for index, record in enumerate(records, start=1):
            rendered.append(format_publication(record, total - index + 1))
            rendered.append("")
        path.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")

    curated_payload = {
        "ads_metrics": ads_metrics,
        "publications": curated,
    }
    if scholar_metrics:
        curated_payload["google_scholar_metrics"] = scholar_metrics
    CURATED_PUBLICATIONS_PATH.write_text(
        json.dumps(curated_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    excluded.sort(key=lambda item: (item["reason"], item.get("year") or "", item.get("title") or ""))
    audit_payload = {
        "ads_metrics": ads_metrics,
        "counts_by_section": {name: len(records) for name, records in sections.items()},
        "counts_by_exclusion_reason": dict(Counter(entry["reason"] for entry in excluded)),
        "included": [
            {
                "bibcode": record["bibcode"],
                "title": record["title"],
                "section": record["section"],
                "author_position": record["author_position"] + 1,
                "advisee_led": record["advisee_led"],
                "advisee_name": record["advisee_name"],
                "advisee_category": record["advisee_category"],
                "selection_reason": record["selection_reason"],
            }
            for record in curated
        ],
        "excluded": excluded,
    }
    if scholar_metrics:
        audit_payload["google_scholar_metrics"] = scholar_metrics
    PUBLICATIONS_AUDIT_PATH.write_text(
        json.dumps(audit_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ORCID_AUDIT_PATH.write_text(
        json.dumps(orcid_audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit_lines = [
        "# Publication Audit",
        "",
        f"- ADS curated total papers: {ads_metrics['total_papers']}",
        f"- ADS curated total citations: {ads_metrics['total_citations']}",
        f"- ADS curated h-index: {ads_metrics['h_index']}",
    ]
    if scholar_metrics:
        audit_lines.extend(
            [
                f"- Google Scholar citations: {scholar_metrics['citations']}",
                f"- Google Scholar h-index: {scholar_metrics['h_index']}",
            ]
        )

    audit_lines.extend(["", "## Included Counts", ""])
    for name, records in sections.items():
        audit_lines.append(f"- {name.replace('_', ' ')}: {len(records)}")

    advisee_led_counts = Counter(
        record["advisee_category"] for record in curated if record.get("advisee_category")
    )
    if advisee_led_counts:
        audit_lines.extend(["", "## Advisee-led Counts", ""])
        for category, metadata in advisee_categories.items():
            audit_lines.append(
                f"- {metadata['legend']}: {advisee_led_counts.get(category, 0)}"
            )

    audit_lines.extend(["", "## Exclusion Reasons", ""])
    for reason, count in Counter(entry["reason"] for entry in excluded).most_common():
        audit_lines.append(f"- {reason}: {count}")

    PUBLICATIONS_AUDIT_MARKDOWN_PATH.write_text(
        "\n".join(audit_lines) + "\n",
        encoding="utf-8",
    )

    return {
        "ads_metrics": ads_metrics,
        "ads_snapshot": {
            "path": str(ads_snapshot.path),
            "fetched_at": ads_snapshot.fetched_at.isoformat(),
            "record_count": ads_snapshot.record_count,
            "legacy": ads_snapshot.legacy,
        },
        "counts_by_section": {name: len(records) for name, records in sections.items()},
        "curated_publications": curated,
        "orcid_audit": orcid_audit,
    }
