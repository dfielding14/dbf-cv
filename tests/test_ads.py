import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dbf_cv.ads import load_snapshot, snapshot_payload, validate_snapshot_freshness


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
