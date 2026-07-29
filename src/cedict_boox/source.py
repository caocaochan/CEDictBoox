"""Local-only source archive validation and ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any
from zipfile import BadZipFile, ZipFile, ZipInfo

from .parser import CedictDocument, CedictParseError, parse_cedict_bytes


SOURCE_PAGE = "https://www.mdbg.net/chinese/dictionary?page=cc-cedict"
SOURCE_ARCHIVE_URL = (
    "https://www.mdbg.net/chinese/export/cedict/"
    "cedict_1_0_ts_utf-8_mdbg.zip"
)
ARCHIVE_NAME = "cedict_1_0_ts_utf-8_mdbg.zip"
MANIFEST_NAME = "source.json"
MAX_ARCHIVE_SIZE = 64 * 1024 * 1024
MAX_TEXT_SIZE = 256 * 1024 * 1024


class SourceError(ValueError):
    """Raised when an upstream archive or source manifest is invalid."""


@dataclass(frozen=True, slots=True)
class ValidatedSource:
    archive_path: Path
    text_name: str
    text_bytes: bytes
    archive_sha256: str
    text_sha256: str
    release_timestamp: str
    release_date: str
    format_version: str
    declared_entries: int | None
    document: CedictDocument


def validate_archive(path: Path) -> ValidatedSource:
    path = path.resolve()
    if path.suffix.lower() != ".zip":
        raise SourceError(f"{path}: expected a .zip archive")
    if not path.is_file():
        raise SourceError(f"{path}: archive does not exist or is not a file")
    if path.stat().st_size > MAX_ARCHIVE_SIZE:
        raise SourceError(f"{path}: archive exceeds the {MAX_ARCHIVE_SIZE}-byte limit")

    archive_bytes = path.read_bytes()
    archive_hash = hashlib.sha256(archive_bytes).hexdigest()
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            _validate_members(infos, path)
            files = [info for info in infos if not info.is_dir()]
            if len(files) != 1:
                raise SourceError(
                    f"{path}: expected exactly one source file, found {len(files)}"
                )
            info = files[0]
            suffix = Path(info.filename).suffix.lower()
            if suffix not in {".txt", ".u8"}:
                raise SourceError(
                    f"{path}: source member must end in .txt or .u8, "
                    f"found {info.filename!r}"
                )
            if info.file_size > MAX_TEXT_SIZE:
                raise SourceError(
                    f"{path}: source member exceeds the {MAX_TEXT_SIZE}-byte limit"
                )
            try:
                text_bytes = archive.read(info)
            except RuntimeError as exc:
                raise SourceError(f"{path}: cannot read {info.filename!r}: {exc}") from exc
    except BadZipFile as exc:
        raise SourceError(f"{path}: invalid or truncated ZIP archive") from exc

    try:
        document = parse_cedict_bytes(text_bytes, f"{path}!{info.filename}")
    except CedictParseError as exc:
        raise SourceError(str(exc)) from exc

    release_dt = _release_datetime(document, info)
    declared = document.metadata.get("entries")
    return ValidatedSource(
        archive_path=path,
        text_name=info.filename,
        text_bytes=text_bytes,
        archive_sha256=archive_hash,
        text_sha256=hashlib.sha256(text_bytes).hexdigest(),
        release_timestamp=release_dt.isoformat().replace("+00:00", "Z"),
        release_date=release_dt.date().isoformat(),
        format_version=document.metadata.get("version", "1"),
        declared_entries=int(declared) if declared is not None else None,
        document=document,
    )


def ingest_archive(
    archive_path: Path,
    upstream_dir: Path,
    *,
    allow_same_date_replacement: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate and atomically stage a manually downloaded MDBG ZIP."""
    source = validate_archive(archive_path)
    upstream_dir = upstream_dir.resolve()
    manifest_path = upstream_dir / MANIFEST_NAME
    existing: dict[str, Any] | None = None
    if manifest_path.exists():
        existing = load_manifest(manifest_path)
        old_date = str(existing["release_date"])
        if source.release_date < old_date:
            raise SourceError(
                f"refusing older snapshot {source.release_date}; current is {old_date}"
            )
        if (
            source.release_date == old_date
            and source.archive_sha256 != existing.get("archive_sha256")
            and not allow_same_date_replacement
        ):
            raise SourceError(
                "same-date source has a different SHA-256; rerun with "
                "--allow-same-date-replacement after confirming the replacement"
            )

    ingested_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "source_page": SOURCE_PAGE,
        "archive_url": SOURCE_ARCHIVE_URL,
        "archive_name": ARCHIVE_NAME,
        "archive_member": source.text_name,
        "archive_sha256": source.archive_sha256,
        "text_sha256": source.text_sha256,
        "release_timestamp": source.release_timestamp,
        "release_date": source.release_date,
        "format_version": source.format_version,
        "declared_entries": source.declared_entries,
        "parsed_entries": len(source.document.entries),
        "license": "CC-BY-SA-4.0",
        "attribution": "CC-CEDICT contributors; distributed by MDBG",
        "ingested_at": ingested_at.isoformat().replace("+00:00", "Z"),
    }

    upstream_dir.mkdir(parents=True, exist_ok=True)
    manifest_bytes = _json_bytes(manifest)
    with tempfile.TemporaryDirectory(prefix=".ingest-", dir=upstream_dir) as temp_name:
        temp_dir = Path(temp_name)
        staged_archive = temp_dir / ARCHIVE_NAME
        staged_manifest = temp_dir / MANIFEST_NAME
        staged_archive.write_bytes(source.archive_path.read_bytes())
        staged_manifest.write_bytes(manifest_bytes)
        # Validation is complete before either destination is replaced. The
        # manifest is replaced last, so interrupted writes can never validate.
        os.replace(staged_archive, upstream_dir / ARCHIVE_NAME)
        os.replace(staged_manifest, manifest_path)
    return manifest


