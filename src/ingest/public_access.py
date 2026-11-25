"""Pure public Bybit request policy, shared by transport and diagnostics."""

import re
from urllib.parse import parse_qsl, urlsplit

REST_PATHS = frozenset(
    "/v5/market/" + name
    for name in (
        "time",
        "instruments-info",
        "tickers",
        "kline",
        "mark-price-kline",
        "index-price-kline",
        "premium-index-price-kline",
        "open-interest",
        "funding/history",
        "account-ratio",
        "orderbook",
        "recent-trade",
    )
)
QUERY_KEYS = frozenset(
    (
        "category",
        "symbol",
        "interval",
        "intervalTime",
        "period",
        "start",
        "end",
        "startTime",
        "endTime",
        "limit",
        "cursor",
        "status",
        "baseCoin",
    )
)
# Endpoint-specific narrowing of QUERY_KEYS. /v5/market/recent-trade is a public
# anonymous snapshot (no time range, no cursor): only an explicit derivative or
# spot instrument and the documented page limit (spot 1-60, others 1-1000).
RECENT_TRADE_PATH = "/v5/market/recent-trade"
RECENT_TRADE_LIMITS = {"spot": 60, "linear": 1000, "inverse": 1000}


def _recent_trade_query(pairs):
    if not pairs:
        return  # Bare endpoint identity (policy/guardian scope key), never a request.
    query = dict(pairs)
    if (
        set(query) - {"category", "symbol", "limit"}
        or query.get("category") not in RECENT_TRADE_LIMITS
        or not re.fullmatch(r"[A-Z0-9]{1,40}", query.get("symbol", ""))
    ):
        raise ValueError("unapproved_query")
    if "limit" in query:
        limit = query["limit"]
        if not re.fullmatch(r"[1-9][0-9]{0,3}", limit) or not (
            1 <= int(limit) <= RECENT_TRADE_LIMITS[query["category"]]
        ):
            raise ValueError("unapproved_query")


ARCHIVE_ROOTS = frozenset(
    ("trading", "spot", "premium_index", "spot_index", "kline_for_metatrader4")
)


def public_endpoint_key(url):
    """Validate URL and return credential-free endpoint identity."""
    if type(url) is not str or not url or len(url) > 4096 or any(ord(c) < 33 for c in url):
        raise ValueError("unapproved_endpoint")
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or p.netloc not in {"api.bybit.com", "public.bybit.com"}
        or p.fragment
        or "%" in p.path
        or "\\" in p.path
        or any(s in {".", ".."} for s in p.path.split("/"))
    ):
        raise ValueError("unapproved_endpoint")
    if p.netloc == "api.bybit.com":
        if p.path not in REST_PATHS:
            raise ValueError("unapproved_endpoint")
        try:
            pairs = parse_qsl(
                p.query, keep_blank_values=True, strict_parsing=True, max_num_fields=32
            )
        except ValueError as exc:
            raise ValueError("unapproved_query") from exc
        if len({k for k, _ in pairs}) != len(pairs) or any(
            k not in QUERY_KEYS or not v or len(v) > 1024 or any(ord(c) < 32 for c in v)
            for k, v in pairs
        ):
            raise ValueError("unapproved_query")
        if p.path == RECENT_TRADE_PATH:
            _recent_trade_query(pairs)
    else:
        parts = p.path.strip("/").split("/")
        if p.query or (
            p.path != "/"
            and (
                parts[0] not in ARCHIVE_ROOTS
                or len(parts) > 3
                or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", s) for s in parts)
            )
        ):
            raise ValueError("unapproved_archive")
    return p.netloc + p.path
