"""Audit trail helpers: who changed what in a student's file."""
from .models import StudentEvent

# Sensitive free text: the history records THAT it changed, never the content.
SENSITIVE_FIELDS = {'health_notes', 'notes'}


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
    """{field: [before, after]} for the fields of a bound, valid form that really changed."""
    changes = {}
    for name in form.changed_data:
        if name in ('confirm_duplicate', 'status'):
            continue
        field = form.fields[name]
        before = form.initial.get(name, field.initial)
        after = form.cleaned_data.get(name)
        if _plain(before) == _plain(after):
            continue
        if name in SENSITIVE_FIELDS:
            changes[name] = [None, None]
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
