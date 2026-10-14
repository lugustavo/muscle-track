from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('select2/', include('django_select2.urls')),
    path('', include('core.urls')),
    # Uploaded media (avatars, exercise images/GIFs). Served by Django so a
    # single-container self-hosted setup works; a reverse proxy can take over
    # /media/ later without code changes.
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
