from django.core.management.base import BaseCommand
from django.utils.translation import gettext as _

from core.reminders import run


class Command(BaseCommand):
    help = ('Send the automatic reminders (class tomorrow, plan expiring) and end idle workouts. '
            'Safe to run often: each one goes out once.')

    def handle(self, *args, **options):
        result = run()
        self.stdout.write(_('%(classes)d lembrete(s) de aula, %(plans)d de plano, '
                            '%(packages)d pacote(s) com aviso, %(sessions)d treino(s) parado(s) encerrado(s).') % result)
