"""Create/stop a NEW non-service research PostgreSQL cluster, never an installed lab.

Run only from a separately verified published release. Passwords stay in the
owner-only workspace; output contains no DSN or capabilities. No recursive delete,
SCM, elevation, application worker termination or existing database access.
"""

import argparse
import csv
import io
import json
import os
import re
import secrets
import socket
import subprocess
from pathlib import Path

from src.api.lab_install import plain


class LabScopeError(ValueError):
    pass


def check_layout(workspace, root, *, new=False):
    workspace, root = plain(workspace), plain(root)
    installed = plain(Path("C:/TesterLab"))
    if (
        workspace == installed
        or installed in workspace.parents
        or not workspace.is_dir()
        or workspace == Path(workspace.anchor)
        or root.parent != workspace
        or not re.fullmatch("research-[0-9a-f]{32}", root.name)
    ):
        raise LabScopeError("New direct-child research workspace required")
    if new and root.exists():
        raise LabScopeError("Research root already exists; adoption forbidden")
    return root


def load_lab(root, source_sha):
    root = plain(root)
    try:
        value = json.loads(plain(root / ".research-lab.json").read_text(encoding="utf-8"))
        check_layout(value["workspace"], root)
        valid = (
            re.fullmatch("[0-9a-f]{40}", source_sha)
            and value["schema"] == "tester-research-lab/1"
            and value["root"] == str(root)
            and value["installation_id"] == root.name[9:]
            and value["database"] == "tester_research_" + root.name[9:]
            and value["source_sha"] == source_sha
            and value["postgres_port"] == 55471
            and value["api_port"] == 18767
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LabScopeError("Owned research marker required") from exc
    if not valid:
        raise LabScopeError("Research ownership or fixed isolated endpoints differ")
    return value


def _save(path, value, *, new=False):
    with plain(path).open("x" if new else "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def _run(args, *, timeout=90):
    result = subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    )
    if result.returncode:
        # PostgreSQL diagnostics may contain config/DSN information. Keep output
        # private rather than forwarding it into the user/GitHub test logs.
        raise LabScopeError("Owned PostgreSQL command failed: " + Path(args[0]).name)


def _private_directory(root):
    if os.name == "nt":
        result = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        sid = next(csv.reader(io.StringIO(result.stdout)))[1]
        if not re.fullmatch("S-1-[0-9-]+", sid):
            raise LabScopeError("Current Windows SID unavailable")
        _run(
            [
                "icacls.exe",
                str(root),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
            ],
            timeout=10,
        )
    else:
        root.chmod(0o700)


def _binary_dir(path):
    path = plain(path)
    for name in ("initdb", "pg_ctl", "postgres"):
        if not plain(path / (name + (".exe" if os.name == "nt" else ""))).is_file():
            raise LabScopeError("Explicit PostgreSQL binaries required")
    return path


def _exe(path, name):
    return str(path / (name + (".exe" if os.name == "nt" else "")))


def prepare(workspace, root, pg_bin, source_sha):
    if not re.fullmatch("[0-9a-f]{40}", source_sha):
        raise LabScopeError("Exact published SHA required")
    root = check_layout(workspace, root, new=True)
    pg_bin = _binary_dir(pg_bin)
    # Never start a conflicting process, nor fall back to a different endpoint.
    for port in (55471, 18767):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    root.mkdir()
    _private_directory(root)
    value = dict(
        schema="tester-research-lab/1",
        root=str(root),
        workspace=str(plain(workspace)),
        installation_id=root.name[9:],
        source_sha=source_sha,
        postgres_port=55471,
        api_port=18767,
        database="tester_research_" + root.name[9:],
        pg_bin=str(pg_bin),
    )
    _save(root / ".research-lab.json", value, new=True)
    password = secrets.token_urlsafe(32)
    password_file = plain(root / "initdb-password.txt")
    password_file.write_text(password, encoding="utf-8")
    _run(
        [
            _exe(pg_bin, "initdb"),
            "-D",
            str(root / "pgdata"),
            "-U",
            "research_owner",
            "--auth-host=scram-sha-256",
            "--auth-local=scram-sha-256",
            "--encoding=UTF8",
            "--locale=C",
            "--pwfile=" + str(password_file),
        ]
    )
    settings = (
        "listen_addresses='127.0.0.1'\nport=55471\nmax_connections=24\n"
        "shared_buffers='32MB'\nwork_mem='4MB'\nmax_parallel_workers=0\n"
        "max_wal_size='256MB'\nmin_wal_size='80MB'\ntimezone='UTC'\n"
        "log_statement='none'\nlog_min_error_statement='panic'\n"
    )
    plain(root / "pgdata/postgresql.auto.conf").write_text(settings, encoding="utf-8")
    from psycopg.conninfo import make_conninfo

    _save(
        root / "private.json",
        dict(
            database_url=make_conninfo(
                host="127.0.0.1",
                port=55471,
                user="research_owner",
                password=password,
                dbname=value["database"],
            )
        ),
        new=True,
    )
    _run(
        [
            _exe(pg_bin, "pg_ctl"),
            "-D",
            str(root / "pgdata"),
            "-l",
            str(root / "postgres.log"),
            "-w",
            "start",
        ]
    )
    return confirm_started(root, source_sha)


def confirm_started(root, source_sha):
    """Finish only this fresh owned bootstrap after an ambiguous launcher wait.

    No server is started/stopped here. A legacy root/marker is never adopted;
    SCRAM credentials, actual endpoint, owned path and live process must agree.
    """
    import psutil
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict

    root = plain(root)
    value = load_lab(root, source_sha)
    pg_bin = _binary_dir(value["pg_bin"])
    private = json.loads(plain(root / "private.json").read_text(encoding="utf-8"))
    auth = conninfo_to_dict(private["database_url"])
    if (
        set(auth) != {"host", "port", "user", "password", "dbname"}
        or auth["host"] != "127.0.0.1"
        or auth["port"] != "55471"
        or auth["user"] != "research_owner"
        or auth["dbname"] != value["database"]
    ):
        raise LabScopeError("Fresh research credentials differ")
    lines = plain(root / "pgdata/postmaster.pid").read_text().splitlines()
    pid = int(lines[0])
    process = psutil.Process(pid)
    argv = process.cmdline()
    if (
        plain(lines[1]) != root / "pgdata"
        or int(lines[3]) != 55471
        or plain(process.exe()) != plain(_exe(pg_bin, "postgres"))
        or abs(process.create_time() - int(lines[2])) > 2
        or "-D" not in argv
        or plain(argv[argv.index("-D") + 1]) != root / "pgdata"
    ):
        raise LabScopeError("Fresh postmaster identity differs")
    previously_bound = "postgres_pid" in value
    if previously_bound and (
        pid != value["postgres_pid"] or process.create_time() != value["postgres_created"]
    ):
        raise LabScopeError("Previously bound research process changed")
    bootstrap = dict(auth, dbname="postgres")
    with psycopg.connect(
        **bootstrap, hostaddr="127.0.0.1", autocommit=True, connect_timeout=3
    ) as conn:
        actual = conn.execute("SELECT current_setting('data_directory'),inet_server_port()")
        directory, port = actual.fetchone()
        if plain(directory) != root / "pgdata" or port != 55471:
            raise LabScopeError("Authenticated fresh cluster endpoint differs")
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (value["database"],)
        ).fetchone()
        if exists and not previously_bound:
            raise LabScopeError("Existing unclaimed research database; adoption refused")
        value.update(postgres_pid=pid, postgres_created=process.create_time())
        _save(root / ".research-lab.json", value)
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(value["database"])))
    return dict(
        status="OWNED_RESEARCH_POSTGRES_CONFIRMED",
        root=str(root),
        postgres_pid=pid,
        postgres_port=55471,
        database=value["database"],
        source_sha=source_sha,
    )


