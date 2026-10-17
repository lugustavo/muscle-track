"""Maintaining a sold package: editing it, cancelling it, and giving money back.

Money is never edited in place: a refund (or a reversal of a wrong entry) is a new `Refund` row linked to the payment
and the package; what the student really paid is the payments minus the refunds that were not voided. A wrong refund is
voided (kept, with the reason). Nothing here is shown to students."""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy as _l, ngettext

from . import packages
from .billing import money
from .history import log_event
from .i18n import short_date
from .models import Appointment, Payment, Refund, ScheduleSlot, StudentEvent, compute_prices

CENTS = Decimal('0.01')
Status = Appointment.Status


class RefundError(Exception):
    """Something that cannot be done; the message is for the trainer."""


def active_refunds(queryset):
    return queryset.filter(voided_at__isnull=True)


def payment_balance(payment):
    """What of one payment can still be given back."""
    given = sum((r.amount for r in payment.refunds.all() if r.voided_at is None), Decimal('0'))
    return payment.amount - given


def per_class_price(package):
    """What one class of the package is worth: the price per class, else the total divided by the classes."""
    if package.price_per_session is not None:
        return Decimal(package.price_per_session)
    if package.price_total is not None and package.sessions:
        return (Decimal(package.price_total) / package.sessions).quantize(CENTS, ROUND_HALF_UP)
    return Decimal('0')


def cancellation_quote(package, fee=None):
    """What a cancellation would give back: paid - classes already given x price per class - the agreed fee.

    Never below 0 and never above what was paid."""
    if fee is None:   # a cancelled package remembers the fee that was kept
        fee = package.cancel_fee if package.cancelled_at and package.cancel_fee is not None else Decimal('0')
    fee = max(Decimal(fee or 0), Decimal('0'))
    paid = packages.paid_amount(package)
    used = package.used_at_cancel if package.cancelled_at and package.used_at_cancel is not None else package.appointments.count()
    per = per_class_price(package)
    used_value = (per * used).quantize(CENTS, ROUND_HALF_UP)
    suggested = min(max(paid - used_value - fee, Decimal('0')), max(paid, Decimal('0')))
    return {'paid': paid, 'used': used, 'sessions': package.sessions, 'left': max(package.sessions - used, 0),
            'per_class': per, 'used_value': used_value, 'fee': fee, 'suggested': suggested}


def quote_text(package, quote, amount):
    """The calculation in words, to send to the student (kept on the refund)."""
    if quote['per_class']:
        given = _('Aulas dadas: %(used)d de %(sessions)d x %(per_class)s = %(used_value)s') % {
            'used': quote['used'], 'sessions': quote['sessions'], 'per_class': money(quote['per_class']),
            'used_value': money(quote['used_value'])}
    else:
        given = _('Aulas dadas: %(used)d de %(sessions)d') % {'used': quote['used'], 'sessions': quote['sessions']}
    lines = [
        _('Pacote: %(name)s') % {'name': package.name},
        _('Pago: %(paid)s') % {'paid': money(quote['paid'])},
        given,
    ]
    if quote['fee']:
        lines.append(_('Taxa combinada: %(fee)s') % {'fee': money(quote['fee'])})
    lines.append(_('Valor devolvido: %(amount)s') % {'amount': money(amount)})
    return '\n'.join(lines)


@transaction.atomic
def register_refund(person, *, amount, method, refunded_on=None, reason='', kind=Refund.Kind.REFUND, package=None,
                    payment=None, summary='', actor=None):
    """Give money back (or undo a wrong entry) against a payment and/or a package of this student."""
    amount = Decimal(amount or 0).quantize(CENTS, ROUND_HALF_UP)
    if amount <= 0:
        raise RefundError(_('Informe um valor maior que zero.'))
    if payment is not None:
        if payment.person_id != person.pk:
            raise RefundError(_('Este pagamento é de outro aluno.'))
        package = package or payment.student_package
        if amount > payment_balance(payment):
            raise RefundError(_('Só dá para devolver até %(max)s deste pagamento.') % {'max': money(payment_balance(payment))})
    if package is None and payment is None:
        raise RefundError(_('Escolha o pagamento ou o pacote a que a devolução se refere.'))
    if package is not None:
        if package.person_id != person.pk:
            raise RefundError(_('Este pacote é de outro aluno.'))
        paid = packages.paid_amount(package)
        if amount > paid:
            raise RefundError(_('Só dá para devolver até %(max)s: é o que foi pago por este pacote.') % {'max': money(paid)})
    refund = Refund.objects.create(
        person=person, student_package=package, payment=payment, kind=kind, amount=amount,
        refunded_on=refunded_on or timezone.localdate(), method=method, reason=reason.strip()[:200], summary=summary,
        recorded_by=actor if getattr(actor, 'pk', None) else None)
    if package:
        summary_text = _('%(kind)s registrada (%(method)s) — %(package)s') % {
            'kind': refund.get_kind_display(), 'method': refund.get_method_display(), 'package': package.name}
    else:
        summary_text = _('%(kind)s registrada (%(method)s)') % {
            'kind': refund.get_kind_display(), 'method': refund.get_method_display()}
    log_event(person, StudentEvent.Kind.PAYMENT, actor, summary_text, {'refund': refund.pk})
    if package is not None:
        packages.sync(person)
    return refund


