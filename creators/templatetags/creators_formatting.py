from __future__ import annotations

import math

from django import template

register = template.Library()


def _fmt_sigfigs(v: float, sigfigs: int) -> str:
    """Format v to sigfigs significant figures and append '%'."""
    if v == 0:
        decimals = max(sigfigs - 1, 0)
        return f"{v:.{decimals}f}%"
    magnitude = math.floor(math.log10(abs(v)))
    decimals = max(sigfigs - magnitude - 1, 0)
    return f"{v:.{decimals}f}%"


@register.filter
def percent_sigfigs(value: float | int | None, sigfigs: int = 4) -> str:
    """Convert a decimal fraction (0.1665) to a percentage string with N sig figs."""
    if value in (None, ""):
        return "-"
    try:
        v = float(value) * 100
        sigfigs = int(sigfigs)
    except (TypeError, ValueError):
        return "-"
    return _fmt_sigfigs(v, max(sigfigs, 1))


@register.filter
def pct_sigfigs(value: float | int | None, sigfigs: int = 4) -> str:
    """Format a value already expressed as a percentage (25.44) to N sig figs."""
    if value in (None, ""):
        return "-"
    try:
        v = float(value)
        sigfigs = int(sigfigs)
    except (TypeError, ValueError):
        return "-"
    return _fmt_sigfigs(v, max(sigfigs, 1))
