# Харчоблок

Облік лікувального харчування для харчоблоку лікарні (пілот). Правила проєкту — `CLAUDE.md`,
специфікація — `docs/`.

## Запуск (Docker)

```bash
cp .env.example .env            # задай DJANGO_SECRET_KEY
docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py load_seed          # довідники: 8 дієт, 72 продукти, 130 карток, 250 меню
docker compose exec web python manage.py create_demo_users   # dev: по користувачу на роль, пароль demo12345
```

Відкрий http://localhost:8000 → увійди як `dietician`, `chef`, `nurse`, `accountant` або `admin`.

## Тести

```bash
docker compose exec web pytest
```

## Без Docker

Python 3.12, PostgreSQL 16. У `.env` вкажи `DATABASE_URL` на свій Postgres, потім
`pip install -r requirements-dev.txt && python manage.py migrate && python manage.py runserver`.