@transaction.atomic
def void_refund(refund, reason, actor=None):
    """Undo a refund entered by mistake: it stays on record, marked as voided, and stops counting."""
    if refund.voided_at:
        raise RefundError(_('Esta devolução já foi anulada.'))
    if not reason.strip():
        raise RefundError(_('Escreva o motivo da anulação.'))
    refund.voided_at = timezone.now()
    refund.voided_reason = reason.strip()[:200]
    refund.save(update_fields=['voided_at', 'voided_reason'])
    log_event(refund.person, StudentEvent.Kind.PAYMENT, actor,
              _('%(kind)s de %(date)s anulada') % {'kind': refund.get_kind_display(), 'date': short_date(refund.refunded_on)},
              {'refund': refund.pk})
    return refund


def _clear_future(person, package, now):
    """The classes still ahead leave the agenda without charging anything, and the package's weekly times stop."""
    removed = 0
    for appointment in person.appointments.filter(status=Status.SCHEDULED, starts_at__gte=now):
        if appointment.slot_id is None:
            appointment.delete()
        else:
            appointment.status = Status.CANCELLED
            appointment.status_changed_at = now
            appointment.cancelled_late = False
            appointment.counts_override = False
            appointment.save(update_fields=['status', 'status_changed_at', 'cancelled_late', 'counts_override'])
        removed += 1
    ScheduleSlot.objects.filter(person=person, package=package, is_active=True).update(is_active=False)
    return removed


@transaction.atomic
def cancel_with_refund(package, actor=None, *, amount=Decimal('0'), method=None, refunded_on=None, reason='',
                       fee=Decimal('0'), remove_future=False, now=None):
    """Cancel the package; optionally give `amount` back and take the classes still marked off the agenda.

    Returns {'refund': Refund or None, 'removed': classes taken off the agenda}."""
    now = now or timezone.now()
    amount = Decimal(amount or 0)
    if package.cancelled_at:
        raise RefundError(_('Este pacote já foi cancelado.'))
    quote = cancellation_quote(package, fee)
    if amount > quote['paid']:
        raise RefundError(_('Só dá para devolver até %(max)s: é o que foi pago por este pacote.') % {'max': money(quote['paid'])})
    refund = None
    packages.cancel_package(package, actor)
    package.cancel_fee = max(Decimal(fee or 0), Decimal('0'))
    package.save(update_fields=['cancel_fee'])
    if amount > 0:
        refund = register_refund(package.person, amount=amount, method=method or Payment.Method.TRANSFER, refunded_on=refunded_on,
                                 reason=reason or _('Cancelamento do pacote'), package=package, summary=quote_text(package, quote, amount),
                                 actor=actor)
    removed = _clear_future(package.person, package, now) if remove_future else 0
    packages.sync(package.person)
    return {'refund': refund, 'removed': removed, 'quote': quote}


# What changed in an edit, for the history (name, price and notes: only that they changed)
EDIT_LABELS = {'name': _l('nome alterado'), 'price': _l('valor alterado'), 'notes': _l('observação alterada')}


def _days_text(days):
    return _('sem prazo') if days is None else ngettext('%(n)d dia', '%(n)d dias', days) % {'n': days}


