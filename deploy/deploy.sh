#!/usr/bin/env bash
# Serverda: cd /srv/lesson24 && ./deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only
venv/bin/pip install -q -r requirements.txt
venv/bin/python manage.py migrate --noinput
venv/bin/python manage.py createcachetable
venv/bin/python manage.py collectstatic --noinput -v 0
venv/bin/python manage.py check --deploy --fail-level WARNING

sudo systemctl restart lesson24-web lesson24-bot
echo "Deploy tugadi: $(git rev-parse --short HEAD)"
