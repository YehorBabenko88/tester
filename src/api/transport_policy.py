"""Public request policy adapter using the existing platform PostgreSQL boundary."""

import math
from uuid import UUID

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from src.ingest.public_access import public_endpoint_key
from src.ingest.transport import SystemTrustAdapter, Transport

from .db import connect


class PublicPolicyUnavailable(RuntimeError):
    pass


class PostgresTransportPolicy:
    def __init__(self, dsn, installation_id):
        values = conninfo_to_dict(dsn)
        host = values.get("host")
        allowed = {
            "host",
            "hostaddr",
            "port",
            "dbname",
            "user",
            "password",
            "sslmode",
            "sslrootcert",
            "connect_timeout",
            "require_auth",
        }
        if (
            host not in {"127.0.0.1", "::1"}
            or values.get("hostaddr", host) != host
            or not values.get("port", "").isdecimal()
            or not 1024 <= int(values["port"]) <= 65535
            or int(values["port"]) == 5432
            or not values.get("dbname")
            or not values.get("user")
            or values.get("require_auth") != "scram-sha-256"
            or values["dbname"] in {"postgres", "template0", "template1"}
            or set(values) - allowed
        ):
            raise ValueError("public transport policy requires a dedicated loopback database")
        self.dsn = make_conninfo(dsn, hostaddr=host)
        self.installation_id = UUID(str(installation_id))

    def _call(self, function, endpoint, *args):
        key = None if endpoint is None else public_endpoint_key("https://" + endpoint)
        queries = {
            "reserve": "SELECT * FROM tester_platform.reserve_public_transport(%s,%s)",
            "read": "SELECT tester_platform.read_public_transport(%s,%s)",
            "finish": "SELECT tester_platform.finish_public_transport(%s,%s,%s,%s)",
            "reset": "SELECT tester_platform.reset_public_transport(%s,%s)",
            "reserve_dispatch": "SELECT * FROM tester_platform.reserve_public_dispatch(%s)",
            "finish_dispatch": "SELECT tester_platform.finish_public_dispatch(%s,%s,%s)",
        }
        try:
            with connect(self.dsn) as conn:
                parameters = (
                    (self.installation_id, *args)
                    if function in {"reserve_dispatch", "finish_dispatch"}
                    else (self.installation_id, key, *args)
                )
                row = conn.execute(queries[function], parameters).fetchone()
            if row is None:
                raise PublicPolicyUnavailable("Public transport policy unavailable")
            return row
        except Exception:
            raise PublicPolicyUnavailable(
                "Public transport policy unavailable; owner review required"
            ) from None

    def reserve_dispatch(self):
        """Acquire one installation-wide send lease, independent of endpoint/job."""
        row = self._call("reserve_dispatch", None)
        try:
            state, token, delay = row
            if (
                type(delay) not in (int, float)
                or not math.isfinite(delay)
                or not 0 <= delay <= 3600
            ):
                raise ValueError
            if state == "GRANTED":
                if delay != 0 or token is None:
                    raise ValueError
                token = str(UUID(str(token)))
            elif state == "WAIT":
                if token is not None or delay <= 0:
                    raise ValueError
            elif state in {"PENDING", "HALTED"}:
                if token is not None or delay != 0:
                    raise ValueError
            else:
                raise ValueError
            return state, token, float(delay)
        except (TypeError, ValueError):
            raise PublicPolicyUnavailable(
                "Public dispatch authority invalid; owner review required"
            ) from None

    def finish_dispatch(self, token, cooldown_seconds=0):
        """Release only after the actual network call returns; never expire leases."""
        try:
            token = UUID(str(token))
            if (
                type(cooldown_seconds) not in (int, float)
                or not math.isfinite(cooldown_seconds)
                or not 0 <= cooldown_seconds <= 3600
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise PublicPolicyUnavailable("Invalid public dispatch completion") from None
        row = self._call("finish_dispatch", None, token, cooldown_seconds)
        if len(row) != 1 or row[0] is not True:
            raise PublicPolicyUnavailable("Public dispatch lease fenced; owner review required")

    def reserve(self, endpoint):
        row = self._call("reserve", endpoint)
        if len(row) != 2 or row[0] not in {"GRANTED", "PENDING", "HALTED", "CAPACITY"}:
            raise PublicPolicyUnavailable("Public transport policy invalid; owner review required")
        token = str(UUID(str(row[1]))) if row[0] == "GRANTED" else None
        if row[0] != "GRANTED" and row[1] is not None:
            raise PublicPolicyUnavailable("Public transport policy invalid; owner review required")
        return row[0], token

    def finish(self, endpoint, token, outcome):
        row = self._call("finish", endpoint, UUID(str(token)), outcome)
        if len(row) != 1 or row[0] is not True:
            raise PublicPolicyUnavailable(
                "Public transport reservation fenced; owner review required"
            )

    def read(self, endpoint):
        row = self._call("read", endpoint)
        if len(row) != 1 or row[0] not in {"CLEAR", "PENDING", "HALTED", "CAPACITY"}:
            raise PublicPolicyUnavailable("Public transport policy invalid; owner review required")
        return row[0]

    def reset(self, endpoint=None):
        row = self._call("reset", endpoint)
        if len(row) != 1 or type(row[0]) is not bool:
            raise PublicPolicyUnavailable("Public transport reset response invalid")
        return row[0]


def configured_public_transport(settings, **kwargs):
    policy = (
        PostgresTransportPolicy(settings.dsn, settings.public_transport_installation_id)
        if settings.public_transport_policy_enabled
        else None
    )
    return Transport(policy_store=policy, **kwargs)


def native_public_session():
    """Canonical native OS trust; legacy lab pinning remains a separate opt-in."""
    import requests

    session = requests.Session()
    session.mount("https://", SystemTrustAdapter())
    session.trust_env = False
    return session
