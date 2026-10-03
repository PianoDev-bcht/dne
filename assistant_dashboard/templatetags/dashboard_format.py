"""Formats d'affichage français (espaces fines insécables, virgule décimale)."""
from django import template

from ..services import metrics

register = template.Library()
NNBSP = " "


@register.filter
def fr_int(value):
    if value is None:
        return "n.d."
    return f"{round(value):,}".replace(",", NNBSP)


@register.filter
def fr_signed_int(value):
    if value is None:
        return ""
    return ("+" if value > 0 else "") + fr_int(value).replace("-", "−")


@register.filter
def fr_pct(value):
    """Ratio -> « +6,6 % » ; None -> « n.d. » (base nulle ou période incomplète)."""
    if value is None:
        return "n.d."
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    return f"{sign}{abs(value) * 100:.1f}".replace(".", ",") + f"{NNBSP}%"


@register.filter
def fr_decimal(value):
    if value is None:
        return "n.d."
    return f"{value:.0f}" if value >= 100 else f"{value:.1f}".replace(".", ",")


@register.filter
def trend(value):
    """Classe CSS neutre : on montre le sens, sans jugement de valeur."""
    return metrics.trend(value)


@register.filter
def trend_arrow(value):
    return {"up": "↗", "down": "↘", "flat": "→"}[trend(value)] if value is not None else ""


@register.filter
def trend_word(value):
    """Verbe neutre : « en hausse », « en baisse », « stable » (mêmes seuils que les flèches)."""
    return {"up": "en hausse", "down": "en baisse", "flat": "stable"}[trend(value)] if value is not None else "n.d."


@register.filter
def fr_share(value):
    return "" if value is None else f"{round(value * 100)}{NNBSP}%"
