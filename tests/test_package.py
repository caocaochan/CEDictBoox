from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from cedict_boox.package import build_package
from cedict_boox.stardict import PACKAGE_DIR_NAME
from cedict_boox.verify import VerificationError, verify_package

from tests.helpers import make_project


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_build_is_deterministic_and_installation_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(
                Path(temp) / "project",
                REPOSITORY_ROOT / "LICENSE-CC-BY-SA-4.0.txt",
            )
            first = build_package(
                project, project / "dist-one", converter_commit="0123456789abcdef"
            )
            second = build_package(
                project, project / "dist-two", converter_commit="0123456789abcdef"
            )
            self.assertEqual(
                hashlib.sha256(first.archive.read_bytes()).digest(),
                hashlib.sha256(second.archive.read_bytes()).digest(),
            )
            self.assertEqual(first.mdx.read_bytes(), second.mdx.read_bytes())
            verified = verify_package(first.archive)
            self.assertEqual(verified.wordcount, first.unique_lookup_keys)
            verified_mdx = verify_package(first.mdx)
            self.assertEqual(verified_mdx.wordcount, first.unique_lookup_keys)
            with ZipFile(first.archive) as archive:
                names = archive.namelist()
                self.assertTrue(
                    all(name.startswith(f"{PACKAGE_DIR_NAME}/") for name in names)
                )
                self.assertTrue(all(info.date_time[:3] == (2026, 7, 28) for info in archive.infolist()))
            checksum = first.checksum.read_text(encoding="ascii")
            self.assertEqual(
                checksum,
                f"{first.archive_sha256}  {first.archive.name}\n",
            )
            mdx_checksum = first.mdx_checksum.read_text(encoding="ascii")
            self.assertEqual(
                mdx_checksum,
                f"{first.mdx_sha256}  {first.mdx.name}\n",
            )
            self.assertEqual(
                first.mdx_sha256,
                hashlib.sha256(first.mdx.read_bytes()).hexdigest(),
            )
            report = json.loads(first.report.read_text(encoding="utf-8"))
            self.assertEqual(report["mdx"], first.mdx.name)
            self.assertEqual(report["mdx_checksum"], first.mdx_checksum.name)
            self.assertEqual(report["mdx_sha256"], first.mdx_sha256)
            self.assertEqual(report["mdx_size"], first.mdx.stat().st_size)

    def test_rebuild_revision_changes_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(
                Path(temp) / "project",
                REPOSITORY_ROOT / "LICENSE-CC-BY-SA-4.0.txt",
            )
            result = build_package(
                project,
                project / "dist",
                revision=2,
                converter_commit="test",
            )
            self.assertEqual(result.archive.name, "cc-cedict-boox-2026-07-28-r2.zip")
            self.assertEqual(result.mdx.name, "cc-cedict-boox-2026-07-28-r2.mdx")

    def test_verifier_rejects_non_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "empty"
            path.mkdir()
            with self.assertRaises(VerificationError):
                verify_package(path)

    def test_verifier_rejects_corrupt_index_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = make_project(
                Path(temp) / "project",
                REPOSITORY_ROOT / "LICENSE-CC-BY-SA-4.0.txt",
            )
            result = build_package(
                project, project / "dist", converter_commit="test"
            )
            corrupt = project / "dist" / "corrupt.zip"
            with ZipFile(result.archive) as source, ZipFile(corrupt, "w") as target:
                for info in source.infolist():
                    data = bytearray(source.read(info.filename))
                    if info.filename.endswith(".idx"):
                        terminator = data.index(0)
                        data[terminator + 1 : terminator + 5] = b"\x00\x00\x00\x01"
                    target.writestr(info, data)
            with self.assertRaisesRegex(VerificationError, "article offset"):
                verify_package(corrupt)


if __name__ == "__main__":
    unittest.main()
