# Лабораторна робота №3 — завдання 2: монітор Wishlist API

Монітор призначений для локального проєкту [Wishlist UI](https://github.com/nromanen/wishlist-ui) та [Wishlist API](https://github.com/nromanen/withlist-api), запущеного через Docker. Код інструмента — [api_monitor.py](api_monitor.py), звіт про реалізацію — [DEFENSE.md](DEFENSE.md).

## Передумови

- Python 3.10+; залежностей поза стандартною бібліотекою немає.
- Docker Desktop запущений. Compose API містить `api`, `postgres` і `redis`.
- API доступний на `http://127.0.0.1:8000`; UI — на `http://localhost:5173`.

На цьому комп'ютері API запускається так:

```powershell
docker compose -f "C:\Users\stasi\OneDrive\Документы\wishlist-local\withlist-api\docker-compose.yml" up -d
```

Монітор виконується на Windows-хості, тому використовує `127.0.0.1:8000`. Усередині іншого контейнера `localhost` позначатиме сам контейнер; тоді URL треба змінити на адресу сервісу у Docker-мережі.

## Основна конфігурація

[config/api-monitor.json](config/api-monitor.json) містить п'ять реальних цілей:

| Ціль | Очікувана відповідь | Призначення |
|---|---|---|
| `/health` | 200; `status=healthy`, `redis=healthy` | Перевірка API та відповіді Redis |
| `/wishlists` | 200 | Доступність публічного списку через API; дані можуть надходити з кешу |
| `/openapi.json` | 200 | Доступність схеми API |
| `/docs` | 200 | Доступність Swagger UI |
| `/users/me` | 401 без токена | Захищений маршрут правильно вимагає авторизацію |

Перевірка `/wishlists` не гарантує поточного з'єднання з PostgreSQL, якщо спрацював Redis-кеш. Монітор не виконує CRUD-операції, не створює акаунтів і не змінює дані Wishlist.

Приклад цілі:

```json
{
  "name": "wishlist-health",
  "url": "http://127.0.0.1:8000/health",
  "expected_status": 200,
  "timeout_seconds": 5,
  "response_time_threshold_seconds": 2,
  "expected_json": {"status": "healthy", "redis": "healthy"}
}
```

`expected_json` — додаткова перевірка заданих полів верхнього рівня JSON. Wishlist `/health` повертає HTTP 200 навіть при `redis=unhealthy`; перевірка JSON виявляє цей випадок як `unexpected_body`. Зчитується не більше 1 MiB JSON-відповіді.

Під час перевірки реальний Redis-контейнер короткочасно зупинено: монітор виявив `unexpected_body` при HTTP 200 та доставив Discord-повідомлення. Redis відновлено; повторний прогін показав 5 здорових цілей.

## Запуск

Усі наступні команди виконуються з теки `lab-03`:

```powershell
python api_monitor.py --help
python api_monitor.py
$LASTEXITCODE
```

При справному Wishlist: `total=5`, `healthy=5`, `problems=0`, код `0`. Для здорових цілей Discord-повідомлення не надсилаються.

Явні параметри:

```powershell
python api_monitor.py --config config/api-monitor.json --output reports/api-monitor-results.json --log-file logs/api-monitor.log
```

Параметри: `--config` — JSON-конфігурація; `--output` — JSON-звіт; `--log-file` — журнал; `--webhook-env` — ім'я змінної з webhook (типово `DISCORD_WEBHOOK_URL`); `--no-notify` — діагностика без повідомлень.

## Discord

Використовується особистий сервер **DevOps Lab Monitor**, канал **#general**, webhook **DevOps API Monitor**. Для кожної проблемної цілі надсилається повідомлення з назвою, типом збою, деталями та URL.

Webhook читається виключно зі змінної середовища. На іншому комп'ютері його можна задати в поточній PowerShell-сесії:

```powershell
$env:DISCORD_WEBHOOK_URL = "<власний webhook URL>"
python api_monitor.py
```

На цьому Windows-комп'ютері секрет збережено через DPAPI поза репозиторієм: `$env:LOCALAPPDATA/DevOpsLab03/discord-webhook.xml`. Обгортка підставляє його у змінну середовища та відновлює її попереднє значення після запуску:

```powershell
./run-monitor.ps1
```

Без webhook проблеми все одно перевіряються й записуються, але журнал містить `notification=skipped`; це не є підтвердженням доставки. Успішне надсилання має `notification=sent`, невдале — `notification=failed`. Сам webhook не виводиться; помилка доставки записується назвою класу помилки без секретного URL. `--no-notify` / `-NoNotify` вимикає доставку явно.

## Обов'язкові сценарії збоїв

[config/failure-scenarios.json](config/failure-scenarios.json) відтворює чотири різні випадки з умови:

| Сценарій | Результат |
|---|---|
| `wishlist-monitor.invalid/health` | `dns` |
| закритий порт `127.0.0.1:65534` | `connection_refused` |
| `expired.badssl.com` | `certificate` |
| неіснуючий маршрут справжнього Wishlist API | `unexpected_status`: 404 замість 200 |

Локальний Wishlist використовує HTTP, тому для TLS-сценарію потрібен окремий публічний домен із простроченим сертифікатом. Навмисні помилки адреси й порту не змінюють налаштування проєкту.

```powershell
./run-monitor.ps1 -Config config/failure-scenarios.json
$LASTEXITCODE
```

Очікується код `1`, чотири різні типи збою та чотири Discord-повідомлення. Обгортка зберігає цей звіт окремо: `reports/failure-scenarios-results.json`.

Фрагмент журналу:

```text
target=wishlist-dns-failure notification=sent
target=wishlist-closed-port notification=sent
target=tls-expired-certificate notification=sent
target=wishlist-unexpected-status notification=sent
action=monitor_finish result=problem total=4 problems=4
```

## Типи збоїв і повторна спроба

Окремо обробляються `dns`, `connection_refused`, `timeout`, `certificate`, `unexpected_status`, `slow_response`, `unexpected_body` та загальна `network_error`.

Для мережевих помилок виконується рівно одна повторна спроба; максимальна кількість спроб — 2. Помилка HTTP-коду, повільна відповідь і неправильний JSON не повторюються. Якщо друга спроба успішна, ціль здорова та повідомлення про збій не надсилається.

## Журнал і JSON-звіт

Журнал містить часову мітку, дію, ціль, статус, час відповіді, тип збою, кількість спроб і результат сповіщення. JSON-звіт містить `run_started_at`, `run_finished_at`, `summary` і масив `results`; кожен результат має `timestamp`, `target`, `url`, `status`, `http_status`, `response_time_ms`, `failure_type`, `attempts`, `message`.

```powershell
Get-Content logs/api-monitor.log -Tail 30
Get-Content reports/api-monitor-results.json
```

Звіт записується через тимчасовий файл та заміну попереднього. `logs/`, `reports/` і Python-кеші не потрапляють у Git.

## Автоматичні тести

**57 автономних тестів** охоплюють конфігурацію, числові межі та некоректні значення, URL, JSON, класифікацію збоїв, retry/recovery, HTTP-коди, часові пороги, журнал, звіт, коди виходу, Discord-повідомлення й помилки доставки. Частина тестів використовує справжній HTTP-сервер на випадковому loopback-порту для timeout, slow response, JSON і статусів. Зовнішніх звернень до Discord у тестах немає.

Ще **5 інтеграційних тестів** виконують read-only запити до реального Wishlist у Docker. Вони вмикаються явно. Без цієї змінної 57 тестів проходять, а 5 інтеграційних пропускаються.

```powershell
python -m unittest discover -s tests -v

# Docker-проєкт має бути запущений:
$env:WISHLIST_LIVE = "1"
python -m unittest discover -s tests -v
Remove-Item Env:WISHLIST_LIVE
```

Повний прогін 10 листопада 2026 року: **62/62 пройдено**. Початкові 8 тестів збережено без змін; додано окремі файли тестів.

## Коди виходу

| Код | Значення |
|---:|---|
| `0` | усі цілі здорові |
| `1` | є проблемна ціль |
| `2` | помилка конфігурації або аргументів |
| `3` | помилка створення журналу або запису звіту |

Код `1` сигналізує про стан цілей; факт доставки кожного сповіщення перевіряється в журналі. Для конфігурації з неіснуючим файлом повертається зрозуміле повідомлення без traceback.

## Відхилене з'єднання та тайм-аут

**Connection refused** — вузол явно відхиляє TCP-з'єднання: сервіс не слухає потрібний порт або firewall повернув відмову. Перевіряються процес, порт і Docker port mapping.

**Timeout** — операція не завершується у відведений час: можливі втрата пакетів, блокування firewall, проблеми маршруту чи зависання сервісу. Перевіряються мережевий шлях, навантаження й журнали. Явна відмова локалізує пошук біля сервісу/порту; відсутність відповіді потребує ширшої діагностики.