def load_tracked_source(upstream_dir: Path) -> tuple[ValidatedSource, dict[str, Any]]:
    manifest_path = upstream_dir / MANIFEST_NAME
    archive_path = upstream_dir / ARCHIVE_NAME
    if not manifest_path.is_file() or not archive_path.is_file():
        raise SourceError(
            f"{upstream_dir}: no ingested source; run 'cedict-boox ingest ARCHIVE'"
        )
    manifest = load_manifest(manifest_path)
    source = validate_archive(archive_path)

    checks = {
        "archive_sha256": source.archive_sha256,
        "text_sha256": source.text_sha256,
        "release_timestamp": source.release_timestamp,
        "release_date": source.release_date,
        "format_version": source.format_version,
        "parsed_entries": len(source.document.entries),
        "archive_member": source.text_name,
    }
    for field, actual in checks.items():
        if manifest.get(field) != actual:
            raise SourceError(
                f"{manifest_path}: {field} is {manifest.get(field)!r}, "
                f"but validated source is {actual!r}"
            )
    declared = manifest.get("declared_entries")
    if declared != source.declared_entries:
        raise SourceError(
            f"{manifest_path}: declared_entries does not match validated source"
        )
    return source, manifest


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceError(f"{path}: invalid source manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise SourceError(f"{path}: source manifest must be a JSON object")
    required = {
        "schema_version",
        "archive_sha256",
        "text_sha256",
        "release_timestamp",
        "release_date",
        "format_version",
        "parsed_entries",
    }
    missing = sorted(required - value.keys())
    if missing:
        raise SourceError(f"{path}: missing manifest fields: {', '.join(missing)}")
    if value["schema_version"] != 1:
        raise SourceError(f"{path}: unsupported manifest schema")
    for field in ("archive_sha256", "text_sha256"):
        if not isinstance(value[field], str) or not re.fullmatch(
            r"[0-9a-f]{64}", value[field]
        ):
            raise SourceError(f"{path}: invalid {field}")
    if value.get("source_page") != SOURCE_PAGE:
        raise SourceError(f"{path}: source_page is not the canonical MDBG page")
    if value.get("archive_url") != SOURCE_ARCHIVE_URL:
        raise SourceError(f"{path}: archive_url is not the recorded MDBG export")
    if value.get("archive_name") != ARCHIVE_NAME:
        raise SourceError(f"{path}: unexpected archive_name")
    if value.get("license") != "CC-BY-SA-4.0":
        raise SourceError(f"{path}: unexpected data license")
    try:
        date.fromisoformat(str(value["release_date"]))
    except ValueError as exc:
        raise SourceError(f"{path}: invalid release_date") from exc
    if not isinstance(value["parsed_entries"], int) or value["parsed_entries"] <= 0:
        raise SourceError(f"{path}: invalid parsed_entries")
    return value


def _validate_members(infos: list[ZipInfo], path: Path) -> None:
    seen: set[str] = set()
    for info in infos:
        name = info.filename
        if name in seen:
            raise SourceError(f"{path}: duplicate ZIP member {name!r}")
        seen.add(name)
        normalized = name.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if (
            not name
            or name.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", name)
            or ".." in pure.parts
        ):
            raise SourceError(f"{path}: unsafe ZIP member path {name!r}")
        if info.flag_bits & 0x1:
            raise SourceError(f"{path}: encrypted ZIP member {name!r} is not allowed")


def _release_datetime(document: CedictDocument, info: ZipInfo) -> datetime:
    raw = document.metadata.get("date")
    if raw:
        normalized = raw.strip().replace(" UTC", "Z")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise SourceError(f"invalid CC-CEDICT header date {raw!r}") from exc
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    # Official archives normally declare a date. Retaining the member timestamp
    # provides deterministic support for older verified V1 archives.
    return datetime(*info.date_time, tzinfo=timezone.utc)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
