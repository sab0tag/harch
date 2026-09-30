# 06. Развёртывание (Hetzner)

## Сервер

- Hetzner Cloud **CX22** (2 vCPU, 4 GB RAM, 40 GB) — с запасом для одной больницы. Локация: Falkenstein/Helsinki.
- Ubuntu 24.04, пользователь без root, вход только по SSH-ключу, `ufw` открыт 22/80/443, `unattended-upgrades`.
- Hetzner Firewall дублирует правила. Бэкапы сервера Hetzner (+20%) — включить как вторую линию.
- Домен: A-запись `@` на IP сервера и `www` (CNAME на основной домен или A на тот же IP); Caddy сам получает сертификаты Let's Encrypt для обоих и перенаправляет `www` на основной адрес.

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
SENTRY_DSN=                                           # опционально (S5-05, раздел «Мониторинг»)
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
deploy/update.sh   # pull → build → migrate → up -d → проверка /healthz/full/
# с ноутбука: ssh deploy@<сервер> /opt/harch/deploy/update.sh
# то же вручную:
# git pull && docker compose -f compose.prod.yml build web
# docker compose -f compose.prod.yml run --rm web python manage.py migrate
# docker compose -f compose.prod.yml up -d
```
Позже — GitHub Actions: build → push в GHCR → ssh deploy.

## Пользователи пилота (S5-03 / S6-05)

Таблица — CSV (`;` или `,`, UTF-8) или `.xlsx`, как в `docs/pilot_users.example.csv`: `логін` (можно пусто —
будет `фамилия.и` латиницей по таблице КМУ-2010), `ПІБ`, `роль`, `відділення` (для старшей медсестры), `телефон`.
Одна ошибка в таблице — не меняется ничего, команда перечисляет строки с ошибками.

```bash
scp staff.xlsx deploy@<сервер>:/opt/harch/backups/          # ./backups смонтирован в web как /backups (ro)
docker compose -f compose.prod.yml exec web python manage.py create_pilot_users /backups/staff.xlsx --dry-run
docker compose -f compose.prod.yml exec web python manage.py create_pilot_users /backups/staff.xlsx --deactivate-others
rm backups/staff.xlsx
```

- Новым — временный пароль (печатается один раз); при первом входе система пускает только на смену пароля.
- Кто уже есть — обновляются ФИО/роль/відділення, пароль не трогается; `--reset-passwords` — выдать новые всем из таблицы.
- `--deactivate-others` — выключить всех, кого нет в таблице (например, заготовки `nurse-therapy`…), кроме суперпользователей.
- Потерянный пароль: admin → пользователь → «Змінити пароль» + галочка «Змінити пароль при вході».
- Замещение (S7-06): второй вход того же человека с другой ролью — строка с колонкой `основний вхід`
  (логин основного входа), например шеф-кухар `petrenko.l` заміщує дієтсестру входом `petrenko.l.diet`.
  Утвердить поданное своим другим входом можно, но в истории задачи и журнале — «Заміщення: затвердила
  та сама особа», админам — уведомление. Или в admin: поле «Основний вхід (та сама людина)».

## Учебная копия — demo.<домен> (S6-06)

Та же система на отдельной базе `harch_demo`: жёлтая плашка «НАВЧАЛЬНА ВЕРСІЯ», метка «Навчання» в шапке,
на странице входа — учебные логины (`nurse`, `dietician`, `chef`, `accountant`, `admin`, пароль `demo12345`).
Каждую ночь в 03:00 `reset_demo` стирает её базу и строит «живой» день: неделя истории, сегодня — план
утверждён и шеф вносит факт, вчерашнее списание ждёт бухгалтера, на завтра заказы всех отделений ждут
дієтсестру, а Терапевтичне — медсестру. ИИ, Sentry, бэкапы и почта в учебной копии выключены.

Включить: DNS-запись `demo` на сервер, затем в `.env`
```bash
COMPOSE_PROFILES=demo
DEMO_SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(50))">
```
и `deploy/update.sh` (создаст базу, применит миграции и сразу соберёт первый день).
Сбросить вручную: `docker compose -f compose.prod.yml exec demo-web python manage.py reset_demo`.
`reset_demo` отказывается работать без `DEMO_MODE` или на базе без «demo» в имени — рабочую базу он не тронет.

## Бэкапы

Контейнер `backup`, каждую ночь в 02:30 (Київ) — `deploy/backup/backup.sh`:
1. `pg_dump --clean --if-exists` → `./backups/harch-YYYY-MM-DD_HHMM.sql.gz` (на хосте, рядом с compose);
2. проверка: архив читается и дамп дописан до конца — иначе файл не сохраняется, скрипт падает;
3. удаление локальных копий старше `BACKUP_KEEP_DAYS` (14);
4. если задан `BACKUP_REMOTE` — копия туда через rclone и такая же ротация там;
5. время последнего успеха — `./backups/.last_success`; его возраст проверяет `/healthz/full/` (раздел «Мониторинг»).

Удалённое хранилище настраивается только переменными `.env` (`RCLONE_CONFIG_OFFSITE_*`, пример —
`.env.prod.example`): Hetzner Storage Box (SFTP, порт 23, ~€4/мес) или Backblaze B2.
Серверные бэкапы Hetzner (+20%) — вторая линия, они не заменяют дампы.

```bash
docker compose -f compose.prod.yml exec backup backup.sh      # бэкап вручную (сделать сразу после запуска)
ls -lh backups/ && cat backups/.last_success
docker compose -f compose.prod.yml exec backup rclone ls offsite:harch-backups   # что лежит снаружи
```

### Копия вне сервера: Hetzner Storage Box по SSH-ключу (S6-01)

Бокс — в другом дата-центре, чем сервер (сервер hel1 → бокс fsn1). В консоли бокса: **SSH support** и
**External reachability** включены. Пароль бокса не нужен нигде: сервер ходит по своему ключу.

```bash
cd /opt/harch && mkdir -p secrets/storagebox && chmod 700 secrets secrets/storagebox
ssh-keygen -t ed25519 -N '' -C harch-backup -f secrets/storagebox/id_ed25519
cat secrets/storagebox/id_ed25519.pub        # → добавить в Storage Box (консоль Hetzner: SSH keys)
ssh-keyscan -p 23 uXXXXXX.your-storagebox.de > secrets/storagebox/known_hosts   # закрепить ключ хоста
# .env: BACKUP_REMOTE и RCLONE_CONFIG_OFFSITE_* как в .env.prod.example, затем
docker compose -f compose.prod.yml up -d backup
docker compose -f compose.prod.yml exec backup backup.sh                        # «copied to offsite:…»
docker compose -f compose.prod.yml exec backup rclone ls offsite:harch-backups
```
Если копия наружу не удалась, `backup.sh` падает и не обновляет `.last_success` — `/healthz/full/` покажет
`backup: stale` на следующий день. Ключ `secrets/storagebox/id_ed25519` — сохранить и в менеджер паролей:
без него после гибели сервера до копий не добраться (либо войти в бокс паролем из консоли Hetzner).

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

## Мониторинг (S5-05)

Три линии, все бесплатные:

| Что | Как | Что ловит |
|---|---|---|
| `/healthz/` | Docker healthcheck `web`, от него стартует Caddy | приложение или БД не отвечает |
| `/healthz/full/` | внешний uptime-монитор (UptimeRobot или Better Stack) каждые 5 мин | сайт недоступен снаружи (сервер, DNS, сертификат), БД, **тихо остановленный планировщик**, **старый бэкап** |
| Sentry | `SENTRY_DSN` в `.env` | исключения в web и в cron-командах планировщика |

`/healthz/full/` отвечает `200 {"status":"ok","db":"ok","scheduler":"ok","backup":"ok"}` или `503`
с состоянием каждой части: `stale` (давно не было), `missing` (ни разу), `error`. Страница публичная
и не показывает ничего, кроме этих слов. `/healthz/` намеренно проверяет только БД: старый бэкап не
должен делать `web` unhealthy.

- **scheduler** — `check_deadlines` (каждые 15 мин) пишет «сигнал жизни» (`core.Heartbeat`); старше
  `HEALTH_SCHEDULER_MAX_AGE_MINUTES` (45) → `stale`. Сразу после первого запуска — `missing`, пока не
  пройдут первые 15 минут.
- **backup** — время изменения `backups/.last_success`, который `backup.sh` обновляет только после
  проверенного дампа; старше `HEALTH_BACKUP_MAX_AGE_HOURS` (26) → `stale`. В `web` папка смонтирована
  только для чтения. Локально (dev) путь не задан — `off`, на статус не влияет.

### Настройка (один раз, после первого деплоя)

1. **Первый бэкап** — иначе `backup: missing`: `docker compose -f compose.prod.yml exec backup backup.sh`.
2. **Проверка на сервере:** `docker compose -f compose.prod.yml exec web python manage.py monitoring_check`
   — печатает состояние каждой части, код выхода ≠ 0, если что-то не так.
3. **UptimeRobot** (uptimerobot.com, Free): New monitor → HTTP(s) → URL `https://<DOMAIN>/healthz/full/`,
   интервал 5 мин → Alert contacts: email админа (можно добавить Telegram-контакт UptimeRobot).
   Монитор сигналит на любой ответ, кроме 2xx. Better Stack (betterstack.com/uptime, Free) — то же:
   Monitor → URL → «Alert us when URL becomes unavailable».
