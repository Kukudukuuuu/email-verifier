"""Email deliverability engine.

Pipeline, per address:

1. syntax check (RFC 5321-flavoured; strict enough to catch real-world junk)
2. DNS: does the domain exist, and does it have a mail exchanger?
   (no domain / no MX and no A record / null MX -> the address cannot exist)
3. disposable-domain flag (a hint, not a verdict)
4. optional SMTP mailbox probe: talk to the domain's *own* mail exchangers,
   MAIL FROM, RCPT TO — and stop right there. No DATA is ever sent, so no
   message reaches anybody.

Result is one of three verdicts:

- "deliverable"  — a mail server confirmed this exact mailbox exists
- "bounce"       — syntax/domain is hopeless, or a mail server rejected
                   this exact mailbox with a permanent (5xx) error
- "unknown"      — everything else: greylisting, catch-all domains,
                   servers that refuse verification, network blocks, ...

"unknown" is a first-class result, not an error state: many mail servers
deliberately refuse to confirm mailboxes, and some hosting environments
cannot open SMTP connections at all.
"""

from __future__ import annotations

import ipaddress
import random
import re
import socket
import threading
import time
from dataclasses import dataclass

import dns.exception
import dns.resolver

from disposable import is_disposable

DELIVERABLE = "deliverable"
BOUNCE = "bounce"
UNKNOWN = "unknown"

# --------------------------------------------------------------------------
# configuration (overridable via env in app.py)
# --------------------------------------------------------------------------
CONNECT_TIMEOUT = 8.0   # seconds to open one TCP connection
CMD_TIMEOUT = 10.0      # seconds per SMTP command / reply
MAX_MX_TRIED = 3        # try at most this many MX hosts per address
PER_DOMAIN_INTERVAL = 1.5  # min. seconds between connects to the same domain

# Reply text that suggests the *sender* (our IP) is being rejected for
# reputation/policy reasons — not a verdict about the mailbox itself.
IP_REPUTATION_WORDS = (
    "block", "blacklist", "blocklist", "spell", "spam", "policy",
    "reputation", "authenticat", "reverse", "ptr", "dnsbl", "rbl",
    "relay denied", "not permitted", "not allowed", "unresolvable",
    "helo", "banned", "abuse", "water", "indb",
)

_thread_local = threading.local()


