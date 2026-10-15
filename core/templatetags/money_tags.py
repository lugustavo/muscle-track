from django import template

from core.billing import money as format_money

register = template.Library()


@register.filter
def money(value):
    """{{ amount|money }} -> '55,00 €' (the currency comes from CURRENCY_SYMBOL)."""
    return format_money(value)
