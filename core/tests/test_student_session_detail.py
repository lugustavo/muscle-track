from django.urls import reverse

from core.models import SessionExercise, SessionSet, WorkoutSession

from .base import BaseTestCase


class StudentSessionDetailTests(BaseTestCase):
    """The student's history opens the same session detail the trainer sees, with the student's own wording."""

    def setUp(self):
        self.session = WorkoutSession.objects.create(person_routine=self.alice_routine, notes='Treino pesado hoje')
        prx = self.alice_routine.exercises.first()
        log = SessionExercise.objects.create(session=self.session, person_routine_exercise=prx, notes='Ombro doendo um pouco')
        SessionSet.objects.create(session_exercise=log, number=1, weight=40, reps=10, is_done=True)
        SessionSet.objects.create(session_exercise=log, number=2, weight=42.5, reps=8, is_done=True)
        log.sync_from_sets()
        self.session.end_session()
        self.detail = reverse('core:session_detail', kwargs={'pk': self.session.pk})
        self.login(self.alice)

    def test_each_session_in_the_history_links_to_its_detail(self):
        html = self.client.get(reverse('core:my_history')).content.decode()
        self.assertEqual(html.count('data-history-item'), 1)
        self.assertIn(f'href="{self.detail}?next=', html)
        self.assertIn('Ver os detalhes de Treino de Alice', html)

    def test_the_detail_shows_the_sets_and_the_students_own_notes(self):
        response = self.client.get(self.detail)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Supino')
        self.assertContains(response, '40,00 kg')
        self.assertContains(response, '42,50 kg')
        self.assertContains(response, 'Seu comentário sobre o treino:')
        self.assertContains(response, 'Treino pesado hoje')
        self.assertContains(response, '<strong>Sua observação:</strong> Ombro doendo um pouco')
        self.assertNotContains(response, 'do aluno')

    def test_the_trainer_keeps_the_trainer_wording(self):
        self.login(self.trainer)
        response = self.client.get(self.detail)
        self.assertContains(response, 'Comentário do aluno sobre o treino:')
        self.assertContains(response, 'Observação do aluno:')
        self.assertNotContains(response, 'Seu comentário')

    def test_back_returns_to_the_history(self):
        history = reverse('core:my_history')
        response = self.client.get(self.detail, {'next': history})
        self.assertEqual(response.context['back_url'], history)
        self.assertEqual(response.context['back_label'], 'Voltar ao histórico')

    def test_a_student_cannot_open_someone_elses_session(self):
        other = WorkoutSession.objects.create(person_routine=self.bob_routine)
        other.end_session()
        self.assertEqual(self.client.get(reverse('core:session_detail', kwargs={'pk': other.pk})).status_code, 403)

    def test_the_trainers_links_in_the_detail_are_not_shown_to_the_student(self):
        html = self.client.get(self.detail).content.decode()
        self.assertNotIn(reverse('core:student_detail', kwargs={'pk': self.alice.person.pk}), html)
        self.assertNotIn(reverse('core:person_routine_detail', kwargs={'pk': self.alice_routine.pk}), html)

    def test_the_history_still_lists_only_the_last_twenty(self):
        for _ in range(22):
            extra = WorkoutSession.objects.create(person_routine=self.alice_routine)
            extra.end_session()
        html = self.client.get(reverse('core:my_history')).content.decode()
        self.assertEqual(html.count('data-history-item'), 20)
