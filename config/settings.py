"""
Django settings for muscle-track project.
"""

import sys
from pathlib import Path
from decouple import config, Csv
import dj_database_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

TESTING = 'test' in sys.argv

# SECURITY WARNING: don't run with debug turned on in production!
# Off by default: set DEBUG=True in your local .env for development.
DEBUG = config('DEBUG', default=False, cast=bool)

# SECURITY WARNING: keep the secret key used in production secret!
if DEBUG or TESTING:
    SECRET_KEY = config('SECRET_KEY', default='django-insecure-dev-key-only-for-local-use')
else:
    SECRET_KEY = config('SECRET_KEY')  # required: fails loudly if missing

# Public domain of the installation (e.g. treino.example.com). When set, hosts, CSRF
# origins and the invite-link base URL are derived from it, and HTTPS hardening is on.
DOMAIN = config('DOMAIN', default='')
USE_HTTPS = config('USE_HTTPS', default=bool(DOMAIN), cast=bool)

ALLOWED_HOSTS = config(
    'ALLOWED_HOSTS',
    default=','.join(filter(None, [DOMAIN, 'localhost', '127.0.0.1'])),
    cast=Csv(),
)

CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default=f'https://{DOMAIN}' if DOMAIN else 'http://localhost:8000',
    cast=Csv(),
)

if USE_HTTPS and not TESTING:
    # TLS is terminated by the reverse proxy (Caddy), which sends X-Forwarded-Proto
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Start small: a wrong HSTS value locks browsers into HTTPS for that long
    SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=86400, cast=int)
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',

    # Third party apps
    'allauth',
    'allauth.account',
    'crispy_forms',
    'crispy_bootstrap5',
    'django_select2',

    # Local apps
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
# PostgreSQL when DATABASE_URL is set (Docker Compose), SQLite for local development
DATABASE_URL = config('DATABASE_URL', default=None)

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=60)
    }
else:
    # Development: Use SQLite
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        # The manifest storage needs `collectstatic`, so keep the plain one for tests
        'BACKEND': (
            'django.contrib.staticfiles.storage.StaticFilesStorage' if TESTING
            else 'whitenoise.storage.CompressedManifestStaticFilesStorage'
        ),
    },
}

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

SITE_ID = 1

# Email configuration
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='noreply@muscle-track.com')

# Django-allauth settings
ACCOUNT_ADAPTER = 'core.adapters.AccountAdapter'
# Students get an invite link from the trainer; set ALLOW_PUBLIC_SIGNUP=True to re-open self sign-up
ALLOW_PUBLIC_SIGNUP = config('ALLOW_PUBLIC_SIGNUP', default=False, cast=bool)
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = config('ACCOUNT_EMAIL_VERIFICATION', default='optional')  # 'none', 'optional' ou 'mandatory'
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True  # Login automático após confirmação (se acontecer)
LOGIN_REDIRECT_URL = '/'
ACCOUNT_LOGOUT_REDIRECT_URL = '/'

# Crispy Forms
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# OpenAI Configuration
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
OPENAI_MODEL = config('OPENAI_MODEL', default='gpt-4o-mini')

# Avatar upload limits (see core/validators.py)
AVATAR_MAX_SIZE_MB = config('AVATAR_MAX_SIZE_MB', default=5, cast=int)
AVATAR_MAX_WIDTH = config('AVATAR_MAX_WIDTH', default=6000, cast=int)
AVATAR_MAX_HEIGHT = config('AVATAR_MAX_HEIGHT', default=6000, cast=int)
AVATAR_MIN_WIDTH = config('AVATAR_MIN_WIDTH', default=100, cast=int)
AVATAR_MIN_HEIGHT = config('AVATAR_MIN_HEIGHT', default=100, cast=int)

# Progress photos (taken with a phone camera)
PHOTO_MAX_SIZE_MB = config('PHOTO_MAX_SIZE_MB', default=15, cast=int)
PHOTO_MAX_DIMENSION = config('PHOTO_MAX_DIMENSION', default=12000, cast=int)

# Site URL
# Public address of the app, used to build invitation links. Leave empty to use
# the address the trainer is browsing from.
SITE_URL = config('SITE_URL', default=f'https://{DOMAIN}' if DOMAIN else '')

# How long a student invitation link stays valid
PASSWORD_RESET_TIMEOUT = 7 * 24 * 60 * 60

# Logging: everything to stdout so `docker compose logs` shows it
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'simple': {'format': '%(asctime)s %(levelname)s %(name)s: %(message)s'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'simple'}},
    'root': {'handlers': ['console'], 'level': 'CRITICAL' if TESTING else config('LOG_LEVEL', default='INFO')},
    'loggers': {'django.request': {'handlers': ['console'], 'level': 'CRITICAL' if TESTING else 'WARNING', 'propagate': False}},
}
