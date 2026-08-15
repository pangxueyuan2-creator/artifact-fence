"""Static, bounded, no-execution analysis for GitHub Actions artifact uploads."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re
from typing import Any, Iterable

import yaml

UPLOAD_ACTIONS = {
    "actions/upload-artifact",
    "actions/upload-pages-artifact",
}
PAGES_DEFAULT_PATH = "_site/"
MAX_WORKFLOW_BYTES = 1_000_000
MAX_ARTIFACT_FILE_BYTES = 1_000_000
MAX_ENUMERATED_ENTRIES = 10_000
MAX_FILES_PER_ARTIFACT = 5_000
MAX_YAML_NODES = 20_000
MAX_YAML_DEPTH = 100
WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[/\\]")


class BoundedSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects aliases and bounds parser work."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._node_count = 0
        self._depth = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("YAML aliases are not supported in workflow files")
        self._node_count += 1
        if self._node_count > MAX_YAML_NODES:
            raise yaml.YAMLError(f"workflow exceeds {MAX_YAML_NODES} YAML nodes")
        self._depth += 1
        if self._depth > MAX_YAML_DEPTH:
            raise yaml.YAMLError(f"workflow exceeds YAML depth limit of {MAX_YAML_DEPTH}")
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


SENSITIVE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
}

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    (
        "credential-assignment",
        re.compile(
            r"(?im)^\s*(?:[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY)|"
            r"(?:token|secret|password|api[_-]?key))\s*[:=]\s*[^\s#]{8,}"
        ),
    ),
    ("github-token", re.compile(r"\bgh[ps]_[A-Za-z0-9_]{20,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
)


@dataclass(frozen=True)
class Finding:
    """One non-secret-bearing exposure signal."""

    rule_id: str
    severity: str
    message: str
    workflow: str
    job: str
    step: str
    artifact: str
    path: str | None = None


@dataclass
class Artifact:
    """A bounded, resolved artifact upload surface."""

    name: str
    workflow: str
    job: str
    step: str
    action: str
    ref: str
    patterns: list[str]
    include_hidden_files: bool
    hidden_file_mode: str
    files: list[str] = field(default_factory=list)
    dynamic_patterns: list[str] = field(default_factory=list)


@dataclass
class ScanReport:
    """Deterministic output returned by the library API and CLI."""

    root: str
    workflows: list[str]
    artifacts: list[Artifact]
    findings: list[Finding]

    def to_dict(self) -> dict[str, Any]:
        severities = {level: 0 for level in ("high", "medium", "low", "info")}
        for finding in self.findings:
            severities[finding.severity] = severities.get(finding.severity, 0) + 1
        return {
            "schema_version": 1,
            "root": self.root,
            "summary": {
                "workflows": len(self.workflows),
                "artifacts": len(self.artifacts),
                "findings": len(self.findings),
                "by_severity": severities,
            },
            "workflows": self.workflows,
            "artifacts": [asdict(artifact) for artifact in self.artifacts],
            "findings": [asdict(finding) for finding in self.findings],
        }


@dataclass
class WalkResult:
    """Bounded repository entries used to resolve one artifact's static patterns."""

    files: list[Path]
    symlinks: list[tuple[str, bool]]
    truncated: bool


def _relative(root: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _lexical_relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_within(root: Path, path: Path) -> bool:
    return _relative(root, path) is not None


def discover_workflows(root: Path) -> list[Path]:
    """Return root-contained workflow files in stable order; reject symlink escapes."""

    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.exists():
        return []
    resolved_dir = workflow_dir.resolve()
    if not resolved_dir.is_dir() or not _is_within(root, resolved_dir):
        raise ValueError("workflow directory resolves outside root or is not a directory")
    paths: list[Path] = []
    for candidate in [*resolved_dir.glob("*.yml"), *resolved_dir.glob("*.yaml")]:
        resolved = candidate.resolve()
        if not resolved.is_file() or not _is_within(root, resolved):
            raise ValueError(f"workflow resolves outside root or is not a file: {candidate}")
        paths.append(resolved)
    return sorted(set(paths), key=lambda path: path.as_posix())


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_WORKFLOW_BYTES:
            raise ValueError(f"workflow {path.name} exceeds {MAX_WORKFLOW_BYTES} byte safety limit")
        loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=BoundedSafeLoader)
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"cannot read workflow {path.name}") from None
    except yaml.YAMLError:
        # PyYAML exceptions can echo nearby source text; never forward them into CI logs.
        raise ValueError(f"cannot parse workflow {path.name}: unsupported or invalid YAML") from None
    if not isinstance(loaded, dict):
        raise ValueError(f"workflow {path.name} must contain a YAML mapping")
    return loaded


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _with(step: dict[str, Any]) -> dict[str, Any]:
    value = step.get("with")
    return value if isinstance(value, dict) else {}


