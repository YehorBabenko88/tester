"""Owner-protected PostgreSQL launch reservations; no process executor."""

import json
import re
import uuid

from .lab_install import plain

LIMITS = {"host": 3, "api": 6, "worker": 6}
ENV_KEYS = ("TESTER_RESTART_BUDGET_DIR", "TESTER_INSTALLATION_ID", "TESTER_SOURCE_SHA")


class BudgetDenied(ValueError):
    pass


class BudgetUnavailable(BudgetDenied):
    """Infrastructure unavailable; a bounded SCM retry may recover authority."""


class DatabaseRestartBudget:
    """Production authority: owner table, runtime EXECUTE-only definer functions.

    The application SID cannot modify the separate PostgreSQL writer tree, and
    its SQL login cannot reset counters or disable the budget. The existing DB
    connection adapter performs the transaction; no second executor is involved.
    """

    def __init__(self, dsn, directory, installation_id, source_sha):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict

        try:
            if str(uuid.UUID(installation_id)) != installation_id or not re.fullmatch(
                "[0-9a-f]{40}", source_sha
            ):
                raise ValueError
            self.directory = plain(directory)
            endpoint = conninfo_to_dict(dsn)
            if (
                endpoint.get("host") != "127.0.0.1"
                or endpoint.get("hostaddr", "127.0.0.1") != "127.0.0.1"
                or not endpoint.get("port", "").isdecimal()
                or not 1024 <= int(endpoint["port"]) <= 65535
                or int(endpoint["port"]) == 5432
                or endpoint.get("dbname") != "tester"
                or endpoint.get("user") != "tester_app"
                or endpoint.get("require_auth") != "scram-sha-256"
                or set(endpoint)
                - {"host", "hostaddr", "port", "dbname", "user", "password", "require_auth"}
            ):
                raise ValueError
        except (ValueError, TypeError, AttributeError, psycopg.Error) as exc:
            raise BudgetDenied("Invalid isolated restart database identity") from exc
        from psycopg.conninfo import make_conninfo

        self.dsn = make_conninfo(**{**endpoint, "hostaddr": "127.0.0.1"})
        self.installation_id = installation_id
        self.source_sha = source_sha

    @classmethod
    def from_environment(cls, environment):
        values = [environment.get(key) for key in ENV_KEYS]
        if not any(values):
            return None
        config = environment.get("TESTER_CONFIG_FILE")
        if not all(values) or not config:
            raise BudgetDenied("Incomplete restart budget identity/configuration")
        return cls.from_config(config, *values)

    @classmethod
    def from_config(cls, path, directory, installation_id, source_sha):
        try:
            path, directory = plain(path), plain(directory)
            if (
                path.name != "config.json"
                or path.parent != directory.parent
                or path.stat().st_nlink != 1
                or path.stat().st_size > 32768
            ):
                raise ValueError
            config = json.loads(path.read_text(encoding="utf-8"))
            if plain(config["data_dir"]) != plain(directory):
                raise ValueError
            return cls(config["database_url"], directory, installation_id, source_sha)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise BudgetDenied("Restart configuration unavailable or out of scope") from exc

    def _request(self, role=None):
        import psycopg

        from .db import connect

        if (self.directory / "restart.disabled").exists():
            raise BudgetDenied("Restarts explicitly disabled")
        try:
            with connect(self.dsn) as conn:
                conn.execute("SET LOCAL synchronous_commit=on")
                if role is None:
                    result = conn.execute(
                        "SELECT tester_platform.restart_counts(%s::uuid,%s)",
                        (self.installation_id, self.source_sha),
                    ).fetchone()[0]
                else:
                    conn.execute(
                        "SELECT tester_platform.reserve_restart(%s::uuid,%s,%s)",
                        (self.installation_id, self.source_sha, role),
                    )
                    result = None
            # Exiting the context commits before the caller may spawn a child.
            return result
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            raise BudgetUnavailable("Restart authority temporarily unavailable") from exc
        except psycopg.Error as exc:
            raise BudgetDenied("Durable restart reservation denied") from exc

    def counts(self):
        result = self._request()
        if (
            not isinstance(result, dict)
            or set(result) != set(LIMITS)
            or any(
                type(result[role]) is not int or not 0 <= result[role] <= LIMITS[role]
                for role in LIMITS
            )
        ):
            raise BudgetDenied("Invalid restart count result")
        return result

    def admit(self, role):
        if type(role) is not str or role not in LIMITS:
            raise BudgetDenied("Unknown restart role")
        self._request(role)
