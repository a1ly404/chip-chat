"""chip.money.usd: two decimals everywhere money is shown (Refs RAZ-16)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chip.money import usd  # noqa: E402


@pytest.mark.parametrize(
    "value,expected",
    [(0, "$0.00"), (None, "$0.00"), ("junk", "$0.00"), (0.0001, "$0.00"), (-0.001, "$0.00"),
     (0.005, "$0.01"), (1.2345, "$1.23"), (1234.5, "$1,234.50"), (-2.5, "-$2.50")],
)
def test_usd_two_decimals(value, expected) -> None:
    assert usd(value) == expected


def test_usd_sign() -> None:
    assert usd(1, sign=True) == "+$1.00"
