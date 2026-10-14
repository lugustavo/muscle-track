"""Plans, payments and money rules. Pure functions where possible, so they are easy to test."""
import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .history import log_event
from .models import Payment, Person, StudentEvent

CENTS = Decimal('0.01')


def add_months(value, months):
    """Same day N months later; clamps to the end of shorter months (31/01 + 1 = 28/02)."""
    index = value.month - 1 + months
    year, month = value.year + index // 12, index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def period_end_for(start, months):
    return add_months(start, months) - timedelta(days=1)


def money(value):
    """'R$ 1.234,50' (pt-BR) for display in messages and CSV."""
    if value is None:
        return ''
    text = f'{Decimal(value).quantize(CENTS, ROUND_HALF_UP):,.2f}'
    return 'R$ ' + text.replace(',', '#').replace('.', ',').replace('#', '.')


def suggest_amount(person, plan=None):
    """Amount to suggest for a payment. Always editable by the trainer.

    1. the value negotiated with this student (only for the plan the student has);
    2. hourly rate x sessions per week x weeks of the period (when both are known);
    3. the template's table price."""
    plan = plan or person.plan
    if plan is None:
        return person.plan_price
    if person.plan_price is not None and person.plan_id == plan.pk:
        return person.plan_price
    if person.hourly_rate is not None and plan.sessions_per_week:
        weeks = Decimal(52) * plan.months / 12
        return (person.hourly_rate * plan.sessions_per_week * weeks).quantize(CENTS, ROUND_HALF_UP)
    return plan.price


def suggest_period(person, plan, paid_on):
    """(start, end) of the period a payment made on `paid_on` should cover."""
    months = plan.months if plan else 1
    expires = person.plan_expires_on
    start = expires + timedelta(days=1) if expires and expires >= paid_on else paid_on
    return start, period_end_for(start, months)


def recompute_expiry(person):
    """After deleting a payment: expiry = latest period_end left. With none left, keep what is there."""
    latest = person.payments.order_by('-period_end').values_list('period_end', flat=True).first()
    if latest is not None and latest != person.plan_expires_on:
        person.plan_expires_on = latest
        person.save(update_fields=['plan_expires_on', 'updated_at'])


@transaction.atomic
def register_payment(person, *, plan, amount, paid_on, method, period_start, period_end, notes='', actor=None):
    payment = Payment.objects.create(
        person=person, plan=plan, plan_name=(plan.name if plan else person.plan_name), amount=amount,
        paid_on=paid_on, method=method, period_start=period_start, period_end=period_end, notes=notes,
        recorded_by=actor if getattr(actor, 'pk', None) else None)
    fields = ['updated_at']
    if plan is not None and person.plan_id != plan.pk:
        person.plan = plan
        person.plan_name = plan.name
        fields += ['plan', 'plan_name']
    # A late-registered older payment never shortens the plan
    if person.plan_expires_on is None or period_end > person.plan_expires_on:
        person.plan_expires_on = period_end
        fields.append('plan_expires_on')
    person.save(update_fields=fields)
    log_event(person, StudentEvent.Kind.PAYMENT, actor,
              f'Pagamento registrado ({payment.get_method_display()}), plano até {period_end:%d/%m/%Y}')
    return payment


@transaction.atomic
def delete_payment(payment, actor=None):
    person = payment.person
    label = f'{payment.paid_on:%d/%m/%Y} ({payment.get_method_display()})'
    payment.delete()
    recompute_expiry(person)
    log_event(person, StudentEvent.Kind.PAYMENT, actor, f'Pagamento de {label} excluído')


def overdue_rows(today=None):
    """Active students whose plan expired, most late first, with what they owe."""
    today = today or timezone.localdate()
    rows = []
    for person in Person.objects.filter(is_active=True, plan_expires_on__lt=today).select_related('plan', 'user'):
        rows.append({'student': person, 'days': (today - person.plan_expires_on).days,
                     'amount': suggest_amount(person), 'due': person.plan_expires_on})
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
    first, last = month_bounds(year, month)
    payments = list(Payment.objects.filter(paid_on__range=(first, last)).select_related('person'))
    by_method = {}
    for p in payments:
        entry = by_method.setdefault(p.method, {'label': p.get_method_display(), 'total': Decimal('0'), 'count': 0})
        entry['total'] += p.amount
        entry['count'] += 1
    expected = Decimal('0')
    expected_count = 0
    unpriced = 0
    for person in Person.objects.filter(is_active=True, plan_expires_on__range=(first, last)).select_related('plan'):
        expected_count += 1
        amount = suggest_amount(person)
        if amount is None:
            unpriced += 1
        else:
            expected += amount
    return {
        'payments': payments,
        'received': sum((p.amount for p in payments), Decimal('0')),
        'by_method': sorted(by_method.values(), key=lambda e: -e['total']),
        'expected': expected, 'expected_count': expected_count, 'unpriced': unpriced,
    }


def charge_message(person, amount, due):
    first = person.person_name.split()[0]
    parts = [f'Olá, {first}! Passando para lembrar que o seu plano']
    if person.plan_name:
        parts.append(f'({person.plan_name})')
    parts.append(f'venceu em {due:%d/%m/%Y}.' if due < timezone.localdate() else f'vence em {due:%d/%m/%Y}.')
    if amount is not None:
        parts.append(f'Valor: {money(amount)}.')
    if settings.PIX_KEY:
        parts.append(f'Chave Pix: {settings.PIX_KEY}')
    parts.append('Qualquer dúvida é só falar. 💪')
    return ' '.join(parts)
