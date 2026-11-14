"""Pure fail-closed proposal for safe path/runner validation of MASTER composer.

DOES NOT run MASTER, install Windows services, connect to PG or create files.
Separate from reviewed MASTER #77 until that owner explicitly approves.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path, PurePath, PurePosixPath

ALLOWED = (".github/", "docs/", "src/", "frontend/", "scripts/", "tests/")
PROTECTED = frozenset({
    "src/integration/pc4_runtime.py", "src/api/worker.py", "pyproject.toml",
})
_SHA = re.compile(r"^[0-9a-f]{40}$")
_TRUSTED_RUNNER_OS = {"Linux", "Windows"}


class MasterComposeRefused(ValueError):
    pass


def require_ci_runner_identity(environment: dict[str, str], *,
                               expected_staging_sha: str) -> None:
    """Require GitHub Actions job-bound markers, not CI=true alone.

    Still cannot attest GitHub infrastructure cryptographically: trust comes
    from the workflow's immutable checkout+permissions, not env strings.
    """
    if not isinstance(environment, dict) or not _SHA.fullmatch(expected_staging_sha):
        raise MasterComposeRefused("invalid_immutable_staging_pin")
    for key in ("GITHUB_ACTIONS", "CI", "GITHUB_WORKSPACE", "GITHUB_SHA",
                "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_JOB",
                "RUNNER_TEMP", "RUNNER_OS"):
        value = environment.get(key)
        if not isinstance(value, str) or not value.strip():
            raise MasterComposeRefused("missing_hosted_ci_identity:" + key)
    if (environment["GITHUB_ACTIONS"] != "true"
            or environment["CI"] != "true"
            or environment["GITHUB_REPOSITORY"] != "YehorBabenko88/tester"
            or environment["GITHUB_SHA"] != expected_staging_sha
            or not environment["GITHUB_RUN_ID"].isdigit()
            or environment["RUNNER_OS"] not in _TRUSTED_RUNNER_OS):
        raise MasterComposeRefused("untrusted_ci_environment_or_staging")
    workspace = Path(environment["GITHUB_WORKSPACE"])
    temp = Path(environment["RUNNER_TEMP"])
    if (not workspace.is_absolute() or not temp.is_absolute()
            or workspace.is_symlink() or temp.is_symlink()
            or not workspace.is_dir() or not temp.is_dir()
            or workspace.resolve() == temp.resolve()):
        raise MasterComposeRefused("unsafe_hosted_workspace")
    # A forged environment alone cannot provide cryptographic runner proof.
    # Explicit binding to the job's immutable checked-out commit remains required.


def require_repo_file_path(path: str) -> PurePosixPath:
    """Reject escapes, absolute/Windows paths, separators and protected targets."""
    if (not isinstance(path, str) or not 0 < len(path) <= 512
            or path.startswith(("/", "\\", ".\\", "./"))
            or "\\" in path or "\x00" in path or ":" in path
            or path.endswith("/") or "//" in path):
        raise MasterComposeRefused("unsafe_repo_path")
    parts = path.split("/")
    if (any(p in ("", ".", "..") for p in parts)
            or not path.startswith(ALLOWED)
            or path in PROTECTED):
        raise MasterComposeRefused("path_traversal_or_unapproved_file")
    return PurePosixPath(path)


def require_safe_destination(root: Path, path: str) -> Path:
    """Read-only path preflight; symlink recheck needed at actual atomic open.

    Using Path.resolve alone is insufficient against a concurrent swap.
    Follow-up MASTER implementation needs directory-fd/openat + O_NOFOLLOW
    and a staged atomic replacement, without following existing symlinks.
    """
    candidate = require_repo_file_path(path)
    if (not isinstance(root, Path) or not root.is_absolute()
            or not root.is_dir() or root.is_symlink()):
        raise MasterComposeRefused("untrusted_checkout_root")
    base = root.resolve(strict=True)
    at = base
    for part in candidate.parts[:-1]:
        at = at / part
        if at.is_symlink():
            raise MasterComposeRefused("symlinked_destination_parent")
        if at.exists() and not at.is_dir():
            raise MasterComposeRefused("non_directory_destination_parent")
    target = base.joinpath(*candidate.parts)
    if target.is_symlink():
        raise MasterComposeRefused("symlinked_destination_file")
    if target.exists() and not target.is_file():
        raise MasterComposeRefused("not_regular_destination_file")
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise MasterComposeRefused("destination_outside_checkout") from exc
    return target


def audit_composer_runtime_surface(source: str) -> dict:
    """Static detection: the approved MASTER v1 fails several hardening gates."""
    if not isinstance(source, str) or not source:
        raise MasterComposeRefused("missing_master_source")
    tree = ast.parse(source)
    call_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                call_names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                call_names.add(fn.attr)
    text = source
    runner_bound = (
        "GITHUB_ACTIONS" in text and "GITHUB_WORKSPACE" in text
        and "GITHUB_REPOSITORY" in text and "GITHUB_SHA" in text
    )
    nofollow = "O_NOFOLLOW" in text or "openat" in text
    path_checked = "is_symlink" in text and "relative_to" in text
    native_spawn = bool(call_names & {
        "Popen", "runpy", "exec", "eval", "CreateProcess", "StartService",
        "start_service", "reboot", "system", "kill", "terminate",
    })
    return {
        "schema": "ops-master-composer-hardening-proposal/1",
        "git_only_source": True,
        "runtime_runner_binding_proven": runner_bound,
        "symlink_path_preflight_present": path_checked,
        "atomic_no_follow_open_present": nofollow,
        "native_launcher_activation_call_found": native_spawn,
        "protected_installed_runtime_paths": sorted(PROTECTED),
        "safe_to_run_outside_isolated_ci": False,
        "overall": "BLOCKED_HARDENING_REQUIRED" if not (
            runner_bound and path_checked and nofollow
        ) or native_spawn else "SOURCE_REVIEW_ONLY",
    }
