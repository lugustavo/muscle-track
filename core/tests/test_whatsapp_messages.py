from datetime import timedelta
from decimal import Decimal
from urllib.parse import unquote

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import whatsapp_messages as wm
from core.billing import charge_message
from core.dashboard import recent_misses
from core.models import Appointment, Package, WhatsAppTemplate

from .base import BaseTestCase


class RenderTests(BaseTestCase):
    @override_settings(PIX_KEY='pix@exemplo.com')
    def test_default_charge_text_with_everything(self):
        pack = Package.objects.create(name='Pacote 10', sessions=10, valid_days=60, pricing_mode='per_session',
                                      price_per_session=Decimal('50'))
        text = charge_message(self.alice.person, pack, Decimal('500'))
        self.assertEqual(text.splitlines()[0], 'Olá, Alice! Passando para lembrar do pagamento do pacote Pacote 10.')
        self.assertIn('Valor: 500,00 €.', text)
        self.assertIn('Chave Pix: pix@exemplo.com', text)
        self.assertTrue(text.endswith('💪'))

    @override_settings(PIX_KEY='')
    def test_lines_without_a_value_are_left_out(self):
        text = charge_message(self.alice.person, None, None)
        self.assertNotIn('Valor', text)
        self.assertNotIn('Pix', text)
        self.assertEqual(text.splitlines()[0], 'Olá, Alice! Passando para lembrar do pagamento do pacote.')   # no " ." left behind

    def test_unknown_variables_stay_as_typed_and_extra_spaces_collapse(self):
        self.assertEqual(wm.render(wm.Kind.MISSED, {'nome': 'Ana', 'data': '12/10'}, 'Oi  {nome} ,  {xyz}  em {data} !'),
                         'Oi Ana, {xyz} em 12/10!')

    def test_saved_text_replaces_the_default(self):
        WhatsAppTemplate.objects.create(kind='missed', text='Cadê você, {nome}? Aula de {data}!')
        Appointment.objects.create(person=self.alice.person, status='missed', starts_at=timezone.now() - timedelta(days=1))
        row = recent_misses()[0]
        local = timezone.localtime(Appointment.objects.get().starts_at)
        self.assertEqual(unquote(row['whatsapp'].split('text=')[1]), f'Cadê você, Alice? Aula de {local:%d/%m}!')

    def test_invitation_uses_its_text(self):
        WhatsAppTemplate.objects.create(kind='invite', text='Oi {nome}, entre: {link}')
        self.client.force_login(self.trainer)
        response = self.client.get(reverse('core:student_invite', kwargs={'pk': self.alice.person.pk}))
        text = unquote(response.context['whatsapp_url'].split('text=')[1])
        self.assertTrue(text.startswith('Oi Alice, entre: http'))


class ProblemTests(BaseTestCase):
    def test_empty_unknown_and_missing_required_are_refused(self):
        self.assertTrue(wm.problems('missed', '   '))
        self.assertIn('{foo}', ' '.join(wm.problems('missed', 'Oi {nome} {foo}')))
        self.assertTrue(wm.problems('invite', 'Oi {nome}, entre no app'))          # no {link}
        self.assertEqual(wm.problems('invite', 'Entre: {link}'), [])
        self.assertTrue(wm.problems('missed', 'x' * (wm.MAX_LENGTH + 1)))

    def test_variables_of_another_message_are_not_accepted(self):
        self.assertTrue(wm.problems('missed', 'Oi {nome}, {valor}'))


class ScreenTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(self.trainer)
        self.page = lambda: self.client.get(reverse('core:notification_settings')).content.decode()
        self.save = lambda kind, **data: self.client.post(reverse('core:whatsapp_template_save', kwargs={'kind': kind}), data)

    def test_tab_shows_the_three_texts_with_variables_and_preview(self):
        html = self.page()
        self.assertIn('id="tab-mensagens"', html)
        for label in ('Cobrança de pacote', 'Aula que o aluno faltou', 'Convite de acesso'):
            self.assertIn(label, html)
        self.assertEqual(html.count('data-whatsapp-card'), 3)
        self.assertIn('data-wa-var="valor"', html)
        self.assertIn('Olá, Carla! Passando para lembrar do pagamento do pacote Pacote 10 aulas.', html)   # the sample student
        self.assertIn('texto padrão', html)

    def test_student_does_not_see_the_tab(self):
        self.client.force_login(self.alice)
        html = self.client.get(reverse('core:notification_settings')).content.decode()
        self.assertNotIn('tab-mensagens', html)
        self.assertEqual(self.save('missed', text='x').status_code, 403)

    def test_save_and_reset(self):
        response = self.save('missed', text='  Sumiu, {nome}?  ', action='save')
        self.assertIn('#tab-mensagens', response['Location'])
        self.assertEqual(WhatsAppTemplate.objects.get(kind='missed').text, 'Sumiu, {nome}?')
        self.assertEqual(WhatsAppTemplate.objects.get().updated_by, self.trainer)
        self.assertIn('personalizada', self.page())
        self.save('missed', text='Outro, {nome}', action='save')
        self.assertEqual(WhatsAppTemplate.objects.count(), 1)                    # updated, not duplicated
        self.save('missed', action='reset')
        self.assertFalse(WhatsAppTemplate.objects.exists())

    def test_invalid_text_is_not_saved_and_says_why(self):
        response = self.save('invite', text='Oi {nome}', action='save')
        self.assertFalse(WhatsAppTemplate.objects.exists())
        self.assertTrue(any('{link}' in str(m) for m in response.wsgi_request._messages))

    def test_unknown_kind_is_a_404(self):
        self.assertEqual(self.save('nope', text='x').status_code, 404)
        self.assertEqual(self.client.get(reverse('core:whatsapp_template_preview', kwargs={'kind': 'nope'})).status_code, 404)

    def test_preview_endpoint(self):
        response = self.client.get(reverse('core:whatsapp_template_preview', kwargs={'kind': 'missed'}),
                                   {'text': 'Oi {nome}, aula de {data} {zzz}'})
        data = response.json()
        self.assertEqual(data['text'], 'Oi Carla, aula de 12/10 {zzz}')
        self.assertIn('{zzz}', data['errors'][0])
        self.assertIn('no-store', response['Cache-Control'])
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse('core:whatsapp_template_preview', kwargs={'kind': 'missed'})).status_code, 403)

    def test_post_only(self):
        self.assertEqual(self.client.get(reverse('core:whatsapp_template_save', kwargs={'kind': 'missed'})).status_code, 405)
