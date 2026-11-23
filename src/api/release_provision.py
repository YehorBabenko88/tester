"""Same App service provisions under its unique SID, then becomes the runtime host."""

import json
import os
import re
import secrets
import subprocess
from pathlib import Path

from .lab_install import LabViolation, plain, verify_tree
from .release_install import load_marker, system_binary, write_json
from .release_service import ReleaseService, plans


def provisioning_plan(marker):
    app, _ = plans(marker)
    return dict(
        app,
        auto_start=False,
        recovery_delays_ms=[],
        failure_on_non_crash=False,
        dependency=None,
        python_class=app["python_class"].removesuffix(".ReleaseService")
        + ".ReleaseProvisionService",
    )


def provision(root):
    marker = load_marker(root)
    authority = json.loads(plain(root / "state/provisioning-authority.json").read_text())
    if authority["marker"] != marker:
        raise LabViolation("Provisioning authority differs from immutable marker")
    for relative, receipt in authority["immutable"].items():
        verify_tree(root / relative, receipt)
    phase_path = root / "state/install.jsonl"

    def stage(name):
        if (root / "runtime/stop.request").exists():
            raise InterruptedError("Provisioning stopped")
        with phase_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    dict(
                        phase=name,
                        installation_id=marker["installation_id"],
                        source_sha=marker["source_sha"],
                    )
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
        GUARDIANS_ENABLED="false",
        TEMP=str(root / "runtime/tmp"),
        TMP=str(root / "runtime/tmp"),
        PIP_NO_CACHE_DIR="1",
    )
    (root / "runtime/tmp").mkdir(exist_ok=True)
    release = root / "releases" / marker["source_sha"]
    log = (root / "runtime/provisioning.log").open("a", encoding="utf-8")

    def run(*args, **kwargs):
        if not kwargs.get("capture_output"):
            kwargs.setdefault("stdout", log)
            kwargs.setdefault("stderr", subprocess.STDOUT)
        return subprocess.run(*args, **kwargs)

    stage("creating-private-venv")
    python = root / "dependencies/python/python.exe"
    run(
        [str(python), "-I", "-B", "-m", "venv", str(root / "dependencies/venv")],
        check=True,
        timeout=120,
        env=env,
    )
    venv = root / "dependencies/venv/Scripts/python.exe"
    stage("installing-hash-locked-dependencies")
    run(
        [
            str(venv),
            "-I",
            "-B",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-compile",
            "--require-hashes",
            "--only-binary=:all:",
            "--find-links",
            str(root / "dependencies/wheelhouse"),
            "-r",
            str(release / "requirements-release-windows314.lock"),
        ],
        check=True,
        timeout=300,
        env=env,
    )
    stage("initializing-dedicated-postgres")
    pgbin = root / "dependencies/postgres/bin"
    password = secrets.token_urlsafe(36)
    password_file = root / "private/initdb-password.txt"
    password_file.write_text(password, encoding="utf-8")
    run(
        [
            str(pgbin / "initdb.exe"),
            "-D",
            str(root / "postgres-data"),
            "-U",
            "tester_owner",
            "--pwfile",
            str(password_file),
            "--auth-host=scram-sha-256",
            "--auth-local=scram-sha-256",
            "--encoding=UTF8",
        ],
        check=True,
        timeout=120,
        env=env,
    )
    password_file.unlink()
    (root / "postgres-data/postgresql.conf").write_text(
        "listen_addresses = '127.0.0.1'\nport = " + str(marker["ports"]["postgres"]) + "\n",
        encoding="utf-8",
    )
    run(
        [
            str(pgbin / "pg_ctl.exe"),
            "-D",
            str(root / "postgres-data"),
            "-l",
            str(root / "runtime/postgres-bootstrap.log"),
            "-w",
            "start",
        ],
        check=True,
        timeout=65,
        env=env,
    )
    try:
        stage("migrating-canonical-databases")
        run(
            [str(venv), "-B", "-m", "src.api.release_install", "--provision-database", str(root)],
            input=password,
            text=True,
            cwd=release,
            check=True,
            timeout=180,
            env=env,
        )
        control = run(
            [str(pgbin / "pg_controldata.exe"), "-D", str(root / "postgres-data")],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
            env=dict(env, LANG="C", LC_MESSAGES="C"),
        )
        identifiers = re.findall(
            r"^Database system identifier:\s*(\d+)\s*$", control.stdout, re.MULTILINE
        )
        if len(identifiers) != 1:
            raise LabViolation("Cannot prove dedicated PostgreSQL identity")
        write_json(
            root / "state/postgres-identity.json",
            dict(installation_id=marker["installation_id"], system_identifier=identifiers[0]),
        )
    finally:
        run(
            [
                str(pgbin / "pg_ctl.exe"),
                "-D",
                str(root / "postgres-data"),
                "-w",
                "-m",
                "fast",
                "stop",
            ],
            check=True,
            timeout=65,
            env=env,
        )
    stage("completed")
    write_json(
        root / "state/provisioning-result.json",
        dict(
            phase="completed",
            installation_id=marker["installation_id"],
            source_sha=marker["source_sha"],
        ),
    )


