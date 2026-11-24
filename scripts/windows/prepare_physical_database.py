"""Issue 27: plan by default; explicit preparation of one owned PG18 cluster.

No service/ACL/global PostgreSQL changes, deletion, or adoption of partial state.
Run with the installed tester Python/module path; never prints DSNs/passwords.
"""

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path

from src.api.physical_scope import ROOT, ScopeViolation, plain_path, validate_owner

DATA = ROOT / "postgres-data"
PRIVATE = ROOT / "private"
STATE = ROOT / "state/database-preparation.json"
SECRETS = PRIVATE / "admin-secrets.json"
PGBIN = Path("C:/Program Files/PostgreSQL/18/bin")
PORT = 55469
ADMIN = "tester_install_admin"
DATABASE = "tester_physical_20261107"
APP = "tester_physical_app"
CONFIGURATION = """listen_addresses='127.0.0.1'
port=55469
max_connections=12
shared_buffers='32MB'
work_mem='4MB'
maintenance_work_mem='16MB'
max_worker_processes=2
max_parallel_workers=0
max_parallel_workers_per_gather=0
temp_file_limit='64MB'
min_wal_size='80MB'
max_wal_size='256MB'
timezone='UTC'
password_encryption='scram-sha-256'
fsync=on
full_page_writes=on
synchronous_commit=on
log_statement='none'
log_min_error_statement='panic'
logging_collector=off
"""
HBA = "host all all 127.0.0.1/32 scram-sha-256\n"


def configuration_documents():
    return {
        name: text.replace("\n", os.linesep).encode("ascii")
        for name, text in (("postgresql.conf", CONFIGURATION), ("pg_hba.conf", HBA))
    }


def validate_initialized_documents(documents):
    if documents != configuration_documents():
        raise ScopeViolation("Initialized cluster configuration differs from exact approved bytes")
    return True


def identity(owner):
    return dict(
        schema_version=1,
        installation_id=owner["installation_id"],
        root=str(ROOT),
        data_directory=str(DATA),
        port=PORT,
        admin=ADMIN,
        database=DATABASE,
        app=APP,
        pg_bin=str(PGBIN),
    )


def validate_state(value, owner, *, allow_initialized=False):
    initialized = bool(
        isinstance(value, dict)
        and value.get("phase") == "preparing"
        and value.get("stage") in ("start_owned_cluster", "verify_cluster_identity")
    )
    if (
        not isinstance(value, dict)
        or any(value.get(k) != v for k, v in identity(owner).items())
        or (
            not (allow_initialized and initialized)
            and (
                value.get("phase") != "ready"
                or not str(value.get("system_identifier", "")).isdigit()
            )
        )
    ):
        raise ScopeViolation(
            "Partial/foreign cluster preparation requires manual recovery; files retained"
        )
    return value


def validate_secrets(value, owner):
    if not isinstance(value, dict) or any(value.get(k) != v for k, v in identity(owner).items()):
        raise ScopeViolation("Secrets are not bound to this installation")
    for field in ("admin_password", "app_password"):
        password = value.get(field)
        if (
            not isinstance(password, str)
            or len(password) < 64
            or not all(c.isalnum() or c in "-_" for c in password)
        ):
            raise ScopeViolation("Invalid installation secret")
    if value["admin_password"] == value["app_password"]:
        raise ScopeViolation("Distinct secrets required")
    return value


def same_directory(expected, actual):
    return os.path.normcase(str(plain_path(expected))) == os.path.normcase(str(plain_path(actual)))


def read_json(path):
    path = plain_path(path)
    if not path.is_file() or path.stat().st_size > 32768:
        raise ScopeViolation("Owned state missing/oversized")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value, *, new=False):
    path = plain_path(path)
    temporary = (
        path if new else plain_path(path.with_name(path.name + "." + uuid.uuid4().hex + ".pending"))
    )
    with temporary.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    if not new:
        os.replace(temporary, path)