def stop(root, source_sha):
    root = plain(root)
    value = load_lab(root, source_sha)
    pg_bin = _binary_dir(value["pg_bin"])
    import psutil

    pid_file = plain(root / "pgdata/postmaster.pid")
    pid = int(pid_file.read_text().splitlines()[0])
    process = psutil.Process(pid)
    if (
        pid != value.get("postgres_pid")
        or process.create_time() != value.get("postgres_created")
        or plain(process.exe()) != plain(_exe(pg_bin, "postgres"))
    ):
        raise LabScopeError("Owned PostgreSQL PID/image/creation changed; stop refused")
    args = process.cmdline()
    if "-D" not in args or plain(args[args.index("-D") + 1]) != root / "pgdata":
        raise LabScopeError("Owned PostgreSQL data-directory command differs")
    # pg_ctl targets only this separately created immutable data-directory, after
    # checking its live PID and creation. No service manager or process kill.
    if int(pid_file.read_text().splitlines()[0]) != pid:
        raise LabScopeError("Postmaster PID changed")
    _run([_exe(pg_bin, "pg_ctl"), "-D", str(root / "pgdata"), "-m", "fast", "-w", "stop"])
    return dict(status="OWNED_RESEARCH_POSTGRES_STOPPED", root=str(root), postgres_pid=pid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "confirm", "stop"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--pg-bin", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            if args.workspace is None or args.pg_bin is None:
                parser.error("prepare requires explicit workspace and pg-bin")
            result = prepare(args.workspace, args.root, args.pg_bin, args.source_sha)
        elif args.action == "confirm":
            result = confirm_started(args.root, args.source_sha)
        else:
            result = stop(args.root, args.source_sha)
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps(dict(status="REFUSED_OR_FAILED", error_type=type(exc).__name__)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
