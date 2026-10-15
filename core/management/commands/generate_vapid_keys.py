"""Prints a VAPID key pair for web push, ready to paste into the .env file."""
import base64

from cryptography.hazmat.primitives import serialization
from django.core.management.base import BaseCommand
from py_vapid import Vapid


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


class Command(BaseCommand):
    help = 'Gera o par de chaves VAPID (web push) para colocar no .env'

    def handle(self, *args, **options):
        vapid = Vapid()
        vapid.generate_keys()
        public = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        private = vapid.private_key.private_numbers().private_value.to_bytes(32, 'big')
        self.stdout.write('# Cole no .env (guarde a chave privada como uma senha; trocar as chaves desliga os aparelhos já inscritos)')
        self.stdout.write(f'VAPID_PUBLIC_KEY={_b64(public)}')
        self.stdout.write(f'VAPID_PRIVATE_KEY={_b64(private)}')
        self.stdout.write('VAPID_SUBJECT=mailto:seu-email@exemplo.com')
