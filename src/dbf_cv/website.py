"""Website sync helpers for publishing CV PDFs into the site repo."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

import yaml

from .paths import VARIANT_TO_PDF, WEBSITE_SYNC_PATH


@dataclass(frozen=True)
class WebsiteSyncConfig:
    """Validated website publishing configuration."""

    site_data_path: str
    timezone: str
    pdf_targets: dict[str, str]
    expected_document_urls: dict[str, str]
    last_updated_keys: dict[str, str]


def load_sync_config(path: Path = WEBSITE_SYNC_PATH) -> WebsiteSyncConfig:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)

    if not isinstance(payload, dict):
        raise ValueError("data/website_sync.yaml must contain a top-level object.")

    required_fields = (
        "site_data_path",
        "timezone",
        "pdf_targets",
        "expected_document_urls",
        "last_updated_keys",
    )
    missing = [field for field in required_fields if field not in payload]
    if missing:
        raise ValueError(
            "data/website_sync.yaml is missing required fields: "
            + ", ".join(sorted(missing))
        )

    pdf_targets = payload["pdf_targets"]
    expected_document_urls = payload["expected_document_urls"]
    last_updated_keys = payload["last_updated_keys"]
    for field_name, value in (
        ("pdf_targets", pdf_targets),
        ("expected_document_urls", expected_document_urls),
        ("last_updated_keys", last_updated_keys),
    ):
        if not isinstance(value, dict) or not value:
            raise ValueError(f"data/website_sync.yaml field `{field_name}` must be a non-empty object.")

    for variant in pdf_targets:
        if variant not in VARIANT_TO_PDF:
            raise ValueError(
                f"data/website_sync.yaml references unknown PDF variant `{variant}`."
            )

    return WebsiteSyncConfig(
        site_data_path=str(payload["site_data_path"]),
        timezone=str(payload["timezone"]),
        pdf_targets={str(key): str(value) for key, value in pdf_targets.items()},
        expected_document_urls={
            str(key): str(value) for key, value in expected_document_urls.items()
        },
        last_updated_keys={str(key): str(value) for key, value in last_updated_keys.items()},
    )


def required_variants(config: WebsiteSyncConfig) -> list[str]:
    return list(config.pdf_targets)


def required_pdf_paths(
    config: WebsiteSyncConfig,
    source_pdfs: Mapping[str, Path] | None = None,
) -> dict[str, Path]:
    resolved = source_pdfs or VARIANT_TO_PDF
    return {variant: Path(resolved[variant]) for variant in required_variants(config)}


def format_website_date(timezone_name: str, when: datetime | None = None) -> str:
    moment = when or datetime.now(ZoneInfo(timezone_name))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(timezone_name))
    localized = moment.astimezone(ZoneInfo(timezone_name))
    return f"{localized.strftime('%B')} {localized.day}, {localized.year}"


def _files_match(source: Path, target: Path) -> bool:
    if not target.exists():
        return False
    return source.read_bytes() == target.read_bytes()


def _load_site_data(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Website site data must be a YAML object: {path}")
    return payload


def _write_site_data(path: Path, payload: dict) -> None:
    path.write_text(
        yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )


def validate_site_contract(site_data: dict, config: WebsiteSyncConfig) -> None:
    documents = site_data.get("documents")
    if not isinstance(documents, dict):
        raise ValueError("Website `_data/site.yml` is missing the `documents` object.")

    for key, expected in config.expected_document_urls.items():
        actual = documents.get(key)
        if actual != expected:
            raise ValueError(
                "Website `_data/site.yml` no longer matches the expected documents contract: "
                f"`documents.{key}` is `{actual}` but expected `{expected}`."
            )

    last_updated = site_data.get("last_updated")
    if not isinstance(last_updated, dict):
        raise ValueError("Website `_data/site.yml` is missing the `last_updated` object.")

    for logical_key, site_key in config.last_updated_keys.items():
        if logical_key not in config.expected_document_urls and logical_key not in {"cv", "publications"}:
            raise ValueError(
                "data/website_sync.yaml `last_updated_keys` contains an unknown logical key "
                f"`{logical_key}`."
            )
        if site_key not in last_updated:
            raise ValueError(
                "Website `_data/site.yml` is missing the expected last-updated field "
                f"`last_updated.{site_key}`."
            )


def sync_website_repo(
    website_repo: Path,
    config: WebsiteSyncConfig,
    *,
    source_pdfs: Mapping[str, Path] | None = None,
    when: datetime | None = None,
) -> dict:
    website_repo = Path(website_repo).expanduser().resolve()
    if not website_repo.exists():
        raise RuntimeError(f"Website repo path does not exist: {website_repo}")

    site_data_path = website_repo / config.site_data_path
    if not site_data_path.exists():
        raise RuntimeError(f"Website site data file is missing: {site_data_path}")

    pdf_paths = required_pdf_paths(config, source_pdfs)
    for variant, source_path in pdf_paths.items():
        if not source_path.exists():
            raise RuntimeError(
                f"Required PDF output for website publishing is missing: {source_path} "
                f"(variant `{variant}`)."
            )

    site_data = _load_site_data(site_data_path)
    validate_site_contract(site_data, config)

    changed_files: list[str] = []
    copied_files: list[str] = []
    updated_fields: list[str] = []

    for variant, relative_target in config.pdf_targets.items():
        source_path = pdf_paths[variant]
        target_path = website_repo / relative_target
        if not target_path.parent.exists():
            raise RuntimeError(f"Website target directory is missing: {target_path.parent}")
        if not _files_match(source_path, target_path):
            shutil.copy2(source_path, target_path)
            changed_files.append(str(target_path.relative_to(website_repo)))
            copied_files.append(str(target_path.relative_to(website_repo)))

    display_date = format_website_date(config.timezone, when)
    last_updated = site_data["last_updated"]
    for _, site_key in config.last_updated_keys.items():
        if last_updated.get(site_key) != display_date:
            last_updated[site_key] = display_date
            updated_fields.append(f"last_updated.{site_key}")

    if updated_fields:
        _write_site_data(site_data_path, site_data)
        changed_files.append(str(site_data_path.relative_to(website_repo)))

    return {
        "changed": bool(changed_files),
        "changed_files": changed_files,
        "copied_files": copied_files,
        "updated_fields": updated_fields,
        "display_date": display_date,
        "website_repo": str(website_repo),
    }
