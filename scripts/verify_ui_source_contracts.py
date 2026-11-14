"""Read-only signature audit of exact DATA #72 and RESEARCH #71 source trees.

No import or execution of producer code, no checkout mutation and no source
network calls. The audit prevents silently upgrading an incompatible versioned
contract while independent Draft branches are being composed by MASTER.
"""

import argparse
import ast
import hashlib
from pathlib import Path

from src.api.capability_source_contracts import (
    DATA_EXECUTION_VERSION,
    RESEARCH_METRICS_VERSION,
)

DATA_FUNCTIONS = {
    "read_execution_events": {"category", "symbol", "start_ms", "end_ms", "limit"},
    "read_research_trades": {"category", "symbol", "start_ms", "end_ms", "limit"},
}
RESEARCH_FUNCTIONS = {
    "read_data_metrics": {
        "category",
        "symbol",
        "start_ms",
        "end_ms",
        "interval_ms",
        "tick_size",
        "as_of_ms",
        "limit",
    },
}
CALCULATION_FUNCTIONS = {"calculate": {"start_ms", "end_ms", "interval_ms", "tick_size"}}


def _parse(file):
    return ast.parse(Path(file).read_text(encoding="utf-8"), filename=str(file))


def verify_shared_pg_hostaddr_test(
    data_path, research_path, *, expected_blob="402c25410ba4cc6704911148b1ec0c5bba720088"
):
    """Audit DATA/RESEARCH same-path conflict by exact Git blob, not merge order.

    No database or producer module is loaded. This only certifies the test
    source bytes; the actual PGHOSTADDR negative regression still must pass
    on the new immutable combined head against disposable PostgreSQL.
    """
    data = Path(data_path).read_bytes()
    research = Path(research_path).read_bytes()
    if not data or len(data) > 256_000 or data != research:
        raise ValueError("data_research_pghostaddr_overlap_requires_manual_reconciliation")
    sha = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    if sha != expected_blob:
        raise ValueError("pghostaddr_overlap_unreviewed_git_blob")
    text = data.decode("utf-8")
    required = (
        "PGHOSTADDR",
        "PGHOST",
        "PGPORT",
        "PGDATABASE",
        "conn.info.hostaddr",
        "inet_server_addr",
        "127.0.0.2",
        "172.18.0.2",
        "test_actual_postgres_hostaddr_environment_cannot_redirect",
        "test_research_dsn_refuses_explicit_hostaddr_redirect",
    )
    if any(token not in text for token in required):
        raise ValueError("pghostaddr_negative_controls_absent")
    return {
        "source_git_blob": sha,
        "identical_DATA_RESEARCH_bytes": True,
        "hostaddr_negative_guards_preserved": True,
        "runtime_postgres_acceptance": "NOT_RUN_BY_SOURCE_AUDIT",
    }


def _constant(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
    raise ValueError("required_source_constant_missing:" + name)


def _signature(tree, name, required):
    fn = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ),
        None,
    )
    if fn is None:
        raise ValueError("required_source_function_missing:" + name)
    bound = {arg.arg for arg in fn.args.kwonlyargs}
    if not required <= bound:
        raise ValueError("source_function_signature_mismatch:" + name)
    return fn


def _literals(tree):
    literals = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
        if isinstance(node, ast.keyword) and node.arg:
            literals.add(node.arg)
    return literals


