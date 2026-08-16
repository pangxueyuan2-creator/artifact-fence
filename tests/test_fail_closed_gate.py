from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from artifact_fence.cli import main


WORKFLOW_HEADER = """name: test
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class FailClosedGateTests(unittest.TestCase):
    def run_cli(self, root: Path, *args: str) -> tuple[int, str]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["check", str(root), *args])
        return code, stdout.getvalue()

    def test_default_check_fails_on_dynamic_artifact_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                WORKFLOW_HEADER
                + """      - uses: actions/upload-artifact@v4
        with:
          path: ${{ github.workspace }}/${{ matrix.output }}
""",
            )
            code, output = self.run_cli(root)
            self.assertEqual(1, code)
            self.assertIn("HIGH unresolved-artifact-surface", output)
            self.assertIn("dynamic-artifact-path", output)

    def test_default_check_fails_on_upload_hidden_in_local_composite_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                WORKFLOW_HEADER
                + """      - name: publish through local action
        uses: ./.github/actions/publish
""",
            )
            write(
                root,
                ".github/actions/publish/action.yml",
                """name: publish
runs:
  using: composite
  steps:
    - uses: actions/upload-artifact@v4
      with:
        path: build/**
""",
            )
            code, output = self.run_cli(root)
            self.assertEqual(1, code)
            self.assertIn("HIGH local-composite-artifact-upload", output)
            self.assertNotIn("No actions/upload-artifact", output)

    def test_scan_remains_advisory_even_for_unresolved_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                WORKFLOW_HEADER
                + """      - uses: actions/upload-artifact@v4
        with:
          path: ${{ matrix.output }}
""",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["scan", str(root)])
            self.assertEqual(0, code)
            self.assertIn("HIGH unresolved-artifact-surface", stdout.getvalue())

    def test_remote_reusable_workflow_call_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                WORKFLOW_HEADER
                + """      - uses: some-org/deploy/.github/workflows/publish.yml@v1
        with:
          secret: ${{ secrets.TOKEN }}
""",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["scan", str(root)])
            self.assertEqual(0, code)
            self.assertIn("MEDIUM reusable-workflow-upload-unknown", stdout.getvalue())
            # Default high threshold stays usable; stricter gates can lower it.
            code, _ = self.run_cli(root)
            self.assertEqual(0, code)

    def test_local_reusable_workflow_with_upload_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                WORKFLOW_HEADER
                + """      - uses: ./.github/workflows/publish.yml
""",
            )
            write(
                root,
                ".github/workflows/publish.yml",
                """name: publish
on: [workflow_call]
jobs:
  upload:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/upload-artifact@v4
        with:
          path: build/**
""",
            )
            code, output = self.run_cli(root)
            self.assertEqual(1, code)
            self.assertIn("HIGH local-reusable-workflow-artifact-upload", output)


if __name__ == "__main__":
    unittest.main()
