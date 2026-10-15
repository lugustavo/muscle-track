from django import template

from core.calendar_feed import google_template_url, trainer_title

register = template.Library()


@register.simple_tag(takes_context=True)
def google_event_url(context, appointment):
    """"Add to Google Calendar" link for one class, titled the way the trainer chose (`calendar_style` in context)."""
    summary = trainer_title(appointment, context.get('calendar_style', 'first'))
    details = f'{appointment.duration_minutes} min'
    if appointment.notes:
        details += f'\n{appointment.notes}'
    return google_template_url(appointment, summary, details)
