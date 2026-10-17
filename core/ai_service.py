import json

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext as _
from openai import OpenAI, OpenAIError

from .models import Exercise, Routine, RoutineExercise


class AIGenerationError(Exception):
    """A failure the trainer can understand (misconfiguration, bad AI answer...)."""


def _build_prompt(description, difficulty_level, estimated_duration, exercise_list):
    # The prompt is written in the active language, so the AI answers (routine name, description) in it too.
    # The JSON keys of the format are fixed: they must not be translated.
    return '\n' + _("""Você é um personal trainer especializado. Crie um plano de treino com base na seguinte descrição:

Descrição: %(description)s
Nível: %(level)s
Duração estimada: %(duration)s minutos

Exercícios disponíveis:
%(exercises)s

Retorne APENAS um objeto JSON válido no seguinte formato:
{
  "routine_name": "Nome do Treino",
  "description": "Descrição detalhada",
  "exercises": [
    {"exercise_id": 1, "sets": 3, "reps": 12, "rest_seconds": 60}
  ]
}

Escolha de 5 a 8 exercícios da lista disponível (use somente os ids da lista) que melhor
atendam a descrição, na ordem em que devem ser executados.""") % {
        'description': description, 'level': difficulty_level, 'duration': estimated_duration,
        'exercises': json.dumps(exercise_list, indent=2, ensure_ascii=False)} + '\n'


def _clamp(value, default, low, high):
    try:
        return min(max(int(value), low), high)
    except (TypeError, ValueError):
        return default


def generate_workout_with_ai(description, difficulty_level, estimated_duration):
    """Generate a workout routine template using the OpenAI chat API."""
    if not settings.OPENAI_API_KEY:
        raise AIGenerationError(_('Chave da API OpenAI não configurada (OPENAI_API_KEY).'))

    exercises = {
        ex.id: ex
        for ex in Exercise.objects.filter(is_active=True).select_related('muscle_group')
    }
    if not exercises:
        raise AIGenerationError(_('Cadastre exercícios antes de gerar um treino com IA.'))

    exercise_list = [{
        'id': ex.id,
        'name': ex.exercise_name,
        'muscle_group': ex.muscle_group.name,
        'default_sets': ex.default_sets,
        'default_reps': ex.default_reps,
    } for ex in exercises.values()]

    prompt = _build_prompt(description, difficulty_level, estimated_duration, exercise_list)

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _("Você é um personal trainer expert. Responda sempre apenas com JSON válido.")},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=2000,
        )
        workout_data = json.loads(response.choices[0].message.content)
    except OpenAIError as e:
        raise AIGenerationError(_('Erro ao consultar a IA: %(error)s') % {'error': e})
    except (json.JSONDecodeError, TypeError, IndexError, AttributeError):
        raise AIGenerationError(_('A IA devolveu uma resposta inválida. Tente novamente.'))

    items = workout_data.get('exercises') if isinstance(workout_data, dict) else None
    chosen = []
    for item in items if isinstance(items, list) else []:
        exercise = exercises.get(item.get('exercise_id')) if isinstance(item, dict) else None
        if exercise and exercise.id not in {c[0].id for c in chosen}:
            chosen.append((exercise, item))
    if not chosen:
        raise AIGenerationError(_('A IA não escolheu nenhum exercício válido. Tente reformular a descrição.'))

    with transaction.atomic():
        routine = Routine.objects.create(
            routine_name=str(workout_data.get('routine_name') or _('Treino gerado por IA'))[:200],
            description=str(workout_data.get('description') or ''),
            difficulty_level=difficulty_level,
            estimated_duration=estimated_duration,
        )
        for order, (exercise, item) in enumerate(chosen, start=1):
            RoutineExercise.objects.create(
                routine=routine,
                exercise=exercise,
                order=order,
                sets=_clamp(item.get('sets'), exercise.default_sets, 1, 10),
                reps=_clamp(item.get('reps'), exercise.default_reps, 1, 100),
                rest_seconds=_clamp(item.get('rest_seconds'), 60, 0, 600),
            )

    return routine
