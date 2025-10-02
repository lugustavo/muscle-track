from django.core.management.base import BaseCommand
from core.models import Exercise, MuscleGroup
from django.db import connection


class Command(BaseCommand):
    help = 'Migrate existing exercises to use MuscleGroup foreign key'

    def handle(self, *args, **kwargs):
        # Mapeamento dos valores antigos para os slugs da nova tabela
        mapping = {
            'peito': 'peito',
            'costas': 'costas',
            'ombros': 'ombros',
            'ombro': 'ombros',  # Variação
            'biceps': 'biceps',
            'triceps': 'triceps',
            'pernas': 'pernas',
            'perna': 'pernas',  # Variação
            'gluteos': 'gluteos',
            'gluteo': 'gluteos',  # Variação
            'abdomen': 'abdomen',
            'panturrilha': 'panturrilha',
            'antebraco': 'antebraco',
            'cardio': 'cardio',
            'funcional': 'funcional',
        }

        # Primeiro, verificar se a coluna antiga ainda existe
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA table_info(core_exercise)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'muscle_group_old' not in columns:
                self.stdout.write(
                    self.style.WARNING('No old muscle_group data to migrate.')
                )
                return

        migrated_count = 0
        error_count = 0

        # Migrar dados
        for exercise in Exercise.objects.all():
            try:
                # Acessar o valor antigo diretamente do banco
                old_value = getattr(exercise, 'muscle_group_old', None)

                if old_value:
                    slug = mapping.get(old_value.lower(), old_value.lower())

                    try:
                        muscle_group = MuscleGroup.objects.get(slug=slug)

                        # Atualizar usando SQL direto para evitar problemas com o campo ForeignKey
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "UPDATE core_exercise SET muscle_group_id = %s WHERE id = %s",
                                [muscle_group.id, exercise.id]
                            )

                        migrated_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'Migrated: {exercise.exercise_name} -> {muscle_group.name}'
                            )
                        )
                    except MuscleGroup.DoesNotExist:
                        error_count += 1
                        self.stdout.write(
                            self.style.ERROR(
                                f'MuscleGroup not found for slug: {slug} (exercise: {exercise.exercise_name})'
                            )
                        )
            except Exception as e:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(
                        f'Error migrating {exercise.exercise_name}: {str(e)}'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'\nCompleted! Migrated: {migrated_count}, Errors: {error_count}'
            )
        )
