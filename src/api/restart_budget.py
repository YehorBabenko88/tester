"""Owner-protected PostgreSQL crash-restart reservations; no process executor.

Only crash restarts are counted, inside a fixed sliding window (migration 008).
Clean starts (boot after a recorded clean stop, operator start after a stop) and
the initial API/worker launch of a supervisor run are admitted without consuming
crash budget, but still require the protected identity row and the kill switches.
"""

import json
import re
import secrets
import uuid

from .lab_install import plain

LIMITS = {"host": 3, "api": 6, "worker": 6}  # Crash restarts per WINDOW_SECONDS.
WINDOW_SECONDS = 3600
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
        self.host_token = None

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
        """Reserve before spawning. ``host`` starts a service-host run; ``api``/``worker``
        are child crash restarts. The host start is a crash restart only when the
        previous host run never recorded a clean stop (crash, kill or power loss).
        """
        if type(role) is not str or role not in LIMITS:
            raise BudgetDenied("Unknown restart role")
        if role == "host":
            # The run token stays in the host process; children never receive it.
            # A retry after an ambiguous commit reuses the token and is idempotent.
            self.host_token = self.host_token or secrets.token_hex(16)
            self._request("host:" + self.host_token)
        else:
            self._request(role)

    def admit_initial(self, role):
        """Initial child launch of a supervisor run: identity/kill switch only."""
        if role not in ("api", "worker"):
            raise BudgetDenied("Unknown restart role")
        self._request(role + "-start")

    def record_clean_stop(self):
        """Record that the current host run is ending on request (not a crash)."""
        if self.host_token is None:
            raise BudgetDenied("No admitted host run")
        self._request("host-stop:" + self.host_token)
        self.host_token = None


def _owner_dsn(root):
    """Administrative owner credential; readable only by Administrators/SYSTEM."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from .release_install import load_marker

    root = plain(root)
    marker = load_marker(root)
    path = plain(root / "private/database-owner.json")
    if path.stat().st_nlink != 1 or path.stat().st_size > 65536:
        raise BudgetDenied("Owner credential receipt out of scope")
    endpoint = conninfo_to_dict(json.loads(path.read_text(encoding="utf-8"))["database_url"])
    if (
        endpoint.get("host") != "127.0.0.1"
        or endpoint.get("hostaddr", "127.0.0.1") != "127.0.0.1"
        or endpoint.get("port") != str(marker["ports"]["postgres"])
        or endpoint.get("port") == "5432"
        or endpoint.get("dbname") != "tester"
        or endpoint.get("user") != "tester_owner"
        or endpoint.get("require_auth") != "scram-sha-256"
    ):
        raise BudgetDenied("Owner restart authority scope mismatch")
    return make_conninfo(**{**endpoint, "hostaddr": "127.0.0.1"}), marker["installation_id"]


def owner_command(root, action):
    """Owner-authenticated diagnosis/reviewed reset. The App login cannot run either."""
    from .db import connect

    queries = {
        "status": "SELECT tester_platform.restart_window_status(%s::uuid)",
        "reset": "SELECT tester_platform.reset_restart_window(%s::uuid)",
    }
    if action not in queries:
        raise BudgetDenied("Unknown restart owner action")
    dsn, installation_id = _owner_dsn(root)
    with connect(dsn) as conn:
        result = conn.execute(queries[action], (installation_id,)).fetchone()[0]
    if result is None:
        raise BudgetDenied("Unknown restart installation")
    return result


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Owner-only TESTER restart window tool")
    parser.add_argument("action", choices=("status", "reset"))
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(owner_command(args.root, args.action), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