def _patterns_from_step(step: dict[str, Any], action: str) -> list[str]:
    patterns = _string_list(_with(step).get("path"))
    if not patterns and action == "actions/upload-pages-artifact":
        return [PAGES_DEFAULT_PATH]
    return patterns


def _artifact_name(step: dict[str, Any], fallback: str) -> str:
    name = str(_with(step).get("name", "")).strip()
    return name or fallback


def _step_label(step: dict[str, Any], index: int) -> str:
    return str(step.get("name") or f"step-{index}").strip()


def _hidden_file_policy(step: dict[str, Any]) -> tuple[bool, str]:
    """Return a conservative enumeration policy for hidden files.

    Only an explicit static false proves hidden files are excluded. Omitted or dynamic
    input is enumerated as a potentially uploadable superset, never as a clean result.
    """

    with_value = _with(step)
    if "include-hidden-files" not in with_value:
        return True, "conservative-unspecified"
    value = with_value["include-hidden-files"]
    if isinstance(value, str) and ("${{" in value or "$" in value):
        return True, "conservative-dynamic"
    if isinstance(value, bool):
        return value, "explicit-true" if value else "explicit-false"
    included = str(value).strip().lower() in {"1", "true", "yes", "on"}
    return included, "explicit-true" if included else "explicit-false"


def _action_ref(uses: str) -> tuple[str, str]:
    action, separator, ref = uses.partition("@")
    return action.lower(), ref if separator else "unversioned"


def _is_unsafe_pattern(raw: str) -> bool:
    normalized = raw.replace("\\", "/").strip()
    if not normalized or normalized.startswith(("/", "~", "//")):
        return True
    if WINDOWS_DRIVE_PATH.match(normalized) or re.match(r"^[A-Za-z]:", normalized):
        return True
    return ".." in Path(normalized).parts


def _is_hidden_relative(relative: str) -> bool:
    return any(part.startswith(".") for part in Path(relative).parts)


def _walk_files(root: Path) -> WalkResult:
    """Walk without following directory links, with a deterministic entry bound."""

    files: list[Path] = []
    symlinks: list[tuple[str, bool]] = []
    directories = [root]
    entries_seen = 0
    while directories:
        directory = directories.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > MAX_ENUMERATED_ENTRIES:
                        return WalkResult(files, symlinks, True)
                    path = Path(entry.path)
                    relative = _lexical_relative(root, path)
                    if relative == ".git" or relative.startswith(".git/"):
                        continue
                    try:
                        is_link = entry.is_symlink()
                        if is_link:
                            symlinks.append((relative, _is_within(root, path)))
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            directories.append(path)
                        elif entry.is_file(follow_symlinks=False):
                            files.append(path)
                    except OSError:
                        # Entries can disappear or become unreadable between discovery and stat.
                        continue
        except OSError:
            continue
    return WalkResult(files, symlinks, False)


def _has_glob(pattern: str) -> bool:
    return any(character in pattern for character in "*?[")


def _matches(relative: str, pattern: str) -> bool:
    normalized = pattern.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.rstrip("/")
    if not normalized:
        return False
    if not _has_glob(normalized):
        return relative == normalized or relative.startswith(f"{normalized}/")
    return fnmatchcase(relative, normalized)