def command(arguments, *, allowed=(0,), private_env=None):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}
    env.update(private_env or {})
    try:
        result = subprocess.run(
            [str(a) for a in arguments],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=180,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        raise ScopeViolation(
            "Owned PostgreSQL command could not finish; retained files require inspection"
        ) from None
    if result.returncode not in allowed:
        raise ScopeViolation("Owned PostgreSQL command failed; output suppressed")
    return result


@contextmanager
def installation_lock():
    import msvcrt

    path = plain_path(ROOT / "state/database-preparation.lock")
    with path.open("a+b") as lock:
        if lock.seek(0, 2) == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise ScopeViolation("Another database preparation owns the installation") from None
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def preflight(*, resume_initialized=False):
    owner = validate_owner(ROOT)
    for path in (ROOT, DATA, PRIVATE, ROOT / "state", ROOT / "runtime", ROOT / "backups"):
        if not plain_path(path).is_dir():
            raise ScopeViolation("Prepare owned physical directories first")
    for name in ("initdb.exe", "pg_ctl.exe", "postgres.exe", "pg_dump.exe"):
        if not plain_path(PGBIN / name).is_file():
            raise ScopeViolation("Existing PostgreSQL 18 binaries required")
    state = (
        validate_state(read_json(STATE), owner, allow_initialized=resume_initialized)
        if plain_path(STATE).exists()
        else None
    )
    if resume_initialized and (not state or state["phase"] == "ready"):
        raise ScopeViolation("Initialized resume requires the exact interrupted pre-DDL phase")
    if state is None and (
        any(DATA.iterdir())
        or plain_path(SECRETS).exists()
        or plain_path(PRIVATE / "initdb-password.txt").exists()
    ):
        raise ScopeViolation("Existing unproven cluster/secrets must not be adopted")
    if state:
        validate_secrets(read_json(SECRETS), owner)
        if plain_path(DATA / "PG_VERSION").read_text().strip() != "18":
            raise ScopeViolation("Cluster version differs")
        if state["phase"] != "ready":
            validate_initialized_documents(
                {name: plain_path(DATA / name).read_bytes() for name in configuration_documents()}
            )
            return owner, state
        for filename, expected in state.get("configuration_hashes", {}).items():
            if filename not in ("postgresql.conf", "pg_hba.conf"):
                raise ScopeViolation("Unexpected cluster configuration")
            if hashlib.sha256(plain_path(DATA / filename).read_bytes()).hexdigest() != expected:
                raise ScopeViolation(
                    "Owned PostgreSQL configuration changed; manual review required"
                )
        if set(state.get("configuration_hashes", {})) != {"postgresql.conf", "pg_hba.conf"}:
            raise ScopeViolation("Configuration ownership proof missing")
    return owner, state


def stage(owner, name, **proof):
    write_json(STATE, identity(owner) | dict(phase="preparing", stage=name) | proof)


def permission_probe(connection):
    """Denied statements run in savepoints; DELETE's false predicate cannot remove rows."""
    import psycopg

    connection.execute("SELECT count(*) FROM tester_storage.raw_records")
    connection.execute("SELECT count(*) FROM tester_platform.jobs")
    for statement in (
        "CREATE TABLE tester_storage.physical_privilege_probe(id integer)",
        "CREATE TABLE tester_platform.physical_privilege_probe(id integer)",
        "CREATE TABLE public.physical_privilege_probe(id integer)",
        "CREATE SCHEMA physical_privilege_probe",
        "DELETE FROM tester_storage.raw_records WHERE false",
        "DELETE FROM tester_platform.jobs WHERE false",
    ):
        try:
            with connection.transaction():
                connection.execute(statement)
                raise ScopeViolation("Application role unexpectedly permits DDL/DELETE")
        except psycopg.errors.InsufficientPrivilege:
            pass


def provision(owner, state, credentials, *, expected_system_identifier=None):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    from src.api.db import migrate as platform_migrate
    from src.storage.migrations import migrate as storage_migrate

    admin = make_conninfo(
        host="127.0.0.1",
        port=PORT,
        user=ADMIN,
        password=credentials["admin_password"],
        dbname="postgres",
        connect_timeout=5,
    )
    marker = "tester-physical:" + owner["installation_id"]
    stage(owner, "verify_cluster_identity")
    with psycopg.connect(admin, autocommit=True) as c:
        if not same_directory(DATA, c.execute("SHOW data_directory").fetchone()[0]):
            raise ScopeViolation("Connected server is outside owned cluster")
        if not 180000 <= int(c.execute("SHOW server_version_num").fetchone()[0]) < 190000:
            raise ScopeViolation("PostgreSQL 18 required")
        system = str(c.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
        if expected_system_identifier is not None and expected_system_identifier != system:
            raise ScopeViolation("Initialized cluster system identity changed")
        if state and state["system_identifier"] != system:
            raise ScopeViolation("Cluster system identity changed")
        stage(owner, "verify_cluster_identity", system_identifier=system)
        role = c.execute(
            "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname=%s",
            (APP,),
        ).fetchone()
        database = c.execute(
            "SELECT pg_get_userbyid(datdba),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s",
            (DATABASE,),
        ).fetchone()
        if state:
            if not role or any(role[:5]) or role[5] != marker or database != (ADMIN, marker):
                raise ScopeViolation(
                    "Existing application objects are not owned/minimally privileged"
                )
            if c.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s))",
                (APP,),
            ).fetchone()[0]:
                raise ScopeViolation("Unexpected application role membership")
        elif role or database:
            raise ScopeViolation("Refusing existing application objects")
        if not state:
            stage(owner, "create_owned_application_objects")
            # Statement/error logging disabled before secret-bearing role DDL.
            c.execute("SET log_statement='none'")
            c.execute("SET log_min_error_statement='panic'")
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(APP), sql.Literal(credentials["app_password"]))
            )
            c.execute(
                sql.SQL("COMMENT ON ROLE {} IS {}").format(sql.Identifier(APP), sql.Literal(marker))
            )
            c.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(DATABASE), sql.Identifier(ADMIN)
                )
            )
            c.execute(
                sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                    sql.Identifier(DATABASE), sql.Literal(marker)
                )
            )
        c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(DATABASE)))
        c.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(DATABASE), sql.Identifier(APP)
            )
        )
    target = make_conninfo(admin, dbname=DATABASE)
    stage(owner, "retain_database_backup")
    backup = plain_path(ROOT / "backups" / ("before-migration-" + uuid.uuid4().hex + ".dump"))
    # Reserve this new path exclusively. pg_dump overwrites only our own empty reservation.
    with backup.open("xb"):
        pass
    command(
        [
            PGBIN / "pg_dump.exe",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--host=127.0.0.1",
            "--port=" + str(PORT),
            "--username=" + ADMIN,
            "--dbname=" + DATABASE,
            "--file=" + str(backup),
        ],
        private_env={"PGPASSWORD": credentials["admin_password"]},
    )
    if backup.stat().st_size == 0:
        raise ScopeViolation("Database backup is empty; migrations refused")
    with backup.open("rb") as stream:
        backup_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    write_json(
        backup.with_suffix(".json"),
        identity(owner)
        | dict(backup_file=str(backup), sha256=backup_hash, system_identifier=system),
        new=True,
    )
    stage(owner, "apply_storage_and_platform_migrations")
    with psycopg.connect(target) as c:
        if not same_directory(DATA, c.execute("SHOW data_directory").fetchone()[0]):
            raise ScopeViolation("Migration cluster mismatch")
        storage_migrate(c)
    platform_migrate(target)
    with psycopg.connect(target) as c:
        c.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        for schema in ("tester_storage", "tester_platform"):
            c.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(APP)
                )
            )
            c.execute(
                sql.SQL("GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(APP)
                )
            )
            c.execute(
                sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(APP)
                )
            )
        for table in ("tester_storage.schema_migrations", "tester_platform.migrations"):
            c.execute(
                sql.SQL("REVOKE INSERT ON {} FROM {}").format(
                    sql.Identifier(*table.split(".")), sql.Identifier(APP)
                )
            )
        c.execute(
            sql.SQL(
                "GRANT UPDATE ON tester_storage.download_jobs,tester_storage.ingestion_checkpoints TO {}"
            ).format(sql.Identifier(APP))
        )
        c.execute(
            sql.SQL("GRANT UPDATE ON ALL TABLES IN SCHEMA tester_platform TO {}").format(
                sql.Identifier(APP)
            )
        )
        c.execute(
            sql.SQL("REVOKE UPDATE ON tester_platform.migrations FROM {}").format(
                sql.Identifier(APP)
            )
        )
        c.execute(
            sql.SQL(
                "REVOKE DELETE,TRUNCATE,REFERENCES,TRIGGER ON ALL TABLES IN SCHEMA tester_storage,tester_platform FROM {}"
            ).format(sql.Identifier(APP))
        )
    stage(owner, "probe_application_permissions")
    application = make_conninfo(target, user=APP, password=credentials["app_password"])
    with psycopg.connect(application) as c:
        permission_probe(c)
    return system


