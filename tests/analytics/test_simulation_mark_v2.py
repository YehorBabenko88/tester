"""Independent exact-payoff golden tests for the research-only v2 mark seam."""
from fractions import Fraction
import pytest

from src.analytics.exact import number
from src.analytics.simulation_economics import hypothetical_mark


@pytest.mark.parametrize("category,side,gross,margin,unit", [
    ("spot", "long", "100", "1000", "quote"),
    ("linear", "long", "100", "200", "quote"),
    ("linear", "short", "-100", "200", "quote"),
    ("inverse", "long", "0.0090909090909090909090909090909090909090909090909091",
     "0.02", "base"),
    ("inverse", "short", "-0.0090909090909090909090909090909090909090909090909091",
     "0.02", "base"),
])
def test_mark_golden(category, side, gross, margin, unit):
    leverage = "1" if category == "spot" else "5"
    result = hypothetical_mark(category, side, "10", "100", "110", leverage=leverage)
    if category == "inverse":
        expected = Fraction(1,110) * (1 if side == "long" else -1)
        assert abs(number(result["unrealized_pnl"]) - expected) < Fraction(1,10**48)
    else:
        assert result["unrealized_pnl"] == gross
    assert result["initial_margin_estimate"] == margin
    assert result["settlement_unit"] == unit
    assert result["liquidation_status"] == "NOT_MODELED"
    assert result["admitted_to_f"] is False


@pytest.mark.parametrize("category,side,q,p,mark,leverage", [
    ("inverse", "long", "0", "100", "200", "5"),
    ("inverse", "long", "1", "100", "0", "5"),
    ("inverse", "long", "1", "100", "200", "0"),
    ("inverse", "long", "1", "100", "200", "NaN"),
    ("spot", "short", "1", "100", "200", "1"),
    ("spot", "long", "1", "100", "200", "2"),
    ("linear", "long", 1.0, "100", "200", "2"),
])
def test_mark_unsafe_cases(category, side, q, p, mark, leverage):
    with pytest.raises(ValueError):
        hypothetical_mark(category, side, q, p, mark, leverage=leverage)


def test_mark_requires_pinned_contract_face():
    with pytest.raises(ValueError, match="contract_face_not_pinned"):
        hypothetical_mark("inverse","long","100","100","200",
                          leverage="10",contract_quote_value="10")


def test_no_f_or_source_upgrade():
    out = hypothetical_mark("inverse","long","100","100","200",leverage="10")
    assert out["unrealized_pnl"] == "0.5"
    assert out["initial_margin_estimate"] == "0.1"
    assert out["maintenance_margin"] is None
    assert out["liquidation_price"] is None
    assert not out["source_certified"] and not out["admitted_to_f"]


def test_inverse_exact_reciprocal_pnl_is_not_decimal_multiplication():
    # A 100 USD face long from 100 to 200 earns exactly 1/2 base coin,
    # whereas a linear 100-base contract would earn 10_000 quote.
    inverse = hypothetical_mark("inverse", "long", "100", "100", "200", leverage="10")
    linear = hypothetical_mark("linear", "long", "100", "100", "200", leverage="10")
    assert inverse["unrealized_pnl"] == "0.5"
    assert inverse["initial_margin_estimate"] == "0.1"
    assert inverse["settlement_unit"] == "base"
    assert linear["unrealized_pnl"] == "10000"
    assert linear["initial_margin_estimate"] == "1000"
    assert linear["settlement_unit"] == "quote"
    assert not inverse["admitted_to_f"] and not linear["admitted_to_f"]


def test_inverse_positive_funding_and_fee_must_not_be_inferred_from_mark():
    # Unrealized PnL is explicitly gross of cash funding and opening/closing
    # fees; no optimizer may consume it as a net trade result.
    from src.analytics.simulation_economics import hypothetical_fill
    gross = hypothetical_mark("inverse", "long", "100", "100", "200", leverage="10")
    settled = hypothetical_fill("inverse", "long", "100", "100", "200",
                               fee_rate="0.001", entry_funding_rates=[("0.01", "100")])
    assert gross["unrealized_pnl"] == settled["gross_pnl"] == "0.5"
    assert settled["funding_cost"] == "0.01"
    assert settled["entry_fee"] == "0.001"
    assert settled["exit_fee"] == "0.0005"
    assert settled["net_pnl"] == "0.4885"
    assert settled["admitted_to_f"] is False
