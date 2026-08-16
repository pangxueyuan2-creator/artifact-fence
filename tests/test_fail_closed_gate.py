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

    def test_check_fails_on_absent_credential_class_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                WORKFLOW_HEADER
                + """      - uses: actions/upload-artifact@v4
        with:
          path: .env
""",
            )
            code, output = self.run_cli(root)
            self.assertEqual(1, code)
            self.assertIn("HIGH sensitive-filename-absent", output)
            self.assertNotIn("artifact-path-not-present", output)

    def test_check_fails_on_absent_env_prefixed_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                WORKFLOW_HEADER
                + """      - uses: actions/upload-artifact@v4
        with:
          path: config/.env.production
""",
            )
            code, output = self.run_cli(root)
            self.assertEqual(1, code)
            self.assertIn("HIGH sensitive-filename-absent", output)

    def test_absent_non_sensitive_static_path_remains_info(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                WORKFLOW_HEADER
                + """      - uses: actions/upload-artifact@v4
        with:
          path: build/results.txt
""",
            )
            code, output = self.run_cli(root)
            self.assertEqual(0, code)
            self.assertIn("INFO artifact-path-not-present", output)

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

    def test_remote_reusable_workflow_job_call_is_visible(self) -> None:
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
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["scan", str(root)])
            self.assertEqual(0, code)
            self.assertIn("MEDIUM reusable-workflow-upload-unknown", stdout.getvalue())
            code, _ = self.run_cli(root)
            self.assertEqual(0, code)

    def test_reusable_workflow_with_null_jobs_does_not_crash(self) -> None:
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
                "name: publish\non: [workflow_call]\njobs:\n",
            )
            code, output = self.run_cli(root)
            self.assertEqual(0, code)
            self.assertNotIn("Traceback", output)

    def test_local_reusable_workflow_job_with_upload_fails_closed(self) -> None:
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

    def test_malformed_step_level_workflow_reference_remains_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                WORKFLOW_HEADER
                + """      - uses: some-org/deploy/.github/workflows/publish.yml@v1
""",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["scan", str(root)])
            self.assertEqual(0, code)
            self.assertIn("MEDIUM reusable-workflow-upload-unknown", stdout.getvalue())

    def test_upload_hidden_in_nested_local_composite_chain_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                WORKFLOW_HEADER
                + """      - uses: ./.github/actions/wrapper
""",
            )
            write(
                root,
                ".github/actions/wrapper/action.yml",
                """name: wrapper
runs:
  using: composite
  steps:
    - uses: ./.github/actions/inner
""",
            )
            write(
                root,
                ".github/actions/inner/action.yml",
                """name: inner
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

    def test_composite_cycle_terminates_without_upload_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                WORKFLOW_HEADER
                + """      - uses: ./.github/actions/a
""",
            )
            write(
                root,
                ".github/actions/a/action.yml",
                """name: a
runs:
  using: composite
  steps:
    - uses: ./.github/actions/b
""",
            )
            write(
                root,
                ".github/actions/b/action.yml",
                """name: b
runs:
  using: composite
  steps:
    - uses: ./.github/actions/a
""",
            )
            code, output = self.run_cli(root)
            self.assertEqual(0, code)
            self.assertNotIn("Traceback", output)

    def test_upload_in_composite_behind_local_reusable_workflow_fails_closed(self) -> None:
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
  upload:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/publish
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

    def test_reusable_workflow_chain_behind_explicit_workflow_fails_closed(self) -> None:
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
    uses: ./.github/workflows/deploy.yml
""",
            )
            write(
                root,
                ".github/workflows/deploy.yml",
                """name: deploy
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
            code, output = self.run_cli(root, "--workflow", ".github/workflows/ci.yml")
            self.assertEqual(1, code)
            self.assertIn("HIGH local-reusable-workflow-artifact-upload", output)

    def test_reusable_workflow_cycle_terminates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(
                root,
                ".github/workflows/ci.yml",
                """name: test
on: [push]
jobs:
  publish:
    uses: ./.github/workflows/a.yml
""",
            )
            write(
                root,
                ".github/workflows/a.yml",
                """name: a
on: [workflow_call]
jobs:
  next:
    uses: ./.github/workflows/b.yml
""",
            )
            write(
                root,
                ".github/workflows/b.yml",
                """name: b
on: [workflow_call]
jobs:
  next:
    uses: ./.github/workflows/a.yml
""",
            )
            code, output = self.run_cli(root, "--workflow", ".github/workflows/ci.yml")
            self.assertEqual(0, code)
            self.assertNotIn("Traceback", output)

    def test_remote_reusable_call_inside_local_reusable_workflow_is_visible(self) -> None:
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
  upload:
    runs-on: ubuntu-latest
    steps:
      - uses: some-org/deploy/.github/workflows/publish.yml@v1
""",
            )
            code, output = self.run_cli(root, "--workflow", ".github/workflows/ci.yml")
            self.assertEqual(0, code)
            self.assertIn("MEDIUM reusable-workflow-upload-unknown", output)


if __name__ == "__main__":
    unittest.main()