def verify_paths(data, research, metrics, research_ui=None):
    producer = _parse(data)
    consumer = _parse(research)
    calculator = _parse(metrics)
    projection = _parse(research_ui) if research_ui is not None else None
    if _constant(producer, "VERSION") != DATA_EXECUTION_VERSION:
        raise ValueError("data_contract_version_mismatch")
    if _constant(consumer, "DATA_CONTRACT_VERSION") != DATA_EXECUTION_VERSION:
        raise ValueError("consumer_data_contract_version_mismatch")
    if _constant(calculator, "METHOD") != RESEARCH_METRICS_VERSION:
        raise ValueError("research_metrics_contract_version_mismatch")
    # The RESEARCH source may record a reviewed producer commit as advisory
    # metadata; runtime authorization is based on exact version, shape and
    # independent source blob attestations, never that stale commit literal.
    # The CI job separately verifies every checkout HEAD and source blob.
    if _constant(producer, "MAX_ROWS") != 100:
        raise ValueError("data_page_budget_mismatch")
    if _constant(consumer, "MAX_EVENTS") != 100:
        raise ValueError("research_event_budget_mismatch")
    for name, scope in DATA_FUNCTIONS.items():
        _signature(producer, name, scope)
    for name, scope in RESEARCH_FUNCTIONS.items():
        _signature(consumer, name, scope)
    for name, scope in CALCULATION_FUNCTIONS.items():
        _signature(calculator, name, scope)
    required_event_fields = {
        "exec_id",
        "quantity_unit",
        "event_ms",
        "observed_ms",
        "publication_ms",
        "source_receipts",
        "source_coverage",
        "source_native_linear_size",
        "source_native_inverse_contract",
    }
    if not required_event_fields <= _literals(producer):
        raise ValueError("data_provenance_time_or_quantity_semantics_mismatch")
    data_literals = _literals(producer)
    research_literals = _literals(consumer)
    if not {"Unknown", "UNKNOWN", "UNVERIFIED"} <= data_literals:
        raise ValueError("unknown_taker_side_or_visibility_not_preserved")
    if not {"Unknown", "UNKNOWN", "UNVERIFIED", "source_native_linear_size"} <= research_literals:
        raise ValueError("research_unknown_side_or_coverage_not_preserved")
    required_metric_fields = {
        "unknown_side_volume",
        "delta_known",
        "cvd_known",
        "vwap_volume_coverage_certified",
        "poc_volume_coverage_certified",
        "admitted_to_f",
        "coverage_certified",
    }
    if not required_metric_fields <= _literals(calculator):
        raise ValueError("research_metric_uncertainty_semantics_mismatch")
    if projection is not None:
        if _constant(projection, "SCHEMA") != "tester.research-ui-observation/1":
            raise ValueError("research_ui_observation_version_mismatch")
        _signature(
            projection,
            "read_verified_ui_observation",
            {
                "category",
                "symbol",
                "start_ms",
                "end_ms",
                "interval_ms",
                "tick_size",
                "as_of_ms",
                "limit",
            },
        )
        _signature(projection, "project_source_observation", set())
        required = {
            "OBSERVED_UNVERIFIED",
            "UNKNOWN",
            "DENIED_UNKNOWN_COVERAGE",
            "NOT_AUTHORIZED",
            "NOT_REGISTERED",
            "metrics_digest",
            "source_receipts_digest",
        }
        if not required <= _literals(projection):
            raise ValueError("research_ui_not_fail_closed")
        # An unregistered read-only research module is NOT an HTTP route.
        null_route = any(
            isinstance(node, ast.Dict)
            and any(
                isinstance(key, ast.Constant)
                and key.value == "http_route"
                and isinstance(value, ast.Constant)
                and value.value is None
                for key, value in zip(node.keys, node.values)
            )
            for node in ast.walk(projection)
        )
        if not null_route:
            raise ValueError("research_ui_http_route_not_explicitly_unregistered")
    return {
        "data_contract": DATA_EXECUTION_VERSION,
        "research_contract": RESEARCH_METRICS_VERSION,
        "max_execution_events": 100,
        "history_completeness": "UNKNOWN",
        "admitted_to_f": False,
        "source_runtime_bound_to_ui": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--research-ui", type=Path)
    parser.add_argument("--data-pg-test", type=Path)
    parser.add_argument("--research-pg-test", type=Path)
    args = parser.parse_args()
    result = verify_paths(args.data, args.research, args.metrics, args.research_ui)
    if (args.data_pg_test is None) != (args.research_pg_test is None):
        raise ValueError("both_data_and_research_pg_sources_required")
    if args.data_pg_test is not None:
        result["pg_hostaddr_overlap"] = verify_shared_pg_hostaddr_test(
            args.data_pg_test, args.research_pg_test
        )
    print("PASS: exact DATA/RESEARCH source signatures (no runtime DATA admission)")
    print(result)


if __name__ == "__main__":
    main()
