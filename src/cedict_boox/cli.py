"""Command-line interface for source ingestion, builds, and verification."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .package import BuildError, build_package
from .parser import CedictParseError
from .pinyin import PinyinError
from .source import SourceError, ingest_archive
from .stardict import StarDictError
from .verify import VerificationError, verify_package


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cedict-boox",
        description="Build BOOX-compatible StarDict files from CC-CEDICT.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: current directory)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest", help="validate and record a manually downloaded MDBG ZIP"
    )
    ingest.add_argument("archive", type=Path)
    ingest.add_argument(
        "--allow-same-date-replacement",
        action="store_true",
        help="accept a changed archive with the same upstream release date",
    )

    build = subparsers.add_parser("build", help="build the tracked source snapshot")
    build.add_argument(
        "--output", type=Path, default=Path("dist"), help="artifact directory"
    )
    build.add_argument(
        "--revision", type=int, default=1, help="release rebuild number (default: 1)"
    )

    verify = subparsers.add_parser("verify", help="verify a built directory or ZIP")
    verify.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    try:
        if args.command == "ingest":
            manifest = ingest_archive(
                args.archive,
                project_root / "data" / "upstream",
                allow_same_date_replacement=args.allow_same_date_replacement,
                now=datetime.now(timezone.utc),
            )
            _print_json(manifest)
        elif args.command == "build":
            output = args.output
            if not output.is_absolute():
                output = project_root / output
            result = build_package(
                project_root, output, revision=args.revision
            )
            _print_json(asdict(result))
        elif args.command == "verify":
            result = verify_package(args.path)
            _print_json(asdict(result))
        else:  # pragma: no cover - argparse enforces this
            parser.error(f"unknown command {args.command!r}")
    except (
        BuildError,
        CedictParseError,
        PinyinError,
        SourceError,
        StarDictError,
        VerificationError,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))

