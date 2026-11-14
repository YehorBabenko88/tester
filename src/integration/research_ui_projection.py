"""Read-only DATA→RESEARCH→UI observation contract, not an HTTP endpoint.

This module cannot authorize Strategy Lab F/G/E, install a route, or certify
historical completeness. Only source-backed DATA72 PostgreSQL readback is
eligible for a rendered observation; synthetic fixtures remain untrusted.
A caller-supplied dict is NOT a proof of source authenticity.
"""
import re
from copy import deepcopy
from fractions import Fraction

from src.storage.models import digest

SCHEMA = "tester.research-ui-observation/1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")


def _exact(value, *, nonnegative=False):
    if not isinstance(value, str) or len(value) > 128 or not _DECIMAL.fullmatch(value):
        raise ValueError("research_ui_noncanonical_decimal")
    result = Fraction(value)
    if nonnegative and result < 0:
        raise ValueError("research_ui_negative_volume")
    return result


def read_verified_ui_observation(store, *, category, symbol, start_ms, end_ms,
                                 interval_ms, tick_size, as_of_ms=None, limit=100):
    """Read actual DATA72 persisted evidence; never trust a caller's preview label.

    This is the only intended future API/UI entrypoint. It remains a pure
    readback with no mounted HTTP route, F/G/E job or writer privileges.
    """
    from src.integration.data_research_adapter import read_data_metrics

    source = read_data_metrics(
        store, category=category, symbol=symbol,
        start_ms=start_ms, end_ms=end_ms, interval_ms=interval_ms,
        tick_size=tick_size, as_of_ms=as_of_ms, limit=limit,
    )
    return project_source_observation(source)


