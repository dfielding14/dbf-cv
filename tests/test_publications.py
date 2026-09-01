import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from dbf_cv.ads import snapshot_payload
import dbf_cv.publications as publications
from dbf_cv.publications import build_advisee_data, compute_h_index, latex_text


class PublicationsTest(unittest.TestCase):
    def test_compute_h_index(self):
        self.assertEqual(compute_h_index([12, 8, 5, 4, 3]), 4)
        self.assertEqual(compute_h_index([100, 4, 3, 1]), 3)

    def test_latex_text_normalizes_box_drawing_dash(self):
        self.assertEqual(latex_text("Disk─Halo"), "Disk--Halo")

    def test_latex_text_preserves_escaping_and_symbols(self):
        self.assertEqual(latex_text(None), "")
        self.assertEqual(
            latex_text(r"&%$#_{}~^\ Maëlle"),
            r"\&\%\$\#\_\{\}\textasciitilde{}\^{}\ Maëlle",
        )
        self.assertEqual(
            latex_text("α β γ ∼ ≈ ≤ ≥ – — ─ −"),
            r"$\alpha$ $\beta$ $\gamma$ $\sim$ $\approx$ $\leq$ $\geq$ -- --- -- -",
        )

    def test_format_publication_links(self):
        record = {
            "authors": ["Example, A."],
            "title": "Gas & Hα",
            "doi": "10.0000/example",
            "arxiv": "2601.00001",
            "url": "https://adsabs.harvard.edu/abs/example",
            "citations": 100,
        }
        self.assertEqual(
            publications.format_publication(record, 1),
            r"\item[{\color{deemph}\scriptsize$\star$1}]"
            r"Example, A., \href{https://doi.org/10.0000/example}{Gas \& H$\alpha$}"
            r" (\href{https://arxiv.org/abs/2601.00001}{arXiv:2601.00001})"
            r" [\href{https://adsabs.harvard.edu/abs/example}{\textbf{100} citations}]",
        )

    def test_build_advisee_data(self):
        manifest = {
            "categories": {
                "graduate": {"symbol": "\\ddagger", "legend": "graduate student-led"},
                "postdoc": {"symbol": "\\dagger", "legend": "postdoc-led"},
            },
            "advisees": [
                {
                    "name": "Example Student",
                    "category": "graduate",
                    "role": "Graduate Student",
                    "affiliation": "NYU",
                    "led_papers": ["2024Example....1A"],
                },
                {
                    "name": "Example Postdoc",
                    "category": "postdoc",
                    "show_in_advising": False,
                    "led_papers": ["2024Example....2B"],
                },
            ],
        }
        categories, visible, led = build_advisee_data(manifest)
        self.assertEqual(categories["graduate"]["symbol"], "\\ddagger")
        self.assertEqual(len(visible), 1)
        self.assertEqual(led["2024Example....1A"]["category"], "graduate")
        self.assertEqual(led["2024Example....2B"]["category"], "postdoc")

    def test_advising_without_publication_category(self):
        advisee = {
            "name": "Example Post-bac",
            "role": "Post-bac Student",
            "affiliation": "NYU",
        }
        manifest = {
            "categories": {
                "graduate": {"symbol": "\\ddagger", "legend": "graduate student-led"},
            },
            "advisees": [advisee],
        }
        _, visible, led = build_advisee_data(manifest)
        self.assertEqual(visible, [advisee])
        self.assertEqual(led, {})

        advisee["category"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unknown category"):
            build_advisee_data(manifest)

        del advisee["category"]
        advisee["led_papers"] = ["2024Example....1A"]
        with self.assertRaisesRegex(ValueError, "unknown category"):
            build_advisee_data(manifest)

    def test_ads_metrics_updated_on_uses_snapshot_fetched_at(self):
        fetched_at = datetime(2026, 5, 4, 12, 30, tzinfo=timezone.utc)
        record = {
            "bibcode": "2026ApJ...1...1F",
            "doctype": "article",
            "authors": ["Fielding, Drummond", "Example, A."],
            "year": 2026,
            "pubdate": "2026-04-01",
            "doi": "10.0000/example",
            "title": "Example publication",
            "pub": "Astrophysical Journal",
            "volume": "999",
            "page": 1,
            "arxiv": "2601.00001",
            "citations": 5,
            "url": "http://adsabs.harvard.edu/abs/2026ApJ...1...1F",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot_path = root / "ads_snapshot.json"
            rules_path = root / "publication_rules.yaml"
            advisees_path = root / "advisees.yaml"
            snapshot_path.write_text(
                json.dumps(
                    snapshot_payload(
                        records=[record],
                        fetched_at=fetched_at,
                        query='author:"Fielding, Drummond"',
                        fields=["bibcode"],
                    )
                ),
                encoding="utf-8",
            )
            rules_path.write_text(
                """
allowed_doctypes:
  - article
author_aliases:
  auto_include:
    - Fielding, Drummond
  position_match:
    - Fielding, Drummond
  query:
    - Fielding, Drummond
exclude_overrides: {}
excluded_publications: []
include_overrides: {}
manual_records: []
promoted_ml_conference_papers: {}
""".lstrip(),
                encoding="utf-8",
            )
            advisees_path.write_text(
                """
categories:
  graduate:
    symbol: '\\ddagger'
    legend: graduate student-led
advisees: []
""".lstrip(),
                encoding="utf-8",
            )
            output_paths = {
                "SUMMARY_TEX_PATH": root / "summary.tex",
                "ADVISING_TEX_PATH": root / "advising.tex",
                "FIRST_AUTHOR_TEX_PATH": root / "first_author.tex",
                "SECOND_AUTHOR_TEX_PATH": root / "second_author.tex",
                "COAUTHOR_TEX_PATH": root / "coauthor.tex",
                "CURATED_PUBLICATIONS_PATH": root / "publications_curated.json",
                "PUBLICATIONS_AUDIT_PATH": root / "publications_audit.json",
                "ORCID_AUDIT_PATH": root / "orcid_audit.json",
                "PUBLICATIONS_AUDIT_MARKDOWN_PATH": root / "publications_audit.md",
            }

            with patch.multiple(publications, **output_paths):
                artifacts = publications.generate_publication_artifacts(
                    snapshot_path,
                    rules_path,
                    advisees_path,
                )

            curated_payload = json.loads(
                output_paths["CURATED_PUBLICATIONS_PATH"].read_text(encoding="utf-8")
            )

        self.assertEqual(artifacts["ads_metrics"]["updated_on"], "2026-05-04")
        self.assertEqual(curated_payload["ads_metrics"]["updated_on"], "2026-05-04")
        self.assertEqual(artifacts["ads_snapshot"]["fetched_at"], "2026-05-04T12:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
