# Native database server authentication

Every generated owner and runtime DSN pins numeric loopback, a dedicated non-5432
port and `require_auth=scram-sha-256`. The pinned psycopg binary exposes libpq
18.4 in both tested Python environments. The native cluster also requires SCRAM
in its HBA rules; runtime passwords are randomly generated.

Loopback alone does not authenticate a listener that takes the port while the
owned server is stopped. PostgreSQL documents that libpq otherwise accepts the
authentication method selected by the server, whereas the SCRAM requirement
requires a completed exchange. See the [PostgreSQL connection parameter
documentation](https://www.postgresql.org/docs/18/libpq-connect.html#LIBPQ-CONNECT-REQUIRE-AUTH).

The regression uses a fresh loopback socket and a disposable synthetic password.
Its control confirms an unpinned client answers a plaintext-password challenge;
the native DSN refuses that challenge without sending the password. No real
database or credential is used in this negative test. Real isolated SCRAM cluster
tests separately verify migrations, role permissions and protected SQL authority.

The native configuration and restart-budget adapter reject weaker or missing
authentication requirements. General historical development profiles retain
their existing DSN contract; this is not permission to weaken a native release.
