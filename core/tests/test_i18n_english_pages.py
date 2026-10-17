"""With English active, no page may leak visible Portuguese: every text of the page comes from the catalog.

Seed data is plain ASCII (no accents), so any accented Portuguese letter in the visible text is a missed string."""
import html
import re

from django.test import override_settings
from django.urls import reverse

from core.models import ProgressPhoto, StudentEvent

from .base import BaseTestCase
from .test_evolution import MEDIA, picture
from .test_navigation_global import RichDataMixin

ACCENTED = re.compile(r'[áàâãéêíóôõúç]', re.I)
HIDDEN = re.compile(r'<(script|style|template)\b.*?</\1>|<!--.*?-->', re.S | re.I)
TAGS = re.compile(r'<[^>]+>')
ATTRS = re.compile(r'\b(?:title|placeholder|aria-label|alt)="([^"]*)"')
# Common Portuguese words that carry no accent (the accent test cannot see them); none is an English word
WORDS = re.compile(r'\b(?:voltar|cancelar|salvar|excluir|editar|adicionar|remover|enviar|buscar|confirmar|aluno|alunos|aluna|'
                   r'nome|nenhum|nenhuma|todos|todas|ativo|ativa|inativo|pagamento|pagamentos|pacote|pacotes|financeiro|'
                   r'treinos|exercicio|exercicios|obrigatorio|opcional|criar|novo|nova|proximo|proxima|anterior|'
                   r'concluido|pendente|semana|hoje|amanha|ontem|notas|observacoes|ver mais|ver todos|fotos?|aulas?|vencido|vence)\b', re.I)
NATIVE_NAMES = re.compile(r'Português( \(Brasil\))?|São João|\{\w+\}')   # each language named in itself; a local festival; the {variables} of the WhatsApp texts


def visible(page):
    body = page.content.decode()
    body = HIDDEN.sub(' ', body)
    attrs = ' '.join(ATTRS.findall(body))
    return NATIVE_NAMES.sub(' ', html.unescape(TAGS.sub(' ', body) + ' ' + attrs))


def leaks(text):
    text = ' '.join(text.split())
    found = {text[max(m.start() - 30, 0):m.end() + 20] for m in re.finditer(r'\S*' + ACCENTED.pattern + r'\S*', text, re.I)}
    found |= {text[max(m.start() - 30, 0):m.end() + 20] for m in WORDS.finditer(text)}
    return sorted(found)


@override_settings(MEDIA_ROOT=MEDIA, TELEGRAM_BOT_TOKEN='t', TELEGRAM_BOT_USERNAME='bot', TELEGRAM_WEBHOOK_SECRET='s')
class EnglishPagesTests(RichDataMixin, BaseTestCase):
    maxDiff = None

    def setUp(self):
        super().setUp()
        self.build_world()
        self.link_photo = ProgressPhoto.objects.create(person=self.person, angle='front', image=picture(), uploaded_by_student=True)
        StudentEvent.objects.all().delete()   # history lines are stored in the language of whoever made them
        self.client.cookies[self._cookie()] = 'en'

    @staticmethod
    def _cookie():
        from django.conf import settings
        return settings.LANGUAGE_COOKIE_NAME

    def check(self, urls):
        for url in urls:
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200, url)
                self.assertEqual(page.headers.get('Content-Language'), 'en')
                self.assertIn('<html lang="en"', page.content.decode())
                self.assertEqual(leaks(visible(page)), [], url)

    def test_trainer_pages(self):
        self.login(self.trainer)
        pk = self.person.pk
        tabs = ['', '?tab=agenda', '?tab=avaliacoes', '?tab=financeiro', '?tab=treinos', '?tab=historico', '?tab=pacotes']
        self.check([
            reverse('core:home'), reverse('core:search') + '?q=ali',
            reverse('core:student_list'), reverse('core:student_create'),
            *[reverse('core:student_detail', kwargs={'pk': pk}) + tab for tab in tabs],
            reverse('core:student_edit', kwargs={'pk': pk}), reverse('core:student_invite', kwargs={'pk': pk}),
            reverse('core:student_history', kwargs={'pk': pk}),
            reverse('core:assessment_create', kwargs={'pk': pk}), reverse('core:student_package_sell', kwargs={'pk': pk}),
            reverse('core:payment_create', kwargs={'pk': pk}), reverse('core:refund_create', kwargs={'pk': pk}),
            reverse('core:finance'), reverse('core:finance_refunds'), reverse('core:plan_list'), reverse('core:plan_create'),
            reverse('core:agenda'), reverse('core:agenda') + '?view=month', reverse('core:agenda_holidays'),
            reverse('core:appointment_create'), reverse('core:calendar_settings'),
            reverse('core:exercise_list'), reverse('core:exercise_create'),
            reverse('core:exercise_detail', kwargs={'pk': self.supino.pk}),
            reverse('core:exercise_edit', kwargs={'pk': self.supino.pk}),
            reverse('core:routine_list'), reverse('core:routine_create'), reverse('core:routine_generate_ai'),
            reverse('core:routine_detail', kwargs={'pk': self.template.pk}),
            reverse('core:routine_edit', kwargs={'pk': self.template.pk}),
            reverse('core:person_routine_create') + f'?person={pk}',
            reverse('core:person_routine_detail', kwargs={'pk': self.alice_routine.pk}),
            reverse('core:person_routine_edit', kwargs={'pk': self.alice_routine.pk}),
            reverse('core:session_detail', kwargs={'pk': self.session.pk}),
            reverse('core:photo_link', kwargs={'pk': self.link_photo.pk}), reverse('core:photo_positions'),
            reverse('core:notification_settings'), reverse('core:notification_settings') + '?tab=whatsapp',
        ])

    def test_notification_page_in_every_telegram_state(self):
        from django.utils import timezone
        from core.models import NotificationPreference
        self.client.force_login(self.alice)
        url = reverse('core:notification_settings')
        self.check([url])
        self.client.post(url, {'telegram': 'link'})
        self.check([url])
        NotificationPreference.objects.filter(user=self.alice).update(
            telegram_enabled=True, telegram_chat_id=7, telegram_at=timezone.now())
        self.check([url])

    def test_student_pages(self):
        self.login(self.alice)
        self.check([
            reverse('core:home'), reverse('core:my_workouts'), reverse('core:my_agenda'), reverse('core:my_package'),
            reverse('core:my_intro'), reverse('core:my_evolution'), reverse('core:my_history'),
            reverse('core:my_profile'), reverse('core:my_privacy'), reverse('core:notification_settings'),
            reverse('core:privacy_policy'),
            reverse('core:my_exercise_progress', kwargs={'exercise_pk': self.supino.pk}),
            reverse('core:workout_summary', kwargs={'pk': self.session.pk}),
        ])

    def test_visitor_pages(self):
        self.check([reverse('core:home'), reverse('account_login'), reverse('core:privacy_policy'), reverse('core:offline')])
