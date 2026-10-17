from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core import telegram


class Command(BaseCommand):
    help = 'Tells Telegram where to send what people write to the bot (run once after setting the TELEGRAM_* variables).'

    def add_arguments(self, parser):
        parser.add_argument('--delete', action='store_true', help='stop Telegram from calling the app (turns linking off)')

    def handle(self, *args, **options):
        if not telegram.configured():
            raise CommandError('Defina TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME e TELEGRAM_WEBHOOK_SECRET no .env.')
        if options['delete']:
            telegram.call('deleteWebhook', {})
            self.stdout.write(self.style.SUCCESS('Webhook removido.'))
            return
        base = (settings.SITE_URL or '').rstrip('/')
        if not base.startswith('https://'):
            raise CommandError('O Telegram só chama endereços HTTPS: defina SITE_URL (ou DOMAIN) com o endereço público do app.')
        url = f'{base}/telegram/webhook/'
        try:
            telegram.call('setWebhook', {'url': url, 'secret_token': settings.TELEGRAM_WEBHOOK_SECRET,
                                         'allowed_updates': ['message']})
        except Exception as exc:
            raise CommandError(f'O Telegram recusou: {type(exc).__name__}: {str(exc).replace(settings.TELEGRAM_BOT_TOKEN, "***")[:150]}')
        self.stdout.write(self.style.SUCCESS(f'Webhook ligado em {url}'))
