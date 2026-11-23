"""Read-only exact-blob and API-shape audit for four unmerged Guardian PRs.

Never imports unreviewed Guardian modules, checks out another branch, fetches
network content, writes application state or authorizes runtime effects.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PINS = ROOT / "docs/guardians/guardian-static-pins.json"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN_IMPORTS = frozenset(
    {"subprocess", "socket", "requests", "httpx", "psycopg", "win32service", "web3"}
)


def git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def verify_commit(sha: str) -> None:
    if type(sha) is not str or not _SHA.fullmatch(sha):
        raise ValueError("invalid_exact_sha")
    if git("rev-parse", "--verify", sha + "^{commit}").decode().strip() != sha:
        raise ValueError("commit_not_exact")


def check_entry(sha: str, path: str) -> tuple[str, ast.Module | None]:
    if path.startswith("/") or "\\" in path or ".." in Path(path).parts:
        raise ValueError("unsafe_file_path")
    tree = git("ls-tree", sha, "--", path).decode().strip().split()
    if len(tree) != 4 or tree[0] not in {"100644", "100755"} or tree[1] != "blob":
        raise ValueError("file_mode_or_blob_invalid:" + path)
    data = git("show", sha + ":" + path)
    syntax = None
    if path.endswith(".py"):
        code = data.decode("utf-8")
        syntax = ast.parse(code, filename=path)
        compile(syntax, path, "exec")
    return hashlib.sha256(data).hexdigest(), syntax


def check_manifest(path: Path = DEFAULT_PINS) -> dict:
    pins = json.loads(path.read_text(encoding="utf-8"))
    if pins.get("schema") != "tester-guardian-integration-static-pins/v1":
        raise ValueError("pin_schema_unknown")
    foundation = pins["foundation"]
    verify_commit(foundation)
    guardians = pins.get("guardians")
    if not isinstance(guardians, dict) or set(guardians) != {"code", "data", "storage", "exchange"}:
        raise ValueError("incomplete_guardian_pins")

    observed: set[str] = set()
    receipts: dict[str, dict] = {}
    for role in ("code", "data", "storage", "exchange"):
        spec = guardians[role]
        sha = spec["sha"]
        verify_commit(sha)
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", foundation, sha],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        allowed = {spec["module"], spec["test"], *spec["docs"]}
        if len(allowed) != 4 or allowed & observed:
            raise ValueError("cross_guardian_path_collision:" + role)
        paths = set(git("diff", "--name-only", foundation, sha).decode().splitlines())
        if paths != allowed:
            raise ValueError(
                f"unexpected_files:{role}:" + ",".join(sorted(paths.symmetric_difference(allowed)))
            )
        blobs = {}
        for file in sorted(paths):
            digest, syntax = check_entry(sha, file)
            blobs[file] = digest
            if file == spec["module"]:
                if syntax is None:
                    raise ValueError("module_is_not_python")
                exports = {
                    node.name
                    for node in syntax.body
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                }
                missing = set(spec["exports"]) - exports
                if missing:
                    raise ValueError("missing_api:" + role + ":" + ",".join(sorted(missing)))
                modules = {
                    item.name.split(".")[0]
                    for node in syntax.body
                    if isinstance(node, ast.Import)
                    for item in node.names
                } | {
                    (node.module or "").split(".")[0]
                    for node in syntax.body
                    if isinstance(node, ast.ImportFrom)
                }
                if _FORBIDDEN_IMPORTS & modules:
                    raise ValueError("runtime_effector_import:" + role)
        observed.update(paths)
        receipts[role] = {
            "sha": sha,
            "paths": sorted(paths),
            "sha256": blobs,
            "exports": sorted(spec["exports"]),
            "status": "STATIC_ONLY_NOT_RUNTIME_VERIFIED",
        }
    return {
        "schema": pins["schema"],
        "foundation": foundation,
        "guardians": receipts,
        "status": "PINNED_SOURCE_STATIC_PASS_NOT_RELEASE_GO",
        "guardian_effectors_enabled": False,
    }


if __name__ == "__main__":
    if os.environ.get("GUARDIANS_ENABLED", "false").lower() == "true":
        raise SystemExit("guardian_effectors_must_remain_disabled")
    try:
        report = check_manifest()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print("GUARDIAN_PINNED_STATIC_FAIL:", type(exc).__name__, str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
