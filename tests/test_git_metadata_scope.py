from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from artifact_fence.cli import main


class GitMetadataScopeTests(unittest.TestCase):
    @contextlib.contextmanager
    def project(
        self, patterns: str, *, git_file: bool = False, hidden: bool = True,
        git_name: str = ".git",
    ):
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="git-metadata-test-", dir=workspace) as temporary:
            root = Path(temporary).resolve()
            self.assertTrue(root.is_relative_to(workspace))
            workflow = root / ".github/workflows/ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/upload-artifact@v4\n        with:\n"
                f"          include-hidden-files: {str(hidden).lower()}\n"
                "          path: |\n"
                + "".join(f"            {pattern}\n" for pattern in patterns.splitlines()),
                encoding="utf-8",
            )
            metadata = root / git_name
            if git_file:
                metadata.write_text("gitdir: fake-worktree-private-path", encoding="utf-8")
            else:
                (metadata / "nested").mkdir(parents=True)
                for name in ("config", "nested/config.txt"):
                    (metadata / name).write_text(
                        "API_TOKEN=fake-metadata-test-only", encoding="utf-8"
                    )
            (root / "build").mkdir()
            (root / "build/result.txt").write_text("ok", encoding="utf-8")
            original_scandir, original_open = os.scandir, Path.open

            def no_metadata_scandir(directory):
                self.assertFalse(Path(directory).is_relative_to(metadata))
                return original_scandir(directory)

            def no_metadata_open(path, *args, **kwargs):
                self.assertFalse(path.is_relative_to(metadata))
                return original_open(path, *args, **kwargs)

            with patch("os.scandir", side_effect=no_metadata_scandir), patch.object(
                Path, "open", no_metadata_open
            ):
                yield root

    def check(self, root: Path, command: str = "check"):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([command, str(root), "--format", "json"])
        self.assertNotIn("fake-metadata-test-only", output.getvalue())
        self.assertNotIn("fake-worktree-private-path", output.getvalue())
        return code, json.loads(output.getvalue())

    def test_explicit_git_config_is_uninspected_and_fails_closed(self) -> None:
        with self.project(".git/config") as root:
            code, report = self.check(root)
        self.assertEqual(1, code)
        self.assertEqual([], report["artifacts"][0]["files"])
        self.assertEqual(["uninspected-git-metadata"], [x["rule_id"] for x in report["findings"]])

    def test_broad_metadata_scopes_fail_without_reading_metadata(self) -> None:
        for patterns in (".", "./", "**", "**/config", ".g?t/config", "[.]git/config"):
            with self.subTest(patterns=patterns), self.project(patterns) as root:
                code, report = self.check(root)
                self.assertEqual(1, code)
                self.assertIn("uninspected-git-metadata", {x["rule_id"] for x in report["findings"]})

    def test_normal_upload_does_not_inspect_or_report_git_metadata(self) -> None:
        with self.project("build/**") as root:
            code, report = self.check(root)
        self.assertEqual(0, code)
        self.assertEqual(["build/result.txt"], report["artifacts"][0]["files"])
        self.assertFalse(report["findings"])

    def test_hidden_false_excludes_root_metadata_and_broad_patterns(self) -> None:
        for git_file in (False, True):
            with self.subTest(git_file=git_file), self.project(
                ".git\n.git/**\n**", git_file=git_file, hidden=False
            ) as root:
                code, report = self.check(root)
                self.assertEqual(0, code)
                self.assertNotIn("uninspected-git-metadata", {x["rule_id"] for x in report["findings"]})

    def test_explicit_non_hidden_search_root_inside_metadata_remains_uninspected(self) -> None:
        for patterns in (
            ".git/config", ".git/nested/*.txt", "[.]git/config", ".//.git/config", ".git//config"
        ):
            with self.subTest(patterns=patterns), self.project(patterns, hidden=False) as root:
                code, report = self.check(root)
                self.assertEqual(1, code)
                self.assertIn("uninspected-git-metadata", {x["rule_id"] for x in report["findings"]})

    def test_hidden_false_does_not_enter_metadata_through_a_glob_search_root(self) -> None:
        for patterns in (".git/*.txt", ".g?t/config", ".git/**/config", ".git/.hidden/*"):
            with self.subTest(patterns=patterns), self.project(patterns, hidden=False) as root:
                code, report = self.check(root)
                self.assertEqual(0, code)
                self.assertNotIn("uninspected-git-metadata", {x["rule_id"] for x in report["findings"]})

    def test_whole_metadata_exclusions_remain_clean(self) -> None:
        for exclusion in ("!.git", "!.git/**", "!**/.git/**", "!.git/*"):
            with self.subTest(exclusion=exclusion), self.project("**\n" + exclusion) as root:
                code, report = self.check(root)
                self.assertEqual(0, code)
                self.assertFalse(report["findings"])

    def test_literal_or_identical_scope_exclusions_are_respected(self) -> None:
        for patterns in (
            ".git/config\n!.git/config",
            ".git/config\n!.git/c*",
            ".git/config\n!.git",
            ".git/**/config\n!.git/**/config",
        ):
            with self.subTest(patterns=patterns), self.project(patterns) as root:
                code, report = self.check(root)
                self.assertEqual(0, code)
                self.assertNotIn("uninspected-git-metadata", {x["rule_id"] for x in report["findings"]})

    def test_narrow_exclusion_does_not_prove_nested_metadata_excluded(self) -> None:
        for patterns in (
            ".git/nested/config.txt\n!.git/*.txt",
            ".git\n!.git/config",
            ".git/config\n!.git/config/",
            ".git/**\n!.git/*/",
        ):
            with self.subTest(patterns=patterns), self.project(patterns) as root:
                code, report = self.check(root)
                self.assertEqual(1, code)
                self.assertIn("uninspected-git-metadata", {x["rule_id"] for x in report["findings"]})

    def test_later_inclusion_reopens_excluded_metadata(self) -> None:
        with self.project("**\n!.git\n.git/config") as root:
            code, _ = self.check(root)
        self.assertEqual(1, code)

    def test_worktree_git_file_is_opaque_and_is_not_treated_as_a_directory(self) -> None:
        for patterns, expected in ((".git", 1), ("**", 1), (".git/config", 0), (".git/", 0)):
            with self.subTest(patterns=patterns), self.project(patterns, git_file=True) as root:
                code, _ = self.check(root)
                self.assertEqual(expected, code)

    def test_worktree_git_file_exclusions_use_file_semantics(self) -> None:
        for patterns, expected in (
            (".git\n!.git", 0),
            (".git\n!.git/**", 0),
            (".git\n!.git/*", 1),
            (".git\n!.git/", 1),
        ):
            with self.subTest(patterns=patterns), self.project(patterns, git_file=True) as root:
                code, _ = self.check(root)
                self.assertEqual(expected, code)

    def test_scan_stays_advisory_for_uninspected_metadata(self) -> None:
        with self.project(".git/config") as root:
            code, report = self.check(root, command="scan")
        self.assertEqual(0, code)
        self.assertIn("uninspected-git-metadata", {x["rule_id"] for x in report["findings"]})

    @unittest.skipUnless(os.name == "nt", "Windows metadata case folding")
    def test_windows_uppercase_git_directory_stays_opaque(self) -> None:
        for patterns, expected in ((".git/config", 1), (".GIT/config", 1), ("build/**", 0)):
            with self.subTest(patterns=patterns), self.project(patterns, git_name=".GIT") as root:
                code, report = self.check(root)
                self.assertEqual(expected, code)
                if expected:
                    self.assertIn(
                        "uninspected-git-metadata", {x["rule_id"] for x in report["findings"]}
                    )

    @unittest.skipUnless(os.name == "nt", "Windows metadata case folding")
    def test_windows_uppercase_git_file_stays_opaque(self) -> None:
        for patterns, expected in (
            (".git", 1), (".GIT", 1), (".git/config", 0), ("build/**", 0)
        ):
            with self.subTest(patterns=patterns), self.project(
                patterns, git_name=".GIT", git_file=True
            ) as root:
                code, _ = self.check(root)
                self.assertEqual(expected, code)


if __name__ == "__main__":
    unittest.main()