# --------------------------------------------------------------------------
# syntax
# --------------------------------------------------------------------------
_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_LABEL_RE = re.compile(r"^(?:xn--)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
_RESERVED_TLDS = {"test", "example", "invalid", "localhost", "local", "internal", "onion"}


def parse_address(raw: str) -> tuple[str, str] | None:
    """Return (local, ascii-domain) or None if the syntax is invalid.

    Normalises the domain to IDNA/lowercase; the local part is preserved.
    """
    addr = raw.strip()
    if addr.startswith("<") and addr.endswith(">"):
        addr = addr[1:-1]
    if not addr or addr.count("@") != 1:
        return None
    local, domain = addr.split("@")

    if not local or not domain:
        return None
    if local.startswith('"') or local.endswith('"'):
        return None  # quoted local parts: rare, intentionally unsupported
    if len(local) > 64:
        return None
    if not _LOCAL_RE.match(local):
        return None
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return None

    if len(domain) > 253:
        return None
    domain = domain.rstrip(".")
    try:
        domain_ascii = domain.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None
    if domain_ascii.count(".") < 1:
        return None
    for label in domain_ascii.split("."):
        if not _LABEL_RE.match(label):
            return None
    tld = domain_ascii.rsplit(".", 1)[-1]
    if len(tld) < 2 or not tld.isalpha():
        return None
    if tld in _RESERVED_TLDS:
        return None
    return local, domain_ascii


# --------------------------------------------------------------------------
# DNS
# --------------------------------------------------------------------------
@dataclass
class DomainInfo:
    status: str          # "ok" | "no_mail" | "nxdomain" | "temp"
    mx: list[str]        # relay hostnames (empty for implicit MX)
    implicit_mx: bool
    detail: str


def check_dns(domain: str) -> DomainInfo:
    r = dns.resolver.Resolver()
    r.lifetime = 6.0

    try:
        answers = r.resolve(domain, "MX")
    except dns.resolver.NXDOMAIN:
        return DomainInfo("nxdomain", [], False, "domain does not exist")
    except dns.resolver.LifetimeTimeout:
        return DomainInfo("temp", [], False, "DNS lookup timed out")
    except (dns.resolver.NoNameservers, dns.exception.Timeout):
        return DomainInfo("temp", [], False, "DNS lookup failed (authoritative servers unreachable)")
    except OSError:
        return DomainInfo("temp", [], False, "DNS lookup failed (network error)")

    # "answers" iterates as individual MX rdatums (preference + exchange)
    pairs: list[tuple[int, str]] = []
    saw_record = False
    for record in answers:
        saw_record = True
        exchange = record.exchange
        name = exchange.to_text().rstrip(".") if exchange else ""
        if name and name != ".":
            pairs.append((record.preference, name.lower()))
    pairs.sort(key=lambda p: p[0])
    hosts = [name for _, name in pairs]

    # RFC 7505 "null MX": a single MX of preference 0 pointing at "." means
    # the domain explicitly does not accept mail.
    if saw_record and not hosts:
        return DomainInfo("no_mail", [], False, "domain advertises that it accepts no mail (null MX)")
    if not hosts:
        return DomainInfo("no_mail", [], False, "domain has no MX record")

    detail = ", ".join(hosts[:3]) + ("…" if len(hosts) > 3 else "")
    return DomainInfo("ok", hosts, False, f"MX record(s) for {domain}: {detail}")


def check_dns_implicit(domain: str) -> DomainInfo:
    """No MX: fall back to the A record as an implicit mail exchanger (RFC 5321 §5.1)."""
    r = dns.resolver.Resolver()
    r.lifetime = 6.0
    has_a = False
    try:
        r.resolve(domain, "A")
        has_a = True
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        has_a = False
    except (dns.resolver.LifetimeTimeout, dns.resolver.NoNameservers, dns.exception.Timeout, OSError):
        return DomainInfo("temp", [], False, "DNS lookup failed while checking for a mail exchanger")
    source = socket.getaddrinfo(domain, 25, proto=socket.IPPROTO_TCP) if has_a else []
    if has_a:
        ips = {i[4][0] for i in source}
        if _any_private(ips):
            return DomainInfo("ok", [], True, "no MX; domain points at a private address — skipping SMTP probe")
        return DomainInfo("ok", [domain], True, "no MX record; mail falls back to the domain's A record (implicit MX)")
    return DomainInfo("no_mail", [], False, "domain has no MX record and no A record — it cannot receive mail")


def _any_private(ips: set[str]) -> bool:
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return True
    return False


def resolve_mx_safe(domain: str) -> DomainInfo:
    """check_dns with the implicit-MX fallback and private-IP guard."""
    info = check_dns(domain)
    if info.status == "no_mail" and not info.detail.startswith("domain advertises"):
        info = check_dns_implicit(domain)
    if info.mx:
        # refuse to probe mail hosts sitting on private/reserved space
        r = dns.resolver.Resolver()
        r.lifetime = 6.0
        for host in info.mx[:MAX_MX_TRIED]:
            try:
                ips = {ans.address for ans in r.resolve(host, "A")}
                if _any_private(ips):
                    return DomainInfo("ok", [], info.implicit_mx, info.detail)
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                continue
            except (dns.resolver.LifetimeTimeout, dns.resolver.NoNameservers, dns.exception.Timeout, OSError):
                continue
    return info


# --------------------------------------------------------------------------
# SMTP probe (a deliberately tiny SMTP client; sessions never reach DATA)
# --------------------------------------------------------------------------
class SMTPSession:
    def __init__(self, host: str, connect_timeout: float, cmd_timeout: float):
        self.host = host
        self.sock: socket.socket | None = None
        self.reader = None
        self.connect_timeout = connect_timeout
        self.cmd_timeout = cmd_timeout

    def __enter__(self) -> "SMTPSession":
        self.sock = socket.create_connection((self.host, 25), timeout=self.connect_timeout)
        self.sock.settimeout(self.cmd_timeout)
        self.reader = self.sock.makefile("rb")
        # consume the 220 greeting so replies stay in sync with commands
        self.greeting = self._read_reply()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self.reader:
            try:
                self.reader.close()
            except OSError:
                pass
            self.reader = None
        if self.sock:
            try:
                self.sock.sendall(b"QUIT\r\n")
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _read_reply(self) -> tuple[int, str]:
        assert self.reader is not None
        lines: list[str] = []
        code = 0
        for _ in range(12):  # safety valve against run-away multiline replies
            raw = self.reader.readline()
            if not raw:
                raise ConnectionResetError("connection closed by server")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if len(line) < 4:
                raise ConnectionResetError(f"malformed SMTP reply: {line!r}")
            code = int(line[:3])
            lines.append(line[4:])
            if line[3] != "-":
                break
        return code, "; ".join(lines)

    def cmd(self, what: str) -> tuple[int, str]:
        assert self.sock is not None
        self.sock.sendall((what + "\r\n").encode("utf-8", "replace"))
        return self._read_reply()


@dataclass
class ProbeResult:
    ok: bool                     # True if we got a usable RCPT verdict
    code: int | None
    text: str
    catch_all_code: int | None   # code a random probe address received
    error: str                   # human note when ok is False
    notes: list[str]             # notes about each MX attempt


_domain_locks: dict[str, float] = {}
_throttle_lock = threading.Lock()


def _throttle(domain: str) -> None:
    """Space out connects to the same domain so a batch of many addresses
    doesn't look like an attack on one mail server."""
    while True:
        with _throttle_lock:
            last = _domain_locks.get(domain, 0.0)
            wait = last + PER_DOMAIN_INTERVAL - time.monotonic()
            if wait <= 0:
                _domain_locks[domain] = time.monotonic()
                return
            if wait > PER_DOMAIN_INTERVAL:
                wait = PER_DOMAIN_INTERVAL
        time.sleep(wait)


def _code_is_reputation(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in IP_REPUTATION_WORDS)


def smtp_probe(
    domain_info: DomainInfo,
    local: str,
    domain: str,
    helo: str,
    mail_from: str,
    catch_all: bool,
) -> ProbeResult:
    # Implicit MX: the domain's A records double as mail relays.
    if domain_info.implicit_mx:
        hosts: list[str] = [domain]
    else:
        hosts = domain_info.mx[:MAX_MX_TRIED]
    if not hosts:
        return ProbeResult(False, None, "", None, "no reachable mail exchanger", [])
    notes: list[str] = []
    for host in hosts:
        _throttle(domain)
        try:
            with SMTPSession(host, CONNECT_TIMEOUT, CMD_TIMEOUT) as s:
                code, text = s.cmd(f"EHLO {helo}")
                if code >= 400:
                    code, text = s.cmd(f"HELO {helo}")
                    if code >= 400:
                        notes.append(f"{host}: HELO refused ({code})")
                        continue

                code, text = s.cmd(f"MAIL FROM:<{mail_from}>")
                if code >= 400:
                    notes.append(f"{host}: refused our envelope sender ({code} {text})")
                    continue

                code, text = s.cmd(f"RCPT TO:<{local}@{domain}>")
                ca_code: int | None = None
                if catch_all:
                    # deliberately neutral-looking token: several providers
                    # filter obvious "verifier" patterns like verify-*, probe-*
                    token = "".join(random.choices("abcdefghijklmnopqrstuvwxyz23456789", k=14))
                    try:
                        ca_code, _ = s.cmd(f"RCPT TO:<{token}@{domain}>")
                    except OSError:
                        ca_code = None
                return ProbeResult(True, code, text, ca_code, "", notes)
        except socket.timeout:
            notes.append(f"{host}: timed out")
        except (ConnectionResetError, OSError) as exc:
            notes.append(f"{host}: {exc}")

    error = "could not get an SMTP verdict — " + ("; ".join(notes) if notes else "no host tried")
    return ProbeResult(False, None, "", None, error, notes)


# --------------------------------------------------------------------------
# verdict logic
# --------------------------------------------------------------------------
def classify(code: int, text: str, catch_all_code: int | None) -> tuple[str, str]:
    if catch_all_code in (250, 251, 252):
        return UNKNOWN, (
            "this domain accepts every recipient (catch-all), so the server's "
            "acceptance does not prove the mailbox exists"
        )
    if code in (250, 251):
        return DELIVERABLE, "a mail server confirmed this mailbox accepts mail"
    if code == 252:
        return UNKNOWN, "the mail server refuses to confirm mailboxes (252), so existence is unverifiable"
    if 500 <= code <= 599:
        if _code_is_reputation(text):
            return UNKNOWN, (
                f"the server rejected our probe for policy reasons ({text.split(';')[0].strip()}), "
                "which says nothing about this mailbox; try again from an IP with better reputation"
            )
        if code in (501, 503, 553):
            return UNKNOWN, f"the server objected to the probe protocol itself ({code} {text})"
        return BOUNCE, f"the mail server rejected this mailbox permanently ({text.split(';')[0].strip()})"
    if 400 <= code <= 499:
        return UNKNOWN, f"temporary failure {code} (greylisting or rate limiting) — verdict may change on retry"
    return UNKNOWN, f"unexpected server answer {code}: {text[:120]}"


def verify_address(
    raw: str,
    helo: str,
    mail_from: str,
    attempt_smtp: bool,
    env_blocked_note: str | None = None,
    catch_all: bool = True,
) -> dict:
    result = {
        "input": raw,
        "email": raw,
        "verdict": UNKNOWN,
        "reason": "",
        "disposable": False,
        "catch_all": None,
        "smtp_attempted": False,
        "notes": [],
    }
    try:
        _verify_inner(result, helo, mail_from, attempt_smtp, env_blocked_note, catch_all)
    except Exception as exc:  # never let one address kill the batch
        result["verdict"] = UNKNOWN
        result["reason"] = f"internal check error: {exc.__class__.__name__}"
        result["notes"].append(f"exception during verification: {exc}")
    return result


def _verify_inner(
    result: dict,
    helo: str,
    mail_from: str,
    attempt_smtp: bool,
    env_blocked_note: str | None,
    catch_all: bool,
) -> None:
    parsed = parse_address(result["input"])
    if parsed is None:
        result["verdict"] = BOUNCE
        result["reason"] = "invalid format — no mail server would accept this address"
        result["notes"].append("failed syntax check")
        return

    local, domain = parsed
    result["email"] = f"{local}@{domain}"
    result["disposable"] = is_disposable(domain)

    dns_info = resolve_mx_safe(domain)

    if dns_info.status == "nxdomain":
        result["verdict"] = BOUNCE
        result["reason"] = f"domain {domain} does not exist in DNS — delivery is impossible"
        return
    if dns_info.status == "no_mail":
        result["verdict"] = BOUNCE
        result["reason"] = f"domain {domain} cannot receive mail — {dns_info.detail}"
        return
    if dns_info.status == "temp":
        result["verdict"] = UNKNOWN
        result["reason"] = f"could not verify domain {domain} — {dns_info.detail}"
        return

    # domain is mail-capable; either probe or explain why not
    if not attempt_smtp:
        result["verdict"] = UNKNOWN
        result["reason"] = (
            "domain looks valid, but this environment cannot open outbound SMTP connections, "
            "so mailbox existence was not checked — treat as unknown"
        )
        if env_blocked_note:
            result["notes"].append(env_blocked_note)
        return

    result["smtp_attempted"] = True
    if dns_info.mx == [] and dns_info.detail.endswith("skipping SMTP probe"):
        result["verdict"] = UNKNOWN
        result["reason"] = f"domain {domain} has a private/reserved mail host — SMTP probe refused for safety, so existence is unknown"
        return

    probe = smtp_probe(dns_info, local, domain, helo, mail_from or "", catch_all)
    if probe.ok:
        verdict, reason = classify(probe.code or 0, probe.text, probe.catch_all_code)
        result["verdict"] = verdict
        result["reason"] = reason
        result["catch_all"] = probe.catch_all_code in (250, 251, 252)
        if probe.notes:
            result["notes"].extend(probe.notes)
        return

    result["verdict"] = UNKNOWN
    result["reason"] = probe.error
    result["notes"].extend(probe.notes)
