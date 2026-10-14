import os
import tempfile
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from allauth.account.models import EmailAddress

from core.dashboard import inactive_students, pending_invites, trainer_dashboard
from core.forms import PersonForm, StudentProfileForm
from core.invites import build_invite_path, get_user_from_invite, revoke_invites
from core.models import Assessment, Person, PersonRoutine, ProgressPhoto, StudentNote, WorkoutSession

from .base import BaseTestCase
from .test_trainer_file import photo_upload

MEDIA = tempfile.mkdtemp(prefix='mt-test-corrections-')


def new_student_data(**extra):
    data = {'person_name': 'Carla Souza', 'email': 'carla@example.com', 'is_active': 'on'}
    data.update(extra)
    return data


class NotesAreInternalTests(BaseTestCase):
    def test_student_form_has_no_notes_field(self):
        self.assertNotIn('notes', StudentProfileForm().fields)

    def test_student_saving_profile_keeps_trainer_notes(self):
        person = self.alice.person
        person.notes = 'Cliente difícil, cobrar adiantado'
        person.save()
        self.login(self.alice)
        page = self.client.get(reverse('core:my_profile'))
        self.assertNotContains(page, 'Cliente difícil')
        self.client.post(reverse('core:my_profile'), {'person_name': 'Alice Nova'})
        person.refresh_from_db()
        self.assertEqual(person.person_name, 'Alice Nova')
        self.assertEqual(person.notes, 'Cliente difícil, cobrar adiantado')

    def test_student_cannot_inject_notes_by_posting_the_field(self):
        self.login(self.alice)
        self.client.post(reverse('core:my_profile'), {'person_name': 'Alice', 'notes': 'hack'})
        self.assertEqual(Person.objects.get(pk=self.alice.person.pk).notes, '')

    def test_trainer_form_labels_notes_as_internal(self):
        self.login(self.trainer)
        response = self.client.get(reverse('core:student_edit', kwargs={'pk': self.alice.person.pk}))
        self.assertContains(response, 'o aluno não vê')


@override_settings(MEDIA_ROOT=MEDIA)
class DeleteStudentTests(BaseTestCase):
    def setUp(self):
        self.person = self.alice.person
        self.url = reverse('core:student_delete', kwargs={'pk': self.person.pk})

    def make_photo(self):
        with self.captureOnCommitCallbacks(execute=True):
            ProgressPhoto.objects.create(person=self.person, date=timezone.localdate(), image=photo_upload())
        return ProgressPhoto.objects.get(person=self.person)

    def test_only_the_trainer_can_delete(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)  # anonymous -> login
        self.login(self.bob)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertTrue(Person.objects.filter(pk=self.person.pk).exists())

    def test_get_asks_for_confirmation_and_deletes_nothing(self):
        self.login(self.trainer)
        Assessment.objects.create(person=self.person, weight_kg=60)
        response = self.client.get(self.url)
        self.assertContains(response, 'não pode ser desfeita')
        self.assertContains(response, '1 avaliaç')
        self.assertTrue(Person.objects.filter(pk=self.person.pk).exists())

    def test_post_removes_account_data_and_files(self):
        self.login(self.trainer)
        photo = self.make_photo()
        paths = [photo.image.path, photo.thumbnail.path]
        self.assertTrue(all(os.path.exists(p) for p in paths))
        StudentNote.objects.create(person=self.person, text='x')
        EmailAddress.objects.create(user=self.alice, email=self.alice.email, primary=True)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url)
        self.assertRedirects(response, reverse('core:student_list'))
        self.assertFalse(User.objects.filter(pk=self.alice.pk).exists())
        self.assertFalse(Person.objects.filter(pk=self.person.pk).exists())
        self.assertFalse(PersonRoutine.objects.filter(pk=self.alice_routine.pk).exists())
        self.assertFalse(StudentNote.objects.exists())
        self.assertFalse(any(os.path.exists(p) for p in paths))
        # the other student is untouched
        self.assertTrue(Person.objects.filter(pk=self.bob.person.pk).exists())

    def test_never_deletes_a_staff_account(self):
        staff_person = Person.objects.create(user=self.trainer, person_name='Personal')
        self.login(self.trainer)
        url = reverse('core:student_delete', kwargs={'pk': staff_person.pk})
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.trainer.pk).exists())

    def test_deleting_person_removes_login_and_photo_files(self):
        """The admin (or any code) deleting just the Person must not leave a working login/files behind."""
        photo = self.make_photo()
        paths = [photo.image.path, photo.thumbnail.path]
        with self.captureOnCommitCallbacks(execute=True):
            self.person.delete()
        self.assertFalse(User.objects.filter(pk=self.alice.pk).exists())
        self.assertFalse(any(os.path.exists(p) for p in paths))

    def test_deleting_person_keeps_staff_user(self):
        staff_person = Person.objects.create(user=self.trainer, person_name='Personal')
        staff_person.delete()
        self.assertTrue(User.objects.filter(pk=self.trainer.pk).exists())

    def test_breadcrumb_and_buttons(self):
        self.login(self.trainer)
        self.assertContains(self.client.get(self.url), 'aria-label="breadcrumb"')
        detail = self.client.get(reverse('core:student_detail', kwargs={'pk': self.person.pk}))
        self.assertContains(detail, self.url)


