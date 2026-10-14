"""LGPD consent: which consents exist, which are required, and the current state."""
from django.utils import timezone

from .models import ConsentRecord, StudentEvent

# Bump to ask every student to accept the (changed) texts again.
CURRENT_VERSION = '2026-10'

REQUIRED_KINDS = (ConsentRecord.Kind.TERMS, ConsentRecord.Kind.HEALTH_DATA)
OPTIONAL_KINDS = (ConsentRecord.Kind.PHOTOS,)
ALL_KINDS = REQUIRED_KINDS + OPTIONAL_KINDS

DESCRIPTIONS = {
    ConsentRecord.Kind.TERMS: 'Li e aceito os termos de uso e a política de privacidade.',
    ConsentRecord.Kind.HEALTH_DATA: (
        'Autorizo o registro e o uso dos meus dados de saúde (lesões, restrições, avaliações físicas) '
        'pela minha personal trainer, para montar e acompanhar meus treinos.'),
    ConsentRecord.Kind.PHOTOS: 'Autorizo o registro de fotos de evolução, que só eu e minha personal vemos.',
}


def current_consents(person):
    """{kind: latest ConsentRecord or None}."""
    latest = {}
    for record in person.consents.all():  # ordered newest first
        latest.setdefault(record.kind, record)
    return {kind: latest.get(kind) for kind in ALL_KINDS}


def is_granted(record):
    return bool(record and record.granted and record.version == CURRENT_VERSION)


def missing_required(person):
    state = current_consents(person)
    return [kind for kind in REQUIRED_KINDS if not is_granted(state[kind])]


def has_current_consent(person):
    return not missing_required(person)


def record_consent(person, kind, granted, *, source=ConsentRecord.Source.STUDENT, actor=None, at=None):
    record = ConsentRecord.objects.create(
        person=person, kind=kind, version=CURRENT_VERSION, granted=granted, source=source,
        recorded_by=actor if getattr(actor, 'pk', None) else None, at=at or timezone.now())
    label = ConsentRecord.Kind(kind).label
    StudentEvent.objects.create(
        person=person, kind=StudentEvent.Kind.CONSENT, actor=record.recorded_by,
        summary=f'{"Concedeu" if granted else "Retirou"} consentimento: {label}'
                + (' (registrado pela personal)' if source == ConsentRecord.Source.TRAINER else ''),
        changes={'kind': kind, 'granted': granted, 'version': CURRENT_VERSION})
    return record
