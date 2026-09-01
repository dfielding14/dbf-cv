"""ADS integration for the Fielding CV build."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

SNAPSHOT_SCHEMA_VERSION = 1


DEFAULT_FIELDS = [
    "id",
    "title",
    "author",
    "doi",
    "year",
    "pubdate",
    "pub",
    "volume",
    "page",
    "identifier",
    "doctype",
    "citation_count",
    "bibcode",
]


@dataclass(frozen=True)
class AdsSnapshot:
    """ADS snapshot records plus provenance."""

    records: list[dict]
    fetched_at: datetime
    query: str | None
    fields: list[str]
    record_count: int
    path: Path
    schema_version: int | None = SNAPSHOT_SCHEMA_VERSION
    legacy: bool = False


def load_rules(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def configure_ads(ads_module) -> None:
    for env_var in ("ADS_DEV_KEY", "ADS_TOKEN"):
        token = os.environ.get(env_var)
        if token:
            ads_module.config.token = token.strip()
            return

    token = getattr(getattr(ads_module, "config", None), "token", None)
    if token:
        return

    raise RuntimeError(
        "No ADS API token configured. Set ADS_DEV_KEY or ADS_TOKEN, "
        "or configure the python-ads client locally."
    )


def build_query(config: dict) -> str:
    query_terms = []
    orcid = config.get("orcid")
    if orcid:
        query_terms.append(f'orcid:"{orcid}"')

    for alias in config.get("author_aliases", {}).get("query", []):
        query_terms.append(f'author:"{alias}"')

    if not query_terms:
        raise ValueError("No ADS query terms configured in data/publication_rules.yaml.")

    return " OR ".join(query_terms)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_snapshot_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_snapshot_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def snapshot_payload(
    *,
    records: list[dict],
    fetched_at: datetime,
    query: str | None,
    fields: list[str],
) -> dict:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "fetched_at": format_snapshot_datetime(fetched_at),
        "query": query,
        "fields": list(fields),
        "record_count": len(records),
        "records": records,
    }


def write_snapshot(
    path: Path,
    *,
    records: list[dict],
    fetched_at: datetime,
    query: str | None,
    fields: list[str],
) -> AdsSnapshot:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot_payload(
        records=records,
        fetched_at=fetched_at,
        query=query,
        fields=fields,
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return load_snapshot(path)


def extract_arxiv_id(paper) -> str | None:
    for identifier in getattr(paper, "identifier", None) or []:
        if isinstance(identifier, str) and identifier.startswith("arXiv:"):
            return identifier.split(":", 1)[1]
    page = getattr(paper, "page", None) or []
    if page and isinstance(page[0], str) and page[0].startswith("arXiv:"):
        return page[0].split(":", 1)[1]
    return None


def normalize_record(paper) -> dict:
    for field in ("bibcode", "doctype", "year", "pubdate"):
        value = getattr(paper, field, None)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"ADS record has missing or invalid `{field}`.")
    for field in ("title", "author"):
        value = getattr(paper, field, None)
        if not isinstance(value, list) or not value or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError(f"ADS record has missing or invalid `{field}`.")
    citations = getattr(paper, "citation_count", None)
    if citations is not None and (type(citations) is not int or citations < 0):
        raise ValueError("ADS record has an invalid `citation_count`.")

    page = getattr(paper, "page", None) or []
    page_value = None
    if page:
        candidate = page[0]
        if isinstance(candidate, int):
            page_value = candidate
        elif isinstance(candidate, str) and not candidate.startswith("arXiv:"):
            try:
                page_value = int(candidate)
            except ValueError:
                page_value = candidate

    doi_list = getattr(paper, "doi", None) or []
    bibcode = getattr(paper, "bibcode", None)
    return {
        "bibcode": bibcode,
        "doctype": getattr(paper, "doctype", None),
        "authors": getattr(paper, "author", None) or [],
        "year": getattr(paper, "year", None),
        "pubdate": getattr(paper, "pubdate", None),
        "doi": doi_list[0] if doi_list else None,
        "title": (getattr(paper, "title", None) or [None])[0],
        "pub": getattr(paper, "pub", None),
        "volume": getattr(paper, "volume", None),
        "page": page_value,
        "arxiv": extract_arxiv_id(paper),
        "citations": citations or 0,
        "url": f"http://adsabs.harvard.edu/abs/{bibcode}" if bibcode else None,
    }


def _complete_result_count(response) -> int:
    if response.responseHeader.get("partialResults") or (
        response.json["response"].get("numFoundExact") is False
    ):
        raise ValueError("ADS returned partial results or an inexact result count.")
    count = response.numFound
    if type(count) is not int or count <= 0:
        raise ValueError("ADS returned an empty or invalid result count.")
    return count


def refresh_snapshot(rules_path: Path, output_path: Path) -> AdsSnapshot:
    try:
        import ads
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The `ads` Python package is not installed in this interpreter."
        ) from exc

    configure_ads(ads)
    rules = load_rules(rules_path)
    query = build_query(rules)

    try:
        papers = ads.SearchQuery(
            q=query,
            sort="date desc, bibcode desc",
            rows=200,
            fl=DEFAULT_FIELDS,
        )
        papers.execute()
        expected_count = _complete_result_count(papers.response)
        rows = papers.query["rows"]
        papers.max_pages = (expected_count + rows - 1) // rows

        records = []
        bibcodes = set()
        for paper in papers:
            if _complete_result_count(papers.response) != expected_count:
                raise ValueError("ADS result count changed during pagination; try again later.")
            record = normalize_record(paper)
            if record["bibcode"] in bibcodes:
                raise ValueError(f"ADS returned duplicate bibcode `{record['bibcode']}`.")
            bibcodes.add(record["bibcode"])
            records.append(record)

        if len(records) != expected_count:
            raise ValueError(
                f"ADS returned {len(records)} records but reported {expected_count}; "
                "snapshot was not replaced."
            )
        records.sort(
            key=lambda item: (item.get("pubdate") or "", item.get("bibcode") or ""),
            reverse=True,
        )
    except Exception as exc:  # pragma: no cover - depends on ADS client/network
        raise RuntimeError(f"ADS refresh failed: {exc}") from exc

    return write_snapshot(
        output_path,
        records=records,
        fetched_at=utc_now(),
        query=query,
        fields=DEFAULT_FIELDS,
    )


def load_snapshot(path: Path) -> AdsSnapshot:
    if not path.exists():
        raise RuntimeError(f"ADS snapshot is missing: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ADS snapshot is not valid JSON: {exc}") from exc

    if isinstance(payload, list):
        if not payload:
            raise RuntimeError("ADS snapshot is empty; run `python -m dbf_cv refresh-pubs`.")
        fetched_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        return AdsSnapshot(
            records=payload,
            fetched_at=fetched_at,
            query=None,
            fields=[],
            record_count=len(payload),
            path=path,
            schema_version=None,
            legacy=True,
        )

    if not isinstance(payload, dict):
        raise RuntimeError("ADS snapshot must be a JSON object or legacy record list.")

    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise RuntimeError(
            "Unsupported ADS snapshot schema version: "
            f"{payload.get('schema_version')!r}."
        )

    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise RuntimeError("ADS snapshot is empty; run `python -m dbf_cv refresh-pubs`.")

    fetched_at = payload.get("fetched_at")
    if not isinstance(fetched_at, str) or not fetched_at.strip():
        raise RuntimeError("ADS snapshot is missing `fetched_at` provenance.")

    fields = payload.get("fields")
    if not isinstance(fields, list):
        raise RuntimeError("ADS snapshot field `fields` must be a list.")

    record_count = payload.get("record_count")
    if record_count != len(records):
        raise RuntimeError(
            "ADS snapshot record_count does not match records length: "
            f"{record_count!r} != {len(records)}."
        )

    return AdsSnapshot(
        records=records,
        fetched_at=parse_snapshot_datetime(fetched_at),
        query=payload.get("query"),
        fields=[str(field) for field in fields],
        record_count=len(records),
        path=path,
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        legacy=False,
    )


def validate_snapshot_freshness(
    path: Path,
    max_age_hours: float,
    *,
    now: datetime | None = None,
) -> AdsSnapshot:
    snapshot = load_snapshot(path)
    reference = now or utc_now()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    age = reference - snapshot.fetched_at
    max_age = timedelta(hours=max_age_hours)
    if age > max_age:
        hours_old = age.total_seconds() / 3600
        raise RuntimeError(
            f"ADS snapshot is stale: {path} was last updated at "
            f"{snapshot.fetched_at:%Y-%m-%d %H:%M:%S %Z} "
            f"and is {hours_old:.1f} hours old."
        )
    return snapshot
