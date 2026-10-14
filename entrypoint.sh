#!/bin/sh
# Applies database migrations (waiting for the database if needed) and then
# runs the command given to the container (gunicorn by default).
set -eu

attempts=30
until python manage.py migrate --noinput; do
    attempts=$((attempts - 1))
    if [ "$attempts" -le 0 ]; then
        echo "Banco de dados indisponível: desistindo." >&2
        exit 1
    fi
    echo "Aguardando o banco de dados... ($attempts tentativas restantes)" >&2
    sleep 2
done

exec "$@"
