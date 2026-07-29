"""End-to-end deterministic StarDict ZIP and standalone MDX packaging."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .mdict import write_mdict
from .render import aggregate_entries, render_article
from .source import load_tracked_source
from .stardict import PACKAGE_DIR_NAME, write_stardict
from .verify import verify_directory, verify_mdx, verify_zip


class BuildError(ValueError):
    """Raised when a deterministic package cannot be built."""


@dataclass(frozen=True, slots=True)
class BuildResult:
    archive: Path
    checksum: Path
    report: Path
    release_date: str
    revision: int
    archive_sha256: str
    parsed_entries: int
    unique_simplified_keys: int
    unique_traditional_keys: int
    unique_lookup_keys: int
    multi_reading_articles: int
    idx_size: int
    dict_size: int
    zip_size: int
    converter_commit: str
    mdx: Path
    mdx_checksum: Path
    mdx_sha256: str
    mdx_size: int


def build_package(
    project_root: Path,
    output_dir: Path,
    *,
    revision: int = 1,
    converter_commit: str | None = None,
) -> BuildResult:
    if revision < 1:
        raise BuildError("revision must be at least 1")
    project_root = project_root.resolve()
    output_dir = output_dir.resolve()
    source, manifest = load_tracked_source(project_root / "data" / "upstream")
    entries = source.document.entries
    grouped = aggregate_entries(entries)
    articles = {key: render_article(key, records) for key, records in grouped.items()}
    commit = converter_commit or detect_converter_commit(project_root)
    release_date = source.release_date
    suffix = "" if revision == 1 else f"-r{revision}"
    archive_name = f"cc-cedict-boox-{release_date}{suffix}.zip"
    checksum_name = f"{archive_name}.sha256"
    mdx_name = f"cc-cedict-boox-{release_date}{suffix}.mdx"
    mdx_checksum_name = f"{mdx_name}.sha256"

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".cedict-boox-build-", dir=output_dir
    ) as temp_name:
        staging = Path(temp_name)
        package_dir = staging / PACKAGE_DIR_NAME
        package_dir.mkdir()
        stats = write_stardict(
            package_dir, articles, source_date=source.release_date
        )
        packaged_manifest = dict(manifest)
        packaged_manifest.update(
            {
                "converter_commit": commit,
                "adaptation": (
                    "Converted to a unified BOOX-compatible StarDict dictionary; "
                    "numeric pinyin is displayed with tone marks."
                ),
            }
        )
        (package_dir / "SOURCE.json").write_bytes(_json_bytes(packaged_manifest))
        (package_dir / "README.txt").write_text(
            _package_readme(manifest, commit, revision), encoding="utf-8", newline="\n"
        )
        license_path = project_root / "LICENSE-CC-BY-SA-4.0.txt"
        if not license_path.is_file():
            raise BuildError(f"missing data license text: {license_path}")
        (package_dir / license_path.name).write_bytes(license_path.read_bytes())

        verified = verify_directory(package_dir)
        if verified.wordcount != len(grouped):
            raise BuildError("verification returned an unexpected word count")

        staged_zip = staging / archive_name
        _write_deterministic_zip(
            staged_zip, package_dir, release_date=source.release_date
        )
        verify_zip(staged_zip)
        zip_hash = hashlib.sha256(staged_zip.read_bytes()).hexdigest()
        staged_checksum = staging / checksum_name
        staged_checksum.write_text(
            f"{zip_hash}  {archive_name}\n", encoding="ascii", newline="\n"
        )

        staged_mdx = staging / mdx_name
        mdx_stats = write_mdict(
            staged_mdx, articles, source_date=source.release_date
        )
        verified_mdx = verify_mdx(staged_mdx)
        if verified_mdx.wordcount != len(grouped):
            raise BuildError("MDX verification returned an unexpected word count")
        mdx_hash = hashlib.sha256(staged_mdx.read_bytes()).hexdigest()
        staged_mdx_checksum = staging / mdx_checksum_name
        staged_mdx_checksum.write_text(
            f"{mdx_hash}  {mdx_name}\n", encoding="ascii", newline="\n"
        )

        simplified_keys = {entry.simplified for entry in entries}
        traditional_keys = {entry.traditional for entry in entries}
        result = BuildResult(
            archive=output_dir / archive_name,
            checksum=output_dir / checksum_name,
            report=output_dir / "build-report.json",
            mdx=output_dir / mdx_name,
            mdx_checksum=output_dir / mdx_checksum_name,
            release_date=release_date,
            revision=revision,
            archive_sha256=zip_hash,
            mdx_sha256=mdx_hash,
            parsed_entries=len(entries),
            unique_simplified_keys=len(simplified_keys),
            unique_traditional_keys=len(traditional_keys),
            unique_lookup_keys=len(grouped),
            multi_reading_articles=sum(
                1 for records in grouped.values() if len(records) > 1
            ),
            idx_size=stats.idx_size,
            dict_size=stats.dict_size,
            zip_size=staged_zip.stat().st_size,
            mdx_size=mdx_stats.mdx_size,
            converter_commit=commit,
        )
        staged_report = staging / "build-report.json"
        report_value = asdict(result)
        report_value["archive"] = archive_name
        report_value["checksum"] = checksum_name
        report_value["report"] = "build-report.json"
        report_value["mdx"] = mdx_name
        report_value["mdx_checksum"] = mdx_checksum_name
        report_value["source_archive_sha256"] = source.archive_sha256
        report_value["source_text_sha256"] = source.text_sha256
        staged_report.write_bytes(_json_bytes(report_value))

        os.replace(staged_zip, result.archive)
        os.replace(staged_checksum, result.checksum)
        os.replace(staged_mdx, result.mdx)
        os.replace(staged_mdx_checksum, result.mdx_checksum)
        os.replace(staged_report, result.report)
    return result


def detect_converter_commit(project_root: Path) -> str:
    from_environment = os.environ.get("CEDICT_BOOX_COMMIT")
    if from_environment:
        return from_environment
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = completed.stdout.strip()
    return value if value else "unknown"


def _write_deterministic_zip(
    destination: Path, package_dir: Path, *, release_date: str
) -> None:
    parsed_date = date.fromisoformat(release_date)
    year = max(1980, parsed_date.year)
    timestamp = (year, parsed_date.month, parsed_date.day, 0, 0, 0)
    with ZipFile(destination, "w") as archive:
        for source_path in sorted(package_dir.iterdir(), key=lambda item: item.name):
            if not source_path.is_file():
                raise BuildError(f"unexpected package entry: {source_path}")
            relative = f"{PACKAGE_DIR_NAME}/{source_path.name}"
            info = ZipInfo(relative, date_time=timestamp)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.flag_bits |= 0x800
            archive.writestr(
                info,
                source_path.read_bytes(),
                compress_type=ZIP_DEFLATED,
                compresslevel=9,
            )


def _package_readme(
    manifest: dict[str, Any], converter_commit: str, revision: int
) -> str:
    return (
        "CC-CEDICT for BOOX\n"
        "===================\n\n"
        "Install this entire CC-CEDICT-Boox directory under:\n"
        "  Internal shared storage/dicts/\n\n"
        "Open the BOOX Dictionary app, choose Preferred Dictionary, and select\n"
        "CC-CEDICT Chinese-English (Simplified + Traditional). Restart the app\n"
        "or reboot the device if it is not detected immediately.\n\n"
        f"CC-CEDICT release: {manifest['release_timestamp']}\n"
        f"Source archive SHA-256: {manifest['archive_sha256']}\n"
        f"Source text SHA-256: {manifest['text_sha256']}\n"
        f"Converter commit: {converter_commit}\n"
        f"Build revision: {revision}\n\n"
        "Dictionary data and this adapted dictionary are licensed under\n"
        "Creative Commons Attribution-ShareAlike 4.0 International.\n"
        "Attribution: CC-CEDICT contributors; distributed by MDBG.\n"
        f"Source: {manifest['source_page']}\n"
        "License: https://creativecommons.org/licenses/by-sa/4.0/\n"
    )


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n"
    ).encode("utf-8")
