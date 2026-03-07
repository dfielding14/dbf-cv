# dbf-cv

Personal LuaLaTeX CV build for Drummond Fielding.

## What this repo does

This repo builds three PDF variants from one curated publication dataset:

- `full`: full CV plus publication list
- `publications`: publication list only
- `summary_only`: full CV without the detailed publication list

The publication pipeline refreshes ADS, reconciles against ORCID, curates the final publication set, computes citations and h-index, and marks advisee-led papers with role-specific symbols.

## Repository layout

```text
data/                  human-edited source of truth
src/dbf_cv/            Python package and CLI
tex/                   LuaLaTeX class, preamble, templates, variants
assets/fonts/          redistributable fallback fonts
build/                 generated TeX and LaTeX intermediates (gitignored)
cache/                 ADS snapshot and audits (gitignored)
output/                PDFs and rendered previews (gitignored)
tests/                 lightweight unit tests
```

## Setup

1. Install Python dependencies:

```bash
python -m pip install -e .
```

2. Install LuaLaTeX tooling:

```bash
brew install poppler
brew install --cask mactex-no-gui
```

3. Configure ADS:

```bash
export ADS_DEV_KEY=your_ads_token_here
```

You can also copy `.env.example` into your shell startup or an env loader.

## Build commands

Default build:

```bash
python -m dbf_cv build
```

Build one variant:

```bash
python -m dbf_cv build --variant publications
```

Reuse the current ADS snapshot:

```bash
python -m dbf_cv build --skip-ads-refresh
```

Refresh the ADS snapshot only:

```bash
python -m dbf_cv refresh-pubs
```

Regenerate audits without PDFs:

```bash
python -m dbf_cv audit
```

Build and render PNG previews for visual inspection:

```bash
python -m dbf_cv render-check
```

## Fonts

The default font mode is `auto`.

- If GT America is installed locally, the build uses GT America.
- Otherwise the build falls back to the bundled open fonts in `assets/fonts/`.

To force the open-font build:

```bash
python -m dbf_cv build --font-profile bundled
```

To force GT America:

```bash
python -m dbf_cv build --font-profile gt-america
```

If GT America is installed in a nonstandard location, set:

```bash
export FIELDING_CV_GT_FONT_DIR=/path/to/font/dir
```

## Editing the CV

### Profile and contact information

Edit [`data/profile.yaml`](data/profile.yaml).

### Appointments, grants, talks, awards, service, outreach

Edit [`data/sections.yaml`](data/sections.yaml).

### Advisees and advisee-led papers

Edit [`data/advisees.yaml`](data/advisees.yaml).

Each advisee entry can appear in the visible advising section and can also own a list of `led_papers` by ADS bibcode. The category controls the symbol used in the publication list:

- `graduate` -> `\ddagger`
- `undergraduate` -> `\mathsection`
- `postdoc` -> `\dagger`

### Publication curation rules

Edit [`data/publication_rules.yaml`](data/publication_rules.yaml).

This file controls:

- ADS query aliases
- allowed doctypes
- venue exclusions
- include and exclude overrides
- promoted conference papers
- ORCID reconciliation

## Outputs

After a build, the key outputs are:

- `output/pdf/dbf-cv-full.pdf`
- `output/pdf/dbf-cv-publications.pdf`
- `output/pdf/dbf-cv-summary-only.pdf`
- `cache/publications_curated.json`
- `cache/publications_audit.json`
- `cache/orcid_audit.json`
- `output/rendered/*.png` from `render-check`

## Notes

- ADS is the canonical citation source for per-paper counts, total citations, and h-index.
- Google Scholar is not scraped automatically.
- `cache/`, `build/`, and `output/` are intentionally untracked.
