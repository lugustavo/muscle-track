"""The "Contrato" card of the student's file: what is agreed with this student, in one place.

Vocabulary (used on every screen): **Plano** is the model in the catalog (Financeiro > Planos);
**Contrato** is what is agreed with this student (name, value, hourly rate, expiry)."""
from datetime import time, timedelta

from .agenda import at, week_start
from .models import Appointment, Person


def _status(student, today):
    """(key, label, css) of the contract situation."""
    if student.status == Person.Status.PAUSED:
        return 'paused', 'Pausado', 'secondary'
    if student.status == Person.Status.FORMER:
        return 'former', 'Ex-aluno', 'secondary'
    days = (student.plan_expires_on - today).days if student.plan_expires_on else None
    if not (student.plan_id or student.plan_name or student.plan_expires_on):
        return 'none', 'Sem plano', 'secondary'
    if days is None:
        return 'open', 'Sem vencimento', 'info'
    if days < 0:
        n = -days
        return 'expired', f'Vencido há {n} dia{"" if n == 1 else "s"}', 'danger'
    if days == 0:
        return 'expiring', 'Vence hoje', 'warning'
    if days <= Person.PLAN_EXPIRING_DAYS:
        return 'expiring', f'Vence em {days} dia{"" if days == 1 else "s"}', 'warning'
    return 'ok', 'Em dia', 'success'


def _period(student, last_payment, today):
    """Progress through the current period: from the last payment's start to the expiry (None when unknown)."""
    expires = student.plan_expires_on
    if not expires or not last_payment or last_payment.period_start >= expires:
        return None
    total = (expires - last_payment.period_start).days
    used = min(max((today - last_payment.period_start).days, 0), total)
    return {'start': last_payment.period_start, 'end': expires, 'days_used': used, 'days_total': total,
            'percent': round(100 * used / total) if total else 100}


def _classes_this_week(student, today):
    monday = week_start(today)
    return Appointment.objects.filter(
        person=student, status=Appointment.Status.ATTENDED,
        starts_at__gte=at(monday, time.min),
        starts_at__lt=at(monday + timedelta(days=7), time.min)).count()


def _against_table(student):
    """How the agreed value compares with the plan's table price: {'percent', 'direction'} or None."""
    plan = student.plan
    if not plan or plan.price in (None, 0) or student.plan_price is None:
        return None
    diff = (student.plan_price - plan.price) * 100 / plan.price
    percent = abs(round(float(diff)))
    if percent == 0:
        return {'percent': 0, 'direction': 'same'}
    return {'percent': percent, 'direction': 'below' if diff < 0 else 'above'}


def contract_card(student, last_payment, charge_url, today):
    key, label, css = _status(student, today)
    plan = student.plan
    per_week = plan.sessions_per_week if plan else None
    return {
        'status': key, 'label': label, 'css': css,
        'plan_name': student.plan_name or (plan.name if plan else ''),
        'catalog_plan': plan,
        'periodicity': plan.get_months_display() if plan else '',
        'expires_on': student.plan_expires_on,
        'period': _period(student, last_payment, today),
        'classes_per_week': per_week,
        'classes_done': _classes_this_week(student, today) if per_week else None,
        'price': student.plan_price, 'hourly_rate': student.hourly_rate,
        'against_table': _against_table(student),
        'last_payment': last_payment,
        'charge_url': charge_url,
        # the main button follows the situation: a late or expiring contract highlights "Cobrar"
        'charge_first': bool(charge_url) and key in ('expired', 'expiring'),
    }
