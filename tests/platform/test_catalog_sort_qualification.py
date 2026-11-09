"""Pure cached-page regressions: qualification must precede global pagination."""

from copy import deepcopy

import pytest

from src.api.market_catalog import CatalogService
from test_market_catalog import Source


def catalog():
    service = CatalogService(Source(), clock=lambda: 1000)
    assert service.refresh("linear")
    snapshot = service._snapshots["linear"]
    template = snapshot["rows"][0]
    values = [
        ("A_UNKNOWN_HIGH", "999", "UNKNOWN", "fixture", None),
        ("B_UNKNOWN_LOW", "-999", "UNKNOWN", "fixture", None),
        ("C_NO_SOURCE", "999", "OBSERVED", None, 1000000),
        ("D_EXPONENT", "1e3", "OBSERVED", "fixture", 1000000),
        ("E_TOO_LONG", "9" * 101, "OBSERVED", "fixture", 1000000),
        ("F_FLOAT", 1000.0, "OBSERVED", "fixture", 1000000),
        ("G_BLANK_SOURCE", "999", "OBSERVED", "", 1000000),
        ("VALID_LOW", "-3", "OBSERVED", "fixture", 1000000),
        ("VALID_MID", "1", "OBSERVED", "fixture", 1000000),
        ("VALID_STALE", "2", "STALE", "fixture", 1000000),
    ]
    rows = []
    for symbol, value, state, source, stamp in values:
        row = deepcopy(template)
        row["symbol"] = symbol
        row["metrics"]["price"] = dict(
            value=value,
            state=state,
            source=source,
            source_time_ms=stamp,
            reason=(
                "ticker_refresh_failed" if state == "STALE"
                else "source_time_missing" if state == "UNKNOWN" else None
            ),
        )
        rows.append(row)
    snapshot["rows"] = rows
    return service


@pytest.mark.parametrize(
    "direction,expected",
    [
        ("asc", ["VALID_LOW", "VALID_MID", "VALID_STALE"]),
        ("desc", ["VALID_STALE", "VALID_MID", "VALID_LOW"]),
    ],
)
def test_render_unavailable_values_sort_after_observed_and_stale(direction, expected):
    service = catalog()
    original = deepcopy(service._snapshots["linear"])
    result = service.query("linear", sort="price", direction=direction, limit=50)
    assert [row["symbol"] for row in result["rows"][:3]] == expected
    assert [row["symbol"] for row in result["rows"][3:]] == [
        "A_UNKNOWN_HIGH", "B_UNKNOWN_LOW", "C_NO_SOURCE", "D_EXPONENT",
        "E_TOO_LONG", "F_FLOAT", "G_BLANK_SOURCE",
    ]
    assert result["rows"][0]["metrics"]["price"]["value"] == (
        "-3" if direction == "asc" else "2"
    )
    stale = next(row for row in result["rows"] if row["symbol"] == "VALID_STALE")
    assert stale["metrics"]["price"]["state"] == "STALE"
    assert service._snapshots["linear"] == original


def test_metric_qualification_happens_before_global_page_slice():
    service = catalog()
    pages = [
        service.query("linear", sort="price", direction="desc", offset=offset, limit=2)
        for offset in (0, 2)
    ]
    assert pages[0]["total"] == pages[1]["total"] == 10
    assert pages[0]["revision"] == pages[1]["revision"]
    assert [row["symbol"] for page in pages for row in page["rows"]] == [
        "VALID_STALE", "VALID_MID", "VALID_LOW", "A_UNKNOWN_HIGH",
    ]


def test_text_and_unsorted_modes_do_not_apply_metric_qualification():
    service = catalog()
    expected = sorted(row["symbol"] for row in service._snapshots["linear"]["rows"])
    for sort, direction in [("price", "none"), ("symbol", "asc")]:
        result = service.query("linear", sort=sort, direction=direction)
        assert [row["symbol"] for row in result["rows"]] == expected
