"""Payments and money rules. A payment is for a package sold to the student (the first class is when it is paid)."""
import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.formats import number_format
from django.utils import translation
from django.utils.translation import get_language, gettext as _

from . import packages
from .history import log_event
from .models import Payment, StudentEvent

CENTS = Decimal('0.01')


def money(value):
    """'1.234,50 €' (or 'R$ 1.234,50', see CURRENCY_SYMBOL / CURRENCY_POSITION) for pages, messages and CSV.

    The number follows the active language ('1,234.50' in English) and, in English, the symbol goes before it."""
    if value is None:
        return ''
    text = number_format(Decimal(value).quantize(CENTS, ROUND_HALF_UP), decimal_pos=2, use_l10n=True, force_grouping=True)
    symbol = settings.CURRENCY_SYMBOL
    if (get_language() or '').startswith('en'):
        return f'{symbol}{text}' if len(symbol) == 1 else f'{symbol} {text}'
    return f'{symbol} {text}' if settings.CURRENCY_POSITION == 'before' else f'{text} {symbol}'


@transaction.atomic
def register_payment(person, *, package, amount, paid_on, method, notes='', actor=None):
    """Record a payment for `package` (a StudentPackage of this student, or None for a payment without one)."""
    payment = Payment.objects.create(
        person=person, student_package=package, plan=package.package if package else None,
        plan_name=package.name if package else '', amount=amount, paid_on=paid_on, method=method, notes=notes,
        recorded_by=actor if getattr(actor, 'pk', None) else None)
    if package:
        summary = _('Pagamento registrado (%(method)s) — %(package)s') % {
            'method': payment.get_method_display(), 'package': package.name}
    else:
        summary = _('Pagamento registrado (%(method)s)') % {'method': payment.get_method_display()}
    log_event(person, StudentEvent.Kind.PAYMENT, actor, summary)
    return payment


@transaction.atomic
def delete_payment(payment, actor=None):
    person = payment.person
    label = f'{payment.paid_on:%d/%m/%Y} ({payment.get_method_display()})'
    payment.delete()
    log_event(person, StudentEvent.Kind.PAYMENT, actor, _('Pagamento de %(label)s excluído') % {'label': label})


def overdue_rows(today=None):
    """Packages whose first class already happened and are not fully paid, longest waiting first.

    (Kept under the old name: the dashboard, the finance page and the bottom bar read it.)"""
    rows = packages.pending_payments(today)
    rows.sort(key=lambda r: -r['days'])
    return rows


def month_bounds(year, month):
    first = date(year, month, 1)
    return first, date(year, month, calendar.monthrange(year, month)[1])


def parse_month(value, today=None):
    """'YYYY-MM' -> (year, month); anything else -> the current month."""
    today = today or timezone.localdate()
    try:
        year, month = (int(part) for part in str(value).split('-'))
        date(year, month, 1)
        return year, month
    except (ValueError, TypeError):
        return today.year, today.month


def month_summary(year, month):
    """What came in during the month, by payment method, and what was sold (packages sold in the month)."""
    from .models import StudentPackage

    first, last = month_bounds(year, month)
    payments = list(Payment.objects.filter(paid_on__range=(first, last)).select_related('person', 'student_package'))
    by_method = {}
    for p in payments:
        entry = by_method.setdefault(p.method, {'label': p.get_method_display(), 'total': Decimal('0'), 'count': 0})
        entry['total'] += p.amount
        entry['count'] += 1
    sold = list(StudentPackage.objects.filter(sold_on__range=(first, last), cancelled_at__isnull=True))
    return {
        'payments': payments,
        'received': sum((p.amount for p in payments), Decimal('0')),
        'by_method': sorted(by_method.values(), key=lambda e: -e['total']),
        'sold_count': len(sold),
        'sold_total': sum((s.price_total or Decimal('0') for s in sold), Decimal('0')),
        'sold_unpriced': sum(1 for s in sold if s.price_total is None),
    }


def service_report(year, month):
    """Classes given in the month at the price per class of their package ("realizado"), and the classes still owed on
    open packages with their value ("a executar", as of today). Packages without a price are counted apart.

    Each row: student, classes, amount (given) or student, package, left, amount (owed)."""
    from .models import Appointment, StudentPackage

    first, last = month_bounds(year, month)
    given, unpriced_given = {}, 0
    for a in Appointment.objects.filter(
            package__isnull=False, starts_at__date__range=(first, last)).select_related('package', 'person'):
        price = a.package.price_per_session
        if price is None:
            unpriced_given += 1
            continue
        row = given.setdefault(a.person_id, {'student': a.person, 'classes': 0, 'amount': Decimal('0')})
        row['classes'] += 1
        row['amount'] += price
    owed, unpriced_left = [], 0
    for package in StudentPackage.objects.filter(cancelled_at__isnull=True).select_related('person').annotate(
            n_used=Count('appointments')):
        left = package.sessions - package.n_used
        if left <= 0:
            continue
        if package.price_per_session is None:
            unpriced_left += left
            continue
        owed.append({'student': package.person, 'package': package, 'left': left,
                     'amount': package.price_per_session * left})
    given_rows = sorted(given.values(), key=lambda r: (-r['amount'], r['student'].person_name))
    owed.sort(key=lambda r: (-r['amount'], r['student'].person_name))
    return {
        'given_rows': given_rows, 'given_total': sum((r['amount'] for r in given_rows), Decimal('0')),
        'given_classes': sum(r['classes'] for r in given_rows), 'given_unpriced': unpriced_given,
        'owed_rows': owed, 'owed_total': sum((r['amount'] for r in owed), Decimal('0')),
        'owed_classes': sum(r['left'] for r in owed), 'owed_unpriced': unpriced_left,
    }


def charge_message(person, package, amount, text=None):
    """The WhatsApp text asking for the payment of a package (paid at the first class). Lists pass the trainer's
    `text` (whatsapp_messages.current_text) once, instead of reading it for every row.

    It is written in the student's own language (the amount too), whatever language the trainer is using."""
    from .whatsapp_messages import Kind, language_of, render

    with translation.override(language_of(person)):
        return render(Kind.CHARGE, {
            'nome': person.person_name.split()[0], 'pacote': package.name if package is not None else '',
            'valor': money(amount) if amount is not None else '', 'pix': settings.PIX_KEY}, text)
