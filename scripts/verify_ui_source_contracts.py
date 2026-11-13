"""Read-only signature audit of exact DATA #72 and RESEARCH #71 source trees.

No import or execution of producer code, no checkout mutation and no source
network calls. The audit prevents silently upgrading an incompatible versioned
contract while independent Draft branches are being composed by MASTER.
"""

import argparse
import ast
from pathlib import Path

from src.api.capability_source_contracts import (
    DATA_EXECUTION_VERSION,
    RESEARCH_METRICS_VERSION,
)

DATA_FUNCTIONS = {
    "read_execution_events": {
        "category", "symbol", "start_ms", "end_ms", "limit"
    },
    "read_research_trades": {
        "category", "symbol", "start_ms", "end_ms", "limit"
    },
}
RESEARCH_FUNCTIONS = {
    "read_data_metrics": {
        "category", "symbol", "start_ms", "end_ms", "interval_ms",
        "tick_size", "as_of_ms", "limit"
    },
}
CALCULATION_FUNCTIONS = {
    "calculate": {
        "start_ms", "end_ms", "interval_ms", "tick_size"
    }
}


def _parse(file):
    return ast.parse(Path(file).read_text(encoding="utf-8"), filename=str(file))


def _constant(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name
                   for target in node.targets):
                return ast.literal_eval(node.value)
    raise ValueError("required_source_constant_missing:" + name)


def _signature(tree, name, required):
    fn = next(
        (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
         and node.name == name),
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


def verify_paths(data, research, metrics):
    producer = _parse(data)
    consumer = _parse(research)
    calculator = _parse(metrics)
    if _constant(producer, "VERSION") != DATA_EXECUTION_VERSION:
        raise ValueError("data_contract_version_mismatch")
    if _constant(consumer, "DATA_CONTRACT_VERSION") != DATA_EXECUTION_VERSION:
        raise ValueError("consumer_data_contract_version_mismatch")
    if _constant(calculator, "METHOD") != RESEARCH_METRICS_VERSION:
        raise ValueError("research_metrics_contract_version_mismatch")
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
        "exec_id", "quantity_unit", "event_ms", "observed_ms",
        "publication_ms", "source_receipts", "source_coverage",
        "source_native_linear_size", "source_native_inverse_contract",
    }
    if not required_event_fields <= _literals(producer):
        raise ValueError("data_provenance_time_or_quantity_semantics_mismatch")
    required_metric_fields = {
        "unknown_side_volume", "delta_known", "cvd_known",
        "vwap_volume_coverage_certified", "poc_volume_coverage_certified",
        "admitted_to_f", "coverage_certified",
    }
    if not required_metric_fields <= _literals(calculator):
        raise ValueError("research_metric_uncertainty_semantics_mismatch")
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
    args = parser.parse_args()
    result = verify_paths(args.data, args.research, args.metrics)
    print("PASS: exact DATA/RESEARCH source signatures (no runtime DATA admission)")
    print(result)


if __name__ == "__main__":
    main()
