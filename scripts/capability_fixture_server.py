"""CI-only synthetic source served through the REAL tester FastAPI routes.

No external network, credentials, PostgreSQL, PC4, trading, or historical
completeness claims. This tests HTTP/browser wiring, NOT real Bybit ingestion.
"""
from pathlib import Path

from src.api.config import Settings
from src.api.main import create_app
from src.api.providers import ProviderUnavailable
from src.ingest.rest import expected_times, next_time
from src.integration.reference_prices import ReferencePriceReader


class CiReferenceReader(ReferencePriceReader):
    def __init__(self):
        # Intentionally no Store: source data is deterministic test-only.
        pass

    def read(self, resource, query):
        if resource == "sources":
            # Reuse the real, canonical source listing; it does not query DB.
            return ReferencePriceReader.read(self, resource, query)
        if resource != "price-candles":
            raise ProviderUnavailable("CI fixture only supports price-only sources")
        if query.category != "linear" or query.symbol != "BTCUSDT":
            raise ProviderUnavailable("CI fixture scope not available")
        kind = query.candle_kind
        if kind not in ("official_mark", "official_index"):
            raise ProviderUnavailable("CI fixture kind not supported")
        ts = next(expected_times(query.start_ms, query.end_ms, query.interval), None)
        if ts is None or next_time(ts, query.interval) > query.end_ms:
            rows = []
            refs = []
        else:
            endpoint = ("mark-price-kline" if kind == "official_mark"
                        else "index-price-kline")
            refs = [dict(
                source_kind="bybit_rest",
                source_ref=("https://api.bybit.com/v5/market/" + endpoint
                            + "?category=linear&symbol=BTCUSDT&interval=" + query.interval),
                source_hash="a" * 64, ingestion_run="ci-synthetic-not-acquired",
                timestamp_precision="ms", source_timestamp=str(ts),
            )]
            rows = [dict(
                schema_version="1.1", venue="bybit", category="linear",
                symbol="BTCUSDT", timestamp_ms=ts, start_ms=ts,
                end_ms=next_time(ts, query.interval), interval=query.interval,
                kind=kind, open="100.00", high="102.00",
                low="99.00", close="101.25", is_closed=True,
                availability=dict(status="PARTIAL", reason="CI synthetic observations only"),
                coverage=dict(status="UNKNOWN", start_ms=query.start_ms,
                              end_ms=query.end_ms, evidence_refs=[]),
                provenance=refs,
            )]
        return dict(
            schema_version="1.1", data=rows, next_cursor=None,
            availability=dict(status="PARTIAL" if rows else "UNAVAILABLE",
                              reason="CI synthetic observations only" if rows
                              else "No fixture observations in chosen UTC range"),
            coverage=dict(status="UNKNOWN", start_ms=query.start_ms,
                          end_ms=query.end_ms, evidence_refs=[]),
            provenance=refs,
        )


def create_fixture_app():
    return create_app(
        Settings(
            provider_factory="src.integration.reference_prices:create_reader",
            market_catalog_enabled=False,
            frontend_dir=Path("frontend").resolve(),
            data_dir=Path("runtime-ci-capability").resolve(),
        ),
        provider=CiReferenceReader(),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_fixture_app(), host="127.0.0.1", port=18874, access_log=False)
