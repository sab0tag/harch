# Эксплуатация: как управлять сервером Харчоблока

Для владельца системы. Всё делается одной командой `harch` с ноутбука: она заходит на сервер по SSH
и выполняет нужное. Секреты из `.env` не печатает, опасные действия переспрашивает.
Подробности устройства — `docs/06_deploy.md`.

| Что | Где |
|---|---|
| Рабочая система | https://beyondthesignal.to |
| Учебная копия | https://demo.beyondthesignal.to (сбрасывается каждую ночь в 03:00) |
| Сервер | Hetzner Cloud `ubuntu-4gb-hel1-1`, 95.216.210.228, пользователь `deploy`, папка `/opt/harch` |
| Копии базы | на сервере `/opt/harch/backups` (14 дней) и Hetzner Storage Box `u679286` (FSN1, 14 дней) |
| Код | GitHub `sab0tag/harch`; каждый PR проверяют автотесты |

## Установка `harch` (один раз)

```bash
mkdir -p ~/bin && ln -sf ~/harch/harch/ops/harch ~/bin/harch
grep -q 'HOME/bin' ~/.zshrc || echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
harch help
```
Ключ SSH по умолчанию — `~/.ssh/google_compute_engine`; другой: `export HARCH_KEY=~/.ssh/…`.
Если SSH не пускает с нового места — порт 22 закрыт у провайдера или сменился ключ хоста (см. внизу).

## Раз в неделю — 1 минута

```bash
harch status
```
Всё хорошо, если: все сервисы `Up`, у `db`/`web`/`demo-web` — `(healthy)`; `health: {"status": "ok", …}`;
последний бэкап — сегодня ночью (≈02:30); диск меньше 80%.

## Раз в месяц — проверить, что из копии можно восстановиться

```bash
harch restore-check
```
Берёт последнюю копию со Storage Box, поднимает её во **временную** базу, показывает число записей
рядом с рабочими и удаляет временную базу. Рабочую базу не трогает. Итог — `restore-check: OK`.
Расхождения «змінилось після бекапу» нормальны: система работала после ночной копии.

## Обновить систему после слияния PR

```bash
harch update      # pull → сборка → миграции → перезапуск → проверка /healthz/full/
harch version     # что на сервере и что последнее в main
```
Если `update` закончился ошибкой — он покажет состояние контейнеров; дальше `harch logs web 200`.

## Сотрудники

| Ситуация | Команда |
|---|---|
| Завести всех по списку (Excel/CSV, образец `docs/pilot_users.example.csv`) | `harch import-users сотрудники.xlsx` — проверка; затем то же с `--apply` |
| Первый запуск: убрать заготовки `nurse-therapy`… | `harch import-users сотрудники.xlsx --apply --deactivate-others` |
| Забыл пароль | `harch temp-password koval.m` — выдаст временный, при входе попросит сменить |
| «Вхід тимчасово заблоковано» (5 неверных паролей) | `harch unlock koval.m` (или подождать 15 минут) |
| Уволился | `harch deactivate koval.m` — вход закрыт, его история остаётся |
| Кто есть и кто когда входил | `harch users` (`--all` — и выключенные) |

Временные пароли показываются **один раз** — раздавайте лично, не в мессенджер.
Роль или отделение сменить: поправить строку в таблице и снова `import-users … --apply` (пароль не меняется),
либо в Django admin (`/admin/`, вход админом).

## Учебная копия

`harch demo-reset` — вернуть demo.… к свежему учебному дню сейчас (например, перед занятием).
Логины и пароль (`demo12345`) написаны на её странице входа. Реальные данные туда не попадают.

## Настройки (`.env` на сервере)

```bash
harch env                                        # посмотреть (секреты скрыты)
harch env-set APPROVER_TITLE "Медичний директор" # изменить и перезапустить; прежний .env сохраняется
```
Полезные: `HOSPITAL_NAME` (экран), `HOSPITAL_FULL_NAME` / `HOSPITAL_EDRPOU` / `HOSPITAL_ADDRESS` /
`APPROVER_TITLE` (печатные формы), `HOSPITAL_CITY` (подпись внизу), `ORDER_DEADLINE` (срок заказов, `16:00`),
`SESSION_IDLE_MINUTES` (автовыход, `60`), `AI_ENABLED`. Список — `.env.prod.example`.

## Если что-то не так

| Симптом | Что сделать |
|---|---|
| Сайт не открывается | `harch status`. Сервис не `Up` → `harch restart web` (или `caddy`). Не помогло → `harch logs web 200` |
| `health`: `scheduler: stale` | `harch restart scheduler`; задачи на завтра вручную: `harch tasks` |
| `health`: `backup: stale` / `missing` | `harch logs backup 50`, затем `harch backup` |
| Медсестра не видит задачу на завтра | `harch tasks` (идемпотентно — дубли не создаст) |
| Не пришло «Прострочено» | `harch deadlines` |
| Диск > 80% | `harch ssh`, затем `docker system prune -f` и `du -sh backups` |
| Ошибка на странице у всех после обновления | `harch logs web 200` — строки `Traceback`/`ERROR`. Откат: на GitHub открыть последний PR → **Revert** → слить → `harch update` |
| `harch` пишет «REMOTE HOST IDENTIFICATION HAS CHANGED» | Сервер переустанавливали. Сверить отпечаток в консоли Hetzner, затем `ssh-keygen -R 95.216.210.228` |

## Настоящее восстановление базы (если данные испорчены)

Только осознанно: заменяет **все** данные на момент копии.
```bash
harch backups                                   # выбрать файл
harch restore-check harch-2026-10-01_0230.sql.gz  # убедиться, что он живой
harch ssh
docker compose -f compose.prod.yml stop web scheduler
docker compose -f compose.prod.yml exec backup rclone copy offsite:harch-backups/harch-2026-10-01_0230.sql.gz /backups/   # если локальной нет
docker compose -f compose.prod.yml exec backup restore.sh /backups/harch-2026-10-01_0230.sql.gz --yes
docker compose -f compose.prod.yml start web scheduler
```
Если погиб весь сервер: новый сервер по `docs/06_deploy.md` («Первый запуск»), `.env` из менеджера паролей,
копия — со Storage Box (вход паролем бокса из консоли Hetzner), затем шаги выше.

## Что делается в консоли Hetzner, а не командой

- **Перезагрузка сервера**, если он не отвечает даже по SSH: Servers → `ubuntu-4gb-hel1-1` → Power → Reset.
  После старта все сервисы поднимаются сами (`restart: unless-stopped`).
- **Пароль Storage Box**: Storage Boxes → `u679286` → Reset password. Хранить в менеджере паролей.
- **Firewall**: открыты только 22, 80, 443. Не открывать порт базы наружу.

## Для опытных

`harch shell` — Django shell, `harch psql` — консоль базы, `harch manage <команда>` — любой `manage.py`,
`harch follow web` — логи в реальном времени, `harch ssh` — на сервер в `/opt/harch`.
