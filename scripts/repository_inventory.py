"""Read-only tracked-file, import, documentation and branch consolidation audit."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

STATES = {"KEEP", "MIGRATE", "REPLACE", "DELETE", "REVIEW_REQUIRED"}


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode("utf-8")


def inventory(repo, prs=None):
    repo = Path(repo)
    names = git(repo, "ls-files", "-z").strip("\0").split("\0")
    texts = {}
    for name in names:
        try:
            texts[name] = (repo / name).read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError):
            pass
    modules = {n[:-3].replace("/", "."): n for n in names if n.endswith(".py")}
    modules.update({n[:-12].replace("/", "."): n for n in names if n.endswith("/__init__.py")})
    imports, errors, links = {}, [], []
    for name, text in texts.items():
        if name.endswith(".py"):
            try:
                tree = ast.parse(text)
            except SyntaxError as exc:
                errors.append({"path": name, "line": exc.lineno, "error": "syntax"})
                continue
            edges = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    edges.extend(x.name for x in node.names)
                elif isinstance(node, ast.ImportFrom):
                    base = name[:-3].split("/")[:-1]
                    if node.level:
                        base = base[: len(base) - node.level + 1]
                        module = ".".join(base + ([node.module] if node.module else []))
                    else:
                        module = node.module or ""
                    edges.append(module)
            imports[name] = sorted(set(edges))
        if name.endswith(".md"):
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if "://" in target or target.startswith(("#", "mailto:")):
                    continue
                target = target.split("#")[0]
                if target and not (repo / name).parent.joinpath(target).exists():
                    links.append({"path": name, "target": target, "status": "REVIEW_REQUIRED"})
    files = []
    for name in names:
        basename = Path(name).name
        consumers = sorted(
            n
            for n, text in texts.items()
            if n != name and (name in text or (len(basename) > 7 and basename in text))
        )
        imported_by = sorted(
            n for n, deps in imports.items() if any(modules.get(d) == name for d in deps)
        )
        state = "KEEP"
        reason = (
            "Tracked functionality or evidence preserved; no deletion inferred from static absence"
        )
        if name.startswith("scripts/windows_preview/"):
            state, reason = (
                "REPLACE",
                "Legacy preview entrypoint superseded by signed Setup-Tester.ps1; migration and dynamic consumers require review before deletion",
            )
        elif name.startswith("docs/") and any(
            x in name for x in ("acceptance", "evidence", "physical")
        ):
            reason = "Prior acceptance evidence retained with original provenance"
        elif name.endswith((".pyc", ".pyo")) or "/__pycache__/" in name:
            state, reason = (
                "REVIEW_REQUIRED",
                "Generated artifact candidate; prove consumers/replacement before removal",
            )
        files.append(
            {
                "path": name,
                "sha256": hashlib.sha256((repo / name).read_bytes()).hexdigest(),
                "classification": state,
                "reason": reason,
                "text_consumers": consumers,
                "static_importers": imported_by,
                "dynamic_dependencies": "REVIEW_REQUIRED",
                "installer_consumers": [
                    n for n in consumers if "install" in n.lower() or "windows" in n.lower()
                ],
                "workflow_consumers": [n for n in consumers if n.startswith(".github/workflows/")],
                "test_consumers": [n for n in consumers if n.startswith("tests/")],
                "deletion_authorized": False,
            }
        )
    unresolved_internal = []
    for name, deps in imports.items():
        for dep in deps:
            if (
                dep.startswith(("src.", "contracts."))
                and dep not in modules
                and not any(m.startswith(dep + ".") for m in modules)
            ):
                unresolved_internal.append(
                    {"path": name, "module": dep, "status": "REVIEW_REQUIRED"}
                )
    dependencies = {
        n: text.splitlines()
        for n, text in texts.items()
        if Path(n).name.startswith("requirements")
        or n in {"pyproject.toml", "frontend/package.json"}
    }
    branches = []
    head = git(repo, "rev-parse", "HEAD").strip()
    for line in git(
        repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes"
    ).splitlines():
        ref, sha = line.split()
        if ref.endswith("/HEAD"):
            continue
        unique = git(repo, "rev-list", "--count", head + ".." + sha).strip()
        branches.append(
            {
                "ref": ref,
                "sha": sha,
                "commits_not_in_candidate": int(unique),
                "classification": "INTEGRATED_HISTORY"
                if unique == "0"
                else "UNIQUE_CODE_REVIEW_REQUIRED",
                "cleanup": "Owner approval only after independent GO and main integration; inspect cherry-equivalent and unmerged changes",
                "delete_now": False,
            }
        )
    reviewed_prs = None
    if prs is not None:
        reviewed_prs = []
        for pr in prs:
            item = dict(pr)
            sha = pr.get("headRefOid", "")
            present = (
                bool(re.fullmatch(r"[0-9a-f]{40}", sha))
                and subprocess.run(
                    ["git", "-C", str(repo), "cat-file", "-e", sha + "^{commit}"],
                    capture_output=True,
                ).returncode
                == 0
            )
            item["head_available_locally"] = present
            item["commits_not_in_candidate"] = (
                int(git(repo, "rev-list", "--count", head + ".." + sha).strip())
                if present
                else None
            )
            item["release_action"] = "REVIEW_REQUIRED"
            if pr.get("state") in {"MERGED", "CLOSED"}:
                item["release_action"] = "RETAIN_HISTORICAL_PR_EVIDENCE"
            elif present and item["commits_not_in_candidate"] == 0:
                item["release_action"] = (
                    "CLOSE_CANDIDATE_ONLY_AFTER_INDEPENDENT_GO_AND_OWNER_APPROVAL"
                )
            item["automatic_close_authorized"] = False
            reviewed_prs.append(item)
    return {
        "schema": "tester-repository-inventory/v1",
        "source_sha": head,
        "snapshot_kind": "TRACKED_WORKING_TREE",
        "workspace_changes": git(
            repo, "status", "--porcelain", "--untracked-files=no"
        ).splitlines(),
        "coverage": {
            "tracked_files": len(names),
            "python_modules": len(imports),
            "branches": len(branches),
            "pr_status": "SNAPSHOT_SUPPLIED" if prs is not None else "NOT_FETCHED",
        },
        "limitations": [
            "Text and AST edges are static evidence; dynamic import/plugin and external consumers require review",
            "No file or branch deletion is authorized by this report",
        ],
        "files": files,
        "imports": imports,
        "parse_errors": errors,
        "unresolved_internal_imports": unresolved_internal,
        "dependency_manifests": dependencies,
        "unresolved_document_links": links,
        "branches": branches,
        "pull_requests": reviewed_prs,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--pr-snapshot", type=Path)
    a = p.parse_args()
    prs = json.loads(a.pr_snapshot.read_text(encoding="utf-8-sig")) if a.pr_snapshot else None
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(
        json.dumps(inventory(a.repo, prs), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
