"""Command-line interface for Artifact Fence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .scanner import ScanReport, has_severity, scan_project


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="artifact-fence",
        description="Prove what GitHub Actions can upload before CI publishes it.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("scan", "inspect artifact upload paths; findings do not affect the exit code"),
        ("check", "inspect artifact upload paths and fail when findings meet the threshold"),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("root", nargs="?", default=".", help="repository root (default: .)")
        command_parser.add_argument(
            "--workflow",
            action="append",
            default=None,
            help="workflow path relative to root; repeat to scan an explicit set",
        )
        command_parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            help="report format (default: text)",
        )
        command_parser.add_argument(
            "--min-severity",
            choices=("low", "medium", "high"),
            default="high",
            help="check threshold; applies only to check (default: high)",
        )
    version = subparsers.add_parser("version", help="print version")
    version.set_defaults(version=True)
    return parser


def _text(report: ScanReport) -> str:
    data = report.to_dict()
    summary = data["summary"]
    lines = [
        f"Artifact Fence: {summary['artifacts']} upload surface(s), "
        f"{summary['findings']} finding(s) across {summary['workflows']} workflow(s)."
    ]
    if not report.artifacts:
        lines.append("No actions/upload-artifact or actions/upload-pages-artifact steps found.")
    for artifact in report.artifacts:
        lines.append(
            f"ARTIFACT {artifact.name}: {artifact.workflow} / {artifact.job} / {artifact.step}"
        )
        for file_path in artifact.files:
            lines.append(f"  includes: {file_path}")
        for pattern in artifact.dynamic_patterns:
            lines.append(f"  dynamic: {pattern}")
    for finding in report.findings:
        location = f" ({finding.path})" if finding.path else ""
        lines.append(
            f"{finding.severity.upper()} {finding.rule_id}{location}: {finding.message} "
            f"[{finding.workflow} / {finding.job} / {finding.step}]"
        )
    return "\n".join(lines)


def _render(report: ScanReport, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report.to_dict(), indent=2, sort_keys=True)
    return _text(report)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "version", False):
        from . import __version__

        print(__version__)
        return 0
    try:
        report = scan_project(Path(args.root), workflows=args.workflow)
    except ValueError as exc:
        print(f"artifact-fence: error: {exc}", file=sys.stderr)
        return 2
    print(_render(report, args.format))
    if args.command == "check" and has_severity(report, args.min_severity):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