@override_settings(MEDIA_ROOT=MEDIA)
class AvatarReplacementTests(BaseTestCase):
    def test_old_avatar_file_is_removed_when_replaced_or_cleared(self):
        person = self.alice.person
        with self.captureOnCommitCallbacks(execute=True):
            person.avatar = photo_upload(name='a.jpg')
            person.save()
        first = person.avatar.path
        self.assertTrue(os.path.exists(first))
        with self.captureOnCommitCallbacks(execute=True):
            person.avatar = photo_upload(name='b.jpg')
            person.save()
        second = person.avatar.path
        self.assertNotEqual(first, second)
        self.assertFalse(os.path.exists(first))
        self.assertTrue(os.path.exists(second))
        with self.captureOnCommitCallbacks(execute=True):
            person.avatar = None
            person.save()
        self.assertFalse(os.path.exists(second))

    def test_saving_without_change_keeps_file(self):
        person = self.alice.person
        with self.captureOnCommitCallbacks(execute=True):
            person.avatar = photo_upload(name='a.jpg')
            person.save()
        path = person.avatar.path
        with self.captureOnCommitCallbacks(execute=True):
            Person.objects.get(pk=person.pk).save()
        self.assertTrue(os.path.exists(path))


class AuditTrailTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.person = self.alice.person
        self.status_url = reverse('core:student_status', kwargs={'pk': self.person.pk})

    def test_deactivation_date_is_recorded_and_cleared(self):
        self.client.post(self.status_url, {'status': 'paused', 'reason': 'Viagem'})
        self.person.refresh_from_db()
        self.assertFalse(self.person.is_active)
        first = self.person.deactivated_at
        self.assertIsNotNone(first)
        self.client.post(self.status_url, {'status': 'former', 'reason': 'Mudou de cidade'})  # still closed: date stays
        self.person.refresh_from_db()
        self.assertEqual(self.person.deactivated_at, first)
        self.client.post(self.status_url, {'status': 'active'})
        self.person.refresh_from_db()
        self.assertIsNone(self.person.deactivated_at)
        self.assertTrue(self.person.is_active)

    def test_file_shows_access_state(self):
        detail = reverse('core:student_detail', kwargs={'pk': self.person.pk})
        self.assertContains(self.client.get(detail), 'Senha criada, ainda sem acesso')
        self.alice.last_login = timezone.now()
        self.alice.save()
        self.assertContains(self.client.get(detail), 'Último acesso em')
        self.alice.set_unusable_password()
        self.alice.save()
        self.assertContains(self.client.get(detail), 'Aguardando convite')
        self.client.post(self.status_url, {'status': 'paused', 'reason': 'Viagem'})
        self.assertContains(self.client.get(detail), 'Pausado')


