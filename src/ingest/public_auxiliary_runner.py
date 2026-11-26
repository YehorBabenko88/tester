"""Explicit opt-in public execution (recent-trade) runner for an owned lab DB.

Never invoked by default worker/startup. No service installation or OS changes.
Every network request goes through the canonical src.ingest.transport.Transport
with the durable installation-wide PostgreSQL policy (dispatch lease, 429
cooldown, persistent 403/TLS halt). Without that policy the runner refuses to
touch the network. Funding/open interest use the canonical rest.Backfill path.
"""

import argparse
import json
import os
import uuid

from psycopg.conninfo import conninfo_to_dict

from src.ingest.public_auxiliary import fetch_and_ingest, request
from src.ingest.rest import API
from src.ingest.transport import Transport
from src.storage.store import Store

DSN_ENV = "TESTER_AUX_DISPOSABLE_DSN"
POLICY_DSN_ENV = "TESTER_AUX_POLICY_DSN"
INSTALLATION_ENV = "TESTER_AUX_POLICY_INSTALLATION_ID"


def isolated_dsn(value):
    if not value:
        raise ValueError("explicit disposable PostgreSQL DSN required")
    parsed = conninfo_to_dict(value)
    if set(parsed) != {"host", "port", "dbname", "user", "password", "require_auth"}:
        raise ValueError("unexpected PostgreSQL connection parameters")
    if parsed.get("require_auth") != "scram-sha-256":
        # Same authentication floor as release_database/transport_policy DSNs.
        raise ValueError("SCRAM authentication required")
    if parsed.get("host") != "127.0.0.1":
        raise ValueError("only loopback PostgreSQL allowed")
    port = parsed.get("port", "")
    if not port.isdecimal() or int(port) in (5432, 55471) or not 1024 <= int(port) <= 65535:
        raise ValueError("protected PostgreSQL port")
    name = parsed.get("dbname", "")
    if not name.startswith("tester_aux_disposable_"):
        raise ValueError("disposable database name marker required")
    if parsed.get("user") in (None, "", "postgres", "research_owner") or not parsed.get("password"):
        raise ValueError("explicit limited-role credentials required")
    return value


def policy_api(policy_dsn, installation_id):
    """Reject legacy caller-controlled identity before constructing any Transport.

    A DSN and UUID from environment/CLI are not sufficient evidence of an
    authenticated release installation. D owns the trusted runtime resolver;
    this legacy entrypoint must remain offline until that verifier is bound.
    """
    raise ValueError("trusted release transport identity required before network")


def _require_policy(api):
    # Never trust a caller-constructed Transport solely because a policy_store
    # is present: its DSN/installation_id may identify another installation.
    # Non-API injected objects are a test-only offline seam, never public HTTP.
    if isinstance(api, API):
        raise ValueError("trusted release transport identity required before network")


def run(*, dsn, symbols, category, kind="trades", policy_dsn=None,
        installation_id=None, api=None, store=None):
    isolated_dsn(dsn)
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 10:
        raise ValueError("only 1 to 10 instruments allowed")
    if len(set(symbols)) != len(symbols):
        raise ValueError("duplicate symbols")
    if kind != "trades":
        raise ValueError("unsupported kind; funding/open interest use rest.Backfill")
    for symbol in symbols:
        request(kind, category, symbol)
    if api is None:
        api = policy_api(policy_dsn, installation_id)
    _require_policy(api)
    if store is None:
        store = Store(dsn)
    receipts = []
    for symbol in symbols:
        run_id = "aux-public-" + uuid.uuid4().hex
        outcome = fetch_and_ingest(
            store, api, kind=kind, category=category, symbol=symbol,
            run_id=run_id, job_key=run_id + ":" + symbol, return_receipt=True,
        )
        receipts.append({"symbol": symbol, "kind": kind, "status": "OBSERVED",
                         "pages": 1, "records": outcome.get("records"),
                         "coverage": "UNKNOWN"})
    return {"source": "bybit_public_v5", "coverage": "UNKNOWN",
            "results": receipts, "trader_identity": "ANONYMOUS"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--category", choices=("linear", "inverse"), default="linear")
    args = parser.parse_args()
    result = run(
        dsn=os.environ.get(DSN_ENV),
        symbols=args.symbols, category=args.category,
        policy_dsn=os.environ.get(POLICY_DSN_ENV),
        installation_id=os.environ.get(INSTALLATION_ENV),
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
