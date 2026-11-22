# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

# Dépendances d'abord (cache Docker), puis le code.
COPY pyproject.toml uv.lock ./
ARG UV_SYNC_ARGS="--frozen --no-dev"
RUN uv sync ${UV_SYNC_ARGS} --no-install-project

COPY . .

# Fichiers statiques (manifeste WhiteNoise) générés à la construction de l'image.
# Valeurs factices : aucune base ni aucun secret n'est utilisé par collectstatic.
RUN DJANGO_SECRET_KEY=collectstatic-only DATABASE_URL=sqlite:///:memory: \
    python manage.py collectstatic --noinput --settings=config.settings.prod

RUN useradd --create-home --uid 1000 mafoa && chown -R mafoa /app
USER mafoa

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
