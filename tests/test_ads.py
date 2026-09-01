import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from dbf_cv.ads import (
    DEFAULT_FIELDS,
    load_snapshot,
    refresh_snapshot,
    snapshot_payload,
    validate_snapshot_freshness,
)


class AdsRefreshTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.rules = root / "rules.yaml"
        self.rules.write_text("orcid: example-orcid\n", encoding="utf-8")
        self.output = root / "snapshot.json"
        self.original = json.dumps(snapshot_payload(
            records=[{"bibcode": "previous"}],
            fetched_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
            query="previous query",
            fields=["bibcode"],
        )).encode()
        self.output.write_bytes(self.original)

    def paper(self, index):
        return {
            "id": str(index),
            "bibcode": f"2026Example{index:05d}F",
            "title": ["Example publication"],
            "author": ["Fielding, Drummond"],
            "doctype": "article",
            "year": "2026",
            "pubdate": "2026-07-00",
        }

    def response(self, docs, count, *, rows=200, partial=False, exact=True):
        payload = {
            "responseHeader": {
                "params": {"rows": rows, "fl": DEFAULT_FIELDS},
                "partialResults": partial,
            },
            "response": {"numFound": count, "numFoundExact": exact, "docs": docs},
            "nextCursorMark": f"cursor-{len(docs)}",
        }
        response = Mock(ok=True, text=json.dumps(payload), headers={})
        response.json.return_value = payload
        return response

    @patch("dbf_cv.ads.configure_ads")
    @patch("requests.Session.get")
    def test_refresh_fetches_all_pages(self, get, configure):
        for rows in (200, 50):
            with self.subTest(rows=rows):
                docs = [self.paper(index) for index in range(201)]
                get.reset_mock()
                get.side_effect = [
                    self.response(docs[start:start + rows], 201, rows=rows)
                    for start in range(0, len(docs), rows)
                ]
                snapshot = refresh_snapshot(self.rules, self.output)

                self.assertEqual(snapshot.record_count, 201)
                self.assertEqual(len({record["bibcode"] for record in snapshot.records}), 201)
                self.assertEqual(get.call_count, (201 + rows - 1) // rows)
                self.assertEqual(snapshot.records[0]["citations"], 0)
                self.assertIsNone(snapshot.records[0]["doi"])
                self.assertEqual(snapshot.records[0]["pubdate"], "2026-07-00")

    @patch("dbf_cv.ads.configure_ads")
    @patch("requests.Session.get")
    def test_rejected_results_preserve_snapshot(self, get, configure):
        first_page = [self.paper(index) for index in range(200)]
        cases = {
            "empty": [self.response([], 0)],
            "empty record": [self.response([{}], 1)],
            "invalid count": [self.response([self.paper(0)], "1")],
            "partial": [self.response([self.paper(0)], 1, partial=True)],
            "omitted": [self.response([self.paper(0)], 1, partial="omitted")],
            "inexact count": [self.response([self.paper(0)], 1, exact=False)],
            "too many": [self.response([self.paper(0), self.paper(1)], 1)],
            "short final page": [self.response(first_page, 201), self.response([], 201)],
            "partial later page": [
                self.response(first_page, 201),
                self.response([self.paper(200)], 201, partial=True),
            ],
            "changed count": [
                self.response(first_page, 201), self.response([self.paper(200)], 202),
            ],
            "duplicate bibcode": [
                self.response(first_page, 201), self.response([self.paper(0)], 201),
            ],
            "later request failure": [
                self.response(first_page, 201), Mock(ok=False, text="ADS unavailable"),
            ],
        }
        for name, responses in cases.items():
            with self.subTest(case=name):
                get.side_effect = responses
                with self.assertRaisesRegex(RuntimeError, "ADS refresh failed"):
                    refresh_snapshot(self.rules, self.output)
                self.assertEqual(self.output.read_bytes(), self.original)

    @patch("dbf_cv.ads.configure_ads")
    @patch("requests.Session.get")
    def test_malformed_records_preserve_snapshot(self, get, configure):
        for field, value in (
            ("bibcode", ""), ("doctype", None), ("year", None), ("pubdate", None),
            ("title", "not a list"), ("title", []), ("author", [""]),
            ("citation_count", -1), ("citation_count", "5"), ("citation_count", True),
        ):
            with self.subTest(field=field, value=value):
                doc = {**self.paper(0), field: value}
                get.side_effect = [self.response([doc], 1)]
                with self.assertRaisesRegex(RuntimeError, field):
                    refresh_snapshot(self.rules, self.output)
                self.assertEqual(self.output.read_bytes(), self.original)


class AdsSnapshotTest(unittest.TestCase):
    def test_legacy_list_snapshot_still_works(self):
        fetched_at = datetime(2026, 5, 4, 12, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ads_snapshot.json"
            path.write_text(json.dumps([{"bibcode": "2026ApJ...1...1F"}]), encoding="utf-8")
            os.utime(path, (fetched_at.timestamp(), fetched_at.timestamp()))

            snapshot = load_snapshot(path)

        self.assertTrue(snapshot.legacy)
        self.assertIsNone(snapshot.schema_version)
        self.assertEqual(snapshot.records, [{"bibcode": "2026ApJ...1...1F"}])
        self.assertEqual(snapshot.record_count, 1)
        self.assertEqual(snapshot.fetched_at, fetched_at)

    def test_metadata_snapshot_exposes_records_and_provenance(self):
        fetched_at = datetime(2026, 5, 4, 12, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ads_snapshot.json"
            path.write_text(
                json.dumps(
                    snapshot_payload(
                        records=[{"bibcode": "2026ApJ...1...1F"}],
                        fetched_at=fetched_at,
                        query='author:"Fielding, Drummond"',
                        fields=["bibcode", "title"],
                    )
                ),
                encoding="utf-8",
            )

            snapshot = load_snapshot(path)

        self.assertFalse(snapshot.legacy)
        self.assertEqual(snapshot.fetched_at, fetched_at)
        self.assertEqual(snapshot.query, 'author:"Fielding, Drummond"')
        self.assertEqual(snapshot.fields, ["bibcode", "title"])
        self.assertEqual(snapshot.record_count, 1)
        self.assertEqual(snapshot.records, [{"bibcode": "2026ApJ...1...1F"}])

    def test_stale_snapshot_fails_freshness_validation(self):
        fetched_at = datetime(2026, 5, 4, 12, 30, tzinfo=timezone.utc)
        now = datetime(2026, 6, 5, 12, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ads_snapshot.json"
            path.write_text(
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

            with self.assertRaisesRegex(RuntimeError, "ADS snapshot is stale"):
                validate_snapshot_freshness(path, max_age_hours=504, now=now)

    def test_recent_snapshot_passes_freshness_validation(self):
        fetched_at = datetime(2026, 5, 4, 12, 30, tzinfo=timezone.utc)
        now = datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ads_snapshot.json"
            path.write_text(
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

            snapshot = validate_snapshot_freshness(path, max_age_hours=504, now=now)

        self.assertEqual(snapshot.fetched_at, fetched_at)


if __name__ == "__main__":
    unittest.main()
