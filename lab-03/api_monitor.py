#!/usr/bin/env python3
"""Check HTTP endpoints and report distinct failure types."""

from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_CONFIG_ERROR = 2
EXIT_OUTPUT_ERROR = 3

NETWORK_FAILURES = {"dns", "connection_refused", "timeout", "certificate"}
FAILURE_LABELS = {
    "dns": "DNS: ім'я не вдалося перетворити на IP-адресу",
    "connection_refused": "З'єднання відхилено: на вказаному порту ніхто не слухає",
    "timeout": "Тайм-аут: відповідь не отримано у відведений час",
    "certificate": "Помилка сертифіката: TLS-сертифікат не пройшов перевірку",
    "unexpected_status": "Несподіваний HTTP-код",
    "slow_response": "Повільна відповідь",
    "network_error": "Інша мережева помилка",
}


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Перевіряє HTTP API, розрізняє типи збоїв, пише JSON-звіт "
            "і надсилає проблеми в Discord."
        ),
        epilog=(
            "Приклад: python api_monitor.py --config config/api-monitor.json "
            "--output reports/api-monitor-results.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default="config/api-monitor.json",
        help="шлях до JSON-конфігурації (типово: config/api-monitor.json)",
    )
    parser.add_argument(
        "--output",
        default="reports/api-monitor-results.json",
        help="шлях до структурованого JSON-звіту",
    )
    parser.add_argument(
        "--log-file",
        default="logs/api-monitor.log",
        help="шлях до текстового журналу",
    )
    parser.add_argument(
        "--webhook-env",
        default="DISCORD_WEBHOOK_URL",
        help="назва змінної середовища з Discord webhook URL",
    )
    return parser


def setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("api_monitor")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s level=%(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def load_targets(config_path: Path) -> list[dict[str, Any]]:
    if not config_path.is_file():
        raise ValueError(f"Файл конфігурації не існує: {config_path}")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Некоректний JSON у {config_path}: рядок {exc.lineno}, стовпець {exc.colno}"
        ) from exc
    except OSError as exc:
        raise ValueError(f"Не вдалося прочитати {config_path}: {exc}") from exc

    targets = raw.get("targets") if isinstance(raw, dict) else None
    if not isinstance(targets, list) or not targets:
        raise ValueError("Конфігурація повинна містити непорожній список 'targets'")

    validated: list[dict[str, Any]] = []
    for index, target in enumerate(targets, start=1):
        if not isinstance(target, dict):
            raise ValueError(f"Ціль #{index} повинна бути JSON-об'єктом")

        missing = [
            key
            for key in (
                "name",
                "url",
                "expected_status",
                "timeout_seconds",
                "response_time_threshold_seconds",
            )
            if key not in target
        ]
        if missing:
            raise ValueError(
                f"Ціль #{index}: відсутні поля: {', '.join(missing)}"
            )

        name = target["name"]
        url = target["url"]
        expected_status = target["expected_status"]
        timeout = target["timeout_seconds"]
        threshold = target["response_time_threshold_seconds"]

        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Ціль #{index}: 'name' має бути непорожнім рядком")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError(f"Ціль '{name}': URL має починатися з http:// або https://")
        if not isinstance(expected_status, int) or not 100 <= expected_status <= 599:
            raise ValueError(f"Ціль '{name}': expected_status має бути від 100 до 599")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError(f"Ціль '{name}': timeout_seconds має бути додатним числом")
        if (
            not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
            or threshold <= 0
        ):
            raise ValueError(
                f"Ціль '{name}': response_time_threshold_seconds має бути додатним числом"
            )

        validated.append(target)
    return validated


def exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        reason = getattr(current, "reason", None)
        current = reason if isinstance(reason, BaseException) else current.__cause__
    return chain


def classify_network_error(error: BaseException) -> str:
    chain = exception_chain(error)
    text = " ".join(str(item).lower() for item in chain)

    if any(isinstance(item, ssl.SSLCertVerificationError) for item in chain):
        return "certificate"
    if "certificate verify failed" in text or "certificate has expired" in text:
        return "certificate"
    if any(isinstance(item, socket.gaierror) for item in chain):
        return "dns"
    if any(isinstance(item, ConnectionRefusedError) for item in chain):
        return "connection_refused"
    if any(
        isinstance(item, OSError) and item.errno == errno.ECONNREFUSED
        for item in chain
    ):
        return "connection_refused"
    if "connection refused" in text or "actively refused" in text:
        return "connection_refused"
    if any(isinstance(item, (socket.timeout, TimeoutError)) for item in chain):
        return "timeout"
    if any(
        isinstance(item, OSError) and item.errno == errno.ETIMEDOUT
        for item in chain
    ):
        return "timeout"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return "network_error"


def result_record(
    target: dict[str, Any],
    *,
    status: str,
    response_time_ms: float,
    attempts: int,
    failure_type: str | None = None,
    message: str,
    http_status: int | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": utc_now(),
        "target": target["name"],
        "url": target["url"],
        "status": status,
        "http_status": http_status,
        "response_time_ms": round(response_time_ms, 2),
        "failure_type": failure_type,
        "attempts": attempts,
        "message": message,
    }


