# 06. Развёртывание (Hetzner)

## Сервер

- Hetzner Cloud **CX22** (2 vCPU, 4 GB RAM, 40 GB) — с запасом для одной больницы. Локация: Falkenstein/Helsinki.
- Ubuntu 24.04, пользователь без root, вход только по SSH-ключу, `ufw` открыт 22/80/443, `unattended-upgrades`.
- Hetzner Firewall дублирует правила. Бэкапы сервера Hetzner (+20%) — включить как вторую линию.
- Домен: A-запись на IP сервера; Caddy сам получает сертификат Let's Encrypt.

## Compose (prod)

Файлы: `compose.prod.yml`, `deploy/Caddyfile`, `.env.prod.example`.

```yaml
services:
  db:        postgres:16, volume pgdata, порт наружу не публикуется, пароль из POSTGRES_PASSWORD
  web:       образ приложения, gunicorn -w 3 --timeout 60, settings=config.settings.prod,
             healthcheck на /healthz/ (проверяет БД)
  scheduler: тот же образ, supercronic с deploy/crontab (generate_daily_tasks, check_deadlines)
  backup:    deploy/backup (postgres:16 + rclone + supercronic): 02:30 pg_dump → проверка →
             ./backups, ротация 14 дней, копия в Storage Box / B2
  caddy:     caddy:2, единственный сервис с портами 80/443; сертификат Let's Encrypt для $DOMAIN,
             стартует после того, как web стал healthy
```

`.dockerignore` не пускает `.env` и прочее локальное в образ — ключи живут только в `.env` на сервере.
Prod-настройки проходят `check --deploy` без предупреждений (тест `apps/core/tests/test_deploy.py`);
`security.W021` (HSTS preload) заглушён сознательно — preload не для пилотного домена.

## `.env`

Шаблон — `.env.prod.example`. Скопировать в `.env`, `chmod 600 .env`, заполнить:

```
DOMAIN=harch.example.com
DJANGO_SECRET_KEY=          # python -c "import secrets; print(secrets.token_urlsafe(50))"
DJANGO_ALLOWED_HOSTS=harch.example.com
POSTGRES_PASSWORD=          # тот же пароль — в DATABASE_URL
DATABASE_URL=postgres://harch:<POSTGRES_PASSWORD>@db:5432/harch
EMAIL_URL=smtp+tls://user:pass@smtp.example.com:587   # опционально
SENTRY_DSN=                                           # опционально (S5-05)
HOSPITAL_NAME=КНП "..."
ORDER_DEADLINE=16:00
```

## Первый запуск

```bash
git clone https://github.com/sab0tag/harch.git && cd harch
cp .env.prod.example .env && chmod 600 .env && nano .env
docker compose -f compose.prod.yml build web
docker compose -f compose.prod.yml run --rm web python manage.py migrate
docker compose -f compose.prod.yml run --rm web python manage.py load_seed
docker compose -f compose.prod.yml run --rm web python manage.py createsuperuser
docker compose -f compose.prod.yml up -d
docker compose -f compose.prod.yml ps          # web — healthy, caddy — up
curl -I https://harch.example.com/healthz/     # 200, заголовок Strict-Transport-Security
```

## Деплой

```bash
git pull && docker compose -f compose.prod.yml build web
docker compose -f compose.prod.yml run --rm web python manage.py migrate
docker compose -f compose.prod.yml up -d
```
Позже — GitHub Actions: build → push в GHCR → ssh deploy.

## Бэкапы

Контейнер `backup`, каждую ночь в 02:30 (Київ) — `deploy/backup/backup.sh`:
1. `pg_dump --clean --if-exists` → `./backups/harch-YYYY-MM-DD_HHMM.sql.gz` (на хосте, рядом с compose);
2. проверка: архив читается и дамп дописан до конца — иначе файл не сохраняется, скрипт падает;
3. удаление локальных копий старше `BACKUP_KEEP_DAYS` (14);
4. если задан `BACKUP_REMOTE` — копия туда через rclone и такая же ротация там;
5. время последнего успеха — `./backups/.last_success` (для мониторинга, S5-05).

Удалённое хранилище настраивается только переменными `.env` (`RCLONE_CONFIG_OFFSITE_*`, пример —
`.env.prod.example`): Hetzner Storage Box (SFTP, порт 23, ~€4/мес) или Backblaze B2.
Серверные бэкапы Hetzner (+20%) — вторая линия, они не заменяют дампы.

```bash
docker compose -f compose.prod.yml exec backup backup.sh      # бэкап вручную (сделать сразу после запуска)
ls -lh backups/ && cat backups/.last_success
docker compose -f compose.prod.yml exec backup rclone ls offsite:harch-backups   # что лежит снаружи
```

## Восстановление из бэкапа (проверить до пилота!)

`restore.sh` накатывает дамп поверх текущей БД одной транзакцией: при любой ошибке ничего не меняется.
Без `--yes` не запускается.

```bash
# 1. остановить всё, что пишет в БД
docker compose -f compose.prod.yml stop web scheduler
# 2. (если локальной копии нет) забрать из удалённого хранилища
docker compose -f compose.prod.yml exec backup rclone copy offsite:harch-backups/harch-2026-10-01_0230.sql.gz /backups/
# 3. восстановить
docker compose -f compose.prod.yml exec backup restore.sh /backups/harch-2026-10-01_0230.sql.gz --yes
# 4. запустить и проверить
docker compose -f compose.prod.yml start web scheduler
curl -s https://harch.example.com/healthz/
```

Проверено (S5-02): бэкап → удаление строк и порча данных → restore → данные совпадают с моментом бэкапа,
миграции на месте; битый архив и неудачный дамп отбрасываются. **До пилота повторить на сервере**:
сделать бэкап, завести тестовую запись, восстановить, убедиться, что запись исчезла.

Замечание: `--clean` удаляет и пересоздаёт только объекты из дампа; таблицы, созданные вручную
после бэкапа, останутся.

## Данные и приватность

- В системе нет персональных данных пациентов — только количество порций по відділенню и дієті. Это ключевой аргумент для главврача, почему облако допустимо.
- Персональные данные есть только о сотрудниках (ПІБ, телефон, email). Храним минимум.
- До запуска получить от адміністрації письменное «добро» на облачное размещение (письмо/служебка) — вопрос мамы к главврачу.

## Стоимость пилота

CX22 ≈ €4–5/мес + бэкапы сервера ≈ €1 + домен ≈ €10/год + Storage Box/B2 ≈ €1–4/мес. Итого ~€7–10/мес.
