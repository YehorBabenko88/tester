"""A replacement loopback listener cannot downgrade native DB authentication."""

import socket
import struct
import threading

import psycopg
import pytest

from src.api.release_database import database_identity


def cleartext_challenge(require_auth):
    observed = {"startup": False, "password": None}
    sentinel = "DISPOSABLE_DB_AUTH_SENTINEL"

    def exact(connection, count):
        result = b""
        while len(result) < count:
            block = connection.recv(count - len(result))
            if not block:
                return result
            result += block
        return result

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(4)
        port = server.getsockname()[1]

        def fake_postgres():
            with server.accept()[0] as connection:
                connection.settimeout(4)
                while True:
                    size = struct.unpack("!I", exact(connection, 4))[0]
                    body = exact(connection, size - 4)
                    if size == 8 and struct.unpack("!I", body)[0] in (80877103, 80877104):
                        connection.sendall(b"N")  # No SSL/GSS; no real DB or credentials.
                        continue
                    observed["startup"] = body[:4] == struct.pack("!I", 196608)
                    break
                connection.sendall(b"R" + struct.pack("!II", 8, 3))
                kind = exact(connection, 1)
                if kind == b"p":
                    size = struct.unpack("!I", exact(connection, 4))[0]
                    observed["password"] = exact(connection, size - 4).rstrip(b"\0").decode()

        worker = threading.Thread(target=fake_postgres)
        worker.start()
        options = dict(
            host="127.0.0.1",
            hostaddr="127.0.0.1",
            port=port,
            dbname="tester",
            user="tester_app",
            password=sentinel,
            connect_timeout=2,
        )
        if require_auth:
            options["require_auth"] = require_auth
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(**options)
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert observed["startup"]
    return observed, sentinel


def test_native_release_requires_mutual_scram_before_sending_password():
    marker = {"ports": {"postgres": 55822, "api": 55823}}
    required = database_identity(marker).get("require_auth")
    assert required == "scram-sha-256"
    observed, _ = cleartext_challenge(required)
    assert observed["password"] is None


def test_regression_control_unpinned_libpq_accepts_cleartext_challenge():
    observed, sentinel = cleartext_challenge(None)
    assert observed["password"] == sentinel
