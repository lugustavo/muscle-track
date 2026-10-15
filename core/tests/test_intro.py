from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core import intro

from .base import BaseTestCase


class IntroTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person

    def home(self):
        self.client.force_login(self.alice)
        return self.client.get(reverse('core:home'))

    def test_new_student_sees_the_three_steps_on_hoje(self):
        page = self.home()
        self.assertContains(page, 'id="introTour"')
        for step in intro.STEPS:
            self.assertContains(page, step['title'])
        self.assertContains(page, 'intro_tour.js')

    def test_not_shown_once_seen(self):
        self.person.intro_seen_at = timezone.now()
        self.person.save()
        self.assertNotContains(self.home(), 'id="introTour"')

    def test_post_records_it_and_goes_home(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse('core:my_intro'))
        self.assertRedirects(response, reverse('core:home'), fetch_redirect_response=False)
        self.person.refresh_from_db()
        self.assertIsNotNone(self.person.intro_seen_at)

    def test_fetch_post_gets_no_page_back_and_keeps_the_first_time(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse('core:my_intro'), HTTP_X_REQUESTED_WITH='fetch')
        self.assertEqual(response.status_code, 204)
        self.person.refresh_from_db()
        first = self.person.intro_seen_at
        self.client.post(reverse('core:my_intro'), HTTP_X_REQUESTED_WITH='fetch')
        self.person.refresh_from_db()
        self.assertEqual(self.person.intro_seen_at, first)

    def test_page_can_be_opened_again_and_is_linked_from_the_profile(self):
        self.person.intro_seen_at = timezone.now()
        self.person.save()
        self.client.force_login(self.alice)
        page = self.client.get(reverse('core:my_intro'))
        self.assertEqual(page.content.decode().count('data-intro-step='), 3)
        self.assertContains(self.client.get(reverse('core:my_profile')), reverse('core:my_intro'))

    def test_only_students_and_each_one_marks_only_their_own(self):
        self.assertEqual(Client().get(reverse('core:my_intro')).status_code, 302)
        self.client.force_login(self.trainer)
        self.assertNotEqual(self.client.get(reverse('core:my_intro')).status_code, 200)
        self.assertNotContains(self.client.get(reverse('core:home')), 'id="introTour"')
        self.client.force_login(self.bob)
        self.client.post(reverse('core:my_intro'))
        self.person.refresh_from_db()
        self.assertIsNone(self.person.intro_seen_at)

    def test_migration_marks_existing_students_as_seen(self):
        from importlib import import_module
        from django.apps import apps
        migration = import_module('core.migrations.0028_person_intro_seen')
        migration.mark_existing_as_seen(apps, None)
        self.person.refresh_from_db()
        self.assertIsNotNone(self.person.intro_seen_at)
