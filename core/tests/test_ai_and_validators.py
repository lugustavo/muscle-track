import io
import json
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from core.ai_service import AIGenerationError, generate_workout_with_ai
from core.models import Routine
from core.validators import validate_avatar_image

from .base import BaseTestCase


def fake_openai(content):
    client = mock.Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    return mock.patch('core.ai_service.OpenAI', return_value=client)


@override_settings(OPENAI_API_KEY='test-key')
class AIServiceTests(BaseTestCase):
    def test_builds_routine_from_valid_answer(self):
        answer = json.dumps({
            'routine_name': 'Peito',
            'description': 'Foco em peito',
            'exercises': [
                {'exercise_id': self.crucifixo.pk, 'sets': 99, 'reps': 10},   # sets clamped
                {'exercise_id': self.supino.pk},
                {'exercise_id': self.supino.pk},                               # duplicate dropped
                {'exercise_id': 999999},                                       # unknown dropped
            ],
        })
        with fake_openai(answer):
            routine = generate_workout_with_ai('peito', 'iniciante', 45)
        rows = list(routine.exercises.order_by('order').values_list('exercise_id', 'order', 'sets'))
        self.assertEqual(rows, [(self.crucifixo.pk, 1, 10), (self.supino.pk, 2, self.supino.default_sets)])

    def test_exercise_catalog_sent_to_the_model_is_json_serialisable(self):
        # Regression: muscle_group used to be a model instance inside json.dumps
        with fake_openai(json.dumps({'exercises': [{'exercise_id': self.supino.pk}]})) as patched:
            generate_workout_with_ai('x', 'iniciante', 45)
        prompt = patched.return_value.chat.completions.create.call_args.kwargs['messages'][1]['content']
        self.assertIn('"muscle_group": "Peito"', prompt)

    def test_bad_answers_raise_friendly_error_and_create_nothing(self):
        before = Routine.objects.count()
        for content in ('not json', '[]', json.dumps({'exercises': []}),
                        json.dumps({'exercises': [{'exercise_id': 999999}]}), None):
            with self.subTest(content=content), fake_openai(content), self.assertRaises(AIGenerationError):
                generate_workout_with_ai('x', 'iniciante', 45)
        self.assertEqual(Routine.objects.count(), before)

    @override_settings(OPENAI_API_KEY='')
    def test_requires_api_key(self):
        with self.assertRaises(AIGenerationError):
            generate_workout_with_ai('x', 'iniciante', 45)


def image_file(size=(200, 200), name='a.png', fmt='PNG'):
    buffer = io.BytesIO()
    Image.new('RGB', size, 'red').save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue())


class AvatarValidatorTests(BaseTestCase):
    def test_accepts_valid_image(self):
        validate_avatar_image(image_file())

    def test_rejects_with_specific_messages(self):
        cases = {
            'dimensões mínimas': image_file(size=(50, 50)),
            'dimensões máximas': image_file(size=(7000, 100)),
            'Formato': image_file(name='a.bmp'),
            'Não foi possível': SimpleUploadedFile('a.png', b'definitely not an image'),
        }
        for expected, upload in cases.items():
            with self.subTest(expected=expected), self.assertRaisesMessage(ValidationError, expected):
                validate_avatar_image(upload)

    @override_settings(AVATAR_MAX_SIZE_MB=0)
    def test_rejects_big_files(self):
        with self.assertRaisesMessage(ValidationError, 'tamanho máximo'):
            validate_avatar_image(image_file())
