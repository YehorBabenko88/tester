"""Create only a NEW dedicated database and role. Never adopt or mutate an existing one."""

import argparse
import getpass
import json
import os
import re
import secrets
import shutil
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from .config import Settings
from .db import migrate


def provision(admin_dsn: str, database: str, role: str) -> str:
    for name in (database, role):
        if not re.fullmatch(r"tester_[a-z0-9_]{1,40}", name):
            raise ValueError(
                "Database and role must start with tester_ and contain lowercase letters"
            )
    password = secrets.token_urlsafe(32)
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
        if int(conn.execute("SHOW server_version_num").fetchone()[0]) < 140000:
            raise ValueError("PostgreSQL 14+ required")
        conn.execute("SELECT pg_advisory_lock(725281347)")
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone():
            raise ValueError("Database already exists; use the documented update path")
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            raise ValueError("Role already exists; refusing to change it")
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        try:
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
            conn.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
            )
        except Exception:
            # A failed CREATE DATABASE leaves only the new role; never drop existing databases.
            if not conn.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (database,)
            ).fetchone():
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            raise
    params = conninfo_to_dict(admin_dsn)
    params.update(dbname=database, user=role, password=password)
    return make_conninfo(**params)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--database", default="tester_local")
    parser.add_argument("--role", default="tester_local")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--min-free-bytes", type=int, default=1024**3)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    # Validate options BEFORE any role, database, directory or secret is created.
    Settings(data_dir=root, port=args.port, min_free_bytes=args.min_free_bytes)
    if (root / "config.json").exists():
        raise SystemExit("Configuration already exists; use update instructions")
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < args.min_free_bytes:
        raise SystemExit("Insufficient free space")
    # Installer protects this directory with an ACL before entering this command.
    if os.name != "nt":
        root.chmod(0o700)
    admin = os.environ.pop("TESTER_ADMIN_DSN", None)
    if not admin:
        admin = getpass.getpass("PostgreSQL admin DSN (hidden): ")
    dsn = provision(admin, args.database, args.role)
    config = {
        "database_url": dsn,
        "data_dir": str(root),
        "port": args.port,
        "min_free_bytes": args.min_free_bytes,
    }
    Settings(**config)
    # Save before migration so a partial installation can be diagnosed/recovered.
    path = root / "config.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(config, stream, indent=2)
    if os.name != "nt":
        path.chmod(0o600)
    for directory in ("logs", "tmp", "downloads", "backups"):
        (root / directory).mkdir(exist_ok=True)
    migrate(dsn)
    print("Dedicated database provisioned and platform migrations applied")


if __name__ == "__main__":
    try:
        main()
    except (psycopg.Error, ValueError) as exc:
        # Suppress DSN/password-bearing driver messages on installer failure.
        raise SystemExit(
            "Setup failed: " + type(exc).__name__ + "; see recovery documentation"
        ) from None
