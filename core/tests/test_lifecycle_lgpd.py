import io
import json
import tempfile
import zipfile
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core import consent as consent_module
from core.consent import has_current_consent, record_consent
from core.dashboard import erasure_requests, long_trials
from core.forms import PersonForm
from core.models import (
    Assessment, ConsentRecord, Person, ProgressPhoto, StudentEvent, StudentNote, WorkoutSession,
)

from .base import BaseTestCase
from .test_trainer_file import photo_upload

MEDIA = tempfile.mkdtemp(prefix='mt-test-lifecycle-')


def new_student_data(**extra):
    data = {'person_name': 'Carla Souza', 'email': 'carla@example.com'}
    data.update(extra)
    return data


class LifecycleTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.url = reverse('core:student_status', kwargs={'pk': self.person.pk})

    def test_set_status_keeps_access_and_history_in_sync(self):
        self.assertTrue(self.person.set_status('paused', 'Viagem', actor=self.trainer))
        self.person.refresh_from_db()
        self.alice.refresh_from_db()
        self.assertEqual((self.person.status, self.person.is_active, self.alice.is_active), ('paused', False, False))
        self.assertIsNotNone(self.person.deactivated_at)
        self.assertEqual(self.person.status_reason, 'Viagem')
        event = self.person.events.get(kind='status')
        self.assertEqual(event.actor, self.trainer)
        self.assertEqual(event.changes['status'], ['active', 'paused'])
        self.assertTrue(self.person.set_status('active', actor=self.trainer))
        self.person.refresh_from_db()
        self.alice.refresh_from_db()
        self.assertEqual((self.person.is_active, self.alice.is_active, self.person.deactivated_at), (True, True, None))

    def test_no_change_means_no_event(self):
        self.assertFalse(self.person.set_status('active', ''))
        self.assertFalse(self.person.events.filter(kind='status').exists())

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ValueError):
            self.person.set_status('banished')

    def test_reason_is_required_to_pause_or_close(self):
        self.login(self.trainer)
        for status in ('paused', 'former'):
            response = self.client.post(self.url, {'status': status, 'reason': '  '})
            self.assertEqual(response.status_code, 200)
            self.assertIn('reason', response.context['form'].errors)
        self.person.refresh_from_db()
        self.assertEqual(self.person.status, 'active')
        self.assertEqual(self.client.post(self.url, {'status': 'trial', 'reason': ''}).status_code, 302)

    def test_only_the_trainer_changes_status(self):
        self.login(self.bob)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'status': 'former', 'reason': 'x'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {'status': 'former', 'reason': 'x'}).status_code, 302)
        self.assertEqual(Person.objects.get(pk=self.person.pk).status, 'active')

    def test_paused_student_cannot_log_in_or_use_invite(self):
        self.person.set_status('paused', 'Viagem')
        self.assertFalse(self.client.login(username='alice', password='pw-alice'))

    def test_create_with_trial_status_and_event(self):
        self.login(self.trainer)
        response = self.client.post(reverse('core:student_create'), new_student_data(status='trial'))
        self.assertEqual(response.status_code, 302)
        person = Person.objects.get(person_name='Carla Souza')
        self.assertEqual((person.status, person.is_active), ('trial', True))
        self.assertIsNotNone(person.status_changed_at)
        self.assertEqual(person.events.get().kind, 'created')

    def test_create_only_offers_open_statuses_and_defaults_to_active(self):
        values = [v for v, _ in PersonForm().fields['status'].choices]
        self.assertEqual(sorted(values), ['active', 'trial'])
        form = PersonForm(new_student_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['status'], 'active')
        self.assertFalse(PersonForm(new_student_data(status='former')).is_valid())

    def test_edit_form_cannot_change_status(self):
        self.login(self.trainer)
        self.client.post(reverse('core:student_edit', kwargs={'pk': self.person.pk}), {
            'person_name': 'Alice', 'email': self.alice.email, 'status': 'former'})
        self.assertEqual(Person.objects.get(pk=self.person.pk).status, 'active')
        self.assertNotIn('status', PersonForm(instance=self.person).fields)

    def test_list_filters_by_lifecycle(self):
        self.bob.person.set_status('paused', 'x')
        self.login(self.trainer)
        names = lambda f: [s.person_name for s in self.client.get(reverse('core:student_list'), {'filtro': f}).context['students']]
        self.assertEqual(names('paused'), ['Bob'])
        self.assertEqual(names('trial'), [])
        self.person.set_status('former', 'x')
        self.assertEqual(names('former'), ['Alice'])
        # the old status filter keeps meaning "has access / has no access"
        response = self.client.get(reverse('core:student_list'), {'status': 'inactive'})
        self.assertEqual(sorted(s.person_name for s in response.context['students']), ['Alice', 'Bob'])

    def test_data_migration_rule(self):
        """is_active=False rows become 'former' (same rule as migration 0012)."""
        Person.objects.filter(pk=self.person.pk).update(is_active=False)
        from importlib import import_module
        migration = import_module('core.migrations.0012_lifecycle_history_consent')
        from django.apps import apps
        migration.inactive_students_become_former(apps, None)
        self.assertEqual(Person.objects.get(pk=self.person.pk).status, 'former')
        self.assertEqual(Person.objects.get(pk=self.bob.person.pk).status, 'active')

    def test_dashboard_lists_long_trials(self):
        self.person.set_status('trial', '')
        self.assertEqual(long_trials(), [])
        Person.objects.filter(pk=self.person.pk).update(status_changed_at=timezone.now() - timedelta(days=20))
        self.assertEqual([r['student'].pk for r in long_trials()], [self.person.pk])
        self.login(self.trainer)
        self.assertContains(self.client.get(reverse('core:home')), 'Experimental há mais de 14 dias')


class HistoryTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.edit_url = reverse('core:student_edit', kwargs={'pk': self.person.pk})

    def trainer_edit(self, **extra):
        data = {'person_name': 'Alice', 'email': self.alice.email}
        data.update(extra)
        return self.client.post(self.edit_url, data)

    def test_only_changed_fields_are_recorded_with_actor(self):
        self.login(self.trainer)
        self.trainer_edit(phone='11987654321', city='Santos')
        event = self.person.events.get(kind='profile_edit')
        self.assertEqual(event.actor, self.trainer)
        self.assertEqual(set(event.changes), {'phone', 'city'})
        self.assertEqual(event.changes['phone'], ['', '(11) 98765-4321'])
        self.trainer_edit(phone='11987654321', city='Santos')  # nothing changed
        self.assertEqual(self.person.events.filter(kind='profile_edit').count(), 1)

    def test_email_change_is_recorded(self):
        self.login(self.trainer)
        self.trainer_edit(email='nova@example.com')
        self.assertEqual(self.person.events.get(kind='profile_edit').changes['email'],
                         ['alice@example.com', 'nova@example.com'])

    def test_sensitive_text_is_never_stored(self):
        self.login(self.trainer)
        self.trainer_edit(health_notes='Hérnia de disco L5', notes='Cliente difícil')
        event = self.person.events.get(kind='profile_edit')
        self.assertEqual(event.changes['health_notes'], [None, None])
        self.assertEqual(event.changes['notes'], [None, None])
        self.assertNotIn('Hérnia', json.dumps(event.changes))
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, 'alterado')

    def test_student_edits_are_attributed_to_the_student(self):
        self.login(self.alice)
        self.client.post(reverse('core:my_profile'), {'person_name': 'Alice Nova', 'phone': '11987654321'})
        event = self.person.events.get(kind='profile_edit')
        self.assertEqual(event.actor, self.alice)
        self.assertEqual(event.changes['person_name'], ['Alice', 'Alice Nova'])

    def test_avatar_change_does_not_store_file_names(self):
        with override_settings(MEDIA_ROOT=MEDIA):
            self.login(self.alice)
            self.client.post(reverse('core:my_profile'), {'person_name': 'Alice', 'avatar': photo_upload('me.jpg')})
        self.assertEqual(self.person.events.get(kind='profile_edit').changes['avatar'], ['', 'nova foto'])

    def test_history_tab_is_for_the_trainer_only(self):
        self.login(self.trainer)
        self.trainer_edit(city='Santos')
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, 'id="tab-historico"')
        self.assertContains(page, 'Santos')
        self.login(self.alice)
        self.assertEqual(self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).status_code, 403)

    def test_invite_events(self):
        self.login(self.trainer)
        self.client.post(reverse('core:student_invite', kwargs={'pk': self.person.pk}))
        self.assertTrue(self.person.events.filter(kind='invite_revoked', actor=self.trainer).exists())

    def test_history_is_deleted_with_the_student(self):
        self.person.set_status('paused', 'x')
        pk = self.person.pk
        self.login(self.trainer)
        self.client.post(reverse('core:student_delete', kwargs={'pk': pk}))
        self.assertFalse(StudentEvent.objects.filter(person_id=pk).exists())
        self.assertFalse(ConsentRecord.objects.filter(person_id=pk).exists())

    def test_deletion_log_has_no_personal_data(self):
        self.login(self.trainer)
        with self.assertLogs('core.views', level='INFO') as logs:
            self.client.post(reverse('core:student_delete', kwargs={'pk': self.person.pk}))
        text = '\n'.join(logs.output)
        self.assertIn(f'Student #{self.person.pk} deleted', text)
        self.assertNotIn('Alice', text)
        self.assertNotIn('alice@', text)