def _match_files(
    root: Path, patterns: Iterable[str], include_hidden: bool
) -> tuple[list[Path], list[str], bool, bool, bool]:
    """Resolve static patterns from one bounded walk.

    Returns files, dynamic patterns, selected outside-root links, truncation, and
    selected in-root directory/file links skipped without traversal.
    """

    walk = _walk_files(root)
    included: set[Path] = set()
    included_links: dict[str, bool] = {}
    dynamic: list[str] = []
    for raw in patterns:
        if "${{" in raw or "$(" in raw or "${" in raw:
            dynamic.append(raw)
            continue
        excluded = raw.startswith("!")
        pattern = raw[1:].strip() if excluded else raw
        if _is_unsafe_pattern(pattern):
            return sorted(included), dynamic, True, walk.truncated, False
        matching_files = {
            file_path
            for file_path in walk.files
            if (include_hidden or not _is_hidden_relative(_lexical_relative(root, file_path)))
            and _matches(_lexical_relative(root, file_path), pattern)
        }
        matching_links = {
            relative: target_inside
            for relative, target_inside in walk.symlinks
            if (include_hidden or not _is_hidden_relative(relative))
            and _matches(relative, pattern)
        }
        if excluded:
            included.difference_update(matching_files)
            for relative in matching_links:
                included_links.pop(relative, None)
        else:
            included.update(matching_files)
            included_links.update(matching_links)
        if len(included) > MAX_FILES_PER_ARTIFACT:
            return sorted(included)[:MAX_FILES_PER_ARTIFACT], dynamic, False, True, False
    outside_link = any(not target_inside for target_inside in included_links.values())
    skipped_link = any(target_inside for target_inside in included_links.values())
    return sorted(included), dynamic, outside_link, walk.truncated, skipped_link


def _file_findings(
    root: Path,
    files: Iterable[Path],
    workflow: str,
    job: str,
    step: str,
    artifact: str,
) -> list[Finding]:
    findings: list[Finding] = []
    for file_path in files:
        relative = _relative(root, file_path)
        if relative is None:
            continue
        filename = file_path.name.lower()
        if filename in SENSITIVE_FILENAMES or filename.startswith(".env."):
            findings.append(
                Finding(
                    "sensitive-filename",
                    "high",
                    "Artifact includes a filename commonly used for credentials or environment secrets.",
                    workflow,
                    job,
                    step,
                    artifact,
                    relative,
                )
            )
        try:
            size = file_path.stat().st_size
            with file_path.open("rb") as artifact_file:
                content = artifact_file.read(MAX_ARTIFACT_FILE_BYTES).decode("utf-8", errors="ignore")
        except OSError:
            continue
        if size > MAX_ARTIFACT_FILE_BYTES:
            findings.append(
                Finding(
                    "artifact-file-too-large",
                    "info",
                    f"Only the first {MAX_ARTIFACT_FILE_BYTES} bytes were inspected for credential shapes.",
                    workflow,
                    job,
                    step,
                    artifact,
                    relative,
                )
            )
        for rule_id, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(
                    Finding(
                        rule_id,
                        "high",
                        "Artifact file matches a credential-shaped pattern; value is intentionally not shown.",
                        workflow,
                        job,
                        step,
                        artifact,
                        relative,
                    )
                )
    return findings


