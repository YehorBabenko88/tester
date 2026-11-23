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


NETWORK_CONTRACTS = {
    "bybit.py": {
        "classification": "MIGRATE",
        "semantics": "Legacy public candle/archive endpoint adapter and timeframe helpers; engine.py, test_core.py and live baseline acceptance consume it",
        "migration_gate": "Preserve parser/timeframe/coverage regressions and historical archive semantics before routing network calls through the canonical transport",
    },
    "bybit_history.py": {
        "classification": "MIGRATE",
        "semantics": "Standalone CLI with raw_market/ingest_ranges schema, eight-attempt loop and legacy DATABASE_URL default; no current Python/workflow importer found",
        "migration_gate": "Prove candle/mark/index/premium/OI/funding and raw-schema data migration equivalence before retiring this independent CLI",
    },
    "src/api/bybit_catalog.py": {
        "classification": "KEEP",
        "semantics": "Active catalog API adapter: instruments/tickers, one bounded streaming attempt, Windows system TLS and scheduler-owned retry; different DTO/acquisition budget from history",
        "migration_gate": "Any transport consolidation must preserve streaming byte limits, TLS policy, scheduler retry ownership and catalog contracts; do not add nested retry",
    },
    "src/ingest/transport.py": {
        "classification": "KEEP",
        "semantics": "Canonical historical public GET transport with shared throttling and bounded retries; active lab_market, pipeline and live acceptance consumers",
        "migration_gate": "Retain Guardian/public endpoint, cancellation and retry-budget regression contracts",
    },
}


