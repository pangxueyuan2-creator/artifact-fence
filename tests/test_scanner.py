from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from artifact_fence.cli import main
import artifact_fence.scanner as scanner
from artifact_fence.scanner import has_severity, scan_project


WORKFLOW_HEADER = """name: test
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""


def write_project(root: Path, workflow: str, files: dict[str, str]) -> None:
    workflow_path = root / ".github" / "workflows" / "ci.yml"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text(WORKFLOW_HEADER + workflow, encoding="utf-8")
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def findings_by_rule(report, rule_id: str):
    return [finding for finding in report.findings if finding.rule_id == rule_id]


class ArtifactFenceTests(unittest.TestCase):
    def test_finds_sensitive_filename_and_redacts_value_when_hidden_upload_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                """      - name: publish test output
        uses: actions/upload-artifact@v4
        with:
          name: diagnostics
          include-hidden-files: true
          path: build/**
""",
                {
                    "build/report.txt": "all good\n",
                    "build/.env": "API_TOKEN=fake-only-token-for-test\n",
                },
            )
            report = scan_project(root)
            self.assertEqual(1, len(report.artifacts))
            self.assertTrue(report.artifacts[0].include_hidden_files)
            self.assertEqual(["build/.env", "build/report.txt"], report.artifacts[0].files)
            self.assertEqual(
                {"sensitive-filename", "credential-assignment"},
                {finding.rule_id for finding in report.findings},
            )
            serialized = json.dumps(report.to_dict())
            self.assertNotIn("fake-only-token-for-test", serialized)
            self.assertTrue(has_severity(report, "high"))

    def test_explicit_false_excludes_hidden_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                """      - uses: actions/upload-artifact@v4
        with:
          include-hidden-files: false
          path: build/**
""",
                {"build/report.txt": "all good\n", "build/.env": "TOKEN=not-uploaded"},
            )
            report = scan_project(root)
            self.assertEqual(["build/report.txt"], report.artifacts[0].files)
            self.assertEqual("explicit-false", report.artifacts[0].hidden_file_mode)
            self.assertFalse(report.findings)

    def test_unspecified_and_dynamic_hidden_configuration_scan_hidden_files_conservatively(self) -> None:
        for setting, expected_mode in (("", "conservative-unspecified"), ("          include-hidden-files: ${{ inputs.upload_hidden }}\n", "conservative-dynamic")):
            with self.subTest(setting=setting), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                write_project(
                    root,
                    """      - uses: actions/upload-artifact@v3
        with:
""" + setting + """          path: build/**
""",
                    {"build/.env": "API_TOKEN=potentially-uploaded"},
                )
                report = scan_project(root)
                self.assertEqual(["build/.env"], report.artifacts[0].files)
                self.assertEqual(expected_mode, report.artifacts[0].hidden_file_mode)
                self.assertTrue(findings_by_rule(report, "sensitive-filename"))
                self.assertTrue(has_severity(report, "high"))
                if expected_mode == "conservative-dynamic":
                    self.assertTrue(findings_by_rule(report, "dynamic-include-hidden-files"))

    def test_exclusion_prevents_sensitive_file_from_being_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                """      - uses: actions/upload-artifact@v4
        with:
          name: safe-output
          include-hidden-files: true
          path: |
            build/**
            !build/.env
""",
                {"build/report.txt": "all good\n", "build/.env": "API_TOKEN=not-uploaded"},
            )
            report = scan_project(root)
            self.assertEqual(["build/report.txt"], report.artifacts[0].files)
            self.assertFalse(report.findings)
            self.assertFalse(has_severity(report))

    def test_directory_path_recursively_expands_nested_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                """      - uses: actions/upload-artifact@v4
        with:
          include-hidden-files: true
          path: build
""",
                {"build/nested/report.txt": "ok", "build/.env": "API_TOKEN=directory-case"},
            )
            report = scan_project(root)
            self.assertEqual(["build/.env", "build/nested/report.txt"], report.artifacts[0].files)
            self.assertTrue(findings_by_rule(report, "sensitive-filename"))

    def test_pages_artifact_uses_documented_site_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                """      - uses: actions/upload-pages-artifact@v3
        with:
          name: github-pages
""",
                {"_site/index.html": "hello"},
            )
            report = scan_project(root)
            self.assertEqual(["_site/"], report.artifacts[0].patterns)
            self.assertEqual(["_site/index.html"], report.artifacts[0].files)
            self.assertFalse(report.findings)

    def test_pages_artifact_conservatively_scans_hidden_site_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                """      - uses: actions/upload-pages-artifact@v3
""",
                {"_site/.env": "API_TOKEN=pages-hidden"},
            )
            report = scan_project(root)
            self.assertEqual(["_site/.env"], report.artifacts[0].files)
            self.assertTrue(findings_by_rule(report, "sensitive-filename"))

    def test_dynamic_path_is_reported_but_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                """      - uses: actions/upload-artifact@v4
        with:
          name: dynamic
          path: ${{ github.workspace }}/build/${{ matrix.target }}
""",
                {},
            )
            report = scan_project(root)
            self.assertEqual(
                ["${{ github.workspace }}/build/${{ matrix.target }}"],
                report.artifacts[0].dynamic_patterns,
            )
            self.assertEqual("dynamic-artifact-path", report.findings[0].rule_id)
            self.assertEqual("medium", report.findings[0].severity)
            self.assertTrue(has_severity(report, "medium"))
            self.assertFalse(has_severity(report, "high"))

    def test_unsafe_paths_are_high_findings_across_platform_syntaxes(self) -> None:
        patterns = ["../secrets/*.txt", "/var/secrets/*", r"C:\\\\secrets\\\\*", r"C:relative\\\\*", r"\\\\\\\\server\\\\share\\\\*"]
        for pattern in patterns:
            with self.subTest(pattern=pattern), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                write_project(
                    root,
                    f"""      - uses: actions/upload-artifact@v4
        with:
          name: escape
          path: {pattern}
""",
                    {},
                )
                report = scan_project(root)
                self.assertTrue(findings_by_rule(report, "unsafe-artifact-path"))
                self.assertTrue(has_severity(report, "high"))

    def test_artifact_symlink_escape_is_high_and_safe_files_remain_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            outside = parent / "outside.txt"
            outside.write_text("API_TOKEN=outside-secret", encoding="utf-8")
            write_project(
                root,
                """      - uses: actions/upload-artifact@v4
        with:
          path: build
""",
                {"build/report.txt": "ok"},
            )
            try:
                os.symlink(outside, root / "build" / "outside-link.txt")
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            report = scan_project(root)
            self.assertEqual(["build/report.txt"], report.artifacts[0].files)
            self.assertTrue(findings_by_rule(report, "unsafe-artifact-path"))
            self.assertNotIn("outside-secret", json.dumps(report.to_dict()))

    def test_artifact_directory_symlink_escape_is_high_without_descending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            outside = parent / "outside-directory"
            outside.mkdir()
            (outside / "leaked.txt").write_text("API_TOKEN=directory-link-secret", encoding="utf-8")
            write_project(
                root,
                """      - uses: actions/upload-artifact@v4
        with:
          path: build
""",
                {"build/report.txt": "ok"},
            )
            try:
                os.symlink(outside, root / "build" / "outside-directory")
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            report = scan_project(root)
            self.assertEqual(["build/report.txt"], report.artifacts[0].files)
            self.assertTrue(findings_by_rule(report, "unsafe-artifact-path"))
            self.assertNotIn("directory-link-secret", json.dumps(report.to_dict()))

    def test_explicit_hidden_false_ignores_hidden_outside_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            outside = parent / "outside.txt"
            outside.write_text("API_TOKEN=outside-hidden-link", encoding="utf-8")
            write_project(
                root,
                """      - uses: actions/upload-artifact@v4
        with:
          include-hidden-files: false
          path: build
""",
                {"build/report.txt": "ok"},
            )
            try:
                os.symlink(outside, root / "build" / ".outside-link")
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            report = scan_project(root)
            self.assertEqual(["build/report.txt"], report.artifacts[0].files)
            self.assertFalse(findings_by_rule(report, "unsafe-artifact-path"))
            self.assertFalse(findings_by_rule(report, "artifact-symlink-skipped"))

    def test_in_root_directory_symlink_is_not_traversed_and_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                """      - uses: actions/upload-artifact@v4
        with:
          path: build
""",
                {"build/target/report.txt": "ok"},
            )
            try:
                os.symlink(root / "build" / "target", root / "build" / "linked-target")
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            report = scan_project(root)
            self.assertEqual(["build/target/report.txt"], report.artifacts[0].files)
            self.assertTrue(findings_by_rule(report, "artifact-symlink-skipped"))
            self.assertFalse(findings_by_rule(report, "unsafe-artifact-path"))

    def test_enumeration_limit_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                """      - uses: actions/upload-artifact@v4
        with:
          path: build
""",
                {"build/one.txt": "one", "build/two.txt": "two"},
            )
            original = scanner.MAX_ENUMERATED_ENTRIES
            scanner.MAX_ENUMERATED_ENTRIES = 1
            try:
                report = scan_project(root)
            finally:
                scanner.MAX_ENUMERATED_ENTRIES = original
            self.assertTrue(findings_by_rule(report, "artifact-enumeration-truncated"))
            self.assertTrue(has_severity(report, "high"))

    def test_workflow_symlink_escape_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            root.mkdir()
            outside = parent / "outside-workflows"
            outside.mkdir()
            (outside / "ci.yml").write_text(WORKFLOW_HEADER, encoding="utf-8")
            (root / ".github").mkdir()
            try:
                os.symlink(outside, root / ".github" / "workflows")
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "workflow directory resolves outside root"):
                scan_project(root)

    def test_ignores_non_upload_actions_and_handles_absent_workflow_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual([], scan_project(root).artifacts)
            write_project(
                root,
                """      - uses: actions/download-artifact@v4
        with:
          path: build/**
""",
                {"build/.env": "TOKEN=not-relevant"},
            )
            report = scan_project(root)
            self.assertEqual([], report.artifacts)
            self.assertEqual([], report.findings)

    def test_explicit_workflow_cannot_escape_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "outside root"):
                scan_project(root, workflows=["../outside.yml"])

    def test_explicit_workflow_symlink_escape_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            outside = parent / "outside.yml"
            root.mkdir()
            outside.write_text(WORKFLOW_HEADER, encoding="utf-8")
            link = root / "linked.yml"
            try:
                os.symlink(outside, link)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "outside root"):
                scan_project(root, workflows=["linked.yml"])

    def test_alias_and_oversized_workflow_fail_closed_through_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow_path = root / ".github" / "workflows" / "ci.yml"
            workflow_path.parent.mkdir(parents=True)
            workflow_path.write_text("defaults: &defaults\n  key: value\ncopy: *defaults\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, main(["scan", str(root)]))
            workflow_path.write_text("#" + "x" * 1_000_000, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, main(["scan", str(root)]))

    def test_yaml_parse_failure_never_echoes_workflow_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflow_path = root / ".github" / "workflows" / "ci.yml"
            workflow_path.parent.mkdir(parents=True)
            secret = "API_TOKEN=must-not-appear-in-error-output"
            workflow_path.write_text(
                f"secret: {secret}\ndefaults: &defaults\n  key: value\ncopy: *defaults\n",
                encoding="utf-8",
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(2, main(["scan", str(root)]))
            self.assertNotIn(secret, stderr.getvalue())
            self.assertNotIn(secret, stdout.getvalue())

    def test_cli_exit_codes_and_malformed_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                """      - uses: actions/upload-artifact@v4
        with:
          include-hidden-files: true
          path: build/**
""",
                {"build/.env": "TOKEN=fake-only-token-for-test"},
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(0, main(["scan", str(root), "--format", "json"]))
                self.assertEqual(1, main(["check", str(root), "--min-severity", "high"]))
                self.assertEqual(2, main(["scan", str(root / "missing")]))
                self.assertEqual(0, main(["version"]))
            (root / ".github" / "workflows" / "ci.yml").write_text("jobs: [", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, main(["scan", str(root)]))


if __name__ == "__main__":
    unittest.main()
