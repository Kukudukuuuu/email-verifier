
"""Web UI + JSON API for the email deliverability checker."""

from __future__ import annotations

import os
import socket
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from flask import Flask, jsonify, render_template, request

import checker
from capability import smtp_capability
from emailip import verify_with_api

app = Flask(__name__)

MAX_BATCH = int(os.environ.get("MAX_BATCH", "50"))
MAX_WORKERS = int(os.environ.get("SMTP_MAX_WORKERS", "8"))

fqdn = socket.getfqdn()
_DEFAULT_HELO = (
    fqdn if "." in fqdn and "_" not in fqdn
    else "email-checker.local"
)

HELO_DOMAIN = os.environ.get("HELO_DOMAIN", _DEFAULT_HELO)
MAIL_FROM = os.environ.get("MAIL_FROM", "")
RATE_WINDOW = float(os.environ.get("RATE_WINDOW", "300"))
RATE_MAX_CALLS = int(os.environ.get("RATE_MAX_CALLS", "6"))

_pool = ThreadPoolExecutor(
    max_workers=MAX_WORKERS,
    thread_name_prefix="verify",
)
_pool_lock = Lock()

_rate: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = Lock()


def _rate_ok(ip: str) -> bool:
    now = time.monotonic()

    with _rate_lock:
        bucket = _rate[ip]

        while bucket and bucket[0] < now - RATE_WINDOW:
            bucket.popleft()

        if len(bucket) >= RATE_MAX_CALLS:
            return False

        bucket.append(now)
        return True


def _verify_one(email, do_smtp, cap_detail, catch_all):
    result = checker.verify_address(
        email,
        HELO_DOMAIN,
        MAIL_FROM,
        do_smtp,
        None if do_smtp else cap_detail,
        catch_all,
    )

    # Use the external API only when SMTP is blocked
    # and the existing checker returns an inconclusive verdict.
    if (
        not do_smtp
        and result.get("verdict") == checker.UNKNOWN
    ):
        api_result = verify_with_api(email)

        if api_result is not None:
            verdict = api_result.get("verdict")

            allowed = {
                checker.DELIVERABLE,
                checker.BOUNCE,
                checker.UNKNOWN,
            }

            if verdict in allowed:
                result["verdict"] = verdict
                result["reason"] = api_result.get(
                    "reason", result.get("reason", "")
                )
                result.setdefault("notes", []).extend(
                    api_result.get("notes", [])
                )

    return result


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    available, detail = smtp_capability()

    return jsonify({
        "status": "ok",
        "smtp_available": available,
        "smtp_detail": detail,
    })


@app.post("/api/verify")
def api_verify():
    payload = request.get_json(silent=True)

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("emails"), list)
    ):
        return jsonify({
            "error": 'send JSON of shape {"emails": ["a@b.c", ...]}'
        }), 400

    seen: set[str] = set()
    cleaned: list[str] = []

    for item in payload["emails"]:
        if not isinstance(item, str):
            continue

        value = item.strip()
        if not value:
            continue

        key = value.lower()
        if key in seen:
            continue

        seen.add(key)
        cleaned.append(value)

    if not cleaned:
        return jsonify({
            "error": "no usable addresses in the request"
        }), 400

    if len(cleaned) > MAX_BATCH:
        return jsonify({
            "error": (
                f"at most {MAX_BATCH} addresses per batch "
                f"(got {len(cleaned)}); split your list"
            )
        }), 413

    ip = request.headers.get(
        "X-Forwarded-For",
        request.remote_addr or "?",
    ).split(",")[0].strip()

    if not _rate_ok(ip):
        return jsonify({
            "error": (
                "rate limit: a few batches per minute, "
                "please try again shortly"
            )
        }), 429

    do_smtp, cap_detail = smtp_capability()
    catch_all = payload.get("catch_all_test", True) is not False

    with _pool_lock:
        futures = [
            _pool.submit(
                _verify_one,
                email,
                do_smtp,
                cap_detail,
                catch_all,
            )
            for email in cleaned
        ]

    results = [future.result() for future in futures]

    summary = {
        checker.DELIVERABLE: 0,
        checker.BOUNCE: 0,
        checker.UNKNOWN: 0,
    }

    for result in results:
        verdict = result.get("verdict", checker.UNKNOWN)

        if verdict not in summary:
            result["verdict"] = checker.UNKNOWN
            verdict = checker.UNKNOWN

        summary[verdict] += 1

    return jsonify({
        "results": results,
        "summary": summary,
        "environment": {
            "smtp_available": do_smtp,
            "smtp_detail": cap_detail,
            "helo_domain": HELO_DOMAIN,
        },
    })


# WSGI entry point for Gunicorn and other WSGI servers.
application = app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
