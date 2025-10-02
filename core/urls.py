from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Home
    path('', views.home, name='home'),

    # Students/Persons
    path('students/', views.student_list, name='student_list'),
    path('students/create/', views.student_create, name='student_create'),
    path('students/<int:pk>/', views.student_detail, name='student_detail'),
    path('students/<int:pk>/edit/', views.student_edit, name='student_edit'),

    # Exercises
    path('exercises/', views.exercise_list, name='exercise_list'),
    path('exercises/create/', views.exercise_create, name='exercise_create'),
    path('exercises/<int:pk>/', views.exercise_detail, name='exercise_detail'),
    path('exercises/<int:pk>/edit/', views.exercise_edit, name='exercise_edit'),

    # Routines
    path('routines/', views.routine_list, name='routine_list'),
    path('routines/create/', views.routine_create, name='routine_create'),
    path('routines/<int:pk>/', views.routine_detail, name='routine_detail'),
    path('routines/<int:pk>/edit/', views.routine_edit, name='routine_edit'),

    # Person Routines (Assign routines to students)
    path('person-routines/create/', views.person_routine_create, name='person_routine_create'),
    path('person-routines/<int:pk>/', views.person_routine_detail, name='person_routine_detail'),

    # Student Area
    path('my-workouts/', views.my_workouts, name='my_workouts'),
    path('my-workouts/<int:pk>/start/', views.workout_start, name='workout_start'),
    path('my-workouts/<int:pk>/execute/', views.workout_execute, name='workout_execute'),
    path('my-workouts/session/<int:pk>/end/', views.workout_end, name='workout_end'),

    # Exercise completion API
    path('api/exercise/<int:pk>/toggle/', views.exercise_toggle_completion, name='exercise_toggle_completion'),
    path('api/exercise/<int:pk>/update-weight/', views.exercise_update_weight, name='exercise_update_weight'),

    # Exercise list API for routine form
    path('api/exercises/', views.api_exercises_list, name='api_exercises_list'),

    # History and Progress
    path('my-history/', views.my_history, name='my_history'),
    path('students/<int:pk>/history/', views.student_history, name='student_history'),

    # AI-powered workout generation
    path('routines/generate-ai/', views.routine_generate_ai, name='routine_generate_ai'),
]
