from django.core.management.base import BaseCommand

from core.reminders import run


class Command(BaseCommand):
    help = 'Send the automatic reminders (class tomorrow, plan expiring). Safe to run often: each one goes out once.'

    def handle(self, *args, **options):
        result = run()
        self.stdout.write(f"{result['classes']} lembrete(s) de aula, {result['plans']} de plano.")
