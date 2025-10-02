#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script para popular o banco de dados com dados iniciais
Execute: python populate_db.py
"""
import os
import sys
import django

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth.models import User
from core.models import Person, Exercise, Routine, RoutineExercise, PersonRoutine
from datetime import date, timedelta


def create_users_and_students():
    """Criar usuários e alunos"""
    print("Criando usuários e alunos...")

    # Criar superusuário admin
    if not User.objects.filter(email='admin@muscletrack.com').exists():
        admin = User.objects.create_superuser(
            username='admin',
            email='admin@muscletrack.com',
            password='admin123'
        )
        print("✓ Superusuário admin criado (email: admin@muscletrack.com, senha: admin123)")

    # Criar alunos exemplo
    students_data = [
        {'name': 'João Silva', 'email': 'joao@example.com', 'phone': '(11) 98765-4321', 'city': 'São Paulo'},
        {'name': 'Maria Santos', 'email': 'maria@example.com', 'phone': '(21) 98765-1234', 'city': 'Rio de Janeiro'},
        {'name': 'Pedro Oliveira', 'email': 'pedro@example.com', 'phone': '(31) 98765-5678', 'city': 'Belo Horizonte'},
    ]

    for student_data in students_data:
        if not User.objects.filter(email=student_data['email']).exists():
            user = User.objects.create_user(
                username=student_data['email'].split('@')[0],
                email=student_data['email'],
                password='senha123'
            )
            Person.objects.create(
                user=user,
                person_name=student_data['name'],
                phone=student_data['phone'],
                city=student_data['city'],
                state='SP' if 'São Paulo' in student_data['city'] else 'RJ'
            )
            print(f"✓ Aluno criado: {student_data['name']} ({student_data['email']}, senha: senha123)")


def create_exercises():
    """Criar exercícios básicos"""
    print("\nCriando exercícios...")

    exercises_data = [
        # Peito
        {'name': 'Supino Reto', 'muscle_group': 'peito', 'sets': 4, 'reps': 10, 'description': 'Exercício composto para peito'},
        {'name': 'Supino Inclinado', 'muscle_group': 'peito', 'sets': 3, 'reps': 12, 'description': 'Foco na parte superior do peito'},
        {'name': 'Crucifixo', 'muscle_group': 'peito', 'sets': 3, 'reps': 12, 'description': 'Exercício de isolamento para peito'},

        # Costas
        {'name': 'Barra Fixa', 'muscle_group': 'costas', 'sets': 4, 'reps': 8, 'description': 'Exercício composto para costas'},
        {'name': 'Remada Curvada', 'muscle_group': 'costas', 'sets': 4, 'reps': 10, 'description': 'Desenvolvimento da largura das costas'},
        {'name': 'Puxada Frontal', 'muscle_group': 'costas', 'sets': 3, 'reps': 12, 'description': 'Trabalha latíssimo do dorso'},

        # Pernas
        {'name': 'Agachamento', 'muscle_group': 'pernas', 'sets': 4, 'reps': 12, 'description': 'Exercício fundamental para pernas'},
        {'name': 'Leg Press', 'muscle_group': 'pernas', 'sets': 4, 'reps': 15, 'description': 'Desenvolvimento de quadríceps'},
        {'name': 'Cadeira Extensora', 'muscle_group': 'pernas', 'sets': 3, 'reps': 15, 'description': 'Isolamento de quadríceps'},
        {'name': 'Mesa Flexora', 'muscle_group': 'pernas', 'sets': 3, 'reps': 15, 'description': 'Trabalho de posteriores de coxa'},

        # Ombros
        {'name': 'Desenvolvimento com Barra', 'muscle_group': 'ombros', 'sets': 4, 'reps': 10, 'description': 'Exercício composto para ombros'},
        {'name': 'Elevação Lateral', 'muscle_group': 'ombros', 'sets': 3, 'reps': 12, 'description': 'Foco no deltoide médio'},
        {'name': 'Elevação Frontal', 'muscle_group': 'ombros', 'sets': 3, 'reps': 12, 'description': 'Trabalha deltoide anterior'},

        # Braços
        {'name': 'Rosca Direta', 'muscle_group': 'biceps', 'sets': 3, 'reps': 12, 'description': 'Exercício básico para bíceps'},
        {'name': 'Rosca Martelo', 'muscle_group': 'biceps', 'sets': 3, 'reps': 12, 'description': 'Trabalha bíceps e antebraços'},
        {'name': 'Tríceps Testa', 'muscle_group': 'triceps', 'sets': 3, 'reps': 12, 'description': 'Isolamento de tríceps'},
        {'name': 'Tríceps Corda', 'muscle_group': 'triceps', 'sets': 3, 'reps': 15, 'description': 'Exercício de cabo para tríceps'},

        # Abdômen
        {'name': 'Abdominal Crunch', 'muscle_group': 'abdomen', 'sets': 3, 'reps': 20, 'description': 'Exercício básico de abdômen'},
        {'name': 'Prancha', 'muscle_group': 'abdomen', 'sets': 3, 'reps': 60, 'description': 'Isometria para core'},

        # Cardio
        {'name': 'Esteira', 'muscle_group': 'cardio', 'sets': 1, 'reps': 20, 'description': 'Corrida na esteira - 20 minutos'},
        {'name': 'Bicicleta Ergométrica', 'muscle_group': 'cardio', 'sets': 1, 'reps': 30, 'description': 'Pedalar - 30 minutos'},
    ]

    for ex_data in exercises_data:
        if not Exercise.objects.filter(exercise_name=ex_data['name']).exists():
            Exercise.objects.create(
                exercise_name=ex_data['name'],
                muscle_group=ex_data['muscle_group'],
                default_sets=ex_data['sets'],
                default_reps=ex_data['reps'],
                description=ex_data['description'],
                media_type='url',
                media_url='https://via.placeholder.com/400x300.png?text=' + ex_data['name'].replace(' ', '+')
            )
    print(f"✓ {len(exercises_data)} exercícios criados")


def create_routines():
    """Criar treinos de exemplo"""
    print("\nCriando treinos de exemplo...")

    # Treino A - Peito e Tríceps
    routine_a, created = Routine.objects.get_or_create(
        routine_name='Treino A - Peito e Tríceps',
        defaults={
            'description': 'Treino focado em peitorais e tríceps',
            'difficulty_level': 'intermediario',
            'estimated_duration': 60
        }
    )

    exercises_a = [
        ('Supino Reto', 1, 4, 10, 90),
        ('Supino Inclinado', 2, 3, 12, 90),
        ('Crucifixo', 3, 3, 12, 60),
        ('Tríceps Testa', 4, 3, 12, 60),
        ('Tríceps Corda', 5, 3, 15, 60),
    ]

    if created:
        for ex_name, order, sets, reps, rest in exercises_a:
            exercise = Exercise.objects.get(exercise_name=ex_name)
            RoutineExercise.objects.create(
                routine=routine_a,
                exercise=exercise,
                order=order,
                sets=sets,
                reps=reps,
                rest_seconds=rest
            )

    # Treino B - Costas e Bíceps
    routine_b, created = Routine.objects.get_or_create(
        routine_name='Treino B - Costas e Bíceps',
        defaults={
            'description': 'Treino focado em costas e bíceps',
            'difficulty_level': 'intermediario',
            'estimated_duration': 60
        }
    )

    exercises_b = [
        ('Barra Fixa', 1, 4, 8, 90),
        ('Remada Curvada', 2, 4, 10, 90),
        ('Puxada Frontal', 3, 3, 12, 60),
        ('Rosca Direta', 4, 3, 12, 60),
        ('Rosca Martelo', 5, 3, 12, 60),
    ]

    if created:
        for ex_name, order, sets, reps, rest in exercises_b:
            exercise = Exercise.objects.get(exercise_name=ex_name)
            RoutineExercise.objects.create(
                routine=routine_b,
                exercise=exercise,
                order=order,
                sets=sets,
                reps=reps,
                rest_seconds=rest
            )

    # Treino C - Pernas
    routine_c, created = Routine.objects.get_or_create(
        routine_name='Treino C - Pernas',
        defaults={
            'description': 'Treino completo de membros inferiores',
            'difficulty_level': 'avancado',
            'estimated_duration': 70
        }
    )

    exercises_c = [
        ('Agachamento', 1, 4, 12, 120),
        ('Leg Press', 2, 4, 15, 90),
        ('Cadeira Extensora', 3, 3, 15, 60),
        ('Mesa Flexora', 4, 3, 15, 60),
    ]

    if created:
        for ex_name, order, sets, reps, rest in exercises_c:
            exercise = Exercise.objects.get(exercise_name=ex_name)
            RoutineExercise.objects.create(
                routine=routine_c,
                exercise=exercise,
                order=order,
                sets=sets,
                reps=reps,
                rest_seconds=rest
            )

    # Treino D - Ombros e Abdômen
    routine_d, created = Routine.objects.get_or_create(
        routine_name='Treino D - Ombros e Abdômen',
        defaults={
            'description': 'Treino de ombros com abdômen',
            'difficulty_level': 'intermediario',
            'estimated_duration': 50
        }
    )

    exercises_d = [
        ('Desenvolvimento com Barra', 1, 4, 10, 90),
        ('Elevação Lateral', 2, 3, 12, 60),
        ('Elevação Frontal', 3, 3, 12, 60),
        ('Abdominal Crunch', 4, 3, 20, 45),
        ('Prancha', 5, 3, 60, 60),
    ]

    if created:
        for ex_name, order, sets, reps, rest in exercises_d:
            exercise = Exercise.objects.get(exercise_name=ex_name)
            RoutineExercise.objects.create(
                routine=routine_d,
                exercise=exercise,
                order=order,
                sets=sets,
                reps=reps,
                rest_seconds=rest
            )

    print(f"✓ 4 treinos configurados (A, B, C, D)")


def assign_routines_to_students():
    """Atribuir treinos aos alunos"""
    print("\nAtribuindo treinos aos alunos...")

    # Atribuir treinos ao João
    joao = Person.objects.filter(person_name='João Silva').first()
    if joao:
        try:
            routine_a = Routine.objects.filter(routine_name='Treino A - Peito e Tríceps').first()
            routine_b = Routine.objects.filter(routine_name='Treino B - Costas e Bíceps').first()

            if routine_a:
                PersonRoutine.objects.get_or_create(
                    person=joao,
                    routine=routine_a,
                    defaults={
                        'routine_date': date.today(),
                        'start_date': date.today(),
                        'end_date': date.today() + timedelta(days=30),
                        'is_active': True
                    }
                )
            if routine_b:
                PersonRoutine.objects.get_or_create(
                    person=joao,
                    routine=routine_b,
                    defaults={
                        'routine_date': date.today(),
                        'start_date': date.today(),
                        'end_date': date.today() + timedelta(days=30),
                        'is_active': True
                    }
                )
            print(f"✓ Treinos atribuídos a João Silva")
        except Exception as e:
            print(f"⚠ Erro ao atribuir treinos a João: {e}")

    # Atribuir treinos à Maria
    maria = Person.objects.filter(person_name='Maria Santos').first()
    if maria:
        try:
            routine_c = Routine.objects.filter(routine_name='Treino C - Pernas').first()
            routine_d = Routine.objects.filter(routine_name='Treino D - Ombros e Abdômen').first()

            if routine_c:
                PersonRoutine.objects.get_or_create(
                    person=maria,
                    routine=routine_c,
                    defaults={
                        'routine_date': date.today(),
                        'start_date': date.today(),
                        'end_date': date.today() + timedelta(days=30),
                        'is_active': True
                    }
                )
            if routine_d:
                PersonRoutine.objects.get_or_create(
                    person=maria,
                    routine=routine_d,
                    defaults={
                        'routine_date': date.today(),
                        'start_date': date.today(),
                        'end_date': date.today() + timedelta(days=30),
                        'is_active': True
                    }
                )
            print(f"✓ Treinos atribuídos a Maria Santos")
        except Exception as e:
            print(f"⚠ Erro ao atribuir treinos a Maria: {e}")


def main():
    print("=" * 60)
    print("POPULANDO BANCO DE DADOS - MUSCLE TRACK")
    print("=" * 60)

    create_users_and_students()
    create_exercises()
    create_routines()
    assign_routines_to_students()

    print("\n" + "=" * 60)
    print("DADOS INICIAIS CRIADOS COM SUCESSO!")
    print("=" * 60)
    print("\nCREDENCIAIS DE ACESSO:")
    print("\nAdmin:")
    print("  Email: admin@muscletrack.com")
    print("  Senha: admin123")
    print("\nAlunos:")
    print("  Email: joao@example.com / Senha: senha123")
    print("  Email: maria@example.com / Senha: senha123")
    print("  Email: pedro@example.com / Senha: senha123")
    print("\nPróximo passo: python manage.py runserver")
    print("=" * 60)


if __name__ == '__main__':
    main()
