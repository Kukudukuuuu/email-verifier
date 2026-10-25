"""Lazily determine whether this environment can open outbound SMTP
connections at all (TCP port 25).

Many PaaS hosts (Render, Railway, Heroku, most serverless platforms, and
PythonAnywhere) block outbound port 25. When blocked, the checker silently
degrades to syntax + DNS checking and reports every mailbox as "unknown"
with an explanation instead of pretending.
"""

from __future__ import annotations

import socket
import threading
import time

# Well-known, reliably-available inbound mail exchangers. We only open a TCP
# connection and close it again; no SMTP command at all is sent here.
_PROBE_TARGETS = (
    "gmail-smtp-in.l.google.com",
    "outlook-com.olc.protection.outlook.com",
)

_lock = threading.Lock()
_cached: tuple[bool | None, str] = (None, "not checked yet")
_cached_until = 0.0


def smtp_capability(ttl: float = 600.0) -> tuple[bool | None, str]:
    """Return (available, detail). ``None`` means not determined."""
    global _cached, _cached_until
    now = time.time()
    if _cached[0] is not None and now < _cached_until:
        return _cached

    with _lock:
        # Another thread may have filled the cache while we waited.
        if _cached[0] is not None and time.time() < _cached_until:
            return _cached

        notes: list[str] = []
        available: bool | None = None
        for host in _PROBE_TARGETS:
            try:
                with socket.create_connection((host, 25), timeout=6):
                    available = True
                    notes.append(f"connected to {host}:25 — outbound SMTP is available here")
                    break
            except socket.timeout:
                notes.append(f"{host}:25 timed out")
            except OSError as exc:
                notes.append(f"{host}:25 → {exc}")

        if available is None:
            available = False
            notes.insert(0, "outbound SMTP (TCP/25) appears to be blocked in this environment")

        detail = "; ".join(notes)
        _cached = (available, detail)
        _cached_until = time.time() + ttl
        return _cached


def reset_capability_cache() -> None:
    global _cached, _cached_until
    with _lock:
        _cached = (None, "not checked yet")
        _cached_until = 0.0