class ConsentGateTests(BaseTestCase):
    def setUp(self):
        ConsentRecord.objects.filter(person=self.alice.person).delete()
        StudentEvent.objects.filter(person=self.alice.person).delete()
        self.person = self.alice.person
        self.login(self.alice)

    def accept(self, **extra):
        data = {'terms': 'on', 'health_data': 'on'}
        data.update(extra)
        return self.client.post(reverse('core:consent'), data)

    def test_student_without_consent_is_sent_to_the_consent_screen(self):
        for name in ('core:my_workouts', 'core:my_history', 'core:my_profile'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertTrue(response.url.startswith(reverse('core:consent')), name)
        self.assertIn('next=/my-workouts/', self.client.get(reverse('core:my_workouts')).url)

    def test_consent_page_and_privacy_pages_are_not_gated(self):
        for name in ('core:consent', 'core:my_privacy'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('core:privacy_policy')).status_code, 200)

    def test_workout_api_is_not_redirected(self):
        from core.models import PersonRoutineExercise, SessionExercise, SessionSet
        session = WorkoutSession.objects.create(person_routine=self.alice_routine)
        prx = PersonRoutineExercise.objects.filter(person_routine=self.alice_routine).first()
        log = SessionExercise.objects.create(session=session, person_routine_exercise=prx)
        s = SessionSet.objects.create(session_exercise=log, number=1)
        response = self.client.post(reverse('core:workout_set_update', kwargs={'pk': s.pk}),
                                    data=json.dumps({'done': True, 'weight': 20, 'reps': 10}),
                                    content_type='application/json')
        self.assertNotEqual(response.status_code, 302)

    def test_accepting_records_every_kind_and_continues(self):
        response = self.accept(photos='on')
        self.assertRedirects(response, reverse('core:my_workouts'))
        self.assertTrue(has_current_consent(Person.objects.get(pk=self.person.pk)))
        self.assertEqual(self.person.consents.filter(granted=True).count(), 3)
        self.assertEqual(self.person.events.filter(kind='consent').count(), 3)
        self.assertEqual(self.client.get(reverse('core:my_workouts')).status_code, 200)

    def test_photos_are_optional(self):
        self.accept()
        self.assertFalse(self.person.consents.filter(kind='photos').exists())  # nothing "withdrawn" that was never given
        self.assertEqual(self.person.consents.filter(granted=True).count(), 2)
        self.assertTrue(has_current_consent(self.person))

    def test_both_required_boxes_must_be_checked(self):
        for missing in ('terms', 'health_data'):
            data = {'terms': 'on', 'health_data': 'on'}
            del data[missing]
            response = self.client.post(reverse('core:consent'), data)
            self.assertEqual(response.status_code, 200)
            self.assertIn(missing, response.context['form'].errors)
        self.assertFalse(self.person.consents.exists())

    def test_next_is_followed_only_when_local(self):
        response = self.client.post(reverse('core:consent') + '?next=https://evil.example/', {'terms': 'on', 'health_data': 'on'})
        self.assertRedirects(response, reverse('core:my_workouts'))

    def test_already_consented_student_skips_the_screen(self):
        self.accept()
        self.assertRedirects(self.client.get(reverse('core:consent')), reverse('core:my_workouts'))

    def test_new_text_version_asks_again(self):
        self.accept()
        with override_settings():
            old = consent_module.CURRENT_VERSION
            consent_module.CURRENT_VERSION = '2099-01'
            try:
                self.assertFalse(has_current_consent(Person.objects.get(pk=self.person.pk)))
                self.assertEqual(self.client.get(reverse('core:my_workouts')).status_code, 302)
            finally:
                consent_module.CURRENT_VERSION = old

    def test_trainer_and_anonymous_are_not_gated(self):
        self.login(self.trainer)
        self.assertEqual(self.client.get(reverse('core:student_list')).status_code, 200)

    def test_invite_to_consent_to_workouts(self):
        from core.invites import build_invite_path
        user = User.objects.create_user('nova@example.com', 'nova@example.com')
        user.set_unusable_password()
        user.save()
        Person.objects.create(user=user, person_name='Nova')
        path = build_invite_path(User.objects.get(pk=user.pk))
        self.client.logout()
        response = self.client.post(path, {'new_password1': 'Sup3r-segura-123', 'new_password2': 'Sup3r-segura-123'})
        self.assertRedirects(response, reverse('core:my_workouts'), fetch_redirect_response=False)
        redirected = self.client.get(reverse('core:my_workouts'))
        self.assertEqual(redirected.status_code, 302)
        self.assertTrue(redirected.url.startswith(reverse('core:consent')))
        self.assertTrue(self.client.get(redirected.url).status_code, 200)


