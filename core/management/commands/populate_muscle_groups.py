from django.core.management.base import BaseCommand
from core.models import MuscleGroup


class Command(BaseCommand):
    help = 'Populate MuscleGroup table with initial data'

    def handle(self, *args, **kwargs):
        muscle_groups = [
            {'name': 'Peito', 'slug': 'peito', 'order': 1},
            {'name': 'Costas', 'slug': 'costas', 'order': 2},
            {'name': 'Ombros', 'slug': 'ombros', 'order': 3},
            {'name': 'Bíceps', 'slug': 'biceps', 'order': 4},
            {'name': 'Tríceps', 'slug': 'triceps', 'order': 5},
            {'name': 'Pernas', 'slug': 'pernas', 'order': 6},
            {'name': 'Glúteos', 'slug': 'gluteos', 'order': 7},
            {'name': 'Abdômen', 'slug': 'abdomen', 'order': 8},
            {'name': 'Panturrilha', 'slug': 'panturrilha', 'order': 9},
            {'name': 'Antebraço', 'slug': 'antebraco', 'order': 10},
            {'name': 'Cardio', 'slug': 'cardio', 'order': 11},
            {'name': 'Funcional', 'slug': 'funcional', 'order': 12},
        ]

        created_count = 0
        updated_count = 0

        for mg_data in muscle_groups:
            muscle_group, created = MuscleGroup.objects.get_or_create(
                slug=mg_data['slug'],
                defaults={
                    'name': mg_data['name'],
                    'order': mg_data['order'],
                    'is_active': True
                }
            )

            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'Created MuscleGroup: {muscle_group.name}')
                )
            else:
                # Update if needed
                if muscle_group.name != mg_data['name'] or muscle_group.order != mg_data['order']:
                    muscle_group.name = mg_data['name']
                    muscle_group.order = mg_data['order']
                    muscle_group.save()
                    updated_count += 1
                    self.stdout.write(
                        self.style.WARNING(f'Updated MuscleGroup: {muscle_group.name}')
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f'\nCompleted! Created: {created_count}, Updated: {updated_count}'
            )
        )
