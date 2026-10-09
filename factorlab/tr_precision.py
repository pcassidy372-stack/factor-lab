"""Pure TR arithmetic/persistence policy; no I/O or historical repair.

Economic method labels remain unchanged. POLICY_VERSION identifies numerical
precision, not a new action/share-basis definition or per-row provenance.
"""
from datetime import date
from decimal import Decimal, Context, localcontext, ROUND_HALF_EVEN
import math

POLICY_VERSION = 'tr-level-decimal50-v1'
_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


class TRInputError(ValueError):
    pass


class TerminalTreatmentRequired(TRInputError):
    """Zero gross must not be clamped or extended as an ordinary positive index.

    This does not prove a genuine loss: review the input and existing terminal
    event path. The monthly consumer requires positive levels.
    """


def number(value, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise TRInputError('invalid_TR_number')
    try:
        value = Decimal(str(value))
    except Exception:
        raise TRInputError('invalid_TR_number') from None
    if not value.is_finite() or value < 0 or (not zero and value == 0):
        raise TRInputError('nonpositive_or_nonfinite_TR_input')
    return value


def day(value):
    if type(value) is date:
        return value
    if type(value) is not str:
        raise TRInputError('invalid_TR_date')
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise TRInputError('invalid_TR_date') from None
    if result.isoformat() != value:
        raise TRInputError('invalid_TR_date')
    return result


def persist(value):
    """NUMERIC-bound Decimal, without fixed scale; same state on restart.

    Existing consumers convert NUMERIC to float. Fail rather than store a level
    those consumers would see as zero/infinity. No clamp, epsilon or rebase.
    """
    with localcontext(_CONTEXT):
        result = +number(value)
    if not math.isfinite(float(result)) or float(result) <= 0:
        raise TRInputError('TR_consumer_range_exceeded')
    return result


def gross_return(previous_close, current_close, split_ratio=1, cash_distribution=0):
    previous = number(previous_close)
    current = number(current_close, zero=True)
    split = number(split_ratio)
    cash = number(cash_distribution, zero=True)
    with localcontext(_CONTEXT):
        gross = (current * split + cash) / previous
    if gross == 0:
        raise TerminalTreatmentRequired('zero_gross_requires_terminal_review')
    return number(gross)


def advance(level, gross, *, seed_date, price_date, next_date):
    seed, price, nxt = day(seed_date), day(price_date), day(next_date)
    if seed != price or nxt <= seed:
        raise TRInputError('misaligned_TR_seed_or_transition_date')
    with localcontext(_CONTEXT):
        return persist(number(level) * number(gross))


def gross_series(prices, dividends, splits):
    dates = sorted(prices)
    if not dates:
        raise TRInputError('empty_TR_prices')
    for d in dates:
        day(d)
        number(prices[d])
    return {b: gross_return(prices[a], prices[b], splits.get(b, 1), dividends.get(b, 0))
            for a, b in zip(dates, dates[1:])}


def build_levels(prices, dividends, splits, *, oracle_gross=None):
    """Preserve the existing 100 seed and explicitly selected oracle overrides.

    Selection thresholds/window/oracle policy belong to the caller. This helper
    neither discovers overrides nor invents a substitute oracle.
    """
    gross = gross_series(prices, dividends, splits)
    overrides = {} if oracle_gross is None else oracle_gross
    if not set(overrides) <= set(gross):
        raise TRInputError('oracle_override_outside_transitions')
    dates = sorted(prices)
    level = persist(100)
    rows = [(dates[0], level)]
    for a, b in zip(dates, dates[1:]):
        level = advance(level, overrides.get(b, gross[b]), seed_date=a, price_date=a, next_date=b)
        rows.append((b, level))
    return rows