class TrainerConsentTests(BaseTestCase):
    def setUp(self):
        ConsentRecord.objects.filter(person=self.alice.person).delete()
        StudentEvent.objects.filter(person=self.alice.person).delete()
        self.person = self.alice.person
        self.url = reverse('core:student_consent_record', kwargs={'pk': self.person.pk})
        self.login(self.trainer)

    def test_trainer_records_consent_on_behalf_with_date(self):
        response = self.client.post(self.url, {'kinds': ['terms', 'health_data'], 'date': '2026-09-01'})
        self.assertEqual(response.status_code, 302)
        records = self.person.consents.all()
        self.assertEqual({r.kind for r in records}, {'terms', 'health_data'})
        self.assertTrue(all(r.source == 'trainer' and r.recorded_by == self.trainer for r in records))
        self.assertEqual(records[0].at.date().isoformat(), '2026-09-01')
        self.assertTrue(has_current_consent(Person.objects.get(pk=self.person.pk)))
        self.assertTrue(self.person.events.filter(kind='consent', summary__contains='registrado pela personal').exists())

    def test_nothing_checked_records_nothing(self):
        self.client.post(self.url, {'date': '2026-09-01'})
        self.assertFalse(self.person.consents.exists())

    def test_student_cannot_record_consent_for_anyone(self):
        self.login(self.bob)
        self.assertEqual(self.client.post(self.url, {'kinds': ['terms']}).status_code, 403)
        self.assertFalse(self.person.consents.exists())

    def test_file_shows_missing_consent_and_list_filter(self):
        page = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(page, 'Sem consentimento')
        response = self.client.get(reverse('core:student_list'), {'filtro': 'consent_pending'})
        self.assertEqual([s.person_name for s in response.context['students']], ['Alice'])
        record_consent(self.person, 'terms', True)
        record_consent(self.person, 'health_data', True)
        response = self.client.get(reverse('core:student_list'), {'filtro': 'consent_pending'})
        self.assertEqual(list(response.context['students']), [])

    @override_settings(MEDIA_ROOT=MEDIA)
    def test_photo_upload_warns_without_photo_consent(self):
        url = reverse('core:photo_upload', kwargs={'pk': self.person.pk})
        self.assertContains(self.client.get(url), 'ainda não autorizou as fotos')
        record_consent(self.person, 'photos', True)
        self.assertNotContains(self.client.get(url), 'ainda não autorizou as fotos')