def prepare(*, resume_initialized=False):
    if os.name != "nt":
        raise ScopeViolation("Explicit physical preparation is Windows-only")
    preflight(
        resume_initialized=resume_initialized
    )  # prove owner before even creating the preparation lock file
    with installation_lock():
        owner, state = preflight(resume_initialized=resume_initialized)
        # Reject any listener and any already-running own cluster; never stop a preexisting process.
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", PORT))
            except OSError:
                raise ScopeViolation("Physical test port is already in use") from None
        if (
            state
            and command([PGBIN / "pg_ctl.exe", "-D", DATA, "status"], allowed=(0, 3)).returncode
            == 0
        ):
            raise ScopeViolation(
                "Owned PostgreSQL is already running; preparation will not stop it"
            )
        if not state:
            write_json(
                STATE,
                identity(owner) | dict(phase="initializing", stage="create_secrets"),
                new=True,
            )
            credentials = identity(owner) | dict(
                admin_password=secrets.token_urlsafe(64), app_password=secrets.token_urlsafe(64)
            )
            write_json(SECRETS, credentials, new=True)
            password_file = plain_path(PRIVATE / "initdb-password.txt")
            with password_file.open("x", encoding="ascii") as output:
                output.write(credentials["admin_password"] + "\n")
                output.flush()
                os.fsync(output.fileno())
            stage(owner, "initialize_new_cluster")
            command(
                [
                    PGBIN / "initdb.exe",
                    "-D",
                    DATA,
                    "-U",
                    ADMIN,
                    "--auth=scram-sha-256",
                    "--pwfile=" + str(password_file),
                    "--data-checksums",
                    "--no-clean",
                    "--encoding=UTF8",
                    "--locale=C",
                ]
            )
            for name, document in configuration_documents().items():
                plain_path(DATA / name).write_bytes(document)
        else:
            credentials = validate_secrets(read_json(SECRETS), owner)
        started = False
        try:
            stage(owner, "start_owned_cluster")
            started = True  # even a timed-out start may have created our own process
            command(
                [
                    PGBIN / "pg_ctl.exe",
                    "-D",
                    DATA,
                    "-l",
                    ROOT / "runtime/database-preparation.log",
                    "-w",
                    "start",
                ]
            )
            # Initialized recovery is permitted only before any application object
            # creation. provision(None) explicitly refuses an existing APP or DB.
            system = provision(
                owner,
                state if state and state["phase"] == "ready" else None,
                credentials,
                expected_system_identifier=state.get("system_identifier") if state else None,
            )
        finally:
            if started:
                command([PGBIN / "pg_ctl.exe", "-D", DATA, "-m", "fast", "-w", "stop"])
        hashes = {
            name: hashlib.sha256(plain_path(DATA / name).read_bytes()).hexdigest()
            for name in ("postgresql.conf", "pg_hba.conf")
        }
        write_json(
            STATE,
            identity(owner)
            | dict(phase="ready", system_identifier=system, configuration_hashes=hashes),
        )


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument(
        "--prepare",
        action="store_true",
        help="Explicitly prepare only the fixed owned physical cluster",
    )
    value.add_argument(
        "--resume-initialized",
        action="store_true",
        help="Explicitly recover only a stopped, owned, exact-config pre-DDL cluster",
    )
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.resume_initialized and not args.prepare:
            raise ScopeViolation("Initialized resume also requires explicit --prepare")
        if args.prepare:
            prepare(resume_initialized=args.resume_initialized)
            print("Owned physical database prepared; server stopped; private secrets retained")
        else:
            owner, state = preflight()
            print(
                json.dumps(
                    dict(
                        mode="READ_ONLY_PLAN",
                        root=str(ROOT),
                        port=PORT,
                        database=DATABASE,
                        app=APP,
                        phase="ready" if state else "new",
                        prepare_required=True,
                    )
                )
            )
        return 0
    except Exception as error:
        print(
            "Physical database preparation refused/failed: "
            + type(error).__name__
            + "; no credentials printed; retained files require inspection"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
