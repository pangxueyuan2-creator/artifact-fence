from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from artifact_fence.cli import main


class LocalActionBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write(
            ".github/workflows/ci.yml",
            "jobs:\n  test:\n    steps:\n      - uses: ./.github/actions/build\n",
        )

    def write(self, path: str, content: str) -> None:
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def check(self) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                [
                    "check",
                    str(self.root),
                    "--workflow",
                    ".github/workflows/ci.yml",
                    "--format",
                    "json",
                ]
            )
        return code, json.loads(output.getvalue())

    def assert_unresolved(self) -> None:
        code, report = self.check()
        self.assertEqual(code, 1, report)
        self.assertIn("unresolved-local-action", [f["rule_id"] for f in report["findings"]])

    def test_missing_local_action_is_unresolved(self) -> None:
        self.assert_unresolved()

    def test_missing_nested_local_action_is_unresolved(self) -> None:
        self.write(
            ".github/actions/build/action.yml",
            "runs:\n  using: composite\n  steps:\n    - uses: ./missing\n",
        )
        self.assert_unresolved()

    def test_missing_action_inside_selected_reusable_workflow_is_unresolved(self) -> None:
        self.write(
            ".github/workflows/ci.yml",
            "jobs:\n  test:\n    uses: ./.github/workflows/reusable.yml\n",
        )
        self.write(
            ".github/workflows/reusable.yml",
            "jobs:\n  test:\n    steps:\n      - uses: ./missing\n",
        )
        self.assert_unresolved()

    def test_malformed_composite_steps_are_unresolved(self) -> None:
        for body in (
            "name: missing-runs",
            "runs: {}",
            "runs: {using: false}",
            "runs: {using: composite}",
            "runs: {using: composite, steps: null}",
            "runs: {using: composite, steps: [null]}",
        ):
            with self.subTest(body=body):
                self.write(".github/actions/build/action.yml", body)
                self.assert_unresolved()

    def test_contained_metadata_and_empty_composite_remain_clean(self) -> None:
        self.write(".github/actions/build/action.yaml", "runs: {using: composite, steps: []}")
        code, _ = self.check()
        self.assertEqual(code, 0)

    def test_metadata_symlink_escape_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external) / "action.yml"
            outside.write_text("runs: {using: composite, steps: []}", encoding="utf-8")
            metadata = self.root / ".github/actions/build/action.yml"
            metadata.parent.mkdir(parents=True)
            try:
                metadata.symlink_to(outside)
            except OSError:
                self.skipTest("symlink creation unavailable")
            from artifact_fence.gate import _read_yaml

            def guarded(path: Path):
                self.assertTrue(path.resolve().is_relative_to(self.root.resolve()), path)
                return _read_yaml(path)

            with patch("artifact_fence.gate._read_yaml", side_effect=guarded):
                self.assert_unresolved()

    def test_deep_composites_fail_closed_without_recursion_error(self) -> None:
        self.write(
            ".github/actions/build/action.yml",
            "runs:\n  using: composite\n  steps:\n    - uses: ./chain/0\n",
        )
        for index in range(40):
            self.write(
                f"chain/{index}/action.yml",
                f"runs:\n  using: composite\n  steps:\n    - uses: ./chain/{index + 1}\n",
            )
        self.write("chain/40/action.yml", "runs: {using: composite, steps: []}")
        code, report = self.check()
        self.assertEqual(code, 1)
        self.assertIn("local-action-inspection-limit", [f["rule_id"] for f in report["findings"]])

    def test_wide_composites_fail_closed_at_visit_budget(self) -> None:
        self.write(
            ".github/actions/build/action.yml",
            "runs:\n  using: composite\n  steps:\n"
            + "".join(f"    - uses: ./child/{i}\n" for i in range(5)),
        )
        for i in range(5):
            self.write(f"child/{i}/action.yml", "runs: {using: composite, steps: []}")
        with patch("artifact_fence.gate.MAX_LOCAL_ACTIONS", 3):
            code, report = self.check()
        self.assertEqual(code, 1)
        self.assertIn("local-action-inspection-limit", [f["rule_id"] for f in report["findings"]])

    def test_deep_reusable_workflows_fail_closed(self) -> None:
        self.write(
            ".github/workflows/ci.yml", "jobs:\n  call:\n    uses: ./.github/workflows/w0.yml\n"
        )
        for i in range(40):
            self.write(
                f".github/workflows/w{i}.yml",
                f"jobs:\n  call:\n    uses: ./.github/workflows/w{i + 1}.yml\n",
            )
        self.write(".github/workflows/w40.yml", "jobs: {}")
        code, report = self.check()
        self.assertEqual(code, 1)
        self.assertIn("local-workflow-inspection-limit", [f["rule_id"] for f in report["findings"]])

    def test_unparseable_metadata_fails_without_echoing_source(self) -> None:
        self.write(".github/actions/build/action.yml", "runs: [private-source-sentinel")
        code, report = self.check()
        self.assertEqual(code, 1)
        self.assertIn("unresolved-local-action", [f["rule_id"] for f in report["findings"]])
        self.assertNotIn("private-source-sentinel", json.dumps(report))

    def test_non_composite_metadata_keeps_existing_static_scope(self) -> None:
        self.write(".github/actions/build/action.yml", "runs: {using: node24, main: index.js}")
        code, _ = self.check()
        self.assertEqual(code, 0)

    def test_wide_reusable_workflows_fail_closed_at_visit_budget(self) -> None:
        self.write(
            ".github/workflows/ci.yml",
            "jobs:\n"
            + "".join(f"  w{i}:\n    uses: ./.github/workflows/w{i}.yml\n" for i in range(4)),
        )
        for i in range(4):
            self.write(f".github/workflows/w{i}.yml", "jobs: {}")
        with patch("artifact_fence.gate.MAX_LOCAL_WORKFLOWS", 3):
            code, report = self.check()
        self.assertEqual(code, 1)
        self.assertIn("local-workflow-inspection-limit", [f["rule_id"] for f in report["findings"]])


if __name__ == "__main__":
    unittest.main()
