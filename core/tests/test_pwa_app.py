import json

from django.urls import reverse

from .base import BaseTestCase


class ManifestTests(BaseTestCase):
    def manifest(self):
        response = self.client.get(reverse('core:pwa_manifest'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/manifest+json')
        return response, json.loads(response.content)

    def test_basics(self):
        response, data = self.manifest()
        self.assertEqual((data['id'], data['start_url'], data['scope'], data['display']), ('/', '/', '/', 'standalone'))
        self.assertEqual(data['lang'], 'pt-BR')
        self.assertIn('fitness', data['categories'])
        self.assertTrue(all(i['src'].startswith('/static/') for i in data['icons']))
        self.assertIn('Cookie', response['Vary'])
        self.assertIn('no-cache', response['Cache-Control'])

    def test_visitors_get_no_shortcuts(self):
        self.assertEqual(self.manifest()[1]['shortcuts'], [])

    def test_student_shortcuts(self):
        self.login(self.alice)
        shortcuts = self.manifest()[1]['shortcuts']
        self.assertEqual([s['url'] for s in shortcuts], [reverse('core:my_workouts'), reverse('core:my_history')])
        self.assertTrue(all(s['icons'][0]['src'].startswith('/static/') for s in shortcuts))

    def test_trainer_shortcuts(self):
        self.login(self.trainer)
        urls = [s['url'] for s in self.manifest()[1]['shortcuts']]
        self.assertEqual(urls, [reverse('core:agenda'), reverse('core:student_list'), reverse('core:search')])

    def test_every_shortcut_opens_for_its_role(self):
        for user in (self.alice, self.trainer):
            self.login(user)
            for shortcut in self.manifest()[1]['shortcuts']:
                self.assertEqual(self.client.get(shortcut['url']).status_code, 200, (user.username, shortcut['url']))

    def test_page_fetches_the_manifest_with_credentials(self):
        html = self.client.get(reverse('account_login')).content.decode()
        self.assertIn('rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials"', html)
        self.assertIn('apple-mobile-web-app-status-bar-style', html)


class AppFeelTests(BaseTestCase):
    def test_every_page_has_the_offline_notice_and_script(self):
        for user in (None, self.alice, self.trainer):
            if user:
                self.login(user)
            html = self.client.get(reverse('core:home'), follow=True).content.decode()
            self.assertIn('data-offline-banner', html)
            self.assertIn('js/pwa.js', html)
            self.assertIn('role="status"', html[html.index('data-offline-banner') - 60:])

    def test_install_card_only_on_the_home_of_signed_in_users(self):
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('core:home')), 'data-install-card')
        for user in (self.alice, self.trainer):
            self.login(user)
            html = self.client.get(reverse('core:home')).content.decode()
            self.assertEqual(html.count('data-install-card'), 1)
            self.assertIn('d-none', html[html.index('data-install-card') - 120:html.index('data-install-card')])  # hidden until the script decides
            self.assertIn('Adicionar à Tela de Início', html)
        self.assertNotContains(self.client.get(reverse('core:student_list')), 'data-install-card')

    def test_workout_page_is_marked_for_the_offline_note(self):
        self.login(self.alice)
        self.client.post(reverse('core:workout_start', kwargs={'pk': self.alice_routine.pk}))
        html = self.client.get(reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk})).content.decode()
        self.assertIn('data-workout-page', html)
