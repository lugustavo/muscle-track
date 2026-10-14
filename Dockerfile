FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WEB_CONCURRENCY=3

WORKDIR /app

RUN useradd --create-home --uid 1000 app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .
# media/ is a volume: create it owned by the app user so a fresh volume inherits that
RUN mkdir -p /app/media /app/staticfiles && chown app:app /app/media /app/staticfiles \
    && chmod +x /app/entrypoint.sh

USER app

# Static files are baked into the image (served by WhiteNoise). The key is only for this build step.
RUN SECRET_KEY=build-only-not-used-at-runtime python manage.py collectstatic --noinput

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