class ReleaseProvisionService(ReleaseService):
    def admit_start(self, root):
        return True  # Demand-only provisioning has no automatic recovery/startup.

    def service_preflight(self, root):
        marker = load_marker(root)
        authority = json.loads(plain(root / "state/provisioning-authority.json").read_text())
        if authority["marker"] != marker:
            raise LabViolation("Provisioning authority mismatch")
        for relative, receipt in authority["immutable"].items():
            verify_tree(root / relative, receipt)

    def ready_without_http(self, root, marker, instance):
        return True

    def accepts_child_exit(self, exit_code):
        return exit_code == 0

    @staticmethod
    def contract_builder(root):
        marker = load_marker(root)
        env = {
            k: v for k, v in os.environ.items() if not k.upper().startswith(("TESTER_", "PYTHON"))
        }
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", GUARDIANS_ENABLED="false")
        return dict(
            argv=[
                str(root / "dependencies/python/python.exe"),
                "-B",
                "-m",
                "src.api.release_provision",
                str(root),
            ],
            cwd=str(root / "releases" / marker["source_sha"]),
            env=env,
            sha=marker["source_sha"],
        )


def open_directory_guard(path):
    """LIST_DIRECTORY makes no-share-delete effective, including empty folders."""
    import win32con
    import win32file

    return win32file.CreateFile(
        str(plain(path)),
        0x20081,
        win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
        None,
        win32con.OPEN_EXISTING,
        win32file.FILE_FLAG_BACKUP_SEMANTICS | win32file.FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )


