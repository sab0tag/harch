# 06. Развёртывание (Hetzner)

## Сервер

- Hetzner Cloud **CX22** (2 vCPU, 4 GB RAM, 40 GB) — с запасом для одной больницы. Локация: Falkenstein/Helsinki.
- Ubuntu 24.04, пользователь без root, вход только по SSH-ключу, `ufw` открыт 22/80/443, `unattended-upgrades`.
- Hetzner Firewall дублирует правила. Бэкапы сервера Hetzner (+20%) — включить как вторую линию.
- Домен: A-запись на IP сервера; Caddy сам получает сертификат Let's Encrypt.

## Compose (prod)

```yaml
services:
  db:        postgres:16, volume pgdata, без публикации порта наружу
  web:       образ приложения, gunicorn -w 3, env_file .env, depends_on db
  scheduler: тот же образ, supercronic с crontab:
               */15 * * * *  python manage.py check_deadlines
               5 0 * * *     python manage.py generate_daily_tasks --days-ahead 1
  backup:    postgres:16 + cron: 30 2 * * * pg_dump | gzip > /backups/harch-$(date).sql.gz
               ротация 14 дней, затем rclone в Storage Box / B2
  caddy:     caddy:2, Caddyfile:  harch.example.com { reverse_proxy web:8000 }
             volumes caddy_data, caddy_config
```

## `.env` (пример ключей)

```
DJANGO_SECRET_KEY=
DJANGO_ALLOWED_HOSTS=harch.example.com
DATABASE_URL=postgres://harch:***@db:5432/harch
EMAIL_URL=smtp+tls://user:pass@smtp.example.com:587   # опционально
SENTRY_DSN=                                           # опционально
HOSPITAL_NAME=КНП "..."
ORDER_DEADLINE=16:00
```

## Деплой

```bash
git pull && docker compose -f compose.prod.yml build web
docker compose -f compose.prod.yml run --rm web python manage.py migrate
docker compose -f compose.prod.yml up -d
```
Позже — GitHub Actions: build → push в GHCR → ssh deploy.

## Восстановление из бэкапа (проверить до пилота!)

```bash
docker compose -f compose.prod.yml stop web scheduler
gunzip -c harch-YYYY-MM-DD.sql.gz | docker compose -f compose.prod.yml exec -T db psql -U harch harch
docker compose -f compose.prod.yml start web scheduler
```

## Данные и приватность

- В системе нет персональных данных пациентов — только количество порций по відділенню и дієті. Это ключевой аргумент для главврача, почему облако допустимо.
- Персональные данные есть только о сотрудниках (ПІБ, телефон, email). Храним минимум.
- До запуска получить от адміністрації письменное «добро» на облачное размещение (письмо/служебка) — вопрос мамы к главврачу.

## Стоимость пилота

CX22 ≈ €4–5/мес + бэкапы сервера ≈ €1 + домен ≈ €10/год + Storage Box/B2 ≈ €1–4/мес. Итого ~€7–10/мес.
