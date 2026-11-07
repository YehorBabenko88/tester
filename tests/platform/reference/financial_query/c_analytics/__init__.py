"""Stable C integration entry points. All wire numeric values are strings."""
from .footprint import aggregate as legacy_aggregate
from .core import (SourceConflict, normalize_trade, aggregate, footprint,
    price_levels, custom_candles, interval_bounds, trade_notional)
from .statistics import candle_statistics
from .comparison import compare_candles