@override_settings(MEDIA_ROOT=MEDIA)
class DataRightsTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        Person.objects.filter(pk=self.person.pk).update(
            notes='Cliente difícil', health_notes='Joelho operado', phone='(11) 98765-4321')
        Assessment.objects.create(person=self.person, weight_kg=60)
        StudentNote.objects.create(person=self.person, text='Segredo da personal')
        with self.captureOnCommitCallbacks(execute=True):
            ProgressPhoto.objects.create(person=self.person, date=timezone.localdate(), image=photo_upload())
        WorkoutSession.objects.create(person_routine=self.alice_routine, notes='Foi bem')

    def archive(self, response):
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('attachment', response['Content-Disposition'])
        content = b''.join(response.streaming_content)
        zf = zipfile.ZipFile(io.BytesIO(content))
        return zf, json.loads(zf.read('dados.json'))

    def test_student_export_has_own_data_but_not_internal_notes(self):
        self.login(self.alice)
        zf, data = self.archive(self.client.post(reverse('core:my_export')))
        self.assertEqual(data['profile']['health_notes'], 'Joelho operado')
        self.assertEqual(data['profile']['email'], 'alice@example.com')
        self.assertEqual(len(data['assessments']), 1)
        self.assertEqual(len(data['sessions']), 1)
        self.assertEqual(data['routines'][0]['exercises'][0]['exercise'], 'Supino')
        self.assertNotIn('internal_notes', data['profile'])
        self.assertNotIn('trainer_notes', data)
        raw = zf.read('dados.json').decode()
        self.assertNotIn('Segredo da personal', raw)
        self.assertNotIn('Cliente difícil', raw)
        photos = [n for n in zf.namelist() if n.startswith('fotos/')]
        self.assertEqual(len(photos), 1)
        self.assertEqual(data['photos'][0]['file'], photos[0])
        self.assertTrue(self.person.events.filter(kind='data_export', actor=self.alice).exists())

    def test_trainer_export_includes_internal_notes(self):
        self.login(self.trainer)
        zf, data = self.archive(self.client.post(reverse('core:student_export', kwargs={'pk': self.person.pk})))
        self.assertEqual(data['profile']['internal_notes'], 'Cliente difícil')
        self.assertEqual(data['trainer_notes'][0]['text'], 'Segredo da personal')

    def test_export_only_contains_the_students_own_data(self):
        Assessment.objects.create(person=self.bob.person, weight_kg=99)
        self.login(self.alice)
        _, data = self.archive(self.client.post(reverse('core:my_export')))
        self.assertEqual([a['weight_kg'] for a in data['assessments']], [60.0])

    def test_export_needs_post_and_the_right_role(self):
        self.login(self.alice)
        self.assertEqual(self.client.get(reverse('core:my_export')).status_code, 405)
        # a student cannot use the trainer's export of someone else, or of themselves
        self.assertEqual(self.client.post(reverse('core:student_export', kwargs={'pk': self.bob.person.pk})).status_code, 403)
        self.assertEqual(self.client.post(reverse('core:student_export', kwargs={'pk': self.person.pk})).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(reverse('core:my_export')).status_code, 302)

    def test_export_works_without_consent(self):
        ConsentRecord.objects.filter(person=self.person).delete()
        self.login(self.alice)
        self.assertEqual(self.client.post(reverse('core:my_export')).status_code, 200)

    def test_erasure_request_shows_on_dashboard_and_goes_away_with_the_student(self):
        self.login(self.alice)
        page = self.client.get(reverse('core:my_privacy'))
        self.assertContains(page, 'Pedir exclusão')
        self.client.post(reverse('core:my_erasure_request'))
        self.client.post(reverse('core:my_erasure_request'))  # asking twice does not duplicate
        self.assertEqual(self.person.events.filter(kind='erasure_request').count(), 1)
        self.assertContains(self.client.get(reverse('core:my_privacy')), 'Pedido enviado')
        self.assertEqual([r['student'].pk for r in erasure_requests()], [self.person.pk])

        self.login(self.trainer)
        home = self.client.get(reverse('core:home'))
        self.assertContains(home, 'Pedidos de exclusão de dados')
        self.assertContains(home, reverse('core:student_delete', kwargs={'pk': self.person.pk}))
        detail = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(detail, 'Pediu exclusão')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('core:student_delete', kwargs={'pk': self.person.pk}))
        self.assertEqual(erasure_requests(), [])
        self.assertNotContains(self.client.get(reverse('core:home')), 'Pedidos de exclusão de dados')

    def test_withdrawing_photo_consent(self):
        self.login(self.alice)
        self.client.post(reverse('core:my_consent_photos'), {'granted': '0'})
        self.assertFalse(consent_module.is_granted(consent_module.current_consents(self.person)['photos']))
        self.assertTrue(has_current_consent(self.person))  # required ones are untouched
        self.client.post(reverse('core:my_consent_photos'), {'granted': '1'})
        self.assertTrue(consent_module.is_granted(consent_module.current_consents(self.person)['photos']))

    def test_privacy_pages_render(self):
        self.login(self.alice)
        page = self.client.get(reverse('core:my_privacy'))
        for text in ('Baixar meus dados', 'Excluir meus dados', 'Fotos de evolução'):
            self.assertContains(page, text)
        self.assertContains(self.client.get(reverse('core:home')), reverse('core:my_privacy'))

    @override_settings(PRIVACY_CONTROLLER_NAME='Maria Personal', PRIVACY_CONTACT_EMAIL='maria@example.com')
    def test_policy_uses_configured_controller(self):
        self.client.logout()
        page = self.client.get(reverse('core:privacy_policy'))
        self.assertContains(page, 'Maria Personal')
        self.assertContains(page, 'maria@example.com')
        self.assertContains(page, consent_module.CURRENT_VERSION)