def _scan_workflow(root: Path, workflow_path: Path) -> tuple[list[Artifact], list[Finding]]:
    document = _read_yaml(workflow_path)
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return [], []

    workflow_name = _relative(root, workflow_path) or workflow_path.name
    artifacts: list[Artifact] = []
    findings: list[Finding] = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
            continue
        for index, raw_step in enumerate(job["steps"], start=1):
            if not isinstance(raw_step, dict):
                continue
            action, ref = _action_ref(str(raw_step.get("uses", "")))
            if action not in UPLOAD_ACTIONS:
                continue
            step = _step_label(raw_step, index)
            patterns = _patterns_from_step(raw_step, action)
            include_hidden, hidden_mode = _hidden_file_policy(raw_step)
            artifact = Artifact(
                name=_artifact_name(raw_step, f"{job_name}-{index}"),
                workflow=workflow_name,
                job=str(job_name),
                step=step,
                action=action,
                ref=ref,
                patterns=patterns,
                include_hidden_files=include_hidden,
                hidden_file_mode=hidden_mode,
            )
            files, dynamic, unsafe, truncated, skipped_link = _match_files(root, patterns, include_hidden)
            artifact.files = [_relative(root, file_path) for file_path in files if _relative(root, file_path)]
            artifact.dynamic_patterns = dynamic
            artifacts.append(artifact)
            if unsafe:
                findings.append(
                    Finding(
                        "unsafe-artifact-path",
                        "high",
                        "Artifact path is unsafe or selects a symlink whose target escapes the repository root.",
                        workflow_name,
                        str(job_name),
                        step,
                        artifact.name,
                    )
                )
            if truncated:
                findings.append(
                    Finding(
                        "artifact-enumeration-truncated",
                        "high",
                        "Artifact enumeration reached a safety limit; report is incomplete and the gate fails closed.",
                        workflow_name,
                        str(job_name),
                        step,
                        artifact.name,
                    )
                )
            if skipped_link:
                findings.append(
                    Finding(
                        "artifact-symlink-skipped",
                        "info",
                        "An in-root symlink was selected but not traversed; report excludes its target contents.",
                        workflow_name,
                        str(job_name),
                        step,
                        artifact.name,
                    )
                )
            if hidden_mode == "conservative-dynamic":
                findings.append(
                    Finding(
                        "dynamic-include-hidden-files",
                        "medium",
                        "Whether hidden files are uploaded is dynamic; static enumeration includes them conservatively.",
                        workflow_name,
                        str(job_name),
                        step,
                        artifact.name,
                    )
                )
            for dynamic_pattern in dynamic:
                findings.append(
                    Finding(
                        "dynamic-artifact-path",
                        "medium",
                        "Artifact path is dynamic and cannot be resolved safely by static analysis.",
                        workflow_name,
                        str(job_name),
                        step,
                        artifact.name,
                        dynamic_pattern,
                    )
                )
            if not files and not dynamic and not unsafe and not truncated:
                findings.append(
                    Finding(
                        "artifact-path-not-present",
                        "info",
                        "No matching files exist in the current worktree; the path may be produced only during CI.",
                        workflow_name,
                        str(job_name),
                        step,
                        artifact.name,
                    )
                )
            findings.extend(_file_findings(root, files, workflow_name, str(job_name), step, artifact.name))
    return artifacts, findings


def scan_project(root: str | Path, workflows: Iterable[str | Path] | None = None) -> ScanReport:
    """Scan known GitHub Actions upload surfaces without executing repository code."""

    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError(f"root is not a directory: {root_path}")
    if workflows is None:
        workflow_paths = discover_workflows(root_path)
    else:
        workflow_paths = []
        for value in workflows:
            path = Path(value)
            path = path if path.is_absolute() else root_path / path
            resolved = path.resolve()
            if not _is_within(root_path, resolved):
                raise ValueError(f"workflow is outside root: {value}")
            if not resolved.is_file():
                raise ValueError(f"workflow is not a file: {value}")
            workflow_paths.append(resolved)

    all_artifacts: list[Artifact] = []
    all_findings: list[Finding] = []
    for workflow_path in sorted(set(workflow_paths)):
        artifacts, findings = _scan_workflow(root_path, workflow_path)
        all_artifacts.extend(artifacts)
        all_findings.extend(findings)

    severity_rank = {"high": 3, "medium": 2, "low": 1, "info": 0}
    all_artifacts.sort(key=lambda artifact: (artifact.workflow, artifact.job, artifact.step, artifact.name))
    all_findings.sort(
        key=lambda finding: (
            -severity_rank.get(finding.severity, 0),
            finding.workflow,
            finding.job,
            finding.step,
            finding.rule_id,
            finding.path or "",
        )
    )
    return ScanReport(
        root=str(root_path),
        workflows=[_relative(root_path, path) or path.name for path in sorted(set(workflow_paths))],
        artifacts=all_artifacts,
        findings=all_findings,
    )


def has_severity(report: ScanReport, threshold: str = "high") -> bool:
    """Return whether a finding meets or exceeds a severity threshold."""

    ranks = {"info": 0, "low": 1, "medium": 2, "high": 3}
    if threshold not in ranks:
        raise ValueError(f"unknown severity: {threshold}")
    return any(ranks.get(finding.severity, 0) >= ranks[threshold] for finding in report.findings)
