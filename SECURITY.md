# Security Policy

## Supported versions

Artifact Fence is pre-1.0. The latest commit on `main` is the only supported version. Please upgrade before filing a report.

## Reporting a vulnerability

**Do not open a public Issue for a suspected vulnerability.** Use GitHub's private vulnerability reporting feature for this repository, or contact the repository owner privately through the contact method shown on the GitHub profile. Include a minimal reproduction, affected version/commit, impact, and proposed mitigation if known.

Do not include real secrets, access tokens, customer artifacts, or private repositories in a report. Replace every credential with a clearly fake value.

## Security boundaries

Artifact Fence deliberately does not execute workflows, repository code, shell commands, JavaScript, Docker, or external downloads. Its findings are static heuristics over the current working tree and workflow YAML. A clean result is **not** proof that a CI run, artifact, repository, or deployment is secure.

Reports that bypass root-boundary checks, disclose a scanned value, incorrectly follow a symlink outside the requested root, or cause unsafe parsing/resource exhaustion are security-relevant. False positives and missed secret patterns are welcome as normal Issues unless they create a concrete bypass of a documented security boundary.
