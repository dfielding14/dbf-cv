import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dbf_cv.ads import snapshot_payload
import dbf_cv.cli as cli


class BuildInputTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sources = [
            "data/profile.yaml", "data/sections.yaml", "data/advisees.yaml",
            "data/publication_rules.yaml", "src/dbf_cv/render.py",
            "tex/templates/cv_body.tex", "tex/styles/cv.cls", "tex/styles/extra.sty",
            "assets/fonts/example.ttf", "assets/fonts/example.otf", "pyproject.toml",
        ]
        for name in self.sources:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("original\n", encoding="utf-8")
        snapshot_path = self.root / "cache/ads_snapshot.json"
        snapshot_path.parent.mkdir()
        fetched_at = datetime.now(timezone.utc)
        snapshot_path.write_text(json.dumps(snapshot_payload(
            records=[{"bibcode": "2026Example....1F"}],
            fetched_at=fetched_at, query="example", fields=["bibcode"],
        )), encoding="utf-8")
        pdf_path = self.root / "full.pdf"
        pdf_path.write_bytes(b"%PDF original\n")
        self.manifest_path = self.root / "manifest.json"
        for patcher in (
            patch.object(cli, "REPO_ROOT", self.root),
            patch.object(cli, "ADS_SNAPSHOT_PATH", snapshot_path),
            patch.dict(cli.VARIANT_TO_PDF, {"full": pdf_path}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.artifacts = {
            "input_hashes": cli.build_input_hashes(),
            "ads_snapshot": {
                "path": str(snapshot_path), "fetched_at": fetched_at.isoformat(),
                "record_count": 1,
            },
        }
        self.manifest = cli.write_build_manifest(
            variants=["full"], artifacts=self.artifacts, resolved_font="bundled",
            fallback_used=False, output_path=self.manifest_path,
        )
        self.args = SimpleNamespace(max_age_hours=24)

    def test_source_edits_trigger_rebuild_without_pdf_changes(self):
        with patch.object(cli, "load_build_manifest", return_value=self.manifest), patch.object(
            cli, "build_required_publish_pdfs", return_value={"rebuilt": True},
        ) as rebuild:
            self.assertEqual(cli.ensure_publish_pdfs_exist(self.args, ["full"]), self.manifest)
            rebuild.assert_not_called()
            for name in self.sources + ["cache/ads_snapshot.json"]:
                with self.subTest(input=name):
                    path = self.root / name
                    original = path.read_bytes()
                    path.write_bytes(original + b"\n")
                    rebuild.reset_mock()
                    self.assertEqual(
                        cli.ensure_publish_pdfs_exist(self.args, ["full"]), {"rebuilt": True},
                    )
                    rebuild.assert_called_once_with(self.args, ["full"])
                    path.write_bytes(original)

    def test_added_and_deleted_sources_invalidate_manifest(self):
        added = self.root / "src/dbf_cv/new_renderer.py"
        added.write_text("# new source\n", encoding="utf-8")
        with self.assertRaisesRegex(cli.CommandError, "inputs"):
            cli.validate_build_manifest(["full"], max_age_hours=24, path=self.manifest_path)
        added.unlink()
        (self.root / "data/sections.yaml").unlink()
        with self.assertRaisesRegex(cli.CommandError, "inputs"):
            cli.validate_build_manifest(["full"], max_age_hours=24, path=self.manifest_path)

    def test_commit_and_noninput_changes_preserve_reuse(self):
        for name in (
            "README.md", "data/ads_snapshot.json", "cache/publications_curated.json",
            ".github/workflows/publish-website.yml", "output/rendered/preview.png",
        ):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("changed\n", encoding="utf-8")
        with patch.object(cli, "current_git_commit", return_value="new-snapshot-commit"):
            self.assertEqual(
                cli.validate_build_manifest(["full"], max_age_hours=24, path=self.manifest_path),
                self.manifest,
            )

    def test_legacy_manifest_triggers_rebuild(self):
        legacy = {key: value for key, value in self.manifest.items() if key != "input_hashes"}
        with patch.object(cli, "load_build_manifest", return_value=legacy), patch.object(
            cli, "build_required_publish_pdfs", return_value={"rebuilt": True},
        ) as rebuild:
            self.assertEqual(cli.ensure_publish_pdfs_exist(self.args, ["full"]), {"rebuilt": True})
            rebuild.assert_called_once_with(self.args, ["full"])

    def test_live_refresh_is_not_marked_as_fallback(self):
        with patch.object(cli, "ensure_runtime_directories"), patch.object(
            cli, "write_font_profile", return_value="bundled",
        ), patch.object(cli, "refresh_pubs", return_value=None) as refresh, patch.object(
            cli, "generate_static_tex",
        ), patch.object(cli, "generate_publication_artifacts", return_value={}):
            _, _, fallback_used = cli.prepare_generated_files(
                font_profile="bundled", skip_ads_refresh=False, max_age_hours=24,
            )
        refresh.assert_called_once_with(None)
        self.assertFalse(fallback_used)

    def test_edits_during_generation_cannot_be_certified(self):
        original_manifest = self.manifest_path.read_bytes()
        with patch.object(cli, "ensure_runtime_directories"), patch.object(
            cli, "write_font_profile", return_value="bundled",
        ), patch.object(
            cli, "generate_static_tex",
            side_effect=lambda: (self.root / "data/sections.yaml").write_text("edited\n"),
        ), patch.object(
            cli, "generate_publication_artifacts",
            return_value={"ads_snapshot": self.artifacts["ads_snapshot"]},
        ):
            _, artifacts, _ = cli.prepare_generated_files(
                font_profile="bundled", skip_ads_refresh=True, max_age_hours=24,
            )
        with self.assertRaisesRegex(cli.CommandError, "inputs"):
            cli.write_build_manifest(
                variants=["full"], artifacts=artifacts, resolved_font="bundled",
                fallback_used=False, output_path=self.manifest_path,
            )
        self.assertEqual(self.manifest_path.read_bytes(), original_manifest)


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
                        "input_hashes": cli.build_input_hashes(),
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
                        "input_hashes": cli.build_input_hashes(),
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
                        "input_hashes": cli.build_input_hashes(),
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
                self.assertEqual(artifacts["input_hashes"], cli.build_input_hashes())

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
