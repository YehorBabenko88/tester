"""Pure /2 simulation economics; NOT an F execution/admission interface.

Every calculation requires an explicitly declared collateral/settlement unit.
All inputs are decimal strings, never floats. Inverse is coin-settled, while
spot/linear formulas below are quote-settled. No margin/liquidation engine is
implemented here and no production interface calls these functions.

These formulas operate on *hypothetical* fills; source historical visibility,
execution likelihood, latency and fees charged by a venue are not established.
"""
from src.analytics.exact import number, decimal
from src.analytics.core import quantity_unit

CONTRACT = "tester.simulation-economics/2"
SETTLEMENT = {"spot": "quote", "linear": "quote", "inverse": "base"}
SIZE_UNITS = {"spot": "base", "linear": "base", "inverse": "contract"}


def _signed(side):
    if side == "long":
        return 1
    if side == "short":
        return -1
    raise ValueError("unsupported_position_side")


def hypothetical_mark(category, side, quantity, entry_price, mark_price,
                      *, leverage, contract_quote_value="1"):
    """Versioned hypothetical mark and entry-margin estimate.

    Does not liquidate, settle, certify a source mark, or admit a strategy.
    Exact rational calculation is serialized with the canonical /2 helper.
    Quote-settled spot/linear: sign*base_qty*(mark-entry).
    Base-settled inverse: sign*contracts*face*(1/entry-1/mark).
    The initial margin is a static estimate, NOT a maintenance threshold:
    inverse quote contract face / entry / leverage, linear base*entry/leverage,
    spot total cost of unleveraged base position.
    """
    if category not in SETTLEMENT:
        raise ValueError("unsupported_category")
    sign = _signed(side)
    if category == "spot" and sign != 1:
        raise ValueError("spot_short_requires_borrow_model")
    q = number(quantity, positive=True)
    p = number(entry_price, positive=True)
    mark = number(mark_price, positive=True)
    lev = number(leverage, positive=True)
    face = number(contract_quote_value, positive=True)
    if face != 1:
        raise ValueError("contract_face_not_pinned")
    if category == "spot" and lev != 1:
        raise ValueError("spot_leverage_requires_borrow_model")
    if category == "inverse":
        unrealized = sign * q * face * (1 / p - 1 / mark)
        initial_margin = q * face / p / lev
    else:
        unrealized = sign * q * (mark - p)
        initial_margin = q * p / lev
    return {
        "contract_version": CONTRACT,
        "category": category,
        "quantity_unit": SIZE_UNITS[category],
        "settlement_unit": SETTLEMENT[category],
        "unrealized_pnl": decimal(unrealized),
        "initial_margin_estimate": decimal(initial_margin),
        "maintenance_margin": None,
        "liquidation_price": None,
        "liquidation_status": "NOT_MODELED",
        "source_certified": False,
        "admitted_to_f": False,
    }


def hypothetical_fill(category, side, quantity, entry_price, exit_price,
                      *, fee_rate="0", entry_funding_rates=(),
                      contract_quote_value="1"):
    """Compute realized long/short economics for one hypothetical round trip.

    Spot short is refused: borrow/locate costs are not modeled. Derivatives
    funding uses an explicitly supplied sequence of signed rates sampled at
    actual observed funding boundaries; the caller must pin that evidence.
    For inverse, each funding observation is (rate, mark_price) and must be
    recorded in base settlement: sign * contracts*face/mark*rate.
    For linear, funding is sign * base_qty*mark*rate in quote.
    Fee notional is absolute value at each fill and deducted from PnL.
    This does not compute risk-available equity, initial/maintenance margin,
    liquidation, fills from OHLC, or unrealized portfolio valuation.
    """
    # Experimental calculations are not an executable simulator contract.
    # The caller must separately prove historical funding and price knowledge.
    if category not in SETTLEMENT:
        raise ValueError("unsupported_category")
    sign = _signed(side)
    if category == "spot" and sign < 0:
        raise ValueError("spot_short_requires_borrow_model")
    qty = number(quantity, positive=True)
    entry = number(entry_price, positive=True)
    exit = number(exit_price, positive=True)
    fee = number(fee_rate)
    if fee < 0 or fee > 1:
        raise ValueError("invalid_fee_rate")
    face = number(contract_quote_value, positive=True)
    if category != "inverse" and face != 1:
        raise ValueError("noninverse_contract_face_not_supported")
    # The V5 inverse BTCUSD contract is represented by a one-USD face value;
    # a different face requires a new verified instrument contract.
    if category == "inverse" and face != 1:
        raise ValueError("inverse_contract_face_not_pinned")
    if category == "inverse":
        gross = sign * qty * face * (1 / entry - 1 / exit)
        entry_fee = fee * qty * face / entry
        exit_fee = fee * qty * face / exit
    else:
        gross = sign * qty * (exit - entry)
        entry_fee = fee * qty * entry
        exit_fee = fee * qty * exit
    funding = 0
    if not isinstance(entry_funding_rates, (tuple, list)):
        raise ValueError("funding_events_collection_required")
    for record in entry_funding_rates:
        if not isinstance(record, (list, tuple)) or len(record) != 2:
            raise ValueError("funding_event_rate_and_mark_required")
        rate, mark = number(record[0]), number(record[1], positive=True)
        if abs(rate) > 1:
            raise ValueError("invalid_funding_rate")
        if category == "spot":
            raise ValueError("spot_funding_unsupported")
        funding += sign * qty * face * rate / mark if category == "inverse" else sign * qty * mark * rate
    net = gross - entry_fee - exit_fee - funding
    return {
        "contract_version": CONTRACT,
        "category": category,
        "quantity_unit": SIZE_UNITS[category],
        "analytics_quantity_unit": quantity_unit(category),
        "settlement_unit": SETTLEMENT[category],
        "side": side,
        "gross_pnl": decimal(gross),
        "entry_fee": decimal(entry_fee),
        "exit_fee": decimal(exit_fee),
        "funding_cost": decimal(funding),
        "net_pnl": decimal(net),
        "executed": False,
        "source_certified": False,
        "admitted_to_f": False,
        "margin_status": "NOT_MODELED",
        "liquidation_status": "NOT_MODELED",
    }
