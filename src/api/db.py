"""Platform-owned, transactionally applied PostgreSQL migrations."""

import hashlib
from pathlib import Path

import psycopg


def connect(dsn):
    return psycopg.connect(dsn, connect_timeout=3, options="-c statement_timeout=10000")


def migrate(dsn: str):
    with connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(725281346)")
        conn.execute("CREATE SCHEMA IF NOT EXISTS tester_platform")
        conn.execute("""CREATE TABLE IF NOT EXISTS tester_platform.migrations (
            version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz DEFAULT now())""")
        for path in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
            content = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(content.encode()).hexdigest()
            previous = conn.execute(
                "SELECT checksum FROM tester_platform.migrations WHERE version=%s", (path.name,)
            ).fetchone()
            if previous:
                if previous[0] != digest:
                    raise RuntimeError("Applied migration checksum mismatch: " + path.name)
                continue
            conn.execute(content)
            conn.execute(
                "INSERT INTO tester_platform.migrations(version,checksum) VALUES(%s,%s)",
                (path.name, digest),
            )


if __name__ == "__main__":
    from .config import load_settings

    settings = load_settings()
    if not settings.dsn:
        raise SystemExit("TESTER_DATABASE_URL is required")
    migrate(settings.dsn)
