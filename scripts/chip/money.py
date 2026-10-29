"""One money formatter for every operator/Discord-facing string (Refs RAZ-16).

Operator format law: prices show to the cent, two decimals, always
(``$0.00``). Ledgers and JSON keep full precision; only display strings go
through :func:`usd`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


def usd(value: Any, *, sign: bool = False) -> str:
    """``0.004`` -> ``$0.00``; ``5.125`` -> ``$5.13``; ``-1.5`` -> ``-$1.50``.

    ``None`` / garbage render as ``$0.00`` (display never raises). ``sign=True``
    prefixes ``+`` on positive amounts (deltas).
    """
    try:
        d = Decimal(str(value if value is not None else 0)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError, TypeError):
        d = Decimal("0.00")
    if d.is_nan() or d.is_infinite():
        d = Decimal("0.00")
    if d == 0:
        d = Decimal("0.00")  # no "-$0.00"
    body = f"${abs(d):,.2f}"
    if d < 0:
        return "-" + body
    return ("+" + body) if sign and d > 0 else body