4. **Sentry** (sentry.io, план Developer — бесплатно): Create project → Django → DSN в `.env`:
   ```
   SENTRY_DSN=https://...@o....ingest.sentry.io/...
   SENTRY_ENVIRONMENT=production
   ```
   `docker compose -f compose.prod.yml up -d` (перечитать `.env`), затем
   `docker compose -f compose.prod.yml exec web python manage.py monitoring_check --sentry` — в Sentry
   должно появиться событие «harch monitoring_check: test event». В Alerts оставить правило по
   умолчанию «новая ошибка → email».

Sentry не получает персональных данных: без пользователя, IP, cookies и тел запросов
(`config/sentry.py`) — только стек и URL. Трассировка производительности выключена.

### Что делать, когда пришёл алерт

| Сигнал | Первый шаг |
|---|---|
| UptimeRobot: сайт недоступен | `docker compose -f compose.prod.yml ps`; `logs --tail 100 web caddy`; жив ли сервер в Hetzner Console |
| `scheduler: stale` | `ps scheduler`; `logs --tail 50 scheduler`; `up -d scheduler`. Задачи-замовлення до ближайшего рабочего дня (в пятницу — на сб, вс, пн) создать вручную: `exec web python manage.py generate_daily_tasks` (идемпотентно) |
| `backup: stale/missing` | `logs --tail 50 backup`; `exec backup backup.sh` вручную; место на диске (`df -h`) и доступ к Storage Box |
| `db: error` | `logs --tail 100 db`; место на диске; при необходимости — раздел «Восстановление из бэкапа» |
| Сотрудник пишет «Вхід тимчасово заблоковано» | 5 неверных паролей подряд → 15 минут ожидания (S6-03). Снять сразу: Django admin → «Access attempts» → удалить запись, или `exec web python manage.py axes_reset_username <логин>`; забытый пароль — сменить в admin |
| Sentry: новая ошибка | стек и URL; если ломает работу роли — исправить и задеплоить, иначе — тикет |

## Данные и приватность

- В системе нет персональных данных пациентов — только количество порций по відділенню и дієті. Это ключевой аргумент для главврача, почему облако допустимо.
- Персональные данные есть только о сотрудниках (ПІБ, телефон, email). Храним минимум.
- До запуска получить от адміністрації письменное «добро» на облачное размещение (письмо/служебка) — вопрос мамы к главврачу.

## Стоимость пилота

CX22 ≈ €4–5/мес + бэкапы сервера ≈ €1 + домен ≈ €10/год + Storage Box/B2 ≈ €1–4/мес. Итого ~€7–10/мес.