class ProvisioningTransaction:
    """Fsynced plans precede native creation with an atomic unique App SID owner."""

    def __init__(self, root, marker, release, maintenance):
        import win32api
        import win32con
        import win32file
        import win32security

        from .release_service import service_sid
        from .release_uninstall import _identity

        self.root = plain(root)
        self.marker = marker
        self.maintenance = plain(maintenance)
        self.path = self.maintenance / "provisioning.jsonl"
        self.actor_sid = service_sid(marker["services"]["app"])
        self.identities = {}
        self.external_leases = {}
        self.root_guard = None
        self.root_identity = None
        self.token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY
        )
        privilege = win32security.LookupPrivilegeValue(None, win32security.SE_RESTORE_NAME)
        self.previous_privileges = win32security.AdjustTokenPrivileges(
            self.token, False, [(privilege, win32con.SE_PRIVILEGE_ENABLED)]
        )
        if win32api.GetLastError() == 1300:
            self.token.Close()
            raise LabViolation(
                "Installer requires SeRestorePrivilege for atomic creator provenance"
            )
        self.journal = win32file.CreateFile(
            str(self.path),
            win32con.GENERIC_WRITE,
            win32con.FILE_SHARE_READ,
            self.attributes(directory=False, owner="S-1-5-32-544"),
            win32con.CREATE_NEW,
            win32con.FILE_ATTRIBUTE_NORMAL,
            None,
        )
        self.append(
            dict(
                schema="tester-provisioning/v1",
                root=str(root),
                installation_id=marker["installation_id"],
                marker=marker,
                actor_sid=self.actor_sid,
                release_envelope=json.loads(release.manifest),
                release_manifest_sha256=release.manifest_sha256,
                root_parent_identity=_identity(self.root.parent),
                allowed_actor_prefixes=[
                    "runtime",
                    "state",
                    "private",
                    "backups",
                    "postgres-data",
                    "dependencies/venv",
                ],
                allowed_actor_files=["config.json", "config.pending"],
                external_paths=[
                    str(Path(marker["desktop"]) / (name + ".lnk"))
                    for name in (
                        "TESTER \u2014 \u0417\u0430\u043f\u0443\u0441\u043a",
                        "TESTER \u2014 \u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435",
                    )
                ],
            )
        )

    def attributes(self, *, directory, owner=None):
        import pywintypes
        import win32security

        descriptor = win32security.SECURITY_DESCRIPTOR()
        descriptor.SetSecurityDescriptorOwner(
            win32security.ConvertStringSidToSid(owner or self.actor_sid), False
        )
        acl = win32security.ACL()
        for value in ("S-1-5-18", "S-1-5-32-544"):
            acl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION_DS,
                3 if directory else 0,
                0x1F01FF,
                win32security.ConvertStringSidToSid(value),
            )
        descriptor.SetSecurityDescriptorDacl(True, acl, False)
        descriptor.SetSecurityDescriptorControl(
            win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED
        )
        result = pywintypes.SECURITY_ATTRIBUTES()
        result.SECURITY_DESCRIPTOR = descriptor
        return result

    def append(self, value):
        import win32con
        import win32file

        handle = self.journal
        temporary = handle is None
        if temporary:
            handle = win32file.CreateFile(
                str(self.path),
                4,
                win32con.FILE_SHARE_READ,
                None,
                win32con.OPEN_EXISTING,
                win32con.FILE_ATTRIBUTE_NORMAL,
                None,
            )
        try:
            win32file.WriteFile(
                handle,
                (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
            )
            win32file.FlushFileBuffers(handle)
        finally:
            if temporary:
                handle.Close()

    def release_journal(self):
        if self.journal is not None:
            self.journal.Close()
            self.journal = None

    def prepare_external(self, path):
        import win32con
        import win32file

        from .release_uninstall import _identity

        path = plain(path)
        allowed = {
            str(Path(self.marker["desktop"]) / (name + ".lnk"))
            for name in (
                "TESTER \u2014 \u0417\u0430\u043f\u0443\u0441\u043a",
                "TESTER \u2014 \u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435",
            )
        }
        if str(path) not in allowed or path.exists():
            raise LabViolation("External shortcut collision")
        self.append(
            dict(event="planned_external", path=str(path), kind="file", purpose="desktop-shortcut")
        )
        handle = win32file.CreateFile(
            str(path),
            0x20081,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            self.attributes(directory=False),
            win32con.CREATE_NEW,
            win32con.FILE_ATTRIBUTE_NORMAL,
            None,
        )
        identity = _identity(path)
        self.external_leases[str(path)] = handle
        self.append(dict(event="external_created", path=str(path), identity=identity))
        return identity

    def finalize_external(self, path, identity):
        import hashlib

        import win32security

        from .release_uninstall import _identity

        if _identity(path) != identity:
            raise LabViolation("COM shortcut replaced its created object")
        self.append(
            dict(
                event="external_finalized",
                path=str(path),
                identity=identity,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
        self._admin_owner(path)
        acl = win32security.ACL()
        for sid, mask in (
            ("S-1-5-18", 0x1F01FF),
            ("S-1-5-32-544", 0x1F01FF),
            ("S-1-5-32-545", 0x120089),
        ):
            acl.AddAccessAllowedAce(
                win32security.ACL_REVISION, mask, win32security.ConvertStringSidToSid(sid)
            )
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            acl,
            None,
        )

    def relative(self, path):
        return plain(path).relative_to(self.root).as_posix()

    def root_created(self):
        import win32file

        from .release_uninstall import (
            _identity,
            _security_record,
            _validate_provisioning_actor_security,
            _WindowsObject,
        )

        if self.root.exists():
            raise LabViolation("Fresh provisioning requires an absent root")
        win32file.CreateDirectory(str(self.root), self.attributes(directory=True))
        self.root_guard = open_directory_guard(self.root)
        obj = _WindowsObject(self.root, lock=False)
        try:
            _validate_provisioning_actor_security(
                _security_record(obj), self.actor_sid, initial=True
            )
        finally:
            obj.close()
        self.root_identity = _identity(self.root)
        self.append(dict(event="root_created", identity=self.root_identity))
        self._admin_owner(self.root)

    def release_guards(self):
        self.release_journal()
        for handle in self.external_leases.values():
            handle.Close()
        self.external_leases.clear()
        if self.root_guard is not None:
            self.root_guard.Close()
            self.root_guard = None

    def mkdir(self, path):
        import win32file

        from .release_uninstall import _identity

        path = plain(path)
        if path.exists():
            if path != self.root and self.relative(path) not in self.identities:
                raise LabViolation("Unregistered provisioning directory")
            return
        if not path.parent.exists():
            self.mkdir(path.parent)
        relative = self.relative(path)
        self.append(dict(event="planned_dir", path=relative))
        win32file.CreateDirectory(str(path), self.attributes(directory=True))
        self.identities[relative] = _identity(path)
        self.append(
            dict(event="created", path=relative, identity=self.identities[relative], kind="dir")
        )
        self._admin_owner(path)

    def _admin_owner(self, path):
        import win32security

        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION,
            win32security.ConvertStringSidToSid("S-1-5-32-544"),
            None,
            None,
            None,
        )

    def write_bytes(self, path, data, *, signed_source=None):
        import hashlib
        import uuid

        import win32con
        import win32file

        from .release_uninstall import _identity

        path = plain(path)
        self.mkdir(path.parent)
        if path.exists():
            raise LabViolation("Never overwrite provisioning resources")
        temporary = path.parent / (".tester-copy_" + uuid.uuid4().hex + ".pending")
        final_name, temp_name = self.relative(path), self.relative(temporary)
        digest = hashlib.sha256(data).hexdigest()
        self.append(
            dict(
                event="planned_file",
                path=final_name,
                temp=temp_name,
                sha256=digest,
                signed_source=signed_source,
            )
        )
        handle = win32file.CreateFile(
            str(temporary),
            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            win32con.FILE_SHARE_READ,
            self.attributes(directory=False),
            win32con.CREATE_NEW,
            win32con.FILE_ATTRIBUTE_NORMAL,
            None,
        )
        try:
            identity = _identity(temporary)
            self.append(dict(event="created", path=temp_name, identity=identity, kind="file"))
            win32file.WriteFile(handle, data)
            win32file.FlushFileBuffers(handle)
        finally:
            handle.Close()
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
            raise LabViolation("Provisioning copy mismatch")
        os.rename(temporary, path)
        self.append(dict(event="renamed", path=final_name, temp=temp_name, identity=identity))
        self.identities[final_name] = identity
        self.append(dict(event="finalized", path=final_name, identity=identity, sha256=digest))
        self._admin_owner(path)

    def copyfile(self, source, destination):
        self.write_bytes(destination, plain(source).read_bytes())
        return destination

    def copytree(self, source, destination, *, ignore=None):
        source, destination = plain(source), plain(destination)
        self.mkdir(destination)
        excluded = set(ignore(str(source), [p.name for p in source.iterdir()])) if ignore else set()
        for item in sorted(source.iterdir()):
            if item.name in excluded:
                continue
            plain(item)
            if item.is_dir():
                self.copytree(item, destination / item.name, ignore=ignore)
            else:
                self.copyfile(item, destination / item.name)
        return destination

    def close(self):
        import win32security

        self.release_guards()
        win32security.AdjustTokenPrivileges(self.token, False, self.previous_privileges)
        self.token.Close()


def apply_actor_acls(root, marker):
    """The signed code is read-only; only the unique App account creates outputs."""
    import win32security

    from .release_service import service_sid

    sid = service_sid(marker["services"]["app"])

    def acl(path, rights, directory=True):
        entries = win32security.ACL()
        for value, mask in (("S-1-5-18", 0x1F01FF), ("S-1-5-32-544", 0x1F01FF), (sid, rights)):
            entries.AddAccessAllowedAceEx(
                win32security.ACL_REVISION_DS,
                3 if directory else 0,
                mask,
                win32security.ConvertStringSidToSid(value),
            )
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            entries,
            None,
        )

    acl(root, 0x1301BF)
    subprocess.run(
        [str(system_binary("icacls.exe")), str(root / "*"), "/reset", "/T", "/Q"],
        check=True,
        capture_output=True,
    )
    for relative in (
        "releases",
        "dependencies/python",
        "dependencies/postgres",
        "dependencies/service",
        "dependencies/wheelhouse",
    ):
        path = root / relative
        acl(path, 0x1200A9)
        if any(path.iterdir()):
            subprocess.run(
                [str(system_binary("icacls.exe")), str(path / "*"), "/reset", "/T", "/Q"],
                check=True,
                capture_output=True,
            )
    from .release_install import MARKER

    acl(root / MARKER, 0x120089, directory=False)
    acl(root / "state/provisioning-authority.json", 0x120089, directory=False)


