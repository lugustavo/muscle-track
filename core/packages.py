"""Packages of classes: what was sold to a student, how many classes are left and until when they are valid.

The trainer sells a package (a number of classes with a deadline counted **from the first class**; payment is made at the
first class). The balance is never typed: it is counted from the classes linked to the package (`Appointment.package`),
and `sync()` redoes those links from the classes' own state, so it can run after any change and never drifts.

What counts as a class given (Appointment.counts_override can force either way, e.g. an illness):
  * attended; missed (a no-show pays); cancelled with less notice than allowed (`AGENDA_NOTICE_HOURS`, 24 h).
  * rescheduled, scheduled and cancelled in time do not count.
The oldest package with a balance is used first. A frozen package stops its clock; an expired one with a balance is
never zeroed by itself: the trainer extends it."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .history import log_event
from .models import Appointment, StudentEvent, StudentPackage, compute_prices

Status = Appointment.Status

OPEN_STATES = ('not_started', 'active', 'frozen', 'expired')
STATE_LABELS = {
    'not_started': ('Aguardando a primeira aula', 'info'), 'active': ('Em andamento', 'success'),
    'frozen': ('Congelado', 'secondary'), 'expired': ('Vencido com aulas', 'danger'),
    'done': ('Concluído', 'secondary'), 'cancelled': ('Cancelado', 'secondary'),
}
LOW_BALANCE = 2          # "running out": this many classes left or fewer
EXPIRING_DAYS = 7        # "expires soon": this many days left or fewer


class PackageError(Exception):
    """An action that cannot be done; the message is for the trainer."""


def late_cancel(starts_at, now=None):
    """True when cancelling now leaves less notice than allowed (the class then counts)."""
    now = now or timezone.now()
    return starts_at - now < timedelta(hours=max(int(settings.AGENDA_NOTICE_HOURS), 0))


def consumes(appointment):
    """Does this class count against a package?"""
    if appointment.counts_override is not None:
        return appointment.counts_override
    if appointment.status in (Status.ATTENDED, Status.MISSED):
        return True
    return appointment.status == Status.CANCELLED and appointment.cancelled_late


def ends_on(package, today=None):
    """Last day of the package: first class + deadline + extensions + days frozen (None before the first class)."""
    if package.starts_on is None:
        return None
    today = today or timezone.localdate()
    frozen = package.frozen_days
    if package.frozen_since:
        frozen += max((today - package.frozen_since).days, 0)
    return package.starts_on + timedelta(days=package.valid_days + package.extension_days + frozen)


def state(package, used, today=None):
    """'not_started', 'active', 'frozen', 'expired' (past its end with classes left), 'done' or 'cancelled'."""
    today = today or timezone.localdate()
    if package.cancelled_at:
        return 'cancelled'
    if used >= package.sessions:
        return 'done'
    if package.frozen_since:
        return 'frozen'
    if package.starts_on is None:
        return 'not_started'
    return 'expired' if ends_on(package, today) < today else 'active'


def paid_amount(package):
    return sum((p.amount for p in package.payments.all()), Decimal('0'))


def pending_amount(package):
    """What is still to be received for the package (0 when it has no price)."""
    if package.price_total is None:
        return Decimal('0')
    return max(package.price_total - paid_amount(package), Decimal('0'))


@transaction.atomic
def sync(person, today=None):
    """Redo which package each counting class belongs to, each package's first-class date, and the student's cached
    "current package" fields (plan, plan_name, plan_price, hourly_rate, plan_expires_on) that lists, badges and
    reminders read. A student who never had a package keeps whatever the old plan fields hold."""
    today = today or timezone.localdate()
    packages = list(person.packages.filter(cancelled_at__isnull=True).order_by('sold_on', 'pk'))
    if not person.packages.exists():
        return {}
    classes = list(person.appointments.order_by('starts_at'))
    used = {p.pk: 0 for p in packages}
    first = {}
    target = {}
    index = 0
    for appointment in classes:
        if not consumes(appointment):
            target[appointment.pk] = None
            continue
        while index < len(packages) and used[packages[index].pk] >= packages[index].sessions:
            index += 1
        if index < len(packages):
            package = packages[index]
            used[package.pk] += 1
            target[appointment.pk] = package.pk
            first.setdefault(package.pk, timezone.localtime(appointment.starts_at).date())
        else:
            target[appointment.pk] = None   # a class given beyond every package: "sem pacote" on the card
    for appointment in classes:
        if appointment.package_id != target[appointment.pk]:
            Appointment.objects.filter(pk=appointment.pk).update(package_id=target[appointment.pk])
            appointment.package_id = target[appointment.pk]
    for package in packages:
        if package.starts_on != first.get(package.pk):
            package.starts_on = first.get(package.pk)
            package.save(update_fields=['starts_on'])
    _refresh_person(person, packages, used, today)
    return used


def _refresh_person(person, packages, used, today):
    current = next((p for p in packages if state(p, used[p.pk], today) in OPEN_STATES), None)
    values = {'plan': None, 'plan_name': '', 'plan_price': None, 'hourly_rate': None, 'plan_expires_on': None}
    if current is not None:
        values.update(plan=current.package, plan_name=current.name, plan_price=current.price_total,
                      hourly_rate=current.price_per_session)
        if state(current, used[current.pk], today) in ('active', 'expired'):
            values['plan_expires_on'] = ends_on(current, today)
    changed = [name for name, value in values.items() if getattr(person, name) != value]
    if changed:
        for name in changed:
            setattr(person, name, values[name])
        person.save(update_fields=changed + ['updated_at'])


def overview(person, today=None):
    """Everything the screens show about the student's packages, newest last.

    Each row: the package, its `state`, `used`, `left`, dates, percent, money, plus top-level `current` (the one being
    used), `scheduled` (marked classes still to happen) and `without_package` (classes given that no package covers)."""
    today = today or timezone.localdate()
    now = timezone.now()
    packages = list(person.packages.prefetch_related('payments').order_by('sold_on', 'pk'))
    used = {p.pk: p.appointments.count() for p in packages}
    rows = []
    for package in packages:
        s = state(package, used[package.pk], today)
        end = ends_on(package, today)
        total = package.sessions
        rows.append({
            'package': package, 'state': s, 'label': STATE_LABELS[s][0], 'css': STATE_LABELS[s][1],
            'used': used[package.pk], 'left': max(total - used[package.pk], 0), 'sessions': total,
            'percent': round(100 * min(used[package.pk], total) / total) if total else 0,
            'starts_on': package.starts_on, 'ends_on': end,
            'days_left': (end - today).days if end and s in ('active', 'expired') else None,
            'paid': paid_amount(package), 'pending': pending_amount(package),
            'low': s in ('active', 'not_started', 'frozen') and 0 < total - used[package.pk] <= LOW_BALANCE,
        })
    current = next((r for r in rows if r['state'] in OPEN_STATES), None)
    return {
        'rows': rows, 'current': current,
        'scheduled': person.appointments.filter(status=Status.SCHEDULED, starts_at__gte=now).count(),
        'without_package': _without_package(person),
    }


def _without_package(person):
    return sum(1 for a in person.appointments.filter(package__isnull=True).exclude(
        status__in=(Status.SCHEDULED, Status.RESCHEDULED)) if consumes(a))


@transaction.atomic
def sell(person, package, *, mode='per_session', price_per_session=None, price_total=None, notes='', actor=None,
         sold_on=None):
    """Sell `package` (a catalog Package) to the student: a copy of its numbers with the prices agreed for this one."""
    per, total = compute_prices(mode, package.sessions, price_per_session, price_total)
    if per is None and total is None:   # nothing typed: the catalog price
        per, total = package.price_per_session, package.price_total
    sold = StudentPackage.objects.create(
        person=person, package=package, name=package.name, sessions=package.sessions, valid_days=package.valid_days,
        price_per_session=per, price_total=total, notes=notes[:200], sold_on=sold_on or timezone.localdate())
    log_event(person, StudentEvent.Kind.PACKAGE, actor, f'Pacote vendido: {sold.name} ({sold.sessions} aulas)',
              {'package': sold.pk, 'sessions': sold.sessions})
    sync(person)
    return sold


def _open(package):
    s = state(package, package.appointments.count())
    if s in ('done', 'cancelled'):
        raise PackageError('Este pacote já foi concluído ou cancelado.')
    return s


@transaction.atomic
def freeze(package, actor=None, today=None):
    today = today or timezone.localdate()
    s = _open(package)
    if package.frozen_since:
        raise PackageError('O pacote já está congelado.')
    if s == 'not_started':
        raise PackageError('O prazo só começa na primeira aula: não há o que congelar ainda.')
    package.frozen_since = today
    package.save(update_fields=['frozen_since'])
    log_event(package.person, StudentEvent.Kind.PACKAGE, actor, f'Pacote congelado: {package.name}', {'package': package.pk})
    sync(package.person, today)


@transaction.atomic
def unfreeze(package, actor=None, today=None):
    today = today or timezone.localdate()
    if not package.frozen_since:
        raise PackageError('O pacote não está congelado.')
    days = max((today - package.frozen_since).days, 0)
    package.frozen_days += days
    package.frozen_since = None
    package.save(update_fields=['frozen_days', 'frozen_since'])
    log_event(package.person, StudentEvent.Kind.PACKAGE, actor,
              f'Pacote descongelado: {package.name} (prazo +{days} dia{"" if days == 1 else "s"})',
              {'package': package.pk, 'days': days})
    sync(package.person, today)


@transaction.atomic
def extend(package, days, note='', actor=None, today=None):
    if not 1 <= int(days) <= 365:
        raise PackageError('A prorrogação deve ser de 1 a 365 dias.')
    _open(package)
    package.extension_days += int(days)
    package.save(update_fields=['extension_days'])
    reason = f': {note.strip()[:150]}' if note.strip() else ''
    log_event(package.person, StudentEvent.Kind.PACKAGE, actor,
              f'Pacote prorrogado: {package.name} (+{int(days)} dias){reason}', {'package': package.pk, 'days': int(days)})
    sync(package.person, today)


@transaction.atomic
def cancel_package(package, actor=None):
    if package.cancelled_at:
        raise PackageError('Este pacote já foi cancelado.')
    package.cancelled_at = timezone.now()
    package.save(update_fields=['cancelled_at'])
    log_event(package.person, StudentEvent.Kind.PACKAGE, actor, f'Pacote cancelado: {package.name}', {'package': package.pk})
    sync(package.person)


def pending_payments(today=None):
    """Packages whose first class already happened and are not fully paid, oldest first: what to charge."""
    today = today or timezone.localdate()
    rows = []
    packages = StudentPackage.objects.filter(
        cancelled_at__isnull=True, starts_on__isnull=False, person__is_active=True
    ).select_related('person').prefetch_related('payments').order_by('starts_on')
    for package in packages:
        pending = pending_amount(package)
        if pending > 0:
            rows.append({'student': package.person, 'package': package, 'amount': pending,
                         'days': (today - package.starts_on).days, 'due': package.starts_on})
    return rows
