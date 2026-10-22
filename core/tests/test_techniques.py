"""Exercises counted in seconds and the intensity techniques (drop-set, rest-pause, bi-set, tri-set)."""
import json
from decimal import Decimal

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import translation

from core import exports, techniques
from core.models import PersonRoutineExercise, RoutineExercise, SessionExercise, SessionSet, SheetLink, WorkoutSession
from core.workout_summary import session_volume

from .base import BaseTestCase


class Row:
    def __init__(self, technique=''):
        self.technique = technique


class LabelTests(SimpleTestCase):
    def setUp(self):
        translation.activate('pt-br')

    def test_the_target_in_repetitions_and_in_seconds(self):
        self.assertEqual(techniques.target_label(3, 12, 'reps'), '3 × 12')
        self.assertEqual(techniques.target_label(3, 30, 'time'), '3 × 30 s')

    def test_unknown_values_fall_back(self):
        self.assertEqual((techniques.clean_measure('x'), techniques.clean_measure(None), techniques.clean_measure('time')),
                         ('reps', 'reps', 'time'))
        self.assertEqual((techniques.clean_technique('x'), techniques.clean_technique(None), techniques.clean_technique('drop_set')),
                         ('', '', 'drop_set'))

    def test_a_bi_set_ties_a_row_to_the_next_one(self):
        rows = techniques.annotate([Row('bi_set'), Row(''), Row('')])
        self.assertEqual([r.group_label for r in rows], ['A1', 'A2', ''])
        self.assertIn('Bi-set', rows[0].technique_label)
        self.assertIn('sem descanso', rows[0].technique_hint)
        self.assertEqual(rows[1].technique_label, 'Junto com o anterior')
        self.assertEqual(rows[1].technique_hint, '')

    def test_a_tri_set_takes_three_rows_and_groups_are_lettered_in_order(self):
        rows = techniques.annotate([Row('tri_set'), Row(''), Row(''), Row('bi_set'), Row('')])
        self.assertEqual([r.group_label for r in rows], ['A1', 'A2', 'A3', 'B1', 'B2'])

    def test_a_bi_set_on_the_last_row_has_nobody_to_pair_with(self):
        rows = techniques.annotate([Row(''), Row('bi_set')])
        self.assertEqual([r.group_label for r in rows], ['', ''])
        self.assertEqual((rows[1].technique_label, rows[1].technique_hint), ('', ''))

    def test_drop_set_and_rest_pause_have_a_name_and_a_hint_but_no_group(self):
        rows = techniques.annotate([Row('drop_set'), Row('rest_pause'), Row('')])
        self.assertEqual([r.group_label for r in rows], ['', '', ''])
        self.assertEqual([r.technique_label for r in rows], ['Drop-set', 'Rest-pause', ''])
        self.assertIn('reduza a carga', rows[0].technique_hint)
        self.assertIn('descanse', rows[1].technique_hint)

    def test_a_queryset_keeps_working_after_being_annotated(self):
        from core.models import Exercise
        queryset = Exercise.objects.none()
        self.assertIs(techniques.annotate(queryset), queryset)


class SaveTests(BaseTestCase):
    def setUp(self):
        self.login(self.trainer)

    def save_template(self, rows):
        return self.client.post(reverse('core:routine_edit', kwargs={'pk': self.template.pk}), {
            'routine_name': 'Treino A', 'difficulty_level': 'intermediario', 'estimated_duration': 45, 'is_active': 'on',
            'exercises_data': json.dumps(rows)})

    def test_a_template_saves_the_counting_and_the_technique(self):
        self.save_template([{'id': self.supino.pk, 'sets': 3, 'reps': 30, 'rest': 45, 'measure': 'time', 'technique': 'drop_set'},
                            {'id': self.crucifixo.pk, 'sets': 3, 'reps': 12, 'rest': 60}])
        first, second = self.template.exercises.order_by('order')
        self.assertEqual((first.measure, first.technique, first.is_timed), ('time', 'drop_set', True))
        self.assertEqual((second.measure, second.technique), ('reps', ''))       # an old payload without them
        self.assertEqual(first.target_label, '3 × 30 s')

    def test_nonsense_values_are_cleaned(self):
        self.save_template([{'id': self.supino.pk, 'sets': 3, 'reps': 12, 'rest': 60, 'measure': 'lightyears', 'technique': '<b>'}])
        row = self.template.exercises.get()
        self.assertEqual((row.measure, row.technique), ('reps', ''))

    def test_the_api_that_feeds_the_student_form_carries_them(self):
        self.template.exercises.update(measure='time', technique='rest_pause')
        data = self.client.get(reverse('core:api_routine_exercises', kwargs={'pk': self.template.pk})).json()
        self.assertTrue(all(d['measure'] == 'time' and d['technique'] == 'rest_pause' for d in data))

    def test_a_student_routine_saves_them_on_the_same_rows(self):
        routine = self.alice_routine
        ids = list(routine.exercises.order_by('order').values_list('pk', flat=True))
        payload = [{'exercise_id': r.exercise_id, 'sets': 3, 'reps': 30, 'rest_seconds': 45, 'notes': '', 'measure': 'time',
                    'technique': 'bi_set'} for r in routine.exercises.order_by('order')]
        response = self.client.post(reverse('core:person_routine_edit', kwargs={'pk': routine.pk}), {
            'person': routine.person_id, 'routine_name': routine.routine_name, 'difficulty_level': routine.difficulty_level,
            'estimated_duration': routine.estimated_duration, 'routine_date': routine.routine_date.strftime('%d/%m/%Y'),
            'start_date': (routine.start_date or routine.routine_date).strftime('%d/%m/%Y'), 'is_active': 'on',
            'exercises_data': json.dumps(payload)})
        self.assertEqual(response.status_code, 302)
        saved = list(routine.exercises.order_by('order'))
        self.assertEqual([r.pk for r in saved], ids)
        self.assertTrue(all((r.measure, r.technique) == ('time', 'bi_set') for r in saved))

    def test_the_forms_offer_the_two_fields_and_send_them(self):
        for url in (reverse('core:routine_edit', kwargs={'pk': self.template.pk}),
                    reverse('core:person_routine_edit', kwargs={'pk': self.alice_routine.pk})):
            page = self.client.get(url).content.decode()
            for needle in ('id="editMeasure"', 'id="editTechnique"', 'value="bi_set"', 'value="time"', 'T_TECHNIQUES'):
                self.assertIn(needle, page, (url, needle))