def _registrations(marker, scm):
    import subprocess

    from .lab_scm import owned

    app, pg = plans(marker)
    actor = provisioning_plan(marker)
    state = scm.inspect(app["name"])
    if state is not None and state.get("python_class") == actor["python_class"]:
        app = actor
    image = subprocess.list2cmdline([app["host"]])
    owned(state, app, image)
    pg_state = scm.inspect(pg["name"])
    owned(pg_state, pg, pg["image"])
    return [(app, image), (pg, pg["image"])], [state, pg_state]


def validate_resources(marker):
    from .lab_scm import NativeSCM

    scm = NativeSCM()
    try:
        return _registrations(marker, scm)[1]
    finally:
        scm.close()


def stop_and_prove_exit(marker):
    from .lab_scm import NativeSCM
    from .release_service import NativeStopProof

    scm = NativeSCM(mutating=True)
    proof = None
    try:
        registrations, states = _registrations(marker, scm)
        is_actor = registrations[0][0]["python_class"] == provisioning_plan(marker)["python_class"]
        proof = NativeStopProof(marker, validator=validate_resources, pg_descendants=not is_actor)
        with scm.lease(registrations):
            for (plan, _), state in zip(registrations, states, strict=True):
                if state is not None:
                    scm.stop(plan["name"])
                    scm.wait(plan["name"], "stopped")
        proof.prove_exit()
    finally:
        if proof is not None:
            proof.close()
        scm.close()


