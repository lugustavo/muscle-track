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
    path('students/<int:pk>/delete/', views.student_delete, name='student_delete'),
    path('students/<int:pk>/status/', views.student_status, name='student_status'),
    path('students/<int:pk>/export/', views.student_export, name='student_export'),
    path('students/<int:pk>/consent/', views.student_consent_record, name='student_consent_record'),
    path('students/<int:pk>/invite/', views.student_invite, name='student_invite'),
    path('students/<int:pk>/assessments/new/', views.assessment_create, name='assessment_create'),
    path('assessments/<int:pk>/edit/', views.assessment_edit, name='assessment_edit'),
    path('assessments/<int:pk>/delete/', views.assessment_delete, name='assessment_delete'),
    path('students/<int:pk>/photos/new/', views.photo_upload, name='photo_upload'),
    path('photos/<int:pk>/delete/', views.photo_delete, name='photo_delete'),
    path('photos/<int:pk>/file/', views.photo_file, name='photo_file'),
    path('photos/<int:pk>/thumb/', views.photo_file, {'size': 'thumb'}, name='photo_thumb'),
    path('students/<int:pk>/notes/add/', views.note_add, name='note_add'),
    path('notes/<int:pk>/delete/', views.note_delete, name='note_delete'),
    path('invite/<uidb64>/<token>/', views.accept_invite, name='accept_invite'),

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
    path('person-routines/<int:pk>/edit/', views.person_routine_edit, name='person_routine_edit'),
    path('person-routines/<int:pk>/deactivate/', views.person_routine_deactivate, name='person_routine_deactivate'),

    # Student Area
    path('my-profile/', views.my_profile, name='my_profile'),
    path('my-privacy/', views.my_privacy, name='my_privacy'),
    path('my-privacy/export/', views.my_export, name='my_export'),
    path('my-privacy/photos-consent/', views.my_consent_photos, name='my_consent_photos'),
    path('my-privacy/erasure/', views.my_erasure_request, name='my_erasure_request'),
    path('consent/', views.consent, name='consent'),
    path('privacy/', views.privacy_policy, name='privacy_policy'),
    path('my-workouts/', views.my_workouts, name='my_workouts'),
    path('my-workouts/<int:pk>/start/', views.workout_start, name='workout_start'),
    path('my-workouts/<int:pk>/execute/', views.workout_execute, name='workout_execute'),
    path('my-workouts/session/<int:pk>/end/', views.workout_end, name='workout_end'),

    path('my-workouts/session/<int:pk>/pause/', views.workout_pause, name='workout_pause'),
    path('my-workouts/session/<int:pk>/resume/', views.workout_resume, name='workout_resume'),
    path('my-exercises/<int:exercise_pk>/', views.my_exercise_progress, name='my_exercise_progress'),

    # Workout tracking API (per set / per exercise comment)
    path('api/sets/<int:pk>/', views.workout_set_update, name='workout_set_update'),
    path('api/session-exercise/<int:pk>/notes/', views.workout_exercise_notes, name='workout_exercise_notes'),

    # PWA
    path('manifest.webmanifest', views.pwa_manifest, name='pwa_manifest'),
    path('sw.js', views.service_worker, name='service_worker'),
    path('offline/', views.offline_page, name='offline'),
    path('healthz', views.healthz, name='healthz'),

    # APIs for forms
    path('api/exercises/', views.api_exercises_list, name='api_exercises_list'),
    path('api/routines/', views.api_routines_list, name='api_routines_list'),
    path('api/routines/<int:pk>/exercises/', views.api_routine_exercises, name='api_routine_exercises'),

    # History and Progress
    path('my-history/', views.my_history, name='my_history'),
    path('students/<int:pk>/history/', views.student_history, name='student_history'),

    # Session details
    path('sessions/<int:pk>/', views.session_detail, name='session_detail'),

    # AI-powered workout generation
    path('routines/generate-ai/', views.routine_generate_ai, name='routine_generate_ai'),
]