def project_source_observation(preview):
    """Validate sourced execution math and return display-only immutable data.

    G/E commands, synthetic authorization, and F jobs are deliberately absent.
    The consumer must not infer a server route from this pure function.
    """
    if not isinstance(preview, dict) or preview.get("schema_version") != "tester.data-research-preview/2":
        raise ValueError("research_ui_preview_contract")
    if (preview.get("coverage") != "UNKNOWN"
            or preview.get("admitted_to_f") is not False
            or preview.get("f_input") is not None
            or preview.get("g_e_result") is not None
            or preview.get("publication_time") != "UNKNOWN"
            or preview.get("persisted_dataset_revision", "MISSING") is not None
            or preview.get("historical_known_at_ms", "MISSING") is not None):
        raise ValueError("research_ui_historical_authority_refused")
    observed = preview.get("observed_window")
    if (not isinstance(observed, dict)
            or observed.get("historical_completeness_state") != "UNKNOWN"
            or observed.get("complete_market_history") is not False):
        raise ValueError("research_ui_coverage_unverified")
    if not (type(observed.get("start_ms")) is int
            and type(observed.get("end_ms")) is int
            and observed["start_ms"] < observed["end_ms"]):
        raise ValueError("research_ui_invalid_utc_window")
    if (preview.get("status") != "OBSERVED_UNVERIFIED"
            or preview.get("source_verification") != "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS"):
        raise ValueError("research_ui_not_source_backed")
    for key in ("data_execution_digest", "metrics_digest",
                "observed_sample_fingerprint", "data_source_receipts_digest"):
        value = preview.get(key)
        if not isinstance(value, str) or not _HEX.fullmatch(value):
            raise ValueError("research_ui_unqualified_hash")
    metrics = preview.get("metrics")
    if not isinstance(metrics, dict) or digest(metrics) != preview["metrics_digest"]:
        raise ValueError("research_ui_metric_digest_mismatch")
    if (metrics.get("method") != "tester.execution-metrics/1"
            or metrics.get("status") != "DERIVED_UNVERIFIED"
            or metrics.get("admitted_to_f") is not False
            or metrics.get("cvd_prior_history_known") is not False
            or metrics.get("cvd_anchor_start_ms") != observed["start_ms"]):
        raise ValueError("research_ui_metric_authority_refused")
    bars = metrics.get("bars")
    if not isinstance(bars, list) or not 1 <= len(bars) <= 1000:
        raise ValueError("research_ui_invalid_bars")
    running = Fraction()
    count = 0
    has_unknown = False
    previous_end = observed["start_ms"]
    for bar in bars:
        if not isinstance(bar, dict):
            raise ValueError("research_ui_invalid_bar")
        start, end = bar.get("start_ms"), bar.get("end_ms")
        if (type(start) is not int or type(end) is not int
                or start != previous_end or not start < end <= observed["end_ms"]):
            raise ValueError("research_ui_noncontiguous_utc_bars")
        previous_end = end
        volume = _exact(bar.get("volume"), nonnegative=True)
        buy = _exact(bar.get("buy_volume"), nonnegative=True)
        sell = _exact(bar.get("sell_volume"), nonnegative=True)
        unknown = _exact(bar.get("unknown_side_volume"), nonnegative=True)
        delta = _exact(bar.get("delta_known"))
        cvd = _exact(bar.get("cvd_known"))
        if (volume != buy + sell + unknown or delta != buy - sell
                or bar.get("delta_complete") is not False
                or bar.get("coverage_certified") is not False
                or bar.get("vwap_volume_coverage_certified") is not False
                or bar.get("poc_volume_coverage_certified") is not False):
            raise ValueError("research_ui_execution_volume_mismatch")
        running += delta
        if cvd != running:
            raise ValueError("research_ui_cvd_anchor_mismatch")
        count_bar = bar.get("trade_count")
        if type(count_bar) is not int or count_bar < 0 or (count_bar == 0) != (volume == 0):
            raise ValueError("research_ui_trade_count_inconsistent")
        count += count_bar
        has_unknown = has_unknown or unknown > 0
        levels = bar.get("footprint")
        if not isinstance(levels, list) or len(levels) > 100:
            raise ValueError("research_ui_footprint_missing")
        totals = [Fraction(), Fraction(), Fraction(), Fraction()]
        ranked = []
        for level in levels:
            if not isinstance(level, dict):
                raise ValueError("research_ui_invalid_footprint")
            price = _exact(level.get("price"), nonnegative=True)
            amounts = [_exact(level.get(key), nonnegative=True) for key in
                       ("volume", "buy_volume", "sell_volume", "unknown_side_volume")]
            if amounts[0] != sum(amounts[1:]):
                raise ValueError("research_ui_level_volume_mismatch")
            totals = [a + b for a, b in zip(totals, amounts)]
            ranked.append((price, amounts[0]))
        if totals != [volume, buy, sell, unknown]:
            raise ValueError("research_ui_footprint_total_mismatch")
        if count_bar:
            winner = min(ranked, key=lambda item: (-item[1], item[0]))[0]
            if _exact(bar.get("poc")) != winner:
                raise ValueError("research_ui_poc_mismatch")
            if _exact(bar.get("vwap"), nonnegative=True) <= 0:
                raise ValueError("research_ui_vwap_invalid")
        elif bar.get("poc") is not None or bar.get("vwap") is not None or levels:
            raise ValueError("research_ui_fabricated_empty_bar")
    if previous_end != observed["end_ms"] or count != observed.get("observed_unique_trades"):
        raise ValueError("research_ui_observation_count_mismatch")
    return {
        "schema_version": SCHEMA,
        "status": "OBSERVED_UNVERIFIED",
        "source_verification": "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS",
        "source_coverage": "UNKNOWN",
        "publication_time": "UNKNOWN",
        "source_receipts_digest": preview["data_source_receipts_digest"],
        "data_execution_digest": preview["data_execution_digest"],
        "observed_sample_fingerprint": preview["observed_sample_fingerprint"],
        "metrics_digest": preview["metrics_digest"],
        "method": metrics["method"],
        "utc_window": {"start_ms": observed["start_ms"], "end_ms": observed["end_ms"]},
        "bars": deepcopy(bars),
        "unknown_side_present": has_unknown,
        "delta_complete": False,
        "cvd_prior_history_known": False,
        "historical_known_at_ms": None,
        "f_admission": "DENIED_UNKNOWN_COVERAGE",
        "g_e_execution": "NOT_AUTHORIZED",
        "http_route": None,
        "http_route_status": "NOT_REGISTERED",
    }
