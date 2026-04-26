from __future__ import annotations

import math

from django import template

register = template.Library()


@register.filter
def percent_sigfigs(value: float | int | None, sigfigs: int = 4) -> str:
    if value in (None, ""):
        return "-"

    try:
        percentage_value = float(value) * 100
        sigfigs = int(sigfigs)
    except (TypeError, ValueError):
        return "-"

    if sigfigs < 1:
        sigfigs = 1

    if percentage_value == 0:
        decimals = max(sigfigs - 1, 0)
        return f"{percentage_value:.{decimals}f}%"

    magnitude = math.floor(math.log10(abs(percentage_value)))
    decimals = max(sigfigs - magnitude - 1, 0)
    return f"{percentage_value:.{decimals}f}%"