class AdminReadOnlyTests(BaseTestCase):
    def test_history_and_consents_cannot_be_added_in_admin(self):
        admin_user = User.objects.create_superuser('root', 'root@example.com', 'pw-root')
        self.login(admin_user)
        for model in ('studentevent', 'consentrecord'):
            self.assertEqual(self.client.get(f'/admin/core/{model}/').status_code, 200)
            self.assertEqual(self.client.get(f'/admin/core/{model}/add/').status_code, 403)


class TrainerConsentStateTests(BaseTestCase):
    """What the trainer records now becomes the current state, whatever date is typed."""

    def setUp(self):
        ConsentRecord.objects.filter(person=self.alice.person).delete()
        self.person = self.alice.person
        self.url = reverse('core:student_consent_record', kwargs={'pk': self.person.pk})
        self.login(self.trainer)

    def state(self):
        return {kind: consent_module.is_granted(record)
                for kind, record in consent_module.current_consents(Person.objects.get(pk=self.person.pk)).items()}

    def test_today_with_a_later_decision_of_the_student_still_wins(self):
        record_consent(self.person, 'photos', False)          # the student withdrew it a moment ago (at = now)
        today = timezone.localdate().isoformat()              # the form's default date: today at 12:00
        self.client.post(self.url, {'kinds': ['terms', 'health_data', 'photos'], 'date': today})
        self.assertEqual(self.state(), {'terms': True, 'health_data': True, 'photos': True})

    def test_an_old_paper_date_does_not_lose_to_a_newer_withdrawal_either(self):
        record_consent(self.person, 'photos', False)
        self.client.post(self.url, {'kinds': ['photos'], 'date': '2026-01-10'})
        self.assertTrue(self.state()['photos'])
        record_consent(self.person, 'photos', False)          # and a later withdrawal still wins over it
        self.assertFalse(self.state()['photos'])

    def test_form_shows_the_granted_ones_checked(self):
        self.client.post(self.url, {'kinds': ['terms', 'photos'], 'date': timezone.localdate().isoformat()})
        html = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk})).content.decode()
        box = lambda kind: html[html.index(f'id="consent-{kind}"'):].split('>')[0]   # noqa: E731
        self.assertIn('checked disabled', box('terms'))
        self.assertIn('checked disabled', box('photos'))
        self.assertNotIn('checked', box('health_data'))
        self.assertIn('já concedido', html)
