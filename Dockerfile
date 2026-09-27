# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/Kyiv
WORKDIR /app

# WeasyPrint (menu-rozkladka PDF) needs Pango/HarfBuzz and a font with Cyrillic glyphs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# supercronic — cron for containers (scheduler service, deploy/crontab). Checksums pinned.
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.33
ADD --chmod=755 https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH} /usr/local/bin/supercronic
RUN case "${TARGETARCH}" in \
      amd64) sum=71b0d58cc53f6bd72cf2f293e09e294b79c666d8 ;; \
      arm64) sum=e0f0c06ebc5627e43b25475711e694450489ab00 ;; \
      *) echo "unsupported arch: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && echo "${sum}  /usr/local/bin/supercronic" | sha1sum -c -

COPY requirements.txt requirements-dev.txt ./
ARG REQUIREMENTS=requirements.txt
RUN pip install --no-cache-dir -r ${REQUIREMENTS}

COPY . .
RUN DJANGO_SECRET_KEY=build DATABASE_URL=sqlite:///tmp/x.db \
    python manage.py collectstatic --noinput --settings=config.settings.prod

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
