# Generated migration - populate muscle groups

from django.db import migrations


def populate_muscle_groups(apps, schema_editor):
    MuscleGroup = apps.get_model('core', 'MuscleGroup')

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

    for mg_data in muscle_groups:
        MuscleGroup.objects.get_or_create(
            slug=mg_data['slug'],
            defaults={
                'name': mg_data['name'],
                'order': mg_data['order'],
                'is_active': True
            }
        )


def reverse_populate(apps, schema_editor):
    MuscleGroup = apps.get_model('core', 'MuscleGroup')
    MuscleGroup.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_create_musclegroup'),
    ]

    operations = [
        migrations.RunPython(populate_muscle_groups, reverse_populate),
    ]
