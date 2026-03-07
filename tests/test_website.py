import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from dbf_cv.website import WebsiteSyncConfig, format_website_date, sync_website_repo


class WebsiteSyncTest(unittest.TestCase):
    def setUp(self):
        self.config = WebsiteSyncConfig(
            website_repo_slug="dfielding14/dfielding14.github.io",
            target_branch="master",
            site_data_path="_data/site.yml",
            timezone="America/New_York",
            pdf_targets={
                "full": "files/DBF_CV.pdf",
                "summary_only": "files/DBF_CV_nopubs.pdf",
                "publications": "files/DBF_CV-Publist.pdf",
            },
            expected_document_urls={
                "cv": "/files/DBF_CV.pdf",
                "cv_no_publications": "/files/DBF_CV_nopubs.pdf",
                "publication_list": "/files/DBF_CV-Publist.pdf",
            },
            last_updated_keys={"cv": "cv", "publications": "publications"},
        )

    def make_site_repo(self, root: Path) -> Path:
        website_root = root / "website"
        (website_root / "_data").mkdir(parents=True)
        (website_root / "files").mkdir(parents=True)
        site_data = {
            "documents": {
                "cv": "/files/DBF_CV.pdf",
                "cv_no_publications": "/files/DBF_CV_nopubs.pdf",
                "publication_list": "/files/DBF_CV-Publist.pdf",
            },
            "last_updated": {
                "cv": "November 30, 2024",
                "publications": "November 30, 2024",
            },
        }
        (website_root / "_data" / "site.yml").write_text(
            yaml.safe_dump(site_data, sort_keys=False),
            encoding="utf-8",
        )
        return website_root

    def make_source_pdfs(self, root: Path) -> dict[str, Path]:
        pdf_root = root / "pdfs"
        pdf_root.mkdir(parents=True)
        paths = {
            "full": pdf_root / "dbf-cv-full.pdf",
            "summary_only": pdf_root / "dbf-cv-summary-only.pdf",
            "publications": pdf_root / "dbf-cv-publications.pdf",
        }
        for variant, path in paths.items():
            path.write_bytes(f"%PDF-{variant}".encode("utf-8"))
        return paths

    def test_format_website_date(self):
        when = datetime(2026, 3, 7, 9, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(format_website_date("America/New_York", when), "March 7, 2026")

    def test_sync_website_repo_updates_pdfs_and_dates(self):
        when = datetime(2026, 3, 7, 9, 0, tzinfo=ZoneInfo("America/New_York"))
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            website_root = self.make_site_repo(root)
            source_pdfs = self.make_source_pdfs(root)

            result = sync_website_repo(website_root, self.config, source_pdfs=source_pdfs, when=when)

            self.assertTrue(result["changed"])
            self.assertIn("files/DBF_CV.pdf", result["changed_files"])
            self.assertIn("files/DBF_CV_nopubs.pdf", result["changed_files"])
            self.assertIn("files/DBF_CV-Publist.pdf", result["changed_files"])
            self.assertIn("_data/site.yml", result["changed_files"])

            site_data = yaml.safe_load((website_root / "_data" / "site.yml").read_text(encoding="utf-8"))
            self.assertEqual(site_data["last_updated"]["cv"], "March 7, 2026")
            self.assertEqual(site_data["last_updated"]["publications"], "March 7, 2026")
            self.assertEqual(
                (website_root / "files" / "DBF_CV.pdf").read_bytes(),
                source_pdfs["full"].read_bytes(),
            )

    def test_sync_website_repo_is_noop_when_outputs_already_match(self):
        when = datetime(2026, 3, 7, 9, 0, tzinfo=ZoneInfo("America/New_York"))
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            website_root = self.make_site_repo(root)
            source_pdfs = self.make_source_pdfs(root)

            sync_website_repo(website_root, self.config, source_pdfs=source_pdfs, when=when)
            result = sync_website_repo(website_root, self.config, source_pdfs=source_pdfs, when=when)

            self.assertFalse(result["changed"])
            self.assertEqual(result["changed_files"], [])

    def test_sync_website_repo_rejects_contract_drift(self):
        when = datetime(2026, 3, 7, 9, 0, tzinfo=ZoneInfo("America/New_York"))
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            website_root = self.make_site_repo(root)
            source_pdfs = self.make_source_pdfs(root)

            broken = yaml.safe_load((website_root / "_data" / "site.yml").read_text(encoding="utf-8"))
            broken["documents"]["cv"] = "/files/renamed.pdf"
            (website_root / "_data" / "site.yml").write_text(
                yaml.safe_dump(broken, sort_keys=False),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                sync_website_repo(website_root, self.config, source_pdfs=source_pdfs, when=when)


if __name__ == "__main__":
    unittest.main()
