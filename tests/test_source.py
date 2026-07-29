from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from cedict_boox.source import (
    ARCHIVE_NAME,
    SourceError,
    ingest_archive,
    load_tracked_source,
    validate_archive,
)

from tests.helpers import SAMPLE_TEXT, make_source_zip


class SourceTests(unittest.TestCase):
    def test_validates_and_ingests_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = validate_archive(make_source_zip(root / "source.zip"))
            self.assertEqual(source.release_date, "2026-07-28")
            self.assertEqual(len(source.document.entries), 6)
            manifest = ingest_archive(
                root / "source.zip",
                root / "upstream",
                now=datetime(2026, 7, 29, tzinfo=timezone.utc),
            )
            self.assertEqual(manifest["parsed_entries"], 6)
            self.assertTrue((root / "upstream" / ARCHIVE_NAME).is_file())
            tracked, loaded = load_tracked_source(root / "upstream")
            self.assertEqual(tracked.archive_sha256, loaded["archive_sha256"])

    def test_rejects_multiple_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr("one.u8", SAMPLE_TEXT)
                archive.writestr("two.txt", SAMPLE_TEXT)
            with self.assertRaisesRegex(SourceError, "exactly one"):
                validate_archive(path)

    def test_rejects_unsafe_member(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr("../cedict.u8", SAMPLE_TEXT)
            with self.assertRaisesRegex(SourceError, "unsafe"):
                validate_archive(path)

    def test_detects_manifest_hash_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = make_source_zip(root / "source.zip")
            ingest_archive(archive, root / "upstream")
            manifest_path = root / "upstream" / "source.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["archive_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(SourceError, "archive_sha256"):
                load_tracked_source(root / "upstream")

    def test_refuses_older_and_same_date_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = make_source_zip(root / "current.zip")
            ingest_archive(current, root / "upstream")
            changed_text = SAMPLE_TEXT.replace("/China/", "/PRC/")
            changed = make_source_zip(root / "changed.zip", changed_text)
            with self.assertRaisesRegex(SourceError, "same-date"):
                ingest_archive(changed, root / "upstream")
            older_text = SAMPLE_TEXT.replace(
                "2026-07-28T06:50:20Z", "2026-07-27T06:50:20Z"
            )
            older = make_source_zip(root / "older.zip", older_text)
            with self.assertRaisesRegex(SourceError, "older snapshot"):
                ingest_archive(older, root / "upstream")
            manifest = ingest_archive(
                changed,
                root / "upstream",
                allow_same_date_replacement=True,
            )
            self.assertEqual(manifest["parsed_entries"], 6)


if __name__ == "__main__":
    unittest.main()
