import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dbf_cv.ads import snapshot_payload
import dbf_cv.cli as cli


class BuildManifestTest(unittest.TestCase):
    def test_manifest_hashes_match_generated_pdfs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pdf_path = root / "dbf-cv-full.pdf"
            manifest_path = root / "build_manifest.json"
            snapshot_path = root / "ads_snapshot.json"
            pdf_path.write_bytes(b"%PDF fake full cv\n")
            snapshot_path.write_text("{}", encoding="utf-8")

            with patch.dict(cli.VARIANT_TO_PDF, {"full": pdf_path}):
                manifest = cli.write_build_manifest(
                    variants=["full"],
                    artifacts={
                        "ads_snapshot": {
                            "path": str(snapshot_path),
                            "fetched_at": "2026-05-04T12:30:00+00:00",
                            "record_count": 1,
                        }
                    },
                    resolved_font="bundled",
                    fallback_used=False,
                    output_path=manifest_path,
                )
                validated = cli.validate_build_manifest(
                    ["full"],
                    max_age_hours=100000,
                    path=manifest_path,
                )
                expected_hash = cli.sha256_file(pdf_path)

        self.assertEqual(
            manifest["variants"]["full"]["sha256"],
            expected_hash,
        )
        self.assertEqual(validated["variants"]["full"]["sha256"], manifest["variants"]["full"]["sha256"])
        self.assertFalse(validated["fallback_used"])

    def test_stale_manifest_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pdf_path = root / "dbf-cv-full.pdf"
            manifest_path = root / "build_manifest.json"
            pdf_path.write_bytes(b"%PDF fake full cv\n")

            with patch.dict(cli.VARIANT_TO_PDF, {"full": pdf_path}):
                cli.write_build_manifest(
                    variants=["full"],
                    artifacts={
                        "ads_snapshot": {
                            "path": str(root / "ads_snapshot.json"),
                            "fetched_at": "2020-01-01T00:00:00Z",
                            "record_count": 1,
                        }
                    },
                    resolved_font="bundled",
                    fallback_used=False,
                    output_path=manifest_path,
                )

                with self.assertRaisesRegex(cli.CommandError, "Build manifest snapshot is stale"):
                    cli.validate_build_manifest(
                        ["full"],
                        max_age_hours=1,
                        path=manifest_path,
                    )

    def test_manifest_rejects_pdf_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pdf_path = root / "dbf-cv-full.pdf"
            manifest_path = root / "build_manifest.json"
            pdf_path.write_bytes(b"%PDF original\n")

            with patch.dict(cli.VARIANT_TO_PDF, {"full": pdf_path}):
                cli.write_build_manifest(
                    variants=["full"],
                    artifacts={
                        "ads_snapshot": {
                            "path": str(root / "ads_snapshot.json"),
                            "fetched_at": "2026-05-04T12:30:00Z",
                            "record_count": 1,
                        }
                    },
                    resolved_font="bundled",
                    fallback_used=False,
                    output_path=manifest_path,
                )
                pdf_path.write_bytes(b"%PDF modified\n")

                with self.assertRaisesRegex(cli.CommandError, "PDF hash does not match"):
                    cli.validate_build_manifest(
                        ["full"],
                        max_age_hours=100000,
                        path=manifest_path,
                    )

    def test_missing_manifest_triggers_rebuild_path(self):
        args = SimpleNamespace(max_age_hours=504)
        rebuilt_manifest = {"schema_version": 1, "snapshot_fetched_at": "2026-05-04T12:30:00Z"}

        with patch.object(
            cli,
            "validate_build_manifest",
            side_effect=cli.CommandError("Build manifest is missing"),
        ) as validate, patch.object(
            cli,
            "build_required_publish_pdfs",
            return_value=rebuilt_manifest,
        ) as rebuild:
            result = cli.ensure_publish_pdfs_exist(args, ["full", "publications"])

        self.assertEqual(result, rebuilt_manifest)
        validate.assert_called_once_with(["full", "publications"], max_age_hours=504)
        rebuild.assert_called_once_with(args, ["full", "publications"])

    def test_recent_fallback_snapshot_is_used_after_live_refresh_failure(self):
        fetched_at = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fallback_path = root / "data" / "ads_snapshot.json"
            cache_path = root / "cache" / "ads_snapshot.json"
            fallback_path.parent.mkdir(parents=True)
            fallback_path.write_text(
                json.dumps(
                    snapshot_payload(
                        records=[{"bibcode": "2026ApJ...1...1F"}],
                        fetched_at=fetched_at,
                        query=None,
                        fields=[],
                    )
                ),
                encoding="utf-8",
            )

            with patch.object(cli, "ADS_SNAPSHOT_PATH", cache_path), patch.object(
                cli,
                "ensure_runtime_directories",
            ), patch.object(
                cli,
                "write_font_profile",
                return_value="bundled",
            ), patch.object(
                cli,
                "refresh_pubs",
                side_effect=RuntimeError("ADS down"),
            ), patch.object(
                cli,
                "generate_static_tex",
            ), patch.object(
                cli,
                "generate_publication_artifacts",
                return_value={
                    "ads_metrics": {},
                    "ads_snapshot": {
                        "path": str(cache_path),
                        "fetched_at": fetched_at.isoformat(),
                        "record_count": 1,
                    },
                },
            ):
                resolved_font, artifacts, fallback_used = cli.prepare_generated_files(
                    font_profile="bundled",
                    skip_ads_refresh=False,
                    max_age_hours=504,
                    fallback_snapshot=fallback_path,
                    promote_snapshot=None,
                )
                cached_payload = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(resolved_font, "bundled")
        self.assertTrue(fallback_used)
        self.assertEqual(artifacts["ads_snapshot"]["path"], str(cache_path))
        self.assertEqual(cached_payload["record_count"], 1)

    def test_publish_website_uses_manifest_snapshot_time(self):
        args = SimpleNamespace(website_repo="/tmp/website", max_age_hours=504)
        manifest = {
            "schema_version": 1,
            "snapshot_fetched_at": "2026-05-04T12:30:00Z",
        }
        config = object()

        with patch.object(cli, "load_sync_config", return_value=config), patch.object(
            cli,
            "required_variants",
            return_value=["full", "publications"],
        ), patch.object(
            cli,
            "ensure_publish_pdfs_exist",
            return_value=manifest,
        ), patch.object(
            cli,
            "sync_website_repo",
            return_value={
                "changed": False,
                "website_repo": "/tmp/website",
                "display_date": "May 4, 2026",
            },
        ) as sync:
            result = cli.run_publish_website(args)

        self.assertEqual(result, 0)
        called_when = sync.call_args.kwargs["when"]
        self.assertEqual(called_when, datetime(2026, 5, 4, 12, 30, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
