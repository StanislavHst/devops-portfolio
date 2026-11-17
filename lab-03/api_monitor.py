#!/usr/bin/env python3
"""Check HTTP endpoints and report distinct failure types."""

import argparse
import json
import logging
import math
import os
import socket
import ssl
import sys
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


FAILURE_LABELS = {
    "dns": "Помилка DNS",
    "connection_refused": "З'єднання відхилено",
    "timeout": "Тайм-аут",
    "certificate": "Помилка TLS-сертифіката",
    "network_error": "Інша мережева помилка",
    "unexpected_status": "Несподіваний HTTP-код",
    "unexpected_body": "Несподіваний JSON-вміст відповіді",
    "slow_response": "Повільна відповідь",
}
CHECK_PREFIXES = {
    "contract": "[ПЕРЕВІРКА КОНТРАКТУ] ",
    "demonstration": "[ДЕМОНСТРАЦІЯ] ",
}
MAX_JSON_BYTES = 1024 * 1024


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Перевіряє HTTP API, записує журнал і звіт та сповіщає в Discord.",
        epilog="Приклад: python api_monitor.py --config config/api-monitor.json --no-notify",
    )
    parser.add_argument("--config", type=Path, default="config/api-monitor.json", help="JSON-конфігурація")
    parser.add_argument("--output", type=Path, default="reports/api-monitor-results.json", help="JSON-звіт")
    parser.add_argument("--log-file", type=Path, default="logs/api-monitor.log", help="Текстовий журнал")
    parser.add_argument("--webhook-env", default="DISCORD_WEBHOOK_URL", help="Змінна з адресою вебхука")
    parser.add_argument("--no-notify", action="store_true", help="Запустити без сповіщень")
    return parser.parse_args(argv)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def setup_logger(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.Logger("api_monitor", level=logging.INFO)
    formatter = logging.Formatter("%(asctime)s level=%(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S%z")
    for handler in (logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def validate_target(target, names):
    if not isinstance(target, dict):
        raise ValueError("очікувався JSON-об'єкт")
    name = target.get("name")
    if not isinstance(name, str) or not name.strip() or name in names:
        raise ValueError("'name' має бути непорожньою та унікальною назвою")

    url = target.get("url")
    parsed = urlsplit(url if isinstance(url, str) else "")
    if (
        parsed.scheme not in ("http", "https") or not parsed.hostname
        or parsed.username is not None or parsed.port == 0
    ):
        raise ValueError("'url' має бути HTTP(S)-адресою без облікових даних")

    status = target.get("expected_status")
    if not isinstance(status, int) or not 100 <= status <= 599:
        raise ValueError("'expected_status' має бути цілим числом від 100 до 599")

    for key in ("timeout_seconds", "response_time_threshold_seconds"):
        seconds = target.get(key)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"'{key}' має бути додатним скінченним числом")

    if target.get("check_kind", "monitoring") not in ("monitoring", "contract", "demonstration"):
        raise ValueError("невідомий 'check_kind'")
    if "expected_json" in target and not isinstance(target["expected_json"], dict):
        raise ValueError("'expected_json' має бути JSON-об'єктом")
    names.add(name)


def load_targets(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    targets = config.get("targets") if isinstance(config, dict) else None
    if not isinstance(targets, list) or not targets:
        raise ValueError("конфігурація має містити непорожній список 'targets'")

    names = set()
    for index, target in enumerate(targets, start=1):
        try:
            validate_target(target, names)
        except (ValueError, OverflowError) as error:
            raise ValueError(f"Ціль #{index}: {error}") from error
    return targets


def network_failure(error):
    # urllib обгортає помилку сокета або TLS у URLError.
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, socket.gaierror):
        return "dns"
    if isinstance(reason, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(reason, TimeoutError):
        return "timeout"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "certificate"
    return "network_error"


def fetch(target):
    request = Request(target["url"], headers={"User-Agent": "devops-api-monitor/1.0"})
    try:
        response = urlopen(request, timeout=target["timeout_seconds"])
    except HTTPError as error:
        # Очікуваний 401 або 404 теж може означати, що API працює правильно.
        response = error

    with response:
        status = response.getcode()
        body = None
        if status == target["expected_status"] and "expected_json" in target:
            body = response.read(MAX_JSON_BYTES + 1)
        return status, body


def evaluate_response(target, status, body, elapsed_ms):
    expected_status = target["expected_status"]
    if status != expected_status:
        return "unexpected_status", f"очікувався {expected_status}, отримано {status}"

    if body is not None:
        if len(body) > MAX_JSON_BYTES:
            return "unexpected_body", "JSON-відповідь перевищує 1 MiB"
        try:
            actual = json.loads(body)
        except ValueError:
            return "unexpected_body", "відповідь не є коректним JSON"
        if not isinstance(actual, dict) or any(
            key not in actual or actual[key] != value for key, value in target["expected_json"].items()
        ):
            return "unexpected_body", "очікувані поля JSON відсутні або мають інші значення"

    threshold_ms = target["response_time_threshold_seconds"] * 1000
    if elapsed_ms > threshold_ms:
        return "slow_response", f"{elapsed_ms:.2f} мс, поріг {threshold_ms:.2f} мс"
    return None, f"Успіх: HTTP {status}, {elapsed_ms:.2f} мс"


def check_target(target, logger):
    http_status = None
    for attempt in (1, 2):
        started = perf_counter()
        try:
            http_status, body = fetch(target)
        except (OSError, HTTPException, ValueError, OverflowError) as error:
            failure = network_failure(error)
            if attempt == 1:
                logger.warning("target=%s status=retry failure_type=%s", target["name"], failure)
                continue
            details = str(error)
        elapsed_ms = (perf_counter() - started) * 1000
        if http_status is not None:
            failure, details = evaluate_response(target, http_status, body, elapsed_ms)
        break

    kind = target.get("check_kind", "monitoring")
    message = f"{FAILURE_LABELS[failure]}: {details}" if failure else details
    return {
        "timestamp": utc_now(),
        "target": target["name"],
        "url": target["url"],
        "status": "problem" if failure else "healthy",
        "http_status": http_status,
        "response_time_ms": round(elapsed_ms, 2),
        "failure_type": failure,
        "attempts": attempt,
        "message": CHECK_PREFIXES.get(kind, "") + message,
        "check_kind": kind,
        "expected_http_status": target["expected_status"],
    }


def notify_discord(webhook_url, result, logger):
    content = (
        "🚨 API monitor\n"
        f"Ціль: {result['target']}\n"
        f"Тип збою: {result['failure_type']}\n"
        f"Режим: {result['check_kind']}\n"
        f"Деталі: {result['message']}\n"
        f"URL: {result['url']}"
    )
    data = json.dumps({"content": content[:2000], "allowed_mentions": {"parse": []}}).encode("utf-8")
    name = result["target"]
    try:
        request = Request(webhook_url, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": "DevOpsLabMonitor/1.0",
        })
        with urlopen(request, timeout=10) as response:
            status = response.getcode()
    except (OSError, HTTPException, ValueError) as error:
        # Текст винятку може містити секретну адресу вебхука.
        logger.error("target=%s notification=failed error=%s", name, type(error).__name__)
    else:
        if status in (200, 204):
            logger.info("target=%s notification=sent", name)
        else:
            logger.error("target=%s notification=failed http_status=%s", name, status)


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            json.dump(report, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    try:
        logger = setup_logger(args.log_file)
    except OSError as error:
        print(f"Не вдалося відкрити журнал: {error}", file=sys.stderr)
        return 3

    started_at = utc_now()
    logger.info("action=monitor_start config=%s", args.config)
    try:
        targets = load_targets(args.config)
    except (OSError, ValueError) as error:
        logger.error("action=config_load result=error message=%s", error)
        return 2

    results = []
    webhook_url = os.getenv(args.webhook_env, "").strip()
    for target in targets:
        result = check_target(target, logger)
        results.append(result)
        level = logging.ERROR if result["failure_type"] else logging.INFO
        logger.log(
            level, "target=%s status=%s response_time_ms=%.2f failure_type=%s attempts=%s message=%s",
            result["target"], result["status"], result["response_time_ms"],
            result["failure_type"] or "none", result["attempts"], result["message"],
        )
        if result["failure_type"]:
            if args.no_notify:
                logger.info("target=%s notification=disabled", result["target"])
            elif webhook_url:
                notify_discord(webhook_url, result, logger)
            else:
                logger.warning("target=%s notification=skipped env=%s", result["target"], args.webhook_env)

    problems = sum(result["status"] == "problem" for result in results)
    report = {
        "run_started_at": started_at,
        "run_finished_at": utc_now(),
        "summary": {"total": len(results), "healthy": len(results) - problems, "problems": problems},
        "results": results,
    }
    try:
        write_report(args.output, report)
    except OSError as error:
        logger.error("action=report_write result=error message=%s", error)
        return 3

    logger.info(
        "action=monitor_finish result=%s total=%s problems=%s output=%s",
        "problem" if problems else "success", len(results), problems, args.output,
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
