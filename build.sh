#!/usr/bin/env bash
# exit on error
set -o errexit

# Install dependencies
pip install -r requirements.txt

# Collect static files
python manage.py collectstatic --no-input

# Run migrations (NEVER flush/seed here: this runs on every deploy)
python manage.py migrate --no-input
