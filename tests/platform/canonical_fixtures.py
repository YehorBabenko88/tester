from copy import deepcopy


def trade():
    return dict(
        schema_version="1.0",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        timestamp_ms=1700000000123,
        timestamp_ns="1700000000123456789",
        timestamp_precision="ns",
        trade_id="fixture-1",
        id_origin="exchange",
        price="9007199254740993.001",
        quantity="0.00000001",
        taker_side="Unknown",
        provenance=[
            dict(
                source_kind="synthetic_fixture",
                source_ref="fixture://trades",
                source_hash="a" * 64,
                ingestion_run="fixture-run",
                timestamp_precision="ns",
                source_timestamp="1700000000.123456789",
            )
        ],
        availability=dict(status="PARTIAL", reason="unknown taker side"),
        coverage=dict(
            status="PARTIAL",
            start_ms=1700000000000,
            end_ms=1700000060000,
            evidence_refs=["fixture-page"],
        ),
    )


def envelope(row=None):
    row = trade() if row is None else row
    return dict(
        schema_version="1.0",
        data=[row],
        next_cursor="opaque-source-cursor",
        availability=deepcopy(row["availability"]),
        coverage=deepcopy(row["coverage"]),
        provenance=deepcopy(row["provenance"]),
    )
