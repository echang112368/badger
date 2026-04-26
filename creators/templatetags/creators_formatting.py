from django import template

register = template.Library()


@register.filter
def as_percentage(value, decimal_places=2):
    if value in (None, ""):
        return "-"

    try:
        places = int(decimal_places)
        numeric_value = float(value)
    except (TypeError, ValueError):
        return "-"

    return f"{numeric_value * 100:.{places}f}%"
