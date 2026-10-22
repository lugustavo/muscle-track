from django import template
from django.utils.translation import gettext as _

from core.calendar_feed import google_template_url, trainer_title

register = template.Library()


@register.simple_tag(takes_context=True)
def google_event_url(context, appointment):
    """"Add to Google Calendar" link for one class, titled the way the trainer chose (`calendar_style` in context)."""
    summary = trainer_title(appointment, context.get('calendar_style', 'first'))
    details = _('%(minutes)s min') % {'minutes': appointment.duration_minutes}
    if appointment.notes:
        details += f'\n{appointment.notes}'
    return google_template_url(appointment, summary, details)


@register.filter
def forecast_hours(minutes):
    """90 -> '1 h 30', 120 -> '2 h', 0 -> '0 h' (booked or free time in the forecast)."""
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return ''
    hours, rest = divmod(max(minutes, 0), 60)
    return f'{hours} h {rest:02d}' if rest else f'{hours} h'
