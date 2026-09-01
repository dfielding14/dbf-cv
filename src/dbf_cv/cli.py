"""Command-line interface for the Fielding CV build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ads import parse_snapshot_datetime, refresh_snapshot, validate_snapshot_freshness
from .fonts import write_font_profile
from .paths import (
    ADS_SNAPSHOT_PATH,
    ADVISEES_PATH,
    BUILD_MANIFEST_PATH,
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


def repo_relative(path: Path) -> str:
    resolved = Path(path).expanduser().resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_input_hashes() -> dict[str, str]:
    patterns = (
        "data/profile.yaml",
        "data/sections.yaml",
        "data/advisees.yaml",
        "data/publication_rules.yaml",
        "src/dbf_cv/**/*.py",
        "tex/**/*.tex",
        "tex/**/*.cls",
        "tex/**/*.sty",
        "assets/fonts/**/*.ttf",
        "assets/fonts/**/*.otf",
        "pyproject.toml",
    )
    paths = {
        path for pattern in patterns for path in REPO_ROOT.glob(pattern) if path.is_file()
    }
    if ADS_SNAPSHOT_PATH.is_file():
        paths.add(ADS_SNAPSHOT_PATH)
    return {repo_relative(path): sha256_file(path) for path in sorted(paths)}


def current_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def manifest_snapshot_time(manifest: dict) -> datetime:
    value = manifest.get("snapshot_fetched_at")
    if not isinstance(value, str) or not value.strip():
        raise CommandError("Build manifest is missing `snapshot_fetched_at`.")
    return parse_snapshot_datetime(value)


def validate_manifest_freshness(manifest: dict, max_age_hours: float) -> None:
    fetched_at = manifest_snapshot_time(manifest)
    age = datetime.now(timezone.utc) - fetched_at
    max_age = timedelta(hours=max_age_hours)
    if age > max_age:
        hours_old = age.total_seconds() / 3600
        raise CommandError(
            "Build manifest snapshot is stale: "
            f"{fetched_at:%Y-%m-%d %H:%M:%S %Z} is {hours_old:.1f} hours old."
        )


def write_build_manifest(
    *,
    variants: list[str],
    artifacts: dict,
    resolved_font: str,
    fallback_used: bool,
    output_path: Path = BUILD_MANIFEST_PATH,
) -> dict:
    snapshot = artifacts.get("ads_snapshot") or {}
    fetched_at = snapshot.get("fetched_at")
    if not isinstance(fetched_at, str) or not fetched_at.strip():
        raise CommandError("Publication artifacts are missing ADS snapshot provenance.")
    if artifacts.get("input_hashes") != build_input_hashes():
        raise CommandError("Build inputs changed during PDF generation; rebuild before publishing.")

    variant_payload = {}
    for variant in variants:
        pdf_path = VARIANT_TO_PDF[variant]
        if not pdf_path.exists():
            raise CommandError(f"Cannot write build manifest; PDF is missing: {pdf_path}")
        variant_payload[variant] = {
            "path": repo_relative(pdf_path),
            "sha256": sha256_file(pdf_path),
        }

    payload = {
        "schema_version": 1,
        "built_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "source_commit": current_git_commit(),
        "input_hashes": artifacts["input_hashes"],
        "snapshot_path": repo_relative(Path(snapshot.get("path", ADS_SNAPSHOT_PATH))),
        "snapshot_fetched_at": fetched_at,
        "snapshot_record_count": snapshot.get("record_count"),
        "fallback_used": bool(fallback_used),
        "font_profile": resolved_font,
        "variants": variant_payload,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def load_build_manifest(path: Path = BUILD_MANIFEST_PATH) -> dict:
    if not path.exists():
        raise CommandError(f"Build manifest is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CommandError(f"Build manifest is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CommandError("Build manifest must be a JSON object.")
    if payload.get("schema_version") != 1:
        raise CommandError(
            f"Unsupported build manifest schema version: {payload.get('schema_version')!r}."
        )
    return payload


def validate_build_manifest(
    variants: list[str],
    *,
    max_age_hours: float,
    path: Path = BUILD_MANIFEST_PATH,
) -> dict:
    manifest = load_build_manifest(path)
    validate_manifest_freshness(manifest, max_age_hours)
    if manifest.get("input_hashes") != build_input_hashes():
        raise CommandError("Build inputs changed or their hashes are missing from the manifest.")

    manifest_variants = manifest.get("variants")
    if not isinstance(manifest_variants, dict):
        raise CommandError("Build manifest field `variants` must be an object.")

    for variant in variants:
        entry = manifest_variants.get(variant)
        if not isinstance(entry, dict):
            raise CommandError(f"Build manifest is missing variant `{variant}`.")
        expected_path = repo_relative(VARIANT_TO_PDF[variant])
        if entry.get("path") != expected_path:
            raise CommandError(
                f"Build manifest path for `{variant}` is {entry.get('path')!r}; "
                f"expected {expected_path!r}."
            )
        pdf_path = VARIANT_TO_PDF[variant]
        if not pdf_path.exists():
            raise CommandError(f"PDF listed in build manifest is missing: {pdf_path}")
        if entry.get("sha256") != sha256_file(pdf_path):
            raise CommandError(f"PDF hash does not match build manifest: {pdf_path}")

    return manifest


def copy_snapshot(source: Path, target: Path) -> None:
    source = Path(source).expanduser()
    target = Path(target).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def refresh_pubs(promote_snapshot: Path | None = None) -> dict:
    print_step("[1/4] Refreshing ADS snapshot")
    snapshot = refresh_snapshot(PUBLICATION_RULES_PATH, ADS_SNAPSHOT_PATH)
    print_step(f"Refreshed {len(snapshot.records)} ADS records into {ADS_SNAPSHOT_PATH}.")
    if promote_snapshot is not None:
        copy_snapshot(ADS_SNAPSHOT_PATH, promote_snapshot)
        print_step(f"Promoted ADS snapshot into {Path(promote_snapshot).expanduser()}.")
    return {
        "snapshot": snapshot,
        "fallback_used": False,
    }


def prepare_generated_files(
    *,
    font_profile: str,
    skip_ads_refresh: bool,
    max_age_hours: float,
    fallback_snapshot: Path | None = None,
    promote_snapshot: Path | None = None,
) -> tuple[str, dict, bool]:
    ensure_runtime_directories()
    input_hashes = build_input_hashes()

    print_step("[1/4] Selecting font profile")
    resolved_font = write_font_profile(font_profile)
    print_step(f"Using font profile: {resolved_font}")

    if skip_ads_refresh:
        print_step("[2/4] Validating ADS snapshot freshness")
    else:
        try:
            refresh_result = refresh_pubs(promote_snapshot)
            fallback_used = bool(refresh_result["fallback_used"])
        except RuntimeError as exc:
            if fallback_snapshot is None:
                raise
            print_step(f"ADS refresh failed: {exc}")
            print_step(f"Trying fallback ADS snapshot: {Path(fallback_snapshot).expanduser()}")
            validate_snapshot_freshness(Path(fallback_snapshot).expanduser(), max_age_hours)
            copy_snapshot(Path(fallback_snapshot).expanduser(), ADS_SNAPSHOT_PATH)
            fallback_used = True
        print_step("[2/4] Validating ADS snapshot freshness")
    if skip_ads_refresh:
        fallback_used = False
    validate_snapshot_freshness(ADS_SNAPSHOT_PATH, max_age_hours)
    input_hashes[repo_relative(ADS_SNAPSHOT_PATH)] = sha256_file(ADS_SNAPSHOT_PATH)

    print_step("[3/4] Rendering static TeX fragments")
    generate_static_tex()

    print_step("[4/4] Curating publications and advising artifacts")
    artifacts = generate_publication_artifacts(
        ADS_SNAPSHOT_PATH,
        PUBLICATION_RULES_PATH,
        ADVISEES_PATH,
    )
    artifacts["input_hashes"] = input_hashes
    return resolved_font, artifacts, fallback_used


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


def build_required_publish_pdfs(args: argparse.Namespace, variants: list[str]) -> dict:
    latexmk_cmd = resolve_command(args.latexmk)
    print_step("[1/2] Building bundled-font website PDFs")
    resolved_font, artifacts, fallback_used = prepare_generated_files(
        font_profile="bundled",
        skip_ads_refresh=args.skip_ads_refresh,
        max_age_hours=args.max_age_hours,
        fallback_snapshot=args.fallback_snapshot,
        promote_snapshot=args.promote_snapshot,
    )
    print_step("[2/2] Building required PDF variants")
    for variant in variants:
        print_step(f"  - {variant}")
        build_variant(variant, latexmk_cmd)
    return write_build_manifest(
        variants=variants,
        artifacts=artifacts,
        resolved_font=resolved_font,
        fallback_used=fallback_used,
    )


def ensure_publish_pdfs_exist(args: argparse.Namespace, variants: list[str]) -> dict:
    try:
        return validate_build_manifest(variants, max_age_hours=args.max_age_hours)
    except CommandError as exc:
        print_step(f"Build manifest is not usable for website publishing: {exc}")
        return build_required_publish_pdfs(args, variants)


def run_publish_website(args: argparse.Namespace) -> int:
    config = load_sync_config()
    variants = required_variants(config)

    manifest = ensure_publish_pdfs_exist(args, variants)

    print_step("[1/1] Syncing PDFs and dates into the website repo")
    result = sync_website_repo(
        Path(args.website_repo),
        config,
        when=manifest_snapshot_time(manifest),
    )

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
    parser.add_argument(
        "--fallback-snapshot",
        type=Path,
        help="Use this ADS snapshot if a live ADS refresh fails.",
    )
    parser.add_argument(
        "--promote-snapshot",
        type=Path,
        help="Copy a successful live ADS refresh to this tracked snapshot path.",
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
    publish_cmd.add_argument(
        "--fallback-snapshot",
        type=Path,
        help="Use this ADS snapshot if a live ADS refresh is needed and fails.",
    )
    publish_cmd.add_argument(
        "--promote-snapshot",
        type=Path,
        help="Copy a successful live ADS refresh to this tracked snapshot path.",
    )
    publish_cmd.set_defaults(command="publish-website")

    return parser


def run_build(args: argparse.Namespace, *, render_check: bool) -> int:
    variants = resolve_variants(args.variant)
    latexmk_cmd = resolve_command(args.latexmk)
    pdftoppm_cmd = resolve_command(args.pdftoppm) if render_check else None

    resolved_font, artifacts, fallback_used = prepare_generated_files(
        font_profile=args.font_profile,
        skip_ads_refresh=args.skip_ads_refresh,
        max_age_hours=args.max_age_hours,
        fallback_snapshot=args.fallback_snapshot,
        promote_snapshot=args.promote_snapshot,
    )

    print_step("[5/5] Building PDF variants")
    for variant in variants:
        print_step(f"  - {variant}")
        build_variant(variant, latexmk_cmd)

    write_build_manifest(
        variants=variants,
        artifacts=artifacts,
        resolved_font=resolved_font,
        fallback_used=fallback_used,
    )

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
            resolved_font, artifacts, _ = prepare_generated_files(
                font_profile=args.font_profile,
                skip_ads_refresh=args.skip_ads_refresh,
                max_age_hours=args.max_age_hours,
                fallback_snapshot=args.fallback_snapshot,
                promote_snapshot=args.promote_snapshot,
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
