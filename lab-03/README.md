# Лабораторна робота №3 — Завдання 2

## Монітор доступності API

`api_monitor.py` перевіряє HTTP/HTTPS endpoint'и з JSON-конфігурації, вимірює час відповіді, розрізняє причини збою, записує журнал і структурований JSON-звіт. Для кожної проблемної цілі монітор може надіслати повідомлення в Discord.

Реалізація використовує лише стандартну бібліотеку Python, тому окремий файл залежностей не потрібен.

## Файли

```text
lab-03/
├── api_monitor.py
├── config/
│   └── api-monitor.json
├── tests/
│   └── test_api_monitor.py
└── README.md
```

`logs/` і `reports/` створюються автоматично під час запуску та не додаються до Git.

## Конфігурація

Кожна ціль має назву, URL, очікуваний HTTP-код, мережевий тайм-аут і максимально допустимий час відповіді:

```json
{
  "targets": [
    {
      "name": "production-api",
      "url": "https://example.com/health",
      "expected_status": 200,
      "timeout_seconds": 5,
      "response_time_threshold_seconds": 1.5
    }
  ]
}
```

Адреси не зашиті в Python-код. Секретний Discord webhook також не зберігається в репозиторії — він читається зі змінної середовища `DISCORD_WEBHOOK_URL`.

PowerShell:

```powershell
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/ID/TOKEN"
```

Bash:

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/ID/TOKEN"
```

Якщо webhook не заданий, перевірка все одно виконується, але журнал містить запис `notification=skipped`. URL webhook ніколи не виводиться в журнал.

## Запуск

Потрібен Python 3.10 або новіший.

```powershell
cd lab-03
python api_monitor.py
```

Повний варіант із явними шляхами:

```powershell
python api_monitor.py `
  --config config/api-monitor.json `
  --output reports/api-monitor-results.json `
  --log-file logs/api-monitor.log
```

Довідка з усіма параметрами та прикладом:

```powershell
python api_monitor.py --help
```

## Типи результату

| `failure_type` | Значення |
|---|---|
| `dns` | доменне ім'я не вдалося перетворити на IP-адресу |
| `connection_refused` | вузол доступний, але вказаний порт відхилив з'єднання |
| `timeout` | відповідь не надійшла протягом `timeout_seconds` |
| `certificate` | TLS-сертифікат не пройшов перевірку |
| `unexpected_status` | отриманий HTTP-код не дорівнює `expected_status` |
| `slow_response` | HTTP-код правильний, але відповідь повільніша за заданий поріг |

Для `dns`, `connection_refused`, `timeout` і `certificate` виконується рівно одна повторна спроба. Несподіваний код і повільна відповідь не повторюються, бо сервер уже надав повноцінну відповідь.

## Перевірочна конфігурація

Файл `config/api-monitor.json` містить п'ять цілей:

1. робочий endpoint;
2. зарезервований неіснуючий домен `.invalid` для DNS-помилки;
3. закритий локальний порт для відхиленого з'єднання;
4. `expired.badssl.com` із простроченим сертифікатом;
5. endpoint, що повертає `418` замість очікуваного `200`.

Фрагмент консольного виводу:

```text
target=dns-failure status=problem failure_type=dns attempts=2
target=connection-refused status=problem failure_type=connection_refused attempts=2
target=expired-certificate status=problem failure_type=certificate attempts=2
target=unexpected-status status=problem failure_type=unexpected_status attempts=1
```

Після запуску повні результати зберігаються в `reports/api-monitor-results.json`:

```json
{
  "timestamp": "2026-10-27T12:00:00+00:00",
  "target": "unexpected-status",
  "status": "problem",
  "http_status": 418,
  "response_time_ms": 245.71,
  "failure_type": "unexpected_status",
  "attempts": 1
}
```

## Коди виходу

| Код | Значення |
|---:|---|
| `0` | усі цілі здорові |
| `1` | знайдено хоча б одну проблемну ціль |
| `2` | помилка конфігурації або неправильні значення параметрів |
| `3` | не вдалося створити журнал або JSON-звіт |

Перевірити код у PowerShell:

```powershell
python api_monitor.py
$LASTEXITCODE
```

## Автоматичні тести

Тести не звертаються до зовнішньої мережі. Вони перевіряють класифікацію DNS, відхиленого з'єднання, тайм-ауту, сертифіката, несподіваного HTTP-коду, повільної відповіді та наявність лише однієї повторної спроби.

```powershell
cd lab-03
python -m unittest discover -s tests -v
```

## Відхилене з'єднання і тайм-аут

**З'єднання відхилено** означає, що клієнт дістався до потрібного вузла, але операційна система одразу відповіла: на цьому порту немає процесу, який приймає з'єднання. Під час інциденту спочатку перевіряють, чи запущений сервіс, чи слухає правильний порт і чи правильно налаштоване перенаправлення порту.

**Тайм-аут** означає, що за відведений час не надійшло ні успішної відповіді, ні явної відмови. Пакети можуть блокуватися firewall, губитися в мережі або сервіс може зависнути. Тому перевіряють мережевий маршрут, правила firewall, навантаження та журнали завислого сервісу.

Це різні сигнали: при відхиленому з'єднанні вузол відповідає швидко й проблема зазвичай знаходиться біля процесу або порту; при тайм-ауті місце втрати відповіді невідоме, тому потрібна мережна діагностика і перевірка стану сервісу.
