"""Explicit opt-in auxiliary DATA runner for an owned disposable PostgreSQL lab.

Never invoked by default worker/startup. No service installation or OS changes.
"""
import argparse
import json
import os
import uuid
from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict

from src.ingest.public_auxiliary import (
    fetch_and_ingest, fetch_funding_pages, fetch_open_interest_pages,
)
from src.ingest.rest import API
from src.storage.store import Store


def isolated_dsn(value):
    if not value:
        raise ValueError("explicit disposable PostgreSQL DSN required")
    parsed = conninfo_to_dict(value)
    if parsed.get("host") != "127.0.0.1":
        raise ValueError("only loopback PostgreSQL allowed")
    port = int(parsed.get("port", "5432"))
    if port in (5432, 55471) or not 1024 <= port <= 65535:
        raise ValueError("protected PostgreSQL port")
    name = parsed.get("dbname", "")
    if not name.startswith("tester_aux_disposable_"):
        raise ValueError("disposable database name marker required")
    if not parsed.get("user") or not parsed.get("password"):
        raise ValueError("explicit limited-role credentials required")
    return value


def run(*, dsn, symbols, kind, category, start_ms=None, end_ms=None,
        interval=None, max_pages=2, api=None, store=None):
    isolated_dsn(dsn)
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 10:
        raise ValueError("only 1 to 10 instruments allowed")
    if len(set(symbols)) != len(symbols):
        raise ValueError("duplicate symbols")
    if kind not in {"funding", "open_interest", "trades"}:
        raise ValueError("unsupported kind")
    if kind != "trades" and (
        type(start_ms) is not int or type(end_ms) is not int
        or not 0 <= start_ms < end_ms
    ):
        raise ValueError("explicit bounded historical window required")
    if type(max_pages) is not int or not 1 <= max_pages <= 10:
        raise ValueError("invalid page budget")
    if api is None:
        api = API()
    if store is None:
        store = Store(dsn)
    receipts = []
    for symbol in symbols:
        from src.ingest.public_auxiliary import request
        request(kind, category, symbol, start_ms=start_ms, end_ms=end_ms,
                interval=interval)
        run_id = "aux-public-" + uuid.uuid4().hex
        job = run_id + ":" + symbol
        if kind == "funding":
            outcome = fetch_funding_pages(
                store, api, category=category, symbol=symbol, run_id=run_id,
                job_prefix=job, start_ms=start_ms, end_ms=end_ms,
                max_pages=max_pages,
            )
        elif kind == "open_interest":
            outcome = fetch_open_interest_pages(
                store, api, category=category, symbol=symbol, run_id=run_id,
                job_prefix=job, start_ms=start_ms, end_ms=end_ms,
                interval=interval, max_pages=max_pages,
            )
        else:
            outcome = fetch_and_ingest(
                store, api, kind=kind, category=category, symbol=symbol,
                run_id=run_id, job_key=job, return_receipt=True,
            )
        receipts.append({"symbol": symbol, "kind": kind,
                         "status": outcome.get("status", "OBSERVED"),
                         "pages": len(outcome.get("pages", [outcome])),
                         "coverage": "UNKNOWN"})
    return {"source": "bybit_public_v5", "coverage": "UNKNOWN",
            "results": receipts, "trader_identity": "ANONYMOUS"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-env", default="TESTER_AUX_DISPOSABLE_DSN")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--kind", required=True,
                        choices=("funding", "open_interest", "trades"))
    parser.add_argument("--category", choices=("linear", "inverse"), default="linear")
    parser.add_argument("--start-ms", type=int)
    parser.add_argument("--end-ms", type=int)
    parser.add_argument("--interval")
    parser.add_argument("--max-pages", type=int, default=2)
    args = parser.parse_args()
    if args.dsn_env != "TESTER_AUX_DISPOSABLE_DSN":
        parser.error("only dedicated disposable DSN environment variable allowed")
    result = run(
        dsn=os.environ.get(args.dsn_env),
        symbols=args.symbols, kind=args.kind, category=args.category,
        start_ms=args.start_ms, end_ms=args.end_ms,
        interval=args.interval, max_pages=args.max_pages,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
