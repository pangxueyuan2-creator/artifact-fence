from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from artifact_fence.cli import main
from artifact_fence.scanner import WalkResult, scan_project


def write_project(root: Path, patterns: str, files: dict[str, str]) -> None:
    workflow = root / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/upload-artifact@v4\n        with:\n"
        "          include-hidden-files: true\n          path: |\n"
        + "".join(f"            {pattern}\n" for pattern in patterns.splitlines()),
        encoding="utf-8",
    )
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class GlobSemanticsTests(unittest.TestCase):
    def test_root_directory_scope_includes_present_credentials(self) -> None:
        for pattern in (".", "./", ".//"):
            with self.subTest(pattern=pattern), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                write_project(root, pattern, {"secrets.txt": "API_TOKEN=fake-glob-test-only"})
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main(["check", str(root), "--format", "json"])
                report = json.loads(output.getvalue())
                self.assertEqual(1, code)
                self.assertIn("secrets.txt", report["artifacts"][0]["files"])
                self.assertNotIn("fake-glob-test-only", output.getvalue())

    def test_globstar_includes_zero_one_and_multiple_directory_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {
                "build/direct.txt": "API_TOKEN=fake-glob-test-only",
                "build/one/child.txt": "ok",
                "build/one/two/child.txt": "ok",
                "build/one/other.log": "ok",
            }
            write_project(root, "build/**/*.txt", files)
            report = scan_project(root)
            self.assertEqual(
                sorted(path for path in files if path.endswith(".txt")),
                report.artifacts[0].files,
            )
            self.assertIn("credential-assignment", {finding.rule_id for finding in report.findings})

    def test_narrow_exclusion_does_not_remove_nested_credentials(self) -> None:
        for exclusion in ("!build/*.txt", "!build/?.txt", "!build/[a-z].txt"):
            with self.subTest(exclusion=exclusion), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                write_project(
                    root,
                    "build/**\n" + exclusion,
                    {"build/a.txt": "ok", "build/nested/a.txt": "API_TOKEN=fake-glob-test-only"},
                )
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main(["check", str(root), "--format", "json"])
                self.assertEqual(1, code)
                self.assertEqual(
                    ["build/nested/a.txt"],
                    json.loads(output.getvalue())["artifacts"][0]["files"],
                )

    def test_single_segment_globs_do_not_match_unselected_nested_files(self) -> None:
        for pattern in ("build/*.txt", "build/?.txt", "build/[a-z].txt"):
            with self.subTest(pattern=pattern), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                write_project(
                    root,
                    pattern,
                    {"build/a.txt": "ok", "build/nested/a.txt": "API_TOKEN=fake-glob-test-only"},
                )
                report = scan_project(root)
                self.assertEqual(["build/a.txt"], report.artifacts[0].files)
                self.assertFalse(report.findings)

    def test_globbed_directories_include_descendants_and_can_be_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                "build/release-?\n!build/release-b/",
                {
                    "build/release-a/nested/result.txt": "ok",
                    "build/release-b/.env": "API_TOKEN=fake-glob-test-only",
                },
            )
            report = scan_project(root)
            self.assertEqual(["build/release-a/nested/result.txt"], report.artifacts[0].files)
            self.assertFalse(report.findings)

    def test_trailing_separator_exclusion_only_removes_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                "build/**\n!build/*.txt/",
                {
                    "build/secrets.txt": "API_TOKEN=fake-glob-test-only",
                    "build/cache.txt/report.txt": "ok",
                },
            )
            report = scan_project(root)
            self.assertEqual(["build/secrets.txt"], report.artifacts[0].files)
            self.assertIn("credential-assignment", {finding.rule_id for finding in report.findings})

    def test_globstar_exclusions_remove_every_directory_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                "build/**\n!build/**/.env",
                {
                    "build/.env": "API_TOKEN=fake-glob-test-only",
                    "build/nested/.env": "API_TOKEN=fake-glob-test-only",
                    "build/result.txt": "ok",
                },
            )
            report = scan_project(root)
            self.assertEqual(["build/result.txt"], report.artifacts[0].files)
            self.assertFalse(report.findings)

    def test_directory_only_scope_still_reports_outside_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(root, "build/link/", {})
            # Symlink creation needs special privileges on Windows. The bounded
            # walk's link result lets this matching regression run everywhere.
            with patch(
                "artifact_fence.scanner._walk_files",
                return_value=WalkResult([], [("build/link", False)], False),
            ):
                report = scan_project(root)
            self.assertIn("unsafe-artifact-path", {finding.rule_id for finding in report.findings})

    def test_negated_character_class_supports_caret_and_exclamation(self) -> None:
        for pattern in ("build/[^a].txt", "build/[!a].txt"):
            with self.subTest(pattern=pattern), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                write_project(
                    root,
                    pattern,
                    {"build/a.txt": "ok", "build/b.txt": "API_TOKEN=fake-glob-test-only"},
                )
                report = scan_project(root)
                self.assertEqual(["build/b.txt"], report.artifacts[0].files)
                self.assertIn(
                    "credential-assignment", {finding.rule_id for finding in report.findings}
                )


if __name__ == "__main__":
    unittest.main()