class ContactValidationTests(BaseTestCase):
    def form(self, **extra):
        return PersonForm(new_student_data(**extra))

    def test_birth_date_rules(self):
        future = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertIn('birth_date', self.form(birth_date=future).errors)
        self.assertIn('birth_date', self.form(birth_date='1850-01-01').errors)
        self.assertNotIn('birth_date', self.form(birth_date='1990-05-17').errors)

    def test_phone_is_validated_and_normalized(self):
        for bad in ('abc', '123', '1234567890123456'):
            self.assertIn('phone', self.form(phone=bad).errors, bad)
        for raw in ('11987654321', '(11) 98765-4321', '+55 11 98765-4321', '11 98765 4321'):
            f = self.form(phone=raw)
            self.assertTrue(f.is_valid(), (raw, f.errors))
            self.assertEqual(f.cleaned_data['phone'], '(11) 98765-4321')
        f = self.form(phone='1133224455')
        self.assertTrue(f.is_valid())
        self.assertEqual(f.cleaned_data['phone'], '(11) 3322-4455')
        self.assertTrue(self.form(phone='').is_valid())

    def test_state_is_uppercased_and_checked(self):
        f = self.form(state='sp')
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data['state'], 'SP')
        self.assertIn('state', self.form(state='XX').errors)

    def test_zip_code(self):
        f = self.form(zip_code='01310100')
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data['zip_code'], '01310-100')
        self.assertIn('zip_code', self.form(zip_code='xx').errors)
        self.assertIn('zip_code', self.form(zip_code='123').errors)

    def test_emergency_phone_is_validated(self):
        self.assertIn('emergency_contact_phone', self.form(emergency_contact_phone='abc').errors)

    def test_student_profile_form_shares_the_rules(self):
        self.login(self.alice)
        response = self.client.post(reverse('core:my_profile'), {
            'person_name': 'Alice', 'phone': 'abc', 'state': 'xx', 'zip_code': 'x',
            'birth_date': (timezone.localdate() + timedelta(days=5)).isoformat()})
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(set(form.errors), {'phone', 'state', 'zip_code', 'birth_date'})

    def test_student_profile_saves_normalized_values(self):
        self.login(self.alice)
        self.client.post(reverse('core:my_profile'), {
            'person_name': 'Alice', 'phone': '11987654321', 'state': 'rj', 'zip_code': '20040020'})
        person = Person.objects.get(pk=self.alice.person.pk)
        self.assertEqual((person.phone, person.state, person.zip_code), ('(11) 98765-4321', 'RJ', '20040-020'))

    def test_email_longer_than_username_limit_is_rejected_not_a_500(self):
        long_email = 'a' * 145 + '@example.com'  # 157 chars, valid as an e-mail but not as a username
        self.login(self.trainer)
        response = self.client.post(reverse('core:student_create'), new_student_data(email=long_email))
        self.assertEqual(response.status_code, 200)
        self.assertIn('email', response.context['form'].errors)
        self.assertFalse(Person.objects.filter(person_name='Carla Souza').exists())


class DuplicateTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.alice.person.phone = '(11) 98765-4321'
        self.alice.person.save()

    def create(self, **extra):
        return self.client.post(reverse('core:student_create'), new_student_data(**extra))

    def test_same_phone_is_blocked_until_confirmed(self):
        response = self.create(person_name='Outra Pessoa', phone='11987654321')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Possível cadastro duplicado')
        self.assertContains(response, 'mesmo telefone')
        self.assertFalse(Person.objects.filter(person_name='Outra Pessoa').exists())
        response = self.create(person_name='Outra Pessoa', phone='11987654321', confirm_duplicate='on')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Person.objects.filter(person_name='Outra Pessoa').exists())

    def test_same_name_ignoring_case_is_blocked(self):
        response = self.create(person_name='alice', email='other@example.com')
        self.assertContains(response, 'mesmo nome')
        self.assertFalse(User.objects.filter(email='other@example.com').exists())

    def test_editing_a_student_does_not_flag_itself(self):
        response = self.client.post(reverse('core:student_edit', kwargs={'pk': self.alice.person.pk}), {
            'person_name': 'Alice', 'email': self.alice.email, 'phone': '11987654321', 'is_active': 'on'})
        self.assertEqual(response.status_code, 302)

    def test_distinct_students_are_not_flagged(self):
        self.assertEqual(self.create(person_name='Carla Souza', phone='21999998888').status_code, 302)


