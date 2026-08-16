"""Fail-closed checks for upload surfaces the bounded scanner cannot resolve."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .scanner import Finding, ScanReport, _action_ref, _is_within, _read_yaml, discover_workflows


def _workflow_paths(root: Path, workflows: Iterable[str | Path] | None) -> list[Path]:
    if workflows is None:
        return discover_workflows(root)
    paths: list[Path] = []
    for value in workflows:
        path = Path(value)
        path = path if path.is_absolute() else root / path
        resolved = path.resolve()
        if not _is_within(root, resolved):
            raise ValueError(f"workflow is outside root: {value}")
        if not resolved.is_file():
            raise ValueError(f"workflow is not a file: {value}")
        paths.append(resolved)
    return sorted(set(paths))


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _local_composite_uploads(root: Path, workflows: Iterable[str | Path] | None) -> list[Finding]:
    findings: list[Finding] = []
    for workflow_path in _workflow_paths(root, workflows):
        document = _read_yaml(workflow_path)
        jobs = document.get("jobs")
        if not isinstance(jobs, dict):
            continue
        workflow_name = _relative(root, workflow_path)
        for job_name, job in jobs.items():
            if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
                continue
            for index, raw_step in enumerate(job["steps"], start=1):
                if not isinstance(raw_step, dict):
                    continue
                uses = str(raw_step.get("uses", "")).strip()
                if not uses.startswith("./"):
                    continue
                action_dir = (root / uses).resolve()
                step_name = str(raw_step.get("name") or f"step-{index}").strip()
                if not _is_within(root, action_dir):
                    findings.append(
                        Finding(
                            "unresolved-local-action",
                            "high",
                            "Local action resolves outside the repository; upload surface cannot be established.",
                            workflow_name,
                            str(job_name),
                            step_name,
                            uses,
                            uses,
                        )
                    )
                    continue
                metadata = next(
                    (candidate for candidate in (action_dir / "action.yml", action_dir / "action.yaml") if candidate.is_file()),
                    None,
                )
                if metadata is None:
                    continue
                try:
                    action_document = _read_yaml(metadata)
                except ValueError:
                    findings.append(
                        Finding(
                            "unresolved-local-action",
                            "high",
                            "Local action metadata cannot be parsed safely; upload surface cannot be established.",
                            workflow_name,
                            str(job_name),
                            step_name,
                            uses,
                            _relative(root, metadata),
                        )
                    )
                    continue
                runs = action_document.get("runs")
                if not isinstance(runs, dict) or str(runs.get("using", "")).lower() != "composite":
                    continue
                nested_steps = runs.get("steps")
                if not isinstance(nested_steps, list):
                    continue
                for nested in nested_steps:
                    if not isinstance(nested, dict):
                        continue
                    action, _ = _action_ref(str(nested.get("uses", "")))
                    if action in {"actions/upload-artifact", "actions/upload-pages-artifact"}:
                        findings.append(
                            Finding(
                                "local-composite-artifact-upload",
                                "high",
                                "A local composite action contains an artifact upload that the direct workflow scanner cannot enumerate; gate fails closed.",
                                workflow_name,
                                str(job_name),
                                step_name,
                                uses,
                                _relative(root, metadata),
                            )
                        )
                        break
    return findings


def _reusable_workflow_uploads(
    root: Path, workflows: Iterable[str | Path] | None
) -> list[Finding]:
    """Surface artifact uploads hidden behind reusable workflow calls.

    GitHub reusable workflows are invoked at job level via ``jobs.<id>.uses``.
    Local reusable workflows are statically inspectable; remote reusable
    workflows cannot be inspected here, so their upload surface remains
    explicitly visible instead of silently disappearing.
    """

    findings: list[Finding] = []

    def inspect_call(
        workflow_name: str,
        job_name: str,
        uses: str,
        display_name: str,
    ) -> None:
        normalized = uses.replace("\\", "/")
        if uses.startswith("./") and normalized.startswith("./.github/workflows/"):
            candidate = (root / uses).resolve()
            if not _is_within(root, candidate) or not candidate.is_file():
                findings.append(
                    Finding(
                        "unresolved-local-workflow",
                        "high",
                        "Local reusable workflow cannot be resolved safely; upload surface cannot be established.",
                        workflow_name,
                        job_name,
                        display_name,
                        uses,
                        uses,
                    )
                )
                return
            try:
                nested = _read_yaml(candidate)
            except ValueError:
                findings.append(
                    Finding(
                        "unresolved-local-workflow",
                        "high",
                        "Local reusable workflow cannot be parsed safely; upload surface cannot be established.",
                        workflow_name,
                        job_name,
                        display_name,
                        uses,
                        _relative(root, candidate),
                    )
                )
                return
            nested_jobs = nested.get("jobs")
            if not isinstance(nested_jobs, dict):
                return
            for nested_job in nested_jobs.values():
                if not isinstance(nested_job, dict):
                    continue
                nested_steps = nested_job.get("steps")
                if not isinstance(nested_steps, list):
                    continue
                for nested_step in nested_steps:
                    if not isinstance(nested_step, dict):
                        continue
                    action, _ = _action_ref(str(nested_step.get("uses", "")))
                    if action in {"actions/upload-artifact", "actions/upload-pages-artifact"}:
                        findings.append(
                            Finding(
                                "local-reusable-workflow-artifact-upload",
                                "high",
                                "A local reusable workflow contains an artifact upload that the direct workflow scanner cannot enumerate; gate fails closed.",
                                workflow_name,
                                job_name,
                                display_name,
                                uses,
                                _relative(root, candidate),
                            )
                        )
                        return
        elif "/.github/workflows/" in normalized:
            findings.append(
                Finding(
                    "reusable-workflow-upload-unknown",
                    "medium",
                    "Reusable workflow call cannot be statically inspected for artifact uploads; review its upload steps separately.",
                    workflow_name,
                    job_name,
                    display_name,
                    uses,
                    uses,
                )
            )

    for workflow_path in _workflow_paths(root, workflows):
        document = _read_yaml(workflow_path)
        jobs = document.get("jobs")
        if not isinstance(jobs, dict):
            continue
        workflow_name = _relative(root, workflow_path)
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue

            # Real GitHub reusable-workflow syntax: jobs.<job_id>.uses.
            job_uses = str(job.get("uses", "")).strip()
            if job_uses:
                inspect_call(workflow_name, str(job_name), job_uses, str(job_name))

            # Keep surfacing malformed step-level workflow references too. They
            # are not valid reusable-workflow syntax, but treating them as an
            # unknown surface is safer than silently ignoring suspicious input.
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            for index, raw_step in enumerate(steps, start=1):
                if not isinstance(raw_step, dict):
                    continue
                uses = str(raw_step.get("uses", "")).strip()
                if not uses or "/.github/workflows/" not in uses.replace("\\", "/"):
                    continue
                step_name = str(raw_step.get("name") or f"step-{index}").strip()
                inspect_call(workflow_name, str(job_name), uses, step_name)

    return findings


def fail_closed_findings(
    root: str | Path,
    report: ScanReport,
    workflows: Iterable[str | Path] | None = None,
) -> list[Finding]:
    """Return high-severity findings for unresolved upload surfaces."""

    root_path = Path(root).expanduser().resolve()
    findings: list[Finding] = []
    for artifact in report.artifacts:
        if artifact.dynamic_patterns:
            findings.append(
                Finding(
                    "unresolved-artifact-surface",
                    "high",
                    "Artifact upload path is dynamic, so the upload set cannot be established; gate fails closed.",
                    artifact.workflow,
                    artifact.job,
                    artifact.step,
                    artifact.name,
                    artifact.dynamic_patterns[0],
                )
            )
    findings.extend(_local_composite_uploads(root_path, workflows))
    findings.extend(_reusable_workflow_uploads(root_path, workflows))
    findings.sort(
        key=lambda finding: (
            finding.workflow,
            finding.job,
            finding.step,
            finding.rule_id,
            finding.path or "",
        )
    )
    return findings
