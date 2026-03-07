"""Repository path helpers for the Fielding CV project."""

from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PACKAGE_ROOT.parent
REPO_ROOT = SRC_ROOT.parent

DATA_DIR = REPO_ROOT / "data"
TEX_DIR = REPO_ROOT / "tex"
TEX_STYLES_DIR = TEX_DIR / "styles"
TEX_TEMPLATES_DIR = TEX_DIR / "templates"
TEX_VARIANTS_DIR = TEX_DIR / "variants"
ASSETS_DIR = REPO_ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
BUILD_DIR = REPO_ROOT / "build"
GENERATED_DIR = BUILD_DIR / "generated"
TEX_CACHE_DIR = BUILD_DIR / "texmf-var"
CACHE_DIR = REPO_ROOT / "cache"
OUTPUT_DIR = REPO_ROOT / "output"
PDF_OUTPUT_DIR = OUTPUT_DIR / "pdf"
RENDER_OUTPUT_DIR = OUTPUT_DIR / "rendered"
TESTS_DIR = REPO_ROOT / "tests"

PROFILE_PATH = DATA_DIR / "profile.yaml"
SECTIONS_PATH = DATA_DIR / "sections.yaml"
ADVISEES_PATH = DATA_DIR / "advisees.yaml"
PUBLICATION_RULES_PATH = DATA_DIR / "publication_rules.yaml"
WEBSITE_SYNC_PATH = DATA_DIR / "website_sync.yaml"

ADS_SNAPSHOT_PATH = CACHE_DIR / "ads_snapshot.json"
CURATED_PUBLICATIONS_PATH = CACHE_DIR / "publications_curated.json"
PUBLICATIONS_AUDIT_PATH = CACHE_DIR / "publications_audit.json"
ORCID_AUDIT_PATH = CACHE_DIR / "orcid_audit.json"
ORCID_WORKS_PATH = CACHE_DIR / "orcid_works.json"
PUBLICATIONS_AUDIT_MARKDOWN_PATH = CACHE_DIR / "publications_audit.md"

FONT_PROFILE_PATH = GENERATED_DIR / "font_profile.tex"
DOCUMENT_METADATA_PATH = GENERATED_DIR / "document_metadata.tex"
HEADER_PATH = GENERATED_DIR / "header.tex"
SECTIONS_TEX_PATH = GENERATED_DIR / "sections.tex"
ADVISING_TEX_PATH = GENERATED_DIR / "advising.tex"
SUMMARY_TEX_PATH = GENERATED_DIR / "summary.tex"
FIRST_AUTHOR_TEX_PATH = GENERATED_DIR / "publications_first_author.tex"
SECOND_AUTHOR_TEX_PATH = GENERATED_DIR / "publications_second_author.tex"
COAUTHOR_TEX_PATH = GENERATED_DIR / "publications_coauthor.tex"

VARIANT_TO_TEX = {
    "full": TEX_VARIANTS_DIR / "full.tex",
    "publications": TEX_VARIANTS_DIR / "publications.tex",
    "summary_only": TEX_VARIANTS_DIR / "summary_only.tex",
}

VARIANT_TO_PDF = {
    "full": PDF_OUTPUT_DIR / "dbf-cv-full.pdf",
    "publications": PDF_OUTPUT_DIR / "dbf-cv-publications.pdf",
    "summary_only": PDF_OUTPUT_DIR / "dbf-cv-summary-only.pdf",
}

VARIANT_BUILD_DIR = {
    "full": BUILD_DIR / "full",
    "publications": BUILD_DIR / "publications",
    "summary_only": BUILD_DIR / "summary_only",
}


def ensure_runtime_directories() -> None:
    """Create the directories used for generated artifacts."""

    for path in (
        BUILD_DIR,
        GENERATED_DIR,
        TEX_CACHE_DIR,
        CACHE_DIR,
        PDF_OUTPUT_DIR,
        RENDER_OUTPUT_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
