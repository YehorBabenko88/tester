"""Fresh lab lifecycle; no legacy marker adoption and no recursive deletion.

The inventory must come from an independently verified published Git archive.
This module checks bytes against that inventory, not Git provenance itself.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path, PurePosixPath

SCHEMA = "tester-lab/v1"
MARKER = ".tester-lab.json"


class LabViolation(ValueError):
    pass


def plain(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise LabViolation("Reparse/symlink paths are forbidden")
    return path


def verify_tree(source, inventory):
    source = plain(source)
    if not inventory:
        raise LabViolation("Empty archive inventory")
    for name, digest in inventory.items():
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name or str(p) != name:
            raise LabViolation("Unsafe inventory path")
        if not re.fullmatch("[0-9a-f]{64}", digest):
            raise LabViolation("Invalid inventory digest")
    actual = {}
    for p in source.rglob("*"):
        plain(p)
        if p.is_file():
            actual[p.relative_to(source).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    if actual != inventory:
        raise LabViolation("Release does not match exact archive inventory")


def marker(root):
    root = plain(root)
    plain(root / MARKER)
    try:
        data = json.loads((root / MARKER).read_text(encoding="utf-8"))
        ident = uuid.UUID(data["installation_id"])
        expected = {"app": "TesterLabApp_" + ident.hex, "postgres": "TesterLabPG_" + ident.hex}
        valid = (
            data["schema"] == SCHEMA
            and data["root"] == str(root)
            and data["services"] == expected
            and data["ports"] == {"api": 18766, "postgres": 55470}
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", data["repository"])
            and re.fullmatch("[0-9a-f]{40}", data["active_sha"])
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LabViolation("Missing or invalid fresh lab marker") from exc
    if not valid:
        raise LabViolation("Foreign lab marker")
    return data


def write_marker(root, data, *, staging_parent=None):
    target = plain(root / MARKER)
    temporary = (
        plain(root / ".tester-lab.pending")
        if staging_parent is None
        else plain(staging_parent / (".tester-lab-marker-" + uuid.uuid4().hex + ".pending"))
    )
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
    os.replace(temporary, target)


def prepare(root, repository, sha, source, inventory):
    root, source = plain(root), plain(source)
    if not re.fullmatch("[0-9a-f]{40}", sha) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise LabViolation("Exact commit SHA and repository required")
    verify_tree(source, inventory)
    if root.exists() and any(root.iterdir()):
        raise LabViolation("Prepare requires a new empty root; never adopt existing installation")
    root.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4()
    data = dict(
        schema=SCHEMA,
        installation_id=str(ident),
        repository=repository,
        root=str(root),
        services={"app": "TesterLabApp_" + ident.hex, "postgres": "TesterLabPG_" + ident.hex},
        ports={"api": 18766, "postgres": 55470},
        active_sha=sha,
        releases={sha: inventory},
    )
    for name in ("releases", "runtime", "state", "dependencies", "backups"):
        (root / name).mkdir()
    release = root / "releases" / sha
    shutil.copytree(source, release)
    verify_tree(release, inventory)
    write_marker(root, data)
    return data


def check(root):
    root = plain(root)
    data = marker(root)
    for sha, inventory in data["releases"].items():
        if not re.fullmatch("[0-9a-f]{40}", sha):
            raise LabViolation("Invalid release key")
        verify_tree(root / "releases" / sha, inventory)
    if data["active_sha"] not in data["releases"]:
        raise LabViolation("Active release is not inventoried")
    return data


def _stopped_owned(data, query):
    import subprocess

    from .lab_scm import owned, pg_plan, service_plan

    app, pg = service_plan(data["root"]), pg_plan(data["root"])
    found = []
    for name in data["services"].values():
        service = query(name)
        if service is not None:
            plan = app if name == app["name"] else pg
            image = subprocess.list2cmdline([app["host"]]) if plan is app else pg["image"]
            owned(service, plan, image)
            if (
                service.get("installation_id") != data["installation_id"]
                or service.get("root") != data["root"]
                or service.get("state") != "stopped"
            ):
                raise LabViolation("Active or foreign service; no lifecycle mutation allowed")
            found.append(name)
    return found


def rollback(root, sha, query):
    root = plain(root)
    data = check(root)
    if any(query(name) is not None for name in data["services"].values()):
        raise LabViolation(
            "Deregister stopped services before selecting a different immutable release"
        )
    if sha not in data["releases"]:
        raise LabViolation("Rollback only to an inventoried retained release")
    data["active_sha"] = sha
    write_marker(root, data)
    return data


def stage_release(root, sha, source, inventory, query):
    root = plain(root)
    data = check(root)
    _stopped_owned(data, query)
    if not re.fullmatch("[0-9a-f]{40}", sha) or sha in data["releases"]:
        raise LabViolation("New exact unique published SHA required")
    source = plain(source)
    verify_tree(source, inventory)
    destination = plain(root / "releases" / sha)
    if destination.exists():
        raise LabViolation("Never overwrite retained release bytes")
    staging_parent = plain(source.parent)
    if staging_parent == root or root in staging_parent.parents:
        raise LabViolation("Published source staging parent must be outside the installation root")
    if staging_parent.stat().st_dev != destination.parent.stat().st_dev:
        raise LabViolation("Release staging requires a same-volume source parent and destination")
    temporary = plain(staging_parent / (".tester-lab-stage-" + sha + "-" + uuid.uuid4().hex))
    if temporary.parent != staging_parent or destination.parent != root / "releases":
        raise LabViolation("Release staging path bounds mismatch")
    try:
        shutil.copytree(source, temporary)
        verify_tree(temporary, inventory)
        # Windows rename refuses an existing destination; never copy into inherited-RX trees.
        plain(destination)
        if destination.exists():
            raise LabViolation("Destination appeared during staging; never overwrite")
        os.rename(temporary, destination)
        verify_tree(destination, inventory)
        data["releases"][sha] = inventory
        write_marker(root, data, staging_parent=staging_parent)
    except Exception as exc:
        raise LabViolation(
            f"Release staging failed; retained temporary={temporary}; destination={destination}; no cleanup performed"
        ) from exc
    return data


add_release = stage_release


def remove(root, query, deregister):
    root = plain(root)
    data = check(root)
    owned = _stopped_owned(data, query)
    for name in owned:
        deregister(name)
    destination = plain(root / "state" / ("removed-" + data["installation_id"] + ".json"))
    if destination.exists():
        raise LabViolation("Removal archive already exists")
    (root / MARKER).rename(destination)
    return {"retained_root": str(root), "removed_services": owned}
