"""Decimal inputs, exact finite arithmetic, deterministic 50-digit ratios."""
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from fractions import Fraction


def number(value, *, positive=False):
    if isinstance(value, (float, bool)):
        raise ValueError('Use decimal strings, never binary floats')
    try:
        result = Decimal(value)
    except Exception as exc:
        raise ValueError('Invalid decimal') from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError('Expected finite positive decimal')
    return Fraction(result)


def decimal(value):
    """Serialize finite base-10 rationals exactly; repeating ratios at 50 digits."""
    value = Fraction(value)
    den = value.denominator
    twos = fives = 0
    while den % 2 == 0:
        den //= 2; twos += 1
    while den % 5 == 0:
        den //= 5; fives += 1
    if den == 1:
        scale = max(twos, fives)
        coefficient = value.numerator * 2**(scale-twos) * 5**(scale-fives)
        digits = tuple(int(c) for c in str(abs(coefficient)))
        result = Decimal((int(coefficient < 0), digits, -scale))
    else:
        with localcontext() as ctx:
            ctx.prec = 50
            ctx.rounding = ROUND_HALF_EVEN
            result = Decimal(value.numerator) / Decimal(value.denominator)
    text = format(result, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def total(values):
    return sum((number(v) for v in values), Fraction())
