from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from cedict_boox.cli import main

from tests.helpers import make_source_zip


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_ingest_build_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = make_source_zip(root / "download.zip")
            (root / "LICENSE-CC-BY-SA-4.0.txt").write_bytes(
                (REPOSITORY_ROOT / "LICENSE-CC-BY-SA-4.0.txt").read_bytes()
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main(["--project-root", str(root), "ingest", str(archive)]), 0
                )
                self.assertEqual(
                    main(["--project-root", str(root), "build", "--output", "dist"]),
                    0,
                )
            built = root / "dist" / "cc-cedict-boox-2026-07-28.zip"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["verify", str(built)]), 0)

    def test_reports_missing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = main(["--project-root", temp, "build"])
            self.assertEqual(status, 1)
            self.assertIn("no ingested source", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