def request_once(target: dict[str, Any], attempt: int) -> dict[str, Any]:
    started = perf_counter()
    request = Request(
        target["url"],
        headers={"User-Agent": "devops-api-monitor/1.0"},
        method="GET",
    )
    http_status: int | None = None

    try:
        with urlopen(request, timeout=float(target["timeout_seconds"])) as response:
            http_status = response.getcode()
    except HTTPError as exc:
        http_status = exc.code
    except (URLError, OSError, ssl.SSLError) as exc:
        elapsed_ms = (perf_counter() - started) * 1000
        failure_type = classify_network_error(exc)
        return result_record(
            target,
            status="problem",
            response_time_ms=elapsed_ms,
            attempts=attempt,
            failure_type=failure_type,
            message=f"{FAILURE_LABELS[failure_type]}: {exc}",
        )

    elapsed_ms = (perf_counter() - started) * 1000
    expected_status = int(target["expected_status"])
    if http_status != expected_status:
        return result_record(
            target,
            status="problem",
            response_time_ms=elapsed_ms,
            attempts=attempt,
            failure_type="unexpected_status",
            message=(
                f"{FAILURE_LABELS['unexpected_status']}: "
                f"очікувався {expected_status}, отримано {http_status}"
            ),
            http_status=http_status,
        )

    threshold_ms = float(target["response_time_threshold_seconds"]) * 1000
    if elapsed_ms > threshold_ms:
        return result_record(
            target,
            status="problem",
            response_time_ms=elapsed_ms,
            attempts=attempt,
            failure_type="slow_response",
            message=(
                f"{FAILURE_LABELS['slow_response']}: {elapsed_ms:.2f} мс, "
                f"поріг {threshold_ms:.2f} мс"
            ),
            http_status=http_status,
        )

    return result_record(
        target,
        status="healthy",
        response_time_ms=elapsed_ms,
        attempts=attempt,
        message=f"Успіх: HTTP {http_status}, {elapsed_ms:.2f} мс",
        http_status=http_status,
    )


def check_target(target: dict[str, Any], logger: logging.Logger) -> dict[str, Any]:
    first_result = request_once(target, attempt=1)
    if first_result["failure_type"] not in NETWORK_FAILURES:
        return first_result

    logger.warning(
        "target=%s status=retry failure_type=%s message=%s",
        target["name"],
        first_result["failure_type"],
        first_result["message"],
    )
    return request_once(target, attempt=2)


def send_discord_notification(
    webhook_url: str,
    result: dict[str, Any],
    logger: logging.Logger,
) -> bool:
    content = (
        "🚨 API monitor\n"
        f"Ціль: {result['target']}\n"
        f"Тип збою: {result['failure_type']}\n"
        f"Деталі: {result['message']}\n"
        f"URL: {result['url']}"
    )
    body = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
    request = Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            if response.getcode() not in (200, 204):
                logger.error(
                    "target=%s notification=failed http_status=%s",
                    result["target"],
                    response.getcode(),
                )
                return False
    except (URLError, OSError, ssl.SSLError) as exc:
        logger.error(
            "target=%s notification=failed error=%s",
            result["target"],
            exc,
        )
        return False

    logger.info("target=%s notification=sent", result["target"])
    return True


def write_report(output_path: Path, started_at: str, results: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "run_started_at": started_at,
        "run_finished_at": utc_now(),
        "summary": {
            "total": len(results),
            "healthy": sum(result["status"] == "healthy" for result in results),
            "problems": sum(result["status"] == "problem" for result in results),
        },
        "results": results,
    }
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)


def main(argv: list[str] | None = None) -> int:
    configure_console_encoding()
    args = build_parser().parse_args(argv)
    try:
        logger = setup_logger(Path(args.log_file))
    except OSError as exc:
        print(f"Помилка: не вдалося відкрити журнал: {exc}", file=sys.stderr)
        return EXIT_OUTPUT_ERROR

    started_at = utc_now()
    logger.info("action=monitor_start config=%s", args.config)
    try:
        targets = load_targets(Path(args.config))
    except ValueError as exc:
        logger.error("action=config_load result=error message=%s", exc)
        return EXIT_CONFIG_ERROR

    results: list[dict[str, Any]] = []
    webhook_url = os.getenv(args.webhook_env, "").strip()
    for target in targets:
        result = check_target(target, logger)
        results.append(result)
        log_method = logger.info if result["status"] == "healthy" else logger.error
        log_method(
            "target=%s status=%s response_time_ms=%.2f failure_type=%s attempts=%s message=%s",
            result["target"],
            result["status"],
            result["response_time_ms"],
            result["failure_type"] or "none",
            result["attempts"],
            result["message"],
        )

        if result["status"] == "problem":
            if webhook_url:
                send_discord_notification(webhook_url, result, logger)
            else:
                logger.warning(
                    "target=%s notification=skipped reason=environment_variable_%s_is_not_set",
                    result["target"],
                    args.webhook_env,
                )

    try:
        write_report(Path(args.output), started_at, results)
    except OSError as exc:
        logger.error("action=report_write result=error message=%s", exc)
        return EXIT_OUTPUT_ERROR

    problem_count = sum(result["status"] == "problem" for result in results)
    logger.info(
        "action=monitor_finish result=%s total=%s problems=%s output=%s",
        "problem" if problem_count else "success",
        len(results),
        problem_count,
        args.output,
    )
    return EXIT_PROBLEMS if problem_count else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