def remove_resources(marker):
    from .lab_scm import NativeSCM

    scm = NativeSCM(mutating=True)
    try:
        registrations, states = _registrations(marker, scm)
        with scm.lease(registrations):
            for (plan, _), state in zip(registrations, states, strict=True):
                if state is not None:
                    if scm.inspect(plan["name"])["state"] != "stopped":
                        raise LabViolation("Provisioning service still active")
                    scm.remove(plan["name"])
    finally:
        scm.close()


def run_actor(root, marker, *, started=None):
    import time

    from .lab_scm import NativeSCM

    actor = provisioning_plan(marker)
    image = subprocess.list2cmdline([actor["host"]])
    scm = NativeSCM(mutating=True)
    try:
        with scm.lease([(actor, image)]):
            if scm.inspect(actor["name"]) is not None:
                raise LabViolation("Fresh provisioning account already registered")
            scm.install(actor, image, None)
            apply_actor_acls(root, marker)
            scm.start(actor["name"])
            if started is not None:
                started()
            deadline = time.monotonic() + 900
            last_phase = None
            while time.monotonic() < deadline:
                state = scm.inspect(actor["name"])
                try:
                    lines = (root / "state/install.jsonl").read_text().splitlines()
                    phase = json.loads(lines[-1])["phase"] if lines else None
                    if phase and phase != last_phase:
                        print("TESTER: " + phase.replace("-", " "), flush=True)
                        last_phase = phase
                except (OSError, ValueError, KeyError):
                    pass  # Progress is not an ownership/readiness authority.
                if state["state"] == "stopped":
                    break
                time.sleep(0.3)
            else:
                raise LabViolation("Provisioning actor deadline exceeded")
        result_path = plain(root / "state/provisioning-result.json")
        result = json.loads(result_path.read_text())
        if result != dict(
            phase="completed",
            installation_id=marker["installation_id"],
            source_sha=marker["source_sha"],
        ):
            raise LabViolation("Provisioning actor did not complete exact installation")
    finally:
        scm.close()


