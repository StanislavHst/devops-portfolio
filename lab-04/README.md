# Лабораторна робота 4 — внесок Стаса

Командні форки — [API](https://github.com/StanislavHst/withlist-api) і [UI](https://github.com/StanislavHst/wishlist-ui).

Я підготував локальний запуск API та UI, налаштував Python 3.11, Ruff і два pytest-тести. Один перевіряє health без PostgreSQL, другий створює запис у тестовій PostgreSQL та отримує його через HTTP API. Після тесту створені записи видаляються.

API-конвеєр має окремі jobs API lint і API tests, кеш залежностей та PostgreSQL/Redis із перевірками готовності. Тести запускаються після лінтера. Для локального підключення додано DB_SSLMODE. Створення таблиць виконується окремою командою перед запуском застосунку.

Зміни — [API PR #1](https://github.com/StanislavHst/withlist-api/pull/1), [UI PR #1](https://github.com/StanislavHst/wishlist-ui/pull/1).

## Встановлення на Windows

Потрібні Git, GitHub CLI, Docker Desktop, uv та Node.js 24. Docker Desktop має бути запущений.

До злиття PR використовуйте наведені робочі гілки. Після злиття клонуйте main.

```powershell
mkdir devops-lab04
cd devops-lab04
git clone --branch codex/lab04-api-ci https://github.com/StanislavHst/withlist-api.git
git clone --branch codex/lab04-local-env https://github.com/StanislavHst/wishlist-ui.git
cd withlist-api
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
Copy-Item .env.example .env
code .env
```

У .env задайте довільний локальний SECRET_KEY і однаковий власний пароль у DB_PASSWORD та POSTGRES_PASSWORD. Інші приклади вже відповідають портам цієї інструкції. Файл .env ігнорується Git.

```powershell
docker run -d --name lab04-postgres --env-file .env -p 127.0.0.1:5433:5432 postgres:16-alpine
docker run -d --name lab04-redis -p 127.0.0.1:6380:6379 redis:7-alpine
docker exec lab04-postgres pg_isready -U lab04 -d wishlist
docker exec lab04-redis redis-cli ping
```

Продовжуйте після відповідей accepting connections і PONG. Якщо PostgreSQL ще запускається, повторіть pg_isready.

```powershell
docker exec lab04-postgres createdb -U lab04 wishlist_test
.venv/Scripts/python.exe -c "from database import engine, Base; import models; Base.metadata.create_all(bind=engine)"
.venv/Scripts/python.exe -m uvicorn main:app --port 8001
```

Створення контейнерів і бази виконується один раз. При наступному запуску виконайте docker start lab04-postgres lab04-redis, перевірте готовність і запустіть uvicorn.

В іншому терміналі перейдіть до сусідньої папки wishlist-ui.

```powershell
cd ../wishlist-ui
Copy-Item .env.example .env
npm ci
npm run dev -- --port 5174 --strictPort
```

Відкрийте http://localhost:5174, зареєструйте користувача, увійдіть та створіть список у My Wishlists. Перезавантажте сторінку й переконайтеся, що він зберігся. API документація доступна за http://localhost:8001/docs. Для перегляду HTTP-запитів відкрийте F12 → Network.

## Перевірки

З папки API при запущених PostgreSQL та Redis.

```powershell
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -v
```

Перевірено 23 листопада 2026 — All checks passed і 2 passed. Health-тест також успішно пройшов із DB_HOST=127.0.0.1:1. Тестова база називається wishlist_test, її можна змінити через TEST_DB_NAME.

Залежності виводять попередження про застарілі API SQLAlchemy, Pydantic і HTTPX. На цьому прогоні обидва тести пройшли.

## Короткий запуск для показу

Перший термінал, папка API.

```powershell
docker start lab04-postgres lab04-redis
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -v
.venv/Scripts/python.exe -m uvicorn main:app --port 8001
```

Другий термінал, папка UI.

```powershell
npm run dev -- --port 5174 --strictPort
```

Результати — список у UI, Swagger, два успішні тести та API lint / API tests на сторінці PR. Якщо сервери вже працюють, використовуйте відкриті сторінки та виконайте лише Ruff і pytest.
