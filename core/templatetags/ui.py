from urllib.parse import urlencode

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def qs(context, **changes):
    """The current query string with `changes` applied (an empty value removes the key); the page number is dropped,
    because a changed filter or layout starts again from the first page. Used by the layout toggle and the filter tags."""
    params = [(key, value) for key, value in context['request'].GET.items() if key not in changes and key != 'page']
    params += [(key, value) for key, value in changes.items() if value not in (None, '')]
    return '?' + urlencode(params) if params else '?'


@register.simple_tag(takes_context=True)
def qs_remove(context, *keys):
    """The current query string without these filters (and without the page number)."""
    params = [(key, value) for key, value in context['request'].GET.items() if key not in keys and key != 'page']
    return '?' + urlencode(params) if params else '?'


_TONES = {'success': 'ok', 'warning': 'warn', 'danger': 'bad', 'info': 'info', 'primary': 'brand'}


@register.filter
def tone(value):
    """The `ui-badge` tone for a Bootstrap color name ("success" -> "ok"); unknown or neutral names give no tone."""
    return _TONES.get(value, '')
