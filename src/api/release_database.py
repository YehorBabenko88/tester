"""Fresh release DB composition using the existing migrations and adapters."""

import secrets
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from src.storage.migrations import migrate as migrate_storage

from .catalog_store import migrate_catalog
from .db import migrate as migrate_platform
from .history_lab_bootstrap import migrate_release_projections
from .research_ledger import migrate_research, validate_research_dsn

DATABASES = {
    "tester": ("tester_app", ("tester_platform", "tester_storage", "tester_lab")),
    "tester_catalog": ("tester_catalog_app", ("tester_catalog",)),
    "tester_research_release": ("tester_research_app", ("tester_research",)),
}


def database_identity(marker):
    ports = marker.get("ports", {})
    port = ports.get("postgres")
    api = ports.get("api")
    if (
        type(port) is not int
        or not 1024 <= port <= 65535
        or port == 5432
        or type(api) is not int
        or not 1024 <= api <= 65535
        or api in {5432, port}
    ):
        raise ValueError("Release requires separate explicit non-5432 loopback ports")
    return dict(host="127.0.0.1", hostaddr="127.0.0.1", port=port, require_auth="scram-sha-256")


def runtime_configuration(root, marker, app_dsn, catalog_dsn, *, research_dsn=None):
    database_identity(marker)
    root = Path(root)
    config = dict(
        database_url=app_dsn,
        data_dir=str(root / "runtime"),
        host="127.0.0.1",
        port=marker["ports"]["api"],
        frontend_dir=str(root / "releases" / marker["source_sha"] / "frontend"),
        provider_factory="src.integration.reference_prices:create_reader",
        handler_factory="src.integration.reference_prices:create_handler",
        market_catalog_enabled=True,
        market_catalog_database_url=catalog_dsn,
        public_transport_policy_enabled=True,
        public_transport_installation_id=marker["installation_id"],
    )
    if research_dsn is not None:
        endpoint = validate_research_dsn(research_dsn)
        if (
            endpoint.get("host") != "127.0.0.1"
            or endpoint.get("port") != str(marker["ports"]["postgres"])
            or endpoint.get("dbname") != "tester_research_release"
            or endpoint.get("user") != "tester_research_app"
        ):
            raise ValueError("Release projection database scope mismatch")
        config.update(research_projection_enabled=True, research_database_url=research_dsn)
    return config


def provision_release_databases(marker, owner_password):
    """Called only inside the fresh, authenticated installer-owned PG cluster.

    CREATE DATABASE/ROLE intentionally refuse existing names. Interrupted cluster
    recovery belongs to the installer's creation journal, never blind SQL adoption.
    Research schema is prepared but credentials remain operator-only; no synthetic
    result, research credential or automatic Guardian action is published here.
    """
    endpoint = database_identity(marker)
    owner = dict(**endpoint, user="tester_owner", password=owner_password)
    passwords = {name: secrets.token_urlsafe(36) for name in DATABASES}
    owner_dsns = {name: make_conninfo(**owner, dbname=name) for name in DATABASES}
    with psycopg.connect(make_conninfo(**owner, dbname="postgres"), autocommit=True) as conn:
        for name, (role, _) in DATABASES.items():
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                    "NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(passwords[name]))
            )
    migrate_platform(owner_dsns["tester"])
    with psycopg.connect(owner_dsns["tester"]) as conn:
        migrate_storage(conn)
        migrate_release_projections(conn)
        conn.execute(
            "INSERT INTO tester_platform.restart_budgets(installation_id,source_sha) VALUES(%s,%s)",
            (marker["installation_id"], marker["source_sha"]),
        )
    migrate_catalog(owner_dsns["tester_catalog"])
    # The existing research validator deliberately pins its own hostaddr internally.
    research_owner = {k: v for k, v in owner.items() if k != "hostaddr"}
    migrate_research(make_conninfo(**research_owner, dbname="tester_research_release"))
    runtime = {}
    for name, (role, schemas) in DATABASES.items():
        with psycopg.connect(owner_dsns[name]) as conn:
            conn.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
            )
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(name), sql.Identifier(role)
                )
            )
            conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            for schema in schemas:
                # Research capabilities and published chart inputs are owner-issued.
                # Projection evidence is append-only; runtime has no rewrite/delete authority.
                table_privileges = (
                    "SELECT"
                    if schema in {"tester_research", "tester_lab"}
                    else "SELECT,INSERT,UPDATE,DELETE"
                )
                for grant in (
                    "GRANT USAGE ON SCHEMA {} TO {}",
                    f"GRANT {table_privileges} ON ALL TABLES IN SCHEMA {{}} TO {{}}",
                    "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}",
                ):
                    conn.execute(
                        sql.SQL(grant).format(sql.Identifier(schema), sql.Identifier(role))
                    )
                migration = (
                    "schema_migrations"
                    if schema == "tester_storage"
                    else ("release_migrations" if schema == "tester_lab" else "migrations")
                )
                conn.execute(
                    sql.SQL("REVOKE INSERT,UPDATE,DELETE ON {}.{} FROM {}").format(
                        sql.Identifier(schema), sql.Identifier(migration), sql.Identifier(role)
                    )
                )
            if name == "tester_research_release":
                for table, privileges in (
                    ("jobs", "INSERT,UPDATE"),
                    ("acknowledgments", "INSERT"),
                    ("audit", "INSERT"),
                ):
                    conn.execute(
                        sql.SQL("GRANT {} ON tester_research.{} TO {}").format(
                            sql.SQL(privileges), sql.Identifier(table), sql.Identifier(role)
                        )
                    )
                # Definer guards retain row-share locks until the caller commits,
                # without granting runtime UPDATE on credentials or capabilities.
                conn.execute(
                    "GRANT EXECUTE ON FUNCTION tester_research.lock_projection_credential(text), "
                    "tester_research.lock_projection_capability(text,text,text) "
                    "TO tester_research_app"
                )
            if name == "tester":
                conn.execute("GRANT INSERT ON tester_lab.projections TO tester_app")
                conn.execute("REVOKE ALL ON tester_platform.restart_budgets FROM tester_app")
                conn.execute(
                    "GRANT EXECUTE ON FUNCTION tester_platform.reserve_restart(uuid,text,text), "
                    "tester_platform.restart_counts(uuid,text) TO tester_app"
                )
                conn.execute(
                    "INSERT INTO tester_platform.transport_policy_scopes "
                    "(installation_id,runtime_role,owner_role) VALUES(%s,'tester_app',current_user)",
                    (marker["installation_id"],),
                )
                conn.execute(
                    "REVOKE ALL ON tester_platform.transport_policy_scopes, "
                    "tester_platform.transport_policy_entries FROM tester_app"
                )
                conn.execute(
                    "GRANT EXECUTE ON FUNCTION tester_platform.read_public_transport(uuid,text), "
                    "tester_platform.reserve_public_transport(uuid,text), "
                    "tester_platform.finish_public_transport(uuid,text,uuid,text), "
                    "tester_platform.reserve_public_dispatch(uuid), "
                    "tester_platform.finish_public_dispatch(uuid,uuid,double precision) TO tester_app"
                )
        role_endpoint = (
            endpoint
            if name != "tester_research_release"
            else {k: v for k, v in endpoint.items() if k != "hostaddr"}
        )
        runtime[name] = make_conninfo(
            **role_endpoint, dbname=name, user=role, password=passwords[name]
        )
    return runtime, owner_dsns