def hosted_query(repository, endpoint):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Explicit owner/repository required")
    try:
        result = subprocess.run(
            ["gh", "api", "--paginate", "--slurp", f"repos/{repository}/{endpoint}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            return {
                "status": "NOT_VERIFIED",
                "items": None,
                "reason": "Hosted API request failed",
                "endpoint": endpoint,
            }
        pages = json.loads(result.stdout)
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise ValueError("Unexpected paginated response")
        return {
            "status": "VERIFIED",
            "items": [item for page in pages for item in page],
            "endpoint": endpoint,
        }
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {
            "status": "NOT_VERIFIED",
            "items": None,
            "reason": "Hosted API unavailable or malformed",
            "endpoint": endpoint,
        }


def hosted_inventory(repository):
    result = {}
    fields = {
        "tags": ("tags?per_page=100", ("name", "commit")),
        "releases": (
            "releases?per_page=100",
            (
                "id",
                "tag_name",
                "target_commitish",
                "draft",
                "prerelease",
                "published_at",
                "html_url",
            ),
        ),
        "deployments": (
            "deployments?per_page=100",
            ("id", "ref", "sha", "environment", "created_at", "statuses_url"),
        ),
        "pull_requests": ("pulls?state=all&per_page=100", ()),
    }
    for kind, (endpoint, selected) in fields.items():
        query = hosted_query(repository, endpoint)
        if query["status"] == "VERIFIED":
            if kind == "pull_requests":
                query["items"] = [
                    dict(
                        number=x["number"],
                        title=x["title"],
                        state="MERGED" if x.get("merged_at") else x["state"].upper(),
                        headRefName=x["head"]["ref"],
                        headRefOid=x["head"]["sha"],
                        baseRefName=x["base"]["ref"],
                        baseRefOid=x["base"]["sha"],
                        mergedAt=x.get("merged_at"),
                        url=x["html_url"],
                    )
                    for x in query["items"]
                ]
            else:
                query["items"] = [
                    {key: item.get(key) for key in selected} for item in query["items"]
                ]
        result[kind] = query
    return result


def reference_consumers(ref, sha, hosted):
    if not hosted:
        return {"status": "NOT_VERIFIED", "items": None}
    short = re.sub(r"^refs/(heads/|remotes/origin/)", "", ref)
    items = []
    for kind, query in hosted.items():
        if query.get("status") != "VERIFIED":
            continue
        for item in query["items"]:
            match = False
            if kind == "pull_requests":
                match = short in {item.get("headRefName"), item.get("baseRefName")} or sha in {
                    item.get("headRefOid"),
                    item.get("baseRefOid"),
                }
            elif kind == "deployments":
                match = short == item.get("ref") or sha == item.get("sha")
            elif kind == "releases":
                match = short == item.get("target_commitish")
            elif kind == "tags":
                match = sha == (item.get("commit") or {}).get("sha")
            if match:
                items.append({"kind": kind, "reference": item})
    status = (
        "VERIFIED"
        if all(q.get("status") == "VERIFIED" for q in hosted.values())
        else "PARTIALLY_VERIFIED"
    )
    return {
        "status": status,
        "items": items,
        "scope": "Exact ref/tip matches only; ancestor/deployment runtime reachability still requires review",
    }


def workflow_filters(texts):
    result = {}
    for name, text in texts.items():
        if not name.startswith(".github/workflows/"):
            continue
        found = []
        lines = text.splitlines()
        for i, line in enumerate(lines):
            match = re.match(r"^(\s*)(paths|paths-ignore):\s*(.*)$", line)
            if not match:
                continue
            indent, kind, inline = match.groups()
            values = []
            if not inline:
                for child in lines[i + 1 :]:
                    if child.strip() and len(child) - len(child.lstrip()) <= len(indent):
                        break
                    value = re.match(r"^\s*-\s*(.+?)\s*$", child)
                    if value:
                        values.append(value[1].strip("\"'"))
            found.append(
                {
                    "kind": kind,
                    "paths": values if not inline else None,
                    "inline_raw": inline or None,
                    "line": i + 1,
                    "status": "REVIEW_REQUIRED" if inline else "PARSED_STATIC_LIST",
                }
            )
        result[name] = {
            "filters": found,
            "absence_means": "No explicit paths/paths-ignore key observed; event/job conditions still require review",
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
    return result


def inventory(repo, prs=None, hosted=None):
    repo = Path(repo)
    if hosted and hosted.get("pull_requests", {}).get("status") == "VERIFIED":
        prs = hosted["pull_requests"]["items"]
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
        if name in NETWORK_CONTRACTS:
            state = NETWORK_CONTRACTS[name]["classification"]
            reason = (
                NETWORK_CONTRACTS[name]["semantics"]
                + "; "
                + NETWORK_CONTRACTS[name]["migration_gate"]
            )
        elif name == "scripts/windows_preview/migrate_preview_history.py":
            reason = "Owned Preview history migration still consumed by isolation regression tests"
        elif name.startswith("scripts/windows_preview/"):
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
                "protected_reference_consumers": reference_consumers(ref, sha, hosted),
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
    cleanup_path = repo / "docs/release/cleanup-proof.json"
    cleanup = (
        json.loads(cleanup_path.read_text(encoding="utf-8-sig")) if cleanup_path.exists() else {}
    )
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
        "deleted_files": [
            dict(row, classification="DELETE") for row in cleanup.get("deletions", [])
        ],
        "imports": imports,
        "parse_errors": errors,
        "unresolved_internal_imports": unresolved_internal,
        "dependency_manifests": dependencies,
        "unresolved_document_links": links,
        "branches": branches,
        "pull_requests": reviewed_prs,
        "hosted_reference_consumers": hosted
        or {
            kind: {"status": "NOT_VERIFIED", "items": None, "reason": "Hosted query not requested"}
            for kind in ("tags", "releases", "deployments", "pull_requests")
        },
        "local_tags": git(
            repo, "for-each-ref", "--format=%(refname) %(objectname) %(objecttype)", "refs/tags"
        ).splitlines(),
        "workflow_path_filters": workflow_filters(texts),
        "network_contracts": {
            name: proof for name, proof in NETWORK_CONTRACTS.items() if name in names
        },
        "installed_host_pins": {
            "status": "NOT_VERIFIED",
            "reason": "No live installed-host or system-service query authorized by this inventory; historical deployment evidence does not establish current installation",
        },
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--pr-snapshot", type=Path)
    p.add_argument(
        "--github-repository", help="Read-only paginated tags/releases/deployments/PR audit"
    )
    a = p.parse_args()
    prs = json.loads(a.pr_snapshot.read_text(encoding="utf-8-sig")) if a.pr_snapshot else None
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(
        json.dumps(
            inventory(
                a.repo, prs, hosted_inventory(a.github_repository) if a.github_repository else None
            ),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
