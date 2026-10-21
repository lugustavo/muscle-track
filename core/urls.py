from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Home
    path('', views.home, name='home'),

    # Students/Persons
    path('search/', views.search, name='search'),
    path('search/suggest/', views.search_suggest, name='search_suggest'),
    path('students/', views.student_list, name='student_list'),
    path('students/create/', views.student_create, name='student_create'),
    path('students/<int:pk>/', views.student_detail, name='student_detail'),
    path('students/<int:pk>/edit/', views.student_edit, name='student_edit'),
    path('students/<int:pk>/delete/', views.student_delete, name='student_delete'),
    path('students/<int:pk>/status/', views.student_status, name='student_status'),
    path('students/<int:pk>/export/', views.student_export, name='student_export'),
    path('students/<int:pk>/consent/', views.student_consent_record, name='student_consent_record'),
    path('students/<int:pk>/invite/', views.student_invite, name='student_invite'),
    path('students/<int:pk>/sheet/', views.student_sheet, name='student_sheet'),
    path('ficha/<slug:token>/', views.public_sheet, name='public_sheet'),
    path('students/<int:pk>/assessments/new/', views.assessment_create, name='assessment_create'),
    path('assessments/<int:pk>/edit/', views.assessment_edit, name='assessment_edit'),
    path('assessments/<int:pk>/delete/', views.assessment_delete, name='assessment_delete'),
    path('students/<int:pk>/packages/new/', views.student_package_sell, name='student_package_sell'),
    path('students/<int:pk>/packages/<int:package_pk>/action/', views.student_package_action, name='student_package_action'),
    path('students/<int:pk>/packages/<int:package_pk>/edit/', views.student_package_edit, name='student_package_edit'),
    path('students/<int:pk>/packages/<int:package_pk>/cancel/', views.student_package_cancel, name='student_package_cancel'),
    path('students/<int:pk>/refunds/new/', views.refund_create, name='refund_create'),
    path('refunds/<int:pk>/', views.refund_detail, name='refund_detail'),
    path('refunds/<int:pk>/void/', views.refund_void, name='refund_void'),
    path('finance/refunds/', views.finance_refunds, name='finance_refunds'),
    path('students/<int:pk>/photos/new/', views.photo_upload, name='photo_upload'),
    path('photos/positions/', views.photo_position_list, name='photo_positions'),
    path('photos/positions/<int:pk>/<str:action>/', views.photo_position_action, name='photo_position_action'),
    path('photos/<int:pk>/link/', views.photo_link, name='photo_link'),
    path('photos/<int:pk>/delete/', views.photo_delete, name='photo_delete'),
    path('photos/<int:pk>/file/', views.photo_file, name='photo_file'),
    path('photos/<int:pk>/thumb/', views.photo_file, {'size': 'thumb'}, name='photo_thumb'),
    path('students/<int:pk>/notes/add/', views.note_add, name='note_add'),
    path('notes/<int:pk>/delete/', views.note_delete, name='note_delete'),
    path('invite/<uidb64>/<token>/', views.accept_invite, name='accept_invite'),

    # Finance
    path('finance/', views.finance, name='finance'),
    path('finance/export/', views.finance_export, name='finance_export'),
    path('finance/export/services/', views.finance_service_export, name='finance_service_export'),
    path('plans/', views.plan_list, name='plan_list'),
    path('plans/create/', views.plan_create, name='plan_create'),
    path('plans/<int:pk>/edit/', views.plan_edit, name='plan_edit'),
    path('students/<int:pk>/payments/new/', views.payment_create, name='payment_create'),
    path('payments/<int:pk>/delete/', views.payment_delete, name='payment_delete'),

    # Agenda
    path('agenda/', views.agenda, name='agenda'),
    path('calendar/', views.calendar_settings, name='calendar_settings'),
    path('calendar/rotate/', views.calendar_rotate, name='calendar_rotate'),
    path('calendar/style/', views.calendar_style, name='calendar_style'),
    path('calendar/feed/<slug:token>.ics', views.calendar_feed, name='calendar_feed'),
    path('agenda/new/', views.appointment_create, name='appointment_create'),
    path('agenda/holidays/', views.agenda_holidays, name='agenda_holidays'),
    path('agenda/holidays/add/', views.agenda_holiday_add, name='agenda_holiday_add'),
    path('agenda/holidays/<int:pk>/delete/', views.agenda_holiday_delete, name='agenda_holiday_delete'),
    path('appointments/<int:pk>/reschedule/', views.appointment_reschedule, name='appointment_reschedule'),
    path('appointment-requests/<int:pk>/decide/', views.appointment_request_decide, name='appointment_request_decide'),
    path('appointments/<int:pk>/move/', views.appointment_move, name='appointment_move'),
    path('appointments/<int:pk>/move/undo/', views.appointment_move_undo, name='appointment_move_undo'),
    path('appointments/<int:pk>/counts/', views.appointment_counts, name='appointment_counts'),
    path('appointments/<int:pk>/status/', views.appointment_status, name='appointment_status'),
    path('appointments/<int:pk>/delete/', views.appointment_delete, name='appointment_delete'),
    path('appointments/<int:pk>/remove/', views.appointment_remove, name='appointment_remove'),
    path('students/<int:pk>/slots/add/', views.slot_add, name='slot_add'),
    path('agenda/month-rules/', views.month_rules, name='month_rules'),
    path('students/<int:pk>/slots/preview/', views.slot_preview, name='slot_preview'),
    path('students/<int:pk>/slots/fill/', views.slot_fill, name='slot_fill'),
    path('students/<int:pk>/slots/top-up/', views.slot_topup, name='slot_topup'),
    path('slots/<int:pk>/edit/', views.slot_edit, name='slot_edit'),
    path('slots/<int:pk>/delete/', views.slot_delete, name='slot_delete'),
    path('appointments/<int:pk>/end-series/', views.appointment_series_end, name='appointment_series_end'),

    # Exercises
    path('exercises/', views.exercise_list, name='exercise_list'),
    path('exercises/create/', views.exercise_create, name='exercise_create'),
    path('exercises/import/', views.exercise_import, name='exercise_import'),
    path('exercises/<int:pk>/', views.exercise_detail, name='exercise_detail'),
    path('exercises/<int:pk>/edit/', views.exercise_edit, name='exercise_edit'),
    path('exercises/<int:pk>/delete/', views.exercise_delete, name='exercise_delete'),

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
    path('notifications/', views.notification_settings, name='notification_settings'),
    path('notifications/push/subscribe/', views.push_subscribe, name='push_subscribe'),
    path('notifications/push/<int:pk>/remove/', views.push_unsubscribe, name='push_unsubscribe'),
    path('notifications/test/', views.notification_test, name='notification_test'),
    path('telegram/webhook/', views.telegram_webhook, name='telegram_webhook'),
    path('notifications/whatsapp/<str:kind>/save/', views.whatsapp_template_save, name='whatsapp_template_save'),
    path('notifications/whatsapp/<str:kind>/preview/', views.whatsapp_template_preview, name='whatsapp_template_preview'),
    path('my-agenda/', views.my_agenda, name='my_agenda'),
    path('my-package/', views.my_package, name='my_package'),
    path('my-intro/', views.my_intro, name='my_intro'),
    path('my-evolution/', views.my_evolution, name='my_evolution'),
    path('my-evolution/compare/', views.my_evolution_compare, name='my_evolution_compare'),
    path('my-evolution/photos/new/', views.my_photo_upload, name='my_photo_upload'),
    path('my-evolution/photos/<int:pk>/delete/', views.my_photo_delete, name='my_photo_delete'),
    path('students/<int:pk>/compare/', views.student_compare, name='student_compare'),
    path('my-agenda/<int:pk>/request/', views.my_agenda_request, name='my_agenda_request'),
    path('my-agenda/requests/<int:pk>/withdraw/', views.my_agenda_request_withdraw, name='my_agenda_request_withdraw'),
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
    path('my-workouts/<int:pk>/offline/', views.workout_offline_start, name='workout_offline_start'),
    path('my-workouts/session/<int:pk>/end/', views.workout_end, name='workout_end'),

    path('my-workouts/session/<int:pk>/summary/', views.workout_summary, name='workout_summary'),
    path('my-workouts/session/<int:pk>/discard/', views.workout_discard, name='workout_discard'),
    path('my-workouts/session/<int:pk>/pause/', views.workout_pause, name='workout_pause'),
    path('my-workouts/session/<int:pk>/resume/', views.workout_resume, name='workout_resume'),
    path('my-exercises/<int:exercise_pk>/', views.my_exercise_progress, name='my_exercise_progress'),

    # Workout tracking API (per set / per exercise comment)
    path('api/sets/<int:pk>/', views.workout_set_update, name='workout_set_update'),
    path('api/workout-sync/', views.workout_sync, name='workout_sync'),
    path('api/session-exercise/<int:pk>/notes/', views.workout_exercise_notes, name='workout_exercise_notes'),

    # PWA
    path('manifest.webmanifest', views.pwa_manifest, name='pwa_manifest'),
    path('sw.js', views.service_worker, name='service_worker'),
    path('offline/', views.offline_page, name='offline'),
    path('credits/', views.media_credits, name='credits'),
    path('notices/', views.notices, name='notices'),
    path('notices/read-all/', views.notices_read_all, name='notices_read_all'),
    path('notices/<int:pk>/open/', views.notice_open, name='notice_open'),
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