class InviteRevocationTests(BaseTestCase):
    def setUp(self):
        self.alice.set_unusable_password()
        self.alice.save()
        self.person = Person.objects.get(pk=self.alice.person.pk)

    def link_parts(self):
        user = User.objects.select_related('person').get(pk=self.alice.pk)
        path = build_invite_path(user)
        return path.split('/')[2], path.split('/')[3]

    def test_new_link_revokes_the_previous_one(self):
        uid, token = self.link_parts()
        self.assertIsNotNone(get_user_from_invite(uid, token))
        revoke_invites(Person.objects.get(pk=self.person.pk))
        self.assertIsNone(get_user_from_invite(uid, token))
        new_uid, new_token = self.link_parts()
        self.assertNotEqual(token, new_token)
        self.assertIsNotNone(get_user_from_invite(new_uid, new_token))

    def test_regenerating_without_revoking_keeps_link_stable(self):
        self.assertEqual(self.link_parts(), self.link_parts())

    def test_trainer_revokes_through_the_page(self):
        self.login(self.trainer)
        url = reverse('core:student_invite', kwargs={'pk': self.person.pk})
        old = self.client.get(url).context['invite_url']
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        new = self.client.get(url).context['invite_url']
        self.assertNotEqual(old, new)
        self.client.logout()
        self.assertEqual(self.client.get(old[old.index('/invite/'):]).status_code, 400)
        self.assertEqual(self.client.get(new[new.index('/invite/'):]).status_code, 200)

    def test_students_cannot_revoke(self):
        self.login(self.bob)
        url = reverse('core:student_invite', kwargs={'pk': self.person.pk})
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(Person.objects.get(pk=self.person.pk).invite_version, 0)


class DashboardInviteTests(BaseTestCase):
    def setUp(self):
        old = timezone.now() - timedelta(days=30)
        Person.objects.filter(pk__in=[self.alice.person.pk, self.bob.person.pk]).update(created_at=old)

    def test_student_who_never_accepted_invite_is_pending_not_inactive(self):
        self.bob.set_unusable_password()
        self.bob.save()
        names = [r['student'].person_name for r in inactive_students()]
        self.assertIn('Alice', names)
        self.assertNotIn('Bob', names)
        self.assertEqual([r['student'].person_name for r in pending_invites()], ['Bob'])

    def test_dashboard_card_links_to_invite_page(self):
        self.bob.set_unusable_password()
        self.bob.save()
        self.assertEqual(trainer_dashboard()['invites_total'], 1)
        self.login(self.trainer)
        response = self.client.get(reverse('core:home'))
        self.assertContains(response, 'Aguardando convite')
        self.assertContains(response, reverse('core:student_invite', kwargs={'pk': self.bob.person.pk}))

    def test_no_card_without_pending_invites(self):
        self.login(self.trainer)
        self.assertNotContains(self.client.get(reverse('core:home')), 'Aguardando convite')


class PasswordLinksTests(BaseTestCase):
    def test_change_password_link_in_menu(self):
        for user in (self.alice, self.trainer):
            self.login(user)
            self.assertContains(self.client.get(reverse('core:home')), reverse('account_change_password'))
        self.assertEqual(self.client.get(reverse('account_change_password')).status_code, 200)

    def test_login_explains_how_to_recover_without_email(self):
        self.assertContains(self.client.get(reverse('account_login')), 'peça um novo link de acesso')

    def test_reset_link_works_for_student_with_password(self):
        self.login(self.trainer)
        url = reverse('core:student_invite', kwargs={'pk': self.alice.person.pk})
        response = self.client.get(url)
        self.assertTrue(response.context['has_password'])
        self.assertContains(response, 'Revogar e gerar novo link')


class StudentListTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)
        self.url = reverse('core:student_list')

    def make_students(self, n, start=0):
        for i in range(start, start + n):
            user = User.objects.create_user(f's{i}', f's{i}@example.com', 'pw')
            person = Person.objects.create(user=user, person_name=f'Aluno {i:02d}')
            routine = PersonRoutine.objects.create(person=person, routine_name='T')
            WorkoutSession.objects.create(person_routine=routine, is_completed=True)

    def names(self, response):
        return [s.person_name for s in response.context['students']]

    def count_queries(self, **params):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(self.url, params)
        return len(ctx)

    def test_query_count_does_not_grow_with_students(self):
        self.make_students(3)
        few = {mode: self.count_queries(view=mode) for mode in ('grid', 'list')}
        self.make_students(20, start=3)
        many = {mode: self.count_queries(view=mode) for mode in ('grid', 'list')}
        self.assertEqual(few, many)
        self.assertLessEqual(many['list'], 10)  # constant, plus the bottom bar's badge counts

    def test_statistics_are_annotated(self):
        self.make_students(1)
        student = next(s for s in self.client.get(self.url).context['students'] if s.person_name == 'Aluno 00')
        self.assertEqual(student.total_workouts, 1)
        self.assertIsNotNone(student.last_workout_at)
        self.assertTrue(student.has_routine)

    def test_pagination_keeps_filters(self):
        self.make_students(30)
        response = self.client.get(self.url, {'search': 'Aluno'})
        self.assertEqual(response.context['page'].paginator.count, 30)
        self.assertEqual(len(response.context['students']), 24)
        self.assertContains(response, 'search=Aluno&view=grid&page=2')
        second = self.client.get(self.url, {'search': 'Aluno', 'page': 2})
        self.assertEqual(len(second.context['students']), 6)
        self.assertEqual(self.client.get(self.url, {'page': 'nope'}).status_code, 200)
        self.assertEqual(self.client.get(self.url, {'page': 999}).status_code, 200)

    def test_phone_search_ignores_formatting(self):
        Person.objects.filter(pk=self.alice.person.pk).update(phone='(11) 98765-4321')
        for term in ('11987654321', '98765-4321', '(11) 98765', '987654321', '+55 11 98765'):
            self.assertEqual(self.names(self.client.get(self.url, {'search': term})), ['Alice'], term)
        self.assertEqual(self.names(self.client.get(self.url, {'search': '99999'})), [])

    def test_id_search_is_exact_with_hash_and_not_fuzzy(self):
        first = self.alice.person.pk
        self.assertEqual(self.names(self.client.get(self.url, {'search': f'#{first}'})), ['Alice'])
        self.assertEqual(self.names(self.client.get(self.url, {'search': '#99999'})), [])
        # a bare number is no longer matched against ids ("1" used to match #1, #10, #11...)
        self.assertEqual(self.names(self.client.get(self.url, {'search': str(first)})), [])

    def test_search_by_name_and_email(self):
        self.assertEqual(self.names(self.client.get(self.url, {'search': 'bob@'})), ['Bob'])
        self.assertEqual(self.names(self.client.get(self.url, {'search': 'ali'})), ['Alice'])

    def test_filters(self):
        today = timezone.localdate()
        a, b = self.alice.person, self.bob.person
        a.plan_expires_on = today + timedelta(days=5)
        a.save()
        b.plan_expires_on = today - timedelta(days=2)
        b.save()
        names = lambda f: self.names(self.client.get(self.url, {'filtro': f}))
        self.assertEqual(names('plan_expiring'), ['Alice'])
        self.assertEqual(names('plan_expired'), ['Bob'])
        self.assertEqual(sorted(names('inactive_7d')), ['Alice', 'Bob'])  # both have routines, never trained
        # a recent workout takes Alice out
        WorkoutSession.objects.create(person_routine=self.alice_routine, is_completed=True)
        self.assertEqual(names('inactive_7d'), ['Bob'])
        PersonRoutine.objects.filter(person=b).update(is_active=False)
        self.assertEqual(names('no_routine'), ['Bob'])
        self.bob.set_unusable_password()
        self.bob.save()
        self.assertEqual(names('invite_pending'), ['Bob'])
        self.assertEqual(names('inactive_7d'), [])  # pending invite is not "inactive"
        self.assertEqual(sorted(names('')), ['Alice', 'Bob'])
        self.assertEqual(sorted(names('bogus')), ['Alice', 'Bob'])  # unknown filter is ignored

    def test_filters_combine_with_status_and_search(self):
        Person.objects.filter(pk=self.bob.person.pk).update(is_active=False)
        response = self.client.get(self.url, {'status': 'active', 'search': 'b'})
        self.assertEqual(self.names(response), [])
        response = self.client.get(self.url, {'status': 'inactive', 'search': 'b'})
        self.assertEqual(self.names(response), ['Bob'])

    def test_search_term_is_escaped(self):
        response = self.client.get(self.url, {'search': '"><script>alert(1)</script>'})
        self.assertNotContains(response, '<script>alert(1)</script>')
