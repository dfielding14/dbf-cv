"""Command-line interface for the Fielding CV build."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .ads import refresh_snapshot, validate_snapshot_freshness
from .fonts import write_font_profile
from .paths import (
    ADS_SNAPSHOT_PATH,
    ADVISEES_PATH,
    PDF_OUTPUT_DIR,
    PUBLICATION_RULES_PATH,
    RENDER_OUTPUT_DIR,
    REPO_ROOT,
    TEX_CACHE_DIR,
    VARIANT_BUILD_DIR,
    VARIANT_TO_PDF,
    VARIANT_TO_TEX,
    ensure_runtime_directories,
)
from .publications import generate_publication_artifacts
from .render import generate_static_tex
from .website import load_sync_config, required_variants, sync_website_repo


VARIANT_ALIASES = {
    "publist": "publications",
    "no-publist": "summary_only",
    "no_publist": "summary_only",
    "summary": "summary_only",
}


class CommandError(RuntimeError):
    """Raised for user-facing CLI failures."""


def print_step(message: str) -> None:
    print(message, flush=True)


def resolve_command(candidate: str) -> str:
    expanded = Path(candidate).expanduser()
    if expanded.exists():
        return str(expanded)

    resolved = shutil.which(candidate)
    if resolved:
        return resolved

    raise CommandError(f"Required command not found: {candidate}")


def normalize_variant(name: str) -> str:
    normalized = VARIANT_ALIASES.get(name.strip().casefold(), name.strip().casefold())
    if normalized not in VARIANT_TO_TEX:
        raise CommandError(f"Unknown CV variant: {name}")
    return normalized


def resolve_variants(values: list[str] | None) -> list[str]:
    if not values:
        return list(VARIANT_TO_TEX)

    resolved: list[str] = []
    seen = set()
    for value in values:
        for token in value.replace(",", " ").split():
            variant = normalize_variant(token)
            if variant not in seen:
                seen.add(variant)
                resolved.append(variant)
    return resolved


def latex_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["TEXMFVAR"] = str(TEX_CACHE_DIR)
    env["TEXMFCACHE"] = str(TEX_CACHE_DIR)

    texinputs = [
        ".",
        f"{REPO_ROOT.as_posix()}/",
        f"{(REPO_ROOT / 'tex').as_posix()}//",
        f"{(REPO_ROOT / 'build' / 'generated').as_posix()}/",
    ]
    existing = env.get("TEXINPUTS", "")
    env["TEXINPUTS"] = ":".join(texinputs + [existing])
    return env


def run_subprocess(command: list[str], *, env: dict[str, str] | None = None) -> None:
    printable = " ".join(shlex_quote(part) for part in command)
    print(f"$ {printable}", flush=True)
    try:
        subprocess.run(
            [str(part) for part in command],
            cwd=REPO_ROOT,
            env=env,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise CommandError(
            f"Command failed with exit code {exc.returncode}: {printable}"
        ) from exc


def shlex_quote(part: str | Path) -> str:
    text = str(part)
    if not text or any(char.isspace() for char in text):
        return repr(text)
    return text


def refresh_pubs() -> list[dict]:
    print_step("[1/4] Refreshing ADS snapshot")
    records = refresh_snapshot(PUBLICATION_RULES_PATH, ADS_SNAPSHOT_PATH)
    print_step(f"Refreshed {len(records)} ADS records into {ADS_SNAPSHOT_PATH}.")
    return records


def prepare_generated_files(
    *,
    font_profile: str,
    skip_ads_refresh: bool,
    max_age_hours: float,
) -> tuple[str, dict]:
    ensure_runtime_directories()

    print_step("[1/4] Selecting font profile")
    resolved_font = write_font_profile(font_profile)
    print_step(f"Using font profile: {resolved_font}")

    if skip_ads_refresh:
        print_step("[2/4] Validating ADS snapshot freshness")
    else:
        refresh_pubs()
        print_step("[2/4] Validating ADS snapshot freshness")
    validate_snapshot_freshness(ADS_SNAPSHOT_PATH, max_age_hours)

    print_step("[3/4] Rendering static TeX fragments")
    generate_static_tex()

    print_step("[4/4] Curating publications and advising artifacts")
    artifacts = generate_publication_artifacts(
        ADS_SNAPSHOT_PATH,
        PUBLICATION_RULES_PATH,
        ADVISEES_PATH,
    )
    return resolved_font, artifacts


def build_variant(variant: str, latexmk_cmd: str) -> Path:
    source_tex = VARIANT_TO_TEX[variant]
    build_dir = VARIANT_BUILD_DIR[variant]
    output_pdf = VARIANT_TO_PDF[variant]

    build_dir.mkdir(parents=True, exist_ok=True)
    PDF_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    run_subprocess(
        [
            latexmk_cmd,
            "-g",
            "-lualatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-outdir={build_dir}",
            str(source_tex),
        ],
        env=latex_environment(),
    )

    built_pdf = build_dir / source_tex.with_suffix(".pdf").name
    if not built_pdf.exists():
        raise CommandError(f"Expected PDF was not created: {built_pdf}")

    shutil.copy2(built_pdf, output_pdf)
    return output_pdf


def render_variant_previews(variants: list[str], pdftoppm_cmd: str) -> None:
    print_step("[5/5] Rendering PDF previews")
    RENDER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for variant in variants:
        pdf_path = VARIANT_TO_PDF[variant]
        prefix = RENDER_OUTPUT_DIR / pdf_path.stem
        run_subprocess(
            [
                pdftoppm_cmd,
                "-png",
                str(pdf_path),
                str(prefix),
            ]
        )


def print_artifact_summary(
    *,
    variants: list[str] | None,
    artifacts: dict,
    resolved_font: str,
    rendered: bool,
) -> None:
    metrics = artifacts.get("ads_metrics", {})
    print("\nBuild complete.")
    print(f"Font profile: {resolved_font}")
    if metrics:
        print(
            "ADS summary: "
            f"{metrics.get('total_papers', '?')} publications, "
            f"{metrics.get('total_citations', '?')} citations, "
            f"h-index {metrics.get('h_index', '?')} "
            f"({metrics.get('updated_on', 'unknown date')})"
        )

    if variants:
        print("PDF outputs:")
        for variant in variants:
            print(f"- {VARIANT_TO_PDF[variant]}")

    if rendered:
        print(f"PNG previews: {RENDER_OUTPUT_DIR}")


def ensure_publish_pdfs_exist(args: argparse.Namespace, variants: list[str]) -> None:
    missing = [variant for variant in variants if not VARIANT_TO_PDF[variant].exists()]
    if not missing:
        return

    latexmk_cmd = resolve_command(args.latexmk)
    print_step("[1/2] Missing website PDFs detected; building bundled-font outputs")
    prepare_generated_files(
        font_profile="bundled",
        skip_ads_refresh=args.skip_ads_refresh,
        max_age_hours=args.max_age_hours,
    )
    print_step("[2/2] Building required PDF variants")
    for variant in variants:
        print_step(f"  - {variant}")
        build_variant(variant, latexmk_cmd)


def run_publish_website(args: argparse.Namespace) -> int:
    config = load_sync_config()
    variants = required_variants(config)

    ensure_publish_pdfs_exist(args, variants)

    print_step("[1/1] Syncing PDFs and dates into the website repo")
    result = sync_website_repo(Path(args.website_repo), config)

    if result["changed"]:
        print("Website sync complete.")
        print(f"Updated website repo: {result['website_repo']}")
        print(f"Last updated date: {result['display_date']}")
        print("Changed files:")
        for path in result["changed_files"]:
            print(f"- {path}")
    else:
        print("Website sync complete. No changes were needed.")
        print(f"Website repo: {result['website_repo']}")
        print(f"Last updated date: {result['display_date']}")
    return 0


def add_generation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--skip-ads-refresh",
        action="store_true",
        help="Reuse the existing cache/ads_snapshot.json instead of querying ADS.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=24.0,
        help="Maximum allowed age for the ADS snapshot when skipping refresh.",
    )
    parser.add_argument(
        "--font-profile",
        choices=["auto", "bundled", "gt-america"],
        default="auto",
        help="Preferred font profile for LuaLaTeX builds.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbf-cv",
        description="Build Drummond Fielding's CV and publication list variants.",
    )
    subparsers = parser.add_subparsers(dest="command")

    build_cmd = subparsers.add_parser("build", help="Refresh data, generate TeX, and build PDFs.")
    add_generation_arguments(build_cmd)
    build_cmd.add_argument(
        "--variant",
        action="append",
        help="Variant(s) to build: full, publications, summary_only.",
    )
    build_cmd.add_argument(
        "--latexmk",
        default="latexmk",
        help="latexmk executable or absolute path.",
    )
    build_cmd.set_defaults(command="build")

    refresh_cmd = subparsers.add_parser("refresh-pubs", help="Refresh cache/ads_snapshot.json.")
    refresh_cmd.set_defaults(command="refresh-pubs")

    audit_cmd = subparsers.add_parser(
        "audit",
        help="Refresh data if needed and regenerate the machine-readable audits without PDFs.",
    )
    add_generation_arguments(audit_cmd)
    audit_cmd.set_defaults(command="audit")

    render_cmd = subparsers.add_parser(
        "render-check",
        help="Build PDFs and render PNG previews for visual inspection.",
    )
    add_generation_arguments(render_cmd)
    render_cmd.add_argument(
        "--variant",
        action="append",
        help="Variant(s) to build: full, publications, summary_only.",
    )
    render_cmd.add_argument(
        "--latexmk",
        default="latexmk",
        help="latexmk executable or absolute path.",
    )
    render_cmd.add_argument(
        "--pdftoppm",
        default="pdftoppm",
        help="pdftoppm executable or absolute path.",
    )
    render_cmd.set_defaults(command="render-check")

    publish_cmd = subparsers.add_parser(
        "publish-website",
        help="Copy generated PDFs into the website repo and update _data/site.yml dates.",
    )
    publish_cmd.add_argument(
        "--skip-ads-refresh",
        action="store_true",
        help="Reuse the existing cache/ads_snapshot.json if PDFs need to be built first.",
    )
    publish_cmd.add_argument(
        "--max-age-hours",
        type=float,
        default=24.0,
        help="Maximum allowed age for the ADS snapshot when reusing cached data.",
    )
    publish_cmd.add_argument(
        "--website-repo",
        required=True,
        help="Path to the local checkout of dfielding14.github.io.",
    )
    publish_cmd.add_argument(
        "--latexmk",
        default="latexmk",
        help="latexmk executable or absolute path when PDFs need to be built first.",
    )
    publish_cmd.set_defaults(command="publish-website")

    return parser


def run_build(args: argparse.Namespace, *, render_check: bool) -> int:
    variants = resolve_variants(args.variant)
    latexmk_cmd = resolve_command(args.latexmk)
    pdftoppm_cmd = resolve_command(args.pdftoppm) if render_check else None

    resolved_font, artifacts = prepare_generated_files(
        font_profile=args.font_profile,
        skip_ads_refresh=args.skip_ads_refresh,
        max_age_hours=args.max_age_hours,
    )

    print_step("[5/5] Building PDF variants")
    for variant in variants:
        print_step(f"  - {variant}")
        build_variant(variant, latexmk_cmd)

    if render_check and pdftoppm_cmd is not None:
        render_variant_previews(variants, pdftoppm_cmd)

    print_artifact_summary(
        variants=variants,
        artifacts=artifacts,
        resolved_font=resolved_font,
        rendered=render_check,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        args_list = ["build"]
    elif args_list[0] not in {
        "build",
        "refresh-pubs",
        "audit",
        "render-check",
        "publish-website",
        "-h",
        "--help",
    }:
        args_list = ["build", *args_list]

    args = parser.parse_args(args_list)

    try:
        ensure_runtime_directories()
        if args.command == "refresh-pubs":
            refresh_pubs()
            return 0
        if args.command == "audit":
            resolved_font, artifacts = prepare_generated_files(
                font_profile=args.font_profile,
                skip_ads_refresh=args.skip_ads_refresh,
                max_age_hours=args.max_age_hours,
            )
            print_artifact_summary(
                variants=None,
                artifacts=artifacts,
                resolved_font=resolved_font,
                rendered=False,
            )
            return 0
        if args.command == "render-check":
            return run_build(args, render_check=True)
        if args.command == "publish-website":
            return run_publish_website(args)
        return run_build(args, render_check=False)
    except (CommandError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
