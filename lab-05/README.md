# Лабораторна робота 5 — внесок Стаса

Командні репозиторії — [API](https://github.com/StanislavHst/withlist-api) і [UI](https://github.com/StanislavHst/wishlist-ui).

Я підготував 10-рядковий Dockerfile API, перевірив непривілейованого користувача, виключення секретів і тестів, кеш залежностей та завершення контейнера. Розширив CI job-ом API image і публікацією образу ghcr.io/stanislavhst/wishlist-api. Реалізація — [API PR #2](https://github.com/StanislavHst/withlist-api/pull/2).

## Відтворення окремого API

Потрібні Docker Desktop, Git та підготовлені PostgreSQL/Redis з Lab 4. Команди виконуйте в папці API.

```powershell
git clone https://github.com/StanislavHst/withlist-api.git
cd withlist-api
Copy-Item .env.example lab05.env
```

У lab05.env задайте власні SECRET_KEY та паролі відповідно до PostgreSQL. Для контейнерів з Lab 4 використайте DB_HOST=lab04-postgres, DB_NAME=wishlist, DB_USER=lab04, DB_SSLMODE=disable та REDIS_URL=redis://lab04-redis:6379/0.

Мережу й підключення створіть один раз.

```powershell
docker network create lab05
docker network connect lab05 lab04-postgres
docker network connect lab05 lab04-redis
docker start lab04-postgres lab04-redis
docker build -t wishlist-api:lab05 .
docker run --rm --network lab05 --env-file lab05.env wishlist-api:lab05 python -c "from database import engine, Base; import models; Base.metadata.create_all(bind=engine)"
docker run -d --name lab05-api --network lab05 --env-file lab05.env -p 127.0.0.1:8002:8000 wishlist-api:lab05
```

Swagger — http://localhost:8002/docs. Стан API — http://localhost:8002/health.

## Перевірені результати

23 листопада 2026 року контейнер повернув appuser у whoami. У /app відсутні .env, lab05.env, .git, tests та .venv. Після тимчасової зміни main.py шар встановлення залежностей позначений CACHED. Docker stop завершив контейнер за 0,92 секунди. Перевірені health, Swagger та читання списку з PostgreSQL.

API image запускається після успішних API lint та API tests. Для main job публікує main та sha-тег, для релізу v1.0.0 — 1.0.0, 1.0, 1 та latest. Job внесено до обов'язкових перевірок main.

## Короткий запуск і перевірка

На підготовленому комп'ютері, папка API.

```powershell
docker start lab04-postgres lab04-redis lab05-api
docker run --rm wishlist-api:lab05 whoami
docker run --rm wishlist-api:lab05 ls -a
docker logs lab05-api --tail 10
docker pull ghcr.io/stanislavhst/wishlist-api:1.0.0
```

Опис образу та публікації — у docs/containers.md командного API.

## Інтеграція команди

Compose повного стеку належить до частини Макса. UI-образ і його публікація — до частини Жені. Відтворюваний запуск чотирьох сервісів однією командою буде доданий після перевірки спільного Compose.
