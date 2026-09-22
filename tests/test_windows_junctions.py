from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from artifact_fence.cli import main
from artifact_fence.scanner import scan_project


@unittest.skipUnless(os.name == "nt", "Windows junction regression")
class WindowsJunctionTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        temporary = tempfile.TemporaryDirectory(prefix="junction-test-", dir=workspace)
        self.scratch = Path(temporary.name).resolve()
        self.assertTrue(self.scratch.is_relative_to(workspace))
        self.addCleanup(temporary.cleanup)
        self.root = self.scratch / "repo"
        self.root.mkdir()
        self.outside = self.scratch / "outside"
        self.outside.mkdir()
        self.secret = "API_TOKEN=fake-junction-regression-only"
        (self.outside / "secrets.txt").write_text(self.secret, encoding="utf-8")

    def write_workflow(self, patterns: str, *, include_hidden: bool = True) -> None:
        workflow = self.root / ".github/workflows/ci.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "jobs:\n  build:\n    runs-on: windows-latest\n    steps:\n"
            "      - uses: actions/upload-artifact@v4\n        with:\n"
            f"          include-hidden-files: {str(include_hidden).lower()}\n"
            "          path: |\n"
            + "".join(f"            {pattern}\n" for pattern in patterns.splitlines()),
            encoding="utf-8",
        )

    @contextlib.contextmanager
    def junction(self, relative: str, target: Path):
        import _winapi

        link = self.root / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        self.assertTrue(link.parent.resolve().is_relative_to(self.scratch))
        self.assertTrue(target.resolve().is_relative_to(self.scratch))
        _winapi.CreateJunction(str(target), str(link))
        try:
            self.assertEqual(stat.IO_REPARSE_TAG_MOUNT_POINT, link.lstat().st_reparse_tag)
            yield link
        finally:
            # Delete only the junction itself, before the temporary fixture tree.
            # Both its lexical parent and resolved target must remain in this fixture.
            self.assertTrue(link.parent.resolve().is_relative_to(self.scratch))
            self.assertTrue(link.resolve().is_relative_to(self.scratch))
            self.assertEqual(stat.IO_REPARSE_TAG_MOUNT_POINT, link.lstat().st_reparse_tag)
            link.rmdir()
            self.assertTrue(target.is_dir())

    @contextlib.contextmanager
    def forbid_outside_access(self):
        original_scandir = os.scandir
        original_open = Path.open

        def contained_scandir(directory):
            self.assertTrue(Path(directory).resolve().is_relative_to(self.root))
            return original_scandir(directory)

        def contained_open(path, *args, **kwargs):
            self.assertTrue(path.resolve().is_relative_to(self.root))
            return original_open(path, *args, **kwargs)

        with patch("os.scandir", side_effect=contained_scandir), patch.object(
            Path, "open", contained_open
        ):
            yield

    def test_selected_outside_junction_fails_without_enumerating_or_reading_target(self) -> None:
        self.write_workflow("build\nsafe.txt")
        (self.root / "safe.txt").write_text("ok", encoding="utf-8")
        output = io.StringIO()
        with self.junction("build", self.outside), self.forbid_outside_access():
            with contextlib.redirect_stdout(output):
                code = main(["check", str(self.root), "--format", "json"])
        report = json.loads(output.getvalue())
        self.assertEqual(1, code)
        self.assertEqual(["safe.txt"], report["artifacts"][0]["files"])
        self.assertIn("unsafe-artifact-path", {x["rule_id"] for x in report["findings"]})
        self.assertNotIn(self.secret, output.getvalue())
        self.assertTrue((self.outside / "secrets.txt").is_file())

    def test_internal_junction_is_skipped_like_an_internal_symlink(self) -> None:
        self.write_workflow("build")
        target = self.root / "target"
        target.mkdir()
        protected = target / "secrets.txt"
        protected.write_text(self.secret, encoding="utf-8")
        original_open = Path.open
        original_scandir = os.scandir

        def guarded_open(path, *args, **kwargs):
            self.assertNotEqual(protected, path.resolve())
            return original_open(path, *args, **kwargs)

        def guarded_scandir(directory):
            self.assertNotEqual(self.root / "build", Path(directory))
            return original_scandir(directory)

        with self.junction("build", target), patch.object(Path, "open", guarded_open), patch(
            "os.scandir", side_effect=guarded_scandir
        ):
            report = scan_project(self.root)
        self.assertEqual([], report.artifacts[0].files)
        self.assertIn("artifact-symlink-skipped", {x.rule_id for x in report.findings})
        self.assertFalse(any(x.severity == "high" for x in report.findings))

    def test_excluded_outside_junction_is_not_traversed_or_reported(self) -> None:
        self.write_workflow("build/**\n!build/linked")
        (self.root / "build").mkdir()
        (self.root / "build/safe.txt").write_text("ok", encoding="utf-8")
        with self.junction("build/linked", self.outside), self.forbid_outside_access():
            report = scan_project(self.root)
        self.assertEqual(["build/safe.txt"], report.artifacts[0].files)
        self.assertFalse(report.findings)

    def test_hidden_junction_obeys_explicit_hidden_false_without_traversal(self) -> None:
        self.write_workflow("build", include_hidden=False)
        (self.root / "build").mkdir()
        (self.root / "build/safe.txt").write_text("ok", encoding="utf-8")
        with self.junction("build/.linked", self.outside), self.forbid_outside_access():
            report = scan_project(self.root)
        self.assertEqual(["build/safe.txt"], report.artifacts[0].files)
        self.assertFalse(report.findings)


if __name__ == "__main__":
    unittest.main()
