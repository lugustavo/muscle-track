"""A student asks to move or cancel a class; the trainer decides. The rules live here, the views only call them."""
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import packages
from .agenda import MoveError, move_appointment
from .models import Appointment, AppointmentRequest

Kind = AppointmentRequest.Kind
Status = AppointmentRequest.Status


class RequestError(Exception):
    """A request that cannot be made or decided; the message is meant for the person who clicked."""


def notice_hours():
    return max(int(getattr(settings, 'AGENDA_NOTICE_HOURS', 24)), 0)


def deadline(appointment):
    """The last moment the student can still ask (None = no deadline)."""
    hours = notice_hours()
    return appointment.starts_at - timedelta(hours=hours) if hours else None


def request_block(appointment, now=None):
    """Why this class cannot get a new request, or '' when it can."""
    now = now or timezone.now()
    if appointment.status != Appointment.Status.SCHEDULED:
        return 'Esta aula já não está marcada.'
    if appointment.starts_at <= now:
        return 'Esta aula já passou.'
    limit = deadline(appointment)
    if limit and now > limit:
        return f'O prazo para pedir pelo app terminou ({notice_hours()} h antes da aula). Fale direto com a sua personal.'
    if any(r.status == Status.PENDING for r in appointment.requests.all()):   # .all() so a prefetch is reused
        return 'Você já tem um pedido aguardando resposta para esta aula.'
    return ''


def create_request(person, appointment, kind, proposed=None, message=''):
    """Record the student's request. `proposed` (aware datetime) is required to reschedule."""
    if appointment.person_id != person.pk:
        raise RequestError('Aula não encontrada.')
    block = request_block(appointment)
    if block:
        raise RequestError(block)
    if kind == Kind.RESCHEDULE:
        if proposed is None:
            raise RequestError('Informe o dia e o horário que você prefere.')
        if proposed <= timezone.now():
            raise RequestError('Escolha um horário no futuro.')
        if proposed == appointment.starts_at:
            raise RequestError('A aula já está neste horário.')
        if Appointment.objects.filter(person=person, starts_at=proposed).exclude(
                status__in=(Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED)).exists():
            raise RequestError('Você já tem uma aula neste horário.')
    elif kind == Kind.CANCEL:
        proposed = None
    else:
        raise RequestError('Pedido inválido.')
    try:
        with transaction.atomic():
            return AppointmentRequest.objects.create(
                appointment=appointment, kind=kind, proposed_starts_at=proposed, message=message.strip()[:200])
    except IntegrityError:
        raise RequestError('Você já tem um pedido aguardando resposta para esta aula.')


def withdraw(person, request_obj):
    if request_obj.appointment.person_id != person.pk or request_obj.status != Status.PENDING:
        raise RequestError('Este pedido não pode mais ser retirado.')
    request_obj.status = Status.WITHDRAWN
    request_obj.decided_at = timezone.now()
    request_obj.save(update_fields=['status', 'decided_at'])


def _close(request_obj, status, note=''):
    request_obj.status = status
    request_obj.decided_at = timezone.now()
    request_obj.decision_note = note.strip()[:200]
    request_obj.save(update_fields=['status', 'decided_at', 'decision_note'])


def approve(request_obj):
    """Apply what the student asked: move the class to the proposed time, or cancel it."""
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().select_related('person').get(pk=request_obj.appointment_id)
        if request_obj.status != Status.PENDING:
            raise RequestError('Este pedido já foi respondido.')
        if appointment.status != Appointment.Status.SCHEDULED:
            raise RequestError('A aula já mudou de situação; recuse o pedido ou ajuste a aula na agenda.')
        if request_obj.kind == Kind.CANCEL:
            appointment.status = Appointment.Status.CANCELLED
            appointment.status_changed_at = timezone.now()
            appointment.cancelled_late = packages.late_cancel(appointment.starts_at)
            appointment.save(update_fields=['status', 'status_changed_at', 'cancelled_late'])
            packages.sync(appointment.person)
        else:
            try:
                move_appointment(appointment, request_obj.proposed_starts_at)
            except MoveError as error:
                raise RequestError(str(error))
        _close(request_obj, Status.APPROVED)


def decline(request_obj, note=''):
    if request_obj.status != Status.PENDING:
        raise RequestError('Este pedido já foi respondido.')
    _close(request_obj, Status.DECLINED, note)


def pending_for_trainer():
    """Open requests whose class is still marked and still ahead, oldest first."""
    return AppointmentRequest.objects.filter(
        status=Status.PENDING, appointment__status=Appointment.Status.SCHEDULED,
        appointment__starts_at__gte=timezone.now()).select_related('appointment__person')


def label(request_obj):
    """What the student sees: a pending request whose class changed by itself is "handled"."""
    if request_obj.status == Status.PENDING and (
            request_obj.appointment.status != Appointment.Status.SCHEDULED
            or request_obj.appointment.starts_at < timezone.now()):
        return 'Encerrado'
    return request_obj.get_status_display()
