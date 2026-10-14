from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.http import Http404
from django.views.static import serve

def public_media(request, path):
    """Serve uploaded media, except private areas (progress photos go through an authenticated view)."""
    if path.startswith('progress/'):
        raise Http404
    return serve(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('select2/', include('django_select2.urls')),
    path('', include('core.urls')),
    # Uploaded media (avatars, exercise images/GIFs). Served by Django so a
    # single-container self-hosted setup works; a reverse proxy can take over
    # /media/ later without code changes.
    re_path(r'^media/(?P<path>.*)$', public_media),
]
