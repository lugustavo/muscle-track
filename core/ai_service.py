from django.conf import settings
from openai import OpenAI
from .models import Routine, Exercise, RoutineExercise
import json


def generate_workout_with_ai(description, difficulty_level, estimated_duration):
    """
    Generate a workout routine using OpenAI GPT API
    """
    if not settings.OPENAI_API_KEY:
        raise ValueError("OpenAI API key not configured")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # Get available exercises
    exercises = Exercise.objects.filter(is_active=True)
    exercise_list = []
    for ex in exercises:
        exercise_list.append({
            'id': ex.id,
            'name': ex.exercise_name,
            'muscle_group': ex.muscle_group,
            'default_sets': ex.default_sets,
            'default_reps': ex.default_reps
        })

    prompt = f"""
Você é um personal trainer especializado. Crie um plano de treino com base na seguinte descrição:

Descrição: {description}
Nível: {difficulty_level}
Duração estimada: {estimated_duration} minutos

Exercícios disponíveis:
{json.dumps(exercise_list, indent=2, ensure_ascii=False)}

Retorne APENAS um JSON válido no seguinte formato (sem markdown, sem ```json):
{{
  "routine_name": "Nome do Treino",
  "description": "Descrição detalhada",
  "exercises": [
    {{
      "exercise_id": 1,
      "order": 1,
      "sets": 3,
      "reps": 12,
      "rest_seconds": 60
    }}
  ]
}}

Escolha de 5 a 8 exercícios da lista disponível que melhor atendam a descrição.
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Você é um personal trainer expert. Sempre responda apenas com JSON válido, sem formatação markdown."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=1500
        )

        # Parse the response
        content = response.choices[0].message.content.strip()

        # Remove markdown code blocks if present
        if content.startswith('```'):
            lines = content.split('\n')
            content = '\n'.join(lines[1:-1])
            if content.startswith('json'):
                content = content[4:]

        workout_data = json.loads(content)

        # Create the routine
        routine = Routine.objects.create(
            routine_name=workout_data['routine_name'],
            description=workout_data['description'],
            difficulty_level=difficulty_level,
            estimated_duration=estimated_duration
        )

        # Add exercises to the routine
        for ex_data in workout_data['exercises']:
            try:
                exercise = Exercise.objects.get(id=ex_data['exercise_id'])
                RoutineExercise.objects.create(
                    routine=routine,
                    exercise=exercise,
                    order=ex_data['order'],
                    sets=ex_data.get('sets', 3),
                    reps=ex_data.get('reps', 12),
                    rest_seconds=ex_data.get('rest_seconds', 60)
                )
            except Exercise.DoesNotExist:
                continue

        return routine

    except Exception as e:
        raise Exception(f"Erro ao gerar treino com IA: {str(e)}")