@transaction.atomic
def edit_package(package, actor=None, *, name, sessions, valid_days, pricing_mode, price_per_session=None, price_total=None,
                 notes=''):
    """Change a package already sold (name, number of classes, deadline, agreed price). The classes already given are
    never taken away: the package cannot have fewer classes than that. Money values never go to the history."""
    if package.cancelled_at:
        raise RefundError(_('Um pacote cancelado não pode ser editado.'))
    used = package.appointments.count()
    if sessions < max(used, 1):
        raise RefundError(ngettext(
            'O pacote não pode ter menos de %(min)d aula: %(used)d já foram dadas.',
            'O pacote não pode ter menos de %(min)d aulas: %(used)d já foram dadas.', max(used, 1))
            % {'min': max(used, 1), 'used': used})
    per, total = compute_prices(pricing_mode, sessions, price_per_session, price_total)
    changed = []
    if package.name != name:
        changed.append('name')
    if package.sessions != sessions:
        changed.append('sessions')
    if package.valid_days != valid_days:
        changed.append('valid_days')
    if (package.price_per_session, package.price_total) != (per, total):
        changed.append('price')
    if package.notes != notes[:200]:
        changed.append('notes')
    if not changed:
        return []
    old = {'sessions': package.sessions, 'valid_days': package.valid_days}
    package.name, package.sessions, package.valid_days = name, sessions, valid_days
    package.price_per_session, package.price_total, package.notes = per, total, notes[:200]
    package.save()
    parts = []
    if 'sessions' in changed:
        parts.append(_('aulas %(old)d → %(new)d') % {'old': old['sessions'], 'new': sessions})
    if 'valid_days' in changed:
        parts.append(_('prazo %(new)s (era %(old)s)') % {'new': _days_text(valid_days), 'old': _days_text(old['valid_days'])})
    parts += [str(EDIT_LABELS[c]) for c in changed if c in ('name', 'price', 'notes')]
    log_event(package.person, StudentEvent.Kind.PACKAGE, actor,
              _('Pacote editado: %(name)s (%(changes)s)') % {'name': package.name, 'changes': '; '.join(parts)},
              {'package': package.pk, 'fields': changed})
    packages.sync(package.person)
    return changed


def overpaid_text(package, amount):
    """The words for a refund of what was paid above the price, to send to the student."""
    return '\n'.join([
        _('Pacote: %(name)s') % {'name': package.name},
        _('Valor combinado: %(price)s') % {'price': money(package.price_total)},
        _('Pago: %(paid)s') % {'paid': money(packages.paid_amount(package))},
        _('Valor devolvido (diferença): %(amount)s') % {'amount': money(amount)}])


def awaiting_refund():
    """Money that should go back and has not: packages cancelled from now on (the classes given are known) that still
    hold more than the classes given, and open packages paid above their price (e.g. the price was lowered).

    Each row: student, package, quote (with `suggested`), kind ('cancelled' or 'overpaid'). Only rows where something is
    due are listed."""
    from .models import StudentPackage

    rows = []
    for package in StudentPackage.objects.filter(cancelled_at__isnull=False, used_at_cancel__isnull=False).select_related(
            'person').prefetch_related('payments', 'refunds'):
        quote = cancellation_quote(package)
        if quote['suggested'] > 0:
            rows.append({'student': package.person, 'package': package, 'quote': quote, 'kind': 'cancelled'})
    for package in StudentPackage.objects.filter(cancelled_at__isnull=True, price_total__isnull=False).select_related(
            'person').prefetch_related('payments', 'refunds'):
        extra = packages.overpaid_amount(package)
        if extra > 0:
            quote = {'paid': packages.paid_amount(package), 'used': package.appointments.count(), 'suggested': extra,
                     'price': package.price_total}
            rows.append({'student': package.person, 'package': package, 'quote': quote, 'kind': 'overpaid'})
    rows.sort(key=lambda r: (-r['quote']['suggested'], r['student'].person_name))
    return rows


def month_refunds(first, last):
    """Entries of the month: (rows newest first, refunded total, reversed total) — voided ones are listed, not summed."""
    rows = list(Refund.objects.filter(refunded_on__range=(first, last)).select_related('person', 'student_package', 'payment'))
    refunded = sum((r.amount for r in rows if r.voided_at is None and r.kind == Refund.Kind.REFUND), Decimal('0'))
    reversed_ = sum((r.amount for r in rows if r.voided_at is None and r.kind == Refund.Kind.REVERSAL), Decimal('0'))
    return rows, refunded, reversed_
