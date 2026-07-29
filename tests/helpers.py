from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from cedict_boox.source import ingest_archive


SAMPLE_TEXT = """\
#! version=1
#! subversion=0
#! format=ts
#! charset=UTF-8
#! entries=6
#! publisher=MDBG
#! license=https://creativecommons.org/licenses/by-sa/4.0/
#! date=2026-07-28T06:50:20Z
中國 中国 [Zhong1 guo2] /China/
你好 你好 [ni3 hao3] /hello/hi/
行 行 [hang2] /row/profession/
行 行 [xing2] /to walk/to be OK/
女兒 女儿 [nu:3 er2] /daughter/
花兒 花儿 [hua1 r5] /flower/
"""


def make_source_zip(path: Path, text: str = SAMPLE_TEXT) -> Path:
    info = ZipInfo("cedict_ts.u8", (2026, 7, 28, 6, 50, 20))
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    with ZipFile(path, "w") as archive:
        archive.writestr(info, text.encode("utf-8"))
    return path


def make_project(root: Path, license_source: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    archive = make_source_zip(root / "download.zip")
    ingest_archive(
        archive,
        root / "data" / "upstream",
        now=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )
    (root / "LICENSE-CC-BY-SA-4.0.txt").write_bytes(license_source.read_bytes())
    return root
