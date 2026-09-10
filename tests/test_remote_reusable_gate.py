from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from artifact_fence.cli import main


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class RemoteReusableWorkflowGateTests(unittest.TestCase):
    def run_check(self, root: Path, *args: str) -> tuple[int, str]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["check", str(root), *args])
        return code, stdout.getvalue()

    def test_default_check_fails_on_remote_reusable_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                """name: test
on: [push]
jobs:
  publish:
    uses: some-org/deploy/.github/workflows/publish.yml@v1
    secrets: inherit
""",
            )

            code, output = self.run_check(root)

            self.assertEqual(1, code)
            self.assertIn("HIGH reusable-workflow-upload-unknown", output)

    def test_nested_remote_reusable_workflow_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                """name: test
on: [push]
jobs:
  publish:
    uses: ./.github/workflows/publish.yml
""",
            )
            write(
                root,
                ".github/workflows/publish.yml",
                """name: publish
on: [workflow_call]
jobs:
  deploy:
    uses: some-org/deploy/.github/workflows/publish.yml@v1
    secrets: inherit
""",
            )

            code, output = self.run_check(root, "--workflow", ".github/workflows/ci.yml")

            self.assertEqual(1, code)
            self.assertIn("HIGH reusable-workflow-upload-unknown", output)


if __name__ == "__main__":
    unittest.main()
