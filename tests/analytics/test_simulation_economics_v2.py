"""Golden economics vectors; isolated simulator never authorizes real F jobs."""
from fractions import Fraction
import pytest

from src.analytics.exact import number
from src.analytics.simulation_economics import hypothetical_fill, CONTRACT


@pytest.mark.parametrize("category,side,gross,unit", [
    ("spot", "long", "100", "quote"),
    ("linear", "long", "100", "quote"),
    ("linear", "short", "-100", "quote"),
    ("inverse", "long", "1/1100", "base"),
    ("inverse", "short", "-1/1100", "base"),
])
def test_exact_long_short_realized_pnl(category, side, gross, unit):
    r = hypothetical_fill(category, side, "10", "100", "110")
    if category == "inverse":
        # 10 * (1/100 - 1/110) = 1/1100 (base settlement).
        expected = Fraction(1, 1100) * (1 if side == "long" else -1)
        assert abs(number(r["gross_pnl"]) - expected) < Fraction(1, 10**48)
    else:
        assert r["gross_pnl"] == gross
    assert r["settlement_unit"] == unit
    assert r["contract_version"] == CONTRACT
    assert r["admitted_to_f"] is False
    assert r["executed"] is False
    assert r["margin_status"] == "NOT_MODELED"


def test_inverse_fees_funding_and_net_golden():
    # 100 USD face contracts, 100 -> 200 USD price: +0.5 BTC gross.
    # fees 0.1% each = 0.001 + 0.0005 BTC.
    # funding at 100 mark, rate 0.01, long pays 0.01 BTC.
    r = hypothetical_fill("inverse", "long", "100", "100", "200",
                          fee_rate="0.001", entry_funding_rates=[("0.01", "100")])
    assert r["gross_pnl"] == "0.5"
    assert r["entry_fee"] == "0.001"
    assert r["exit_fee"] == "0.0005"
    assert r["funding_cost"] == "0.01"
    assert r["net_pnl"] == "0.4885"
    assert r["quantity_unit"] == "contract"
    assert r["analytics_quantity_unit"] == "quote"
    assert r["settlement_unit"] == "base"


def test_linear_short_funding_receipt_and_costs():
    # 2 base units short, entry 100 -> exit 90, gross +20 quote.
    # 0.1% fee each = 0.2+0.18; positive 1% funding paid by
    # long and received by short = -2 quote funding cost.
    r = hypothetical_fill("linear", "short", "2", "100", "90",
                          fee_rate="0.001", entry_funding_rates=[("0.01", "100")])
    assert r["gross_pnl"] == "20"
    assert r["funding_cost"] == "-2"
    assert r["net_pnl"] == "21.62"


@pytest.mark.parametrize("category,side,q,p,e,fee,funding", [
    ("inverse", "long", "0", "100", "200", "0", ()),
    ("linear", "long", "1", "0", "200", "0", ()),
    ("spot", "short", "1", "100", "200", "0", ()),
    ("inverse", "long", "1", "100", "200", "-0.001", ()),
    ("linear", "long", "1", "100", "200", "NaN", ()),
    ("inverse", "long", "1", "100", "200", "0", (("1.5", "100"),)),
    ("spot", "long", "1", "100", "200", "0", (("0.01", "100"),)),
])
def test_unsafe_or_invalid_economics_rejected(category, side, q, p, e, fee, funding):
    with pytest.raises(ValueError):
        hypothetical_fill(category, side, q, p, e, fee_rate=fee, entry_funding_rates=funding)


def test_inverse_short_sign_and_float_refusal():
    r = hypothetical_fill("inverse", "short", "100", "100", "200")
    assert r["gross_pnl"] == "-0.5"
    with pytest.raises(ValueError):
        hypothetical_fill("inverse", "long", 100.0, "100", "200")
    with pytest.raises(ValueError):
        hypothetical_fill("inverse", "long", "100", "100", "200",
                          contract_quote_value="10")


def test_no_public_strategy_lab_interface_or_margin_pretense():
    r = hypothetical_fill("inverse", "long", "100", "100", "200")
    assert r["liquidation_status"] == "NOT_MODELED"
    assert r["source_certified"] is False
    assert r["admitted_to_f"] is False
    assert "initial_margin" not in r
    assert "maintenance_margin" not in r
    assert "fill_time" not in r
