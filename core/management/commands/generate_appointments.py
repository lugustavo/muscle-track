from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.translation import gettext as _

from core.agenda import ensure_appointments


class Command(BaseCommand):
    help = 'Create the classes of the fixed weekly slots for the next weeks (the calendar also does it when opened).'

    def add_arguments(self, parser):
        parser.add_argument('--weeks', type=int, default=4, help='Weeks ahead to fill (default 4)')

    def handle(self, *args, **options):
        today = timezone.localdate()
        created = ensure_appointments(today, today + timedelta(weeks=options['weeks']))
        self.stdout.write(_('%(n)d aula(s) criada(s).') % {'n': created})
