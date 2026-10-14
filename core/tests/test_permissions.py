from django.urls import reverse

from .base import BaseTestCase

STAFF_GET_URLS = [
    ('core:student_list', {}),
    ('core:student_create', {}),
    ('core:student_detail', lambda t: {'pk': t.alice.person.pk}),
    ('core:student_edit', lambda t: {'pk': t.alice.person.pk}),
    ('core:student_history', lambda t: {'pk': t.alice.person.pk}),
    ('core:exercise_list', {}),
    ('core:exercise_create', {}),
    ('core:exercise_detail', lambda t: {'pk': t.supino.pk}),
    ('core:exercise_edit', lambda t: {'pk': t.supino.pk}),
    ('core:routine_list', {}),
    ('core:routine_create', {}),
    ('core:routine_detail', lambda t: {'pk': t.template.pk}),
    ('core:routine_edit', lambda t: {'pk': t.template.pk}),
    ('core:routine_generate_ai', {}),
    ('core:person_routine_create', {}),
    ('core:person_routine_detail', lambda t: {'pk': t.alice_routine.pk}),
    ('core:person_routine_edit', lambda t: {'pk': t.alice_routine.pk}),
    ('core:api_exercises_list', {}),
    ('core:api_routines_list', {}),
    ('core:api_routine_exercises', lambda t: {'pk': t.template.pk}),
]

STUDENT_GET_URLS = [
    ('core:my_profile', {}),
    ('core:my_workouts', {}),
    ('core:my_history', {}),
]


def _url(test, name, kwargs):
    return reverse(name, kwargs=kwargs(test) if callable(kwargs) else kwargs)


class StaffAreaPermissionTests(BaseTestCase):
    def test_anonymous_is_sent_to_login(self):
        for name, kwargs in STAFF_GET_URLS:
            with self.subTest(url=name):
                response = self.client.get(_url(self, name, kwargs))
                self.assertEqual(response.status_code, 302)
                self.assertIn('/accounts/login/', response['Location'])

    def test_student_is_forbidden(self):
        self.login(self.alice)
        for name, kwargs in STAFF_GET_URLS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(_url(self, name, kwargs)).status_code, 403)

    def test_student_cannot_post_to_staff_endpoints(self):
        self.login(self.alice)
        response = self.client.post(
            reverse('core:person_routine_deactivate', kwargs={'pk': self.bob_routine.pk}))
        self.assertEqual(response.status_code, 403)
        self.bob_routine.refresh_from_db()
        self.assertTrue(self.bob_routine.is_active)

        response = self.client.post(reverse('core:exercise_create'), {'exercise_name': 'Hack'})
        self.assertEqual(response.status_code, 403)

    def test_trainer_can_open_staff_pages(self):
        self.login(self.trainer)
        for name, kwargs in STAFF_GET_URLS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(_url(self, name, kwargs)).status_code, 200)


class StudentAreaPermissionTests(BaseTestCase):
    def test_anonymous_is_sent_to_login(self):
        for name, kwargs in STUDENT_GET_URLS:
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn('/accounts/login/', response['Location'])

    def test_student_can_open_own_pages(self):
        self.login(self.alice)
        for name, kwargs in STUDENT_GET_URLS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_trainer_without_student_profile_is_redirected(self):
        self.login(self.trainer)
        for name, kwargs in STUDENT_GET_URLS:
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                self.assertRedirects(response, reverse('core:home'), fetch_redirect_response=False)
