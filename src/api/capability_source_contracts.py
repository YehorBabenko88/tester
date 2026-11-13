"""UI capability source contracts. Protocol metadata is NOT an admission grant.

DATA #72 owns original public Bybit receipts; RESEARCH #71 owns observed-sample
metrics. This module describes their exact interfaces without importing or
silently composing their independent Draft branches.
"""

PROTOCOL_VERSION = "tester.ui-capabilities/1"
DATA_EXECUTION_VERSION = "tester-public-execution/1"
RESEARCH_METRICS_VERSION = "tester.execution-metrics/1"

# Names and signatures pinned to reviewed DATA #72 and RESEARCH #71.
DEPENDENCIES = {
    "public_executions": {
        "contract_version": DATA_EXECUTION_VERSION,
        "producer": "src.ingest.execution_contract.read_execution_events",
        "research_projection": "src.ingest.execution_contract.read_research_trades",
        "scope": ["category", "symbol", "start_ms", "end_ms", "limit"],
        "max_rows": 100,
        "max_range_ms": 7 * 86400000,
        "quantity_unit": "source_native_linear_size",
        "event_time": "event_ms",
        "observation_time": "observed_ms",
        "publication_time": "UNKNOWN",
        "source_coverage": "UNKNOWN",
        "availability": "UNVERIFIED",
        "source_receipt": "original_http_sha256_and_page_row_proof",
        "readback_state": "NOT_BOUND_TO_UI_ROUTE",
    },
    "observed_execution_metrics": {
        "contract_version": RESEARCH_METRICS_VERSION,
        "consumer": "src.integration.data_research_adapter.read_data_metrics",
        "pure_calculator": "src.analytics.research_metrics.calculate",
        "requires": DATA_EXECUTION_VERSION,
        "scope": [
            "category",
            "symbol",
            "start_ms",
            "end_ms",
            "interval_ms",
            "tick_size",
            "limit",
            "as_of_ms",
        ],
        "supports": ["footprint", "delta", "cvd", "vwap", "poc"],
        "historical_visibility": "UNKNOWN",
        "publication_time": "UNKNOWN",
        "admitted_to_f": False,
        "readback_state": "NOT_BOUND_TO_UI_ROUTE",
    },
    "public_auxiliary": {
        "contract_version": "experimental-unpublished",
        "producer": "src.ingest.public_auxiliary.fetch_and_ingest",
        "sources": {
            "open_interest": "/v5/market/open-interest",
            "funding": "/v5/market/funding/history",
        },
        "native_oi_cadences": ["5min", "15min", "30min", "1h", "4h", "1d"],
        "funding_cadence": "event",
        "publication_time": "UNKNOWN",
        "source_coverage": "UNKNOWN",
        "readback_state": "NOT_BOUND_TO_UI_ROUTE",
    },
}


def dependencies_for_panel(panel):
    """Return explicit expected source consumers; never authorize an HTTP route."""
    if panel in {"footprint", "delta", "cvd", "vwap", "poc", "profile"}:
        return ["public_executions", "observed_execution_metrics"]
    if panel in {"open_interest", "funding"}:
        return ["public_auxiliary"]
    return []


def manifest():
    """Detached metadata; a caller cannot mutate the process-wide manifest."""
    from copy import deepcopy

    return {
        "protocol_version": PROTOCOL_VERSION,
        "source_contracts": deepcopy(DEPENDENCIES),
        "observed_page_revision": {
            "kind": "observed_page_digest",
            "historical_dataset_revision": "UNPINNED",
            "history_complete": False,
            "publication_known": False,
            "supports_f_admission": False,
        },
    }
