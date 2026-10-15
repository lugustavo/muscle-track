"""Audit trail helpers: who changed what in a student's file."""
from .models import Plan, StudentEvent

# Sensitive free text: the history records THAT it changed, never the content.
SENSITIVE_FIELDS = {'health_notes', 'notes', 'hourly_rate', 'plan_price'}  # also: negotiated money


def log_event(person, kind, actor=None, summary='', changes=None):
    return StudentEvent.objects.create(
        person=person, kind=kind, actor=actor if getattr(actor, 'pk', None) else None,
        summary=summary[:300], changes=changes or {})


def _plain(value):
    if value is None or value == '':
        return ''
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if hasattr(value, 'name') and not isinstance(value, str):
        return value.name or ''
    return str(value)


def changed_fields(form):
    """{field: [before, after]} for the fields of a bound, valid form that really changed.

    Compares the cleaned values (not only the posted ones), so a value the form filled in itself (the contract copying
    the plan's name and price) is recorded too."""
    changes = {}
    for name in form.cleaned_data:
        if name in ('confirm_duplicate', 'status'):
            continue
        field = form.fields[name]
        before = form.initial.get(name, field.initial)
        after = form.cleaned_data.get(name)
        if _plain(before) == _plain(after):
            continue
        if name in SENSITIVE_FIELDS:
            changes[name] = [None, None]
        elif name == 'plan':
            changes[name] = [Plan.objects.filter(pk=before).values_list('name', flat=True).first() or '',
                             after.name if after else '']
        elif name == 'avatar':
            changes[name] = ['', 'nova foto' if after else 'removida']
        else:
            changes[name] = [_plain(before), _plain(after)]
    return changes


def log_profile_edit(person, form, actor):
    """Record the edit if anything changed; returns the changes."""
    changes = changed_fields(form)
    if changes:
        labels = [str(form.fields[n].label or n) for n in changes]
        log_event(person, StudentEvent.Kind.PROFILE_EDIT, actor,
                  'Alterou: ' + ', '.join(labels), changes)
    return changes