if __name__ == "__main__":
    import sys
    import traceback

    target = plain(sys.argv[1])
    try:
        provision(target)
    except BaseException:
        with (target / "runtime/provisioning.log").open("a", encoding="utf-8") as log:
            traceback.print_exc(file=log)
        raise


def recover_or_validate_existing(root, release, *, trusted_public_key_sha256, mode):
    """Return an authenticated completed marker, or remove a proven fresh attempt."""
    from .release_install import MARKER
    from .release_security import _unique_object
    from .release_uninstall import (
        _identity,
        _load_ledger,
        _preflight,
        _protected_provisioning_journal,
        _security_record,
        _validate_admin_security,
        _validate_provisioning_actor_security,
        _WindowsObject,
        rollback_provisioning,
    )

    marker = load_marker(root) if (root / MARKER).exists() else None
    parent = Path(os.environ["ProgramData"]) if mode == "production" else root.parent
    if marker is not None:
        candidates = [Path(marker["trust_config"]).parent / "provisioning.jsonl"]
    else:
        candidates = list(parent.glob("TesterReleaseMaintenance_*/provisioning.jsonl"))
    matches = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            with _protected_provisioning_journal(candidate) as journal:
                encoded = journal.read_bytes(max_bytes=128 * 1024 * 1024)
            rows = [
                json.loads(line, object_pairs_hook=_unique_object)
                for line in encoded.splitlines(keepends=True)
                if line.endswith(b"\n")
            ]
            if not rows:
                continue
            header = rows[0]
            if (
                header.get("schema") != "tester-provisioning/v1"
                or header.get("root") != str(root)
                or header.get("release_envelope") != json.loads(release.manifest)
                or header.get("release_manifest_sha256") != release.manifest_sha256
                or header.get("root_parent_identity") != _identity(root.parent)
            ):
                continue
            recorded = next(
                (row["identity"] for row in rows[1:] if row.get("event") == "root_created"), None
            )
            if recorded is not None and recorded != _identity(root):
                continue
            if recorded is None:
                handle = _WindowsObject(root)
                try:
                    _validate_provisioning_actor_security(
                        _security_record(handle), header["actor_sid"], initial=True
                    )
                finally:
                    handle.close()
            if marker is not None and header["marker"] != marker:
                continue
            matches.append((candidate, header, rows))
        except (OSError, ValueError, KeyError):
            if marker is not None:
                raise
            continue
    if len(matches) != 1:
        raise LabViolation("Existing root has no unique authenticated provisioning transaction")
    path, header, rows = matches[0]
    committed = any(
        row.get("event") == "runtime_exposed"
        or row.get("event") == "phase"
        and row.get("phase") in ("ready", "completed", "committed")
        for row in rows[1:]
    )
    if not committed:
        rollback_provisioning(
            root,
            journal_path=path,
            trusted_public_key_sha256=trusted_public_key_sha256,
            expected_source_sha=release.source_sha,
        )
        if root.exists():
            raise LabViolation("Authenticated interrupted root remains; fresh install refused")
        return None
    if marker is None:
        raise LabViolation("Completed installation lost its immutable marker")
    config_path = plain(marker["trust_config"])
    config_handle = _WindowsObject(config_path)
    parent_handle = _WindowsObject(config_path.parent)
    try:
        _validate_admin_security(_security_record(config_handle))
        _validate_admin_security(_security_record(parent_handle))
        config = json.loads(config_handle.read_bytes(), object_pairs_hook=_unique_object)
    finally:
        config_handle.close()
        parent_handle.close()
    if (
        config.get("schema") != "tester-maintenance-trust/v1"
        or config.get("installation_id") != marker["installation_id"]
        or config.get("root") != str(root)
        or config.get("source_sha") != release.source_sha
        or config.get("manifest_sha256") != release.manifest_sha256
        or config.get("trusted_public_key_sha256") != trusted_public_key_sha256
    ):
        raise LabViolation("Completed installation trust differs from approved release")
    ledger = _load_ledger(
        root,
        Path(config["ledger"]),
        config["trusted_ledger_sha256"],
        trusted_public_key_sha256,
        release.source_sha,
    )
    if ledger["marker"] != marker:
        raise LabViolation("Completed ledger marker mismatch")
    _preflight(root, ledger)
    return marker