class ScreenTests(BaseTestCase):
    def setUp(self):
        rows = list(self.alice_routine.exercises.order_by('order'))
        self.first, self.second = rows[0], rows[1]
        PersonRoutineExercise.objects.filter(pk=self.first.pk).update(measure='time', reps=30, technique='bi_set')
        self.login(self.alice)

    def execute(self):
        WorkoutSession.objects.create(person_routine=self.alice_routine)
        return self.client.get(reverse('core:workout_execute', kwargs={'pk': self.alice_routine.pk})).content.decode()

    def test_the_workout_shows_seconds_the_group_the_technique_and_how_to_do_it(self):
        page = self.execute()
        self.assertIn('3 × 30 s', page)
        self.assertIn('data-group="A1"', page)
        self.assertIn('data-group="A2"', page)
        self.assertIn('data-technique-hint', page)
        self.assertIn('sem descanso', page)
        self.assertIn('placeholder="seg"', page)           # the sets of the timed exercise ask for seconds
        self.assertIn('placeholder="reps"', page)          # the other exercise still asks for repetitions

    def test_the_students_list_of_workouts_shows_them(self):
        page = self.client.get(reverse('core:my_workouts')).content.decode()
        self.assertIn('30 s', page)
        self.assertIn('Bi-set', page)

    def test_the_trainers_template_detail_shows_them(self):
        RoutineExercise.objects.filter(routine=self.template, order=1).update(measure='time', reps=40, technique='drop_set')
        self.login(self.trainer)
        page = self.client.get(reverse('core:routine_detail', kwargs={'pk': self.template.pk})).content.decode()
        self.assertIn('data-technique="drop_set"', page)
        self.assertIn('40 s', page)

    def test_the_public_sheet_shows_them(self):
        SheetLink.objects.create(person=self.alice.person, token='tok-tech')
        self.client.logout()
        page = self.client.get(reverse('core:public_sheet', kwargs={'token': 'tok-tech'})).content.decode()
        self.assertIn('3 × 30 s', page)
        self.assertIn('ui-badge info">A1<', page)
        self.assertIn('sem descanso', page)
        self.assertIn(self.supino.muscle_group.label, page)         # the muscle group badge is still its own

    def test_the_history_of_a_session_says_seconds(self):
        session = WorkoutSession.objects.create(person_routine=self.alice_routine, is_completed=True)
        SessionExercise.objects.create(session=session, person_routine_exercise=self.first)
        page = self.client.get(reverse('core:session_detail', kwargs={'pk': session.pk})).content.decode()
        self.assertIn('segundos', page)

    def test_the_students_export_carries_them(self):
        data = exports.build_data(self.alice.person, include_internal_notes=False)
        first = data['routines'][0]['exercises'][0]
        self.assertEqual((first['measure'], first['technique']), ('time', 'bi_set'))


class VolumeTests(BaseTestCase):
    def test_seconds_are_not_repetitions_in_the_volume(self):
        rows = list(self.alice_routine.exercises.order_by('order'))
        PersonRoutineExercise.objects.filter(pk=rows[0].pk).update(measure='time')
        session = WorkoutSession.objects.create(person_routine=self.alice_routine)
        for row, weight, reps in ((rows[0], 10, 60), (rows[1], 20, 10)):
            log = SessionExercise.objects.create(session=session, person_routine_exercise=row)
            SessionSet.objects.create(session_exercise=log, number=1, weight=Decimal(weight), reps=reps, is_done=True)
        self.assertEqual(session_volume(session), Decimal('200'))       # only 20 kg x 10: the 60 seconds do not count
