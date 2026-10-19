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


# The icon and colour of each kind of notice in the notice centre (core/notifications.py kinds)
_NOTICE_LOOK = {
    'request_new': ('bi-hand-index', 'warn'), 'request_answered': ('bi-reply', 'info'),
    'class_moved': ('bi-calendar2-event', 'warn'), 'class_cancelled': ('bi-calendar-x', 'bad'),
    'move_undone': ('bi-arrow-counterclockwise', 'info'), 'class_reminder': ('bi-alarm', 'info'),
    'plan_expiry': ('bi-hourglass-split', 'warn'), 'package_low': ('bi-box-seam', 'warn'),
    'package_attention': ('bi-box-seam', 'warn'),
}


@register.filter
def notice_icon(kind):
    return _NOTICE_LOOK.get(kind, ('bi-bell', ''))[0]


@register.filter
def notice_tone(kind):
    return _NOTICE_LOOK.get(kind, ('bi-bell', ''))[1]
