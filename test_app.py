"""Fast unit-ish tests (no network), plus a few integration-ish cases that do
touch DNS. Run with: python -m pytest test_app.py -v
"""

import pytest

import app as app_module
import checker


# ---------------------------------------------------------------- syntax
@pytest.mark.parametrize(
    "raw,ok",
    [
        ("a@b.co", True),
        ("Irfan.Khan+list@example.co.uk", True),
        ("someone@xn--b-ygbb.rocks", True),              # punycode domain
        ("l@e.com", True),
        ("上午@sunny.example.taipei", False),            # non-ascii local unsupported here
        ("", False),
        ("no-at-sign", False),
        ("two@@at.com", False),
        ("@start.com", False),
        ("end@.com", False),
        ("dot..dot@example.com", False),
        ("ok@-bad.com", False),                          # label may not start with '-'
        ("ok@bad..com", False),
        ("ok@example.invalid", False),                   # reserved TLD
        ("ok@localhost", False),
        ("ok@1.2.3.4", False),                           # ip literal not supported
        ("x" * 70 + "@ok.com", False),                   # local part too long
        ('a"@b.com', False),                             # quote unsupported in local part
        ("spaces in@ok.com", False),
    ],
)
def test_parse_address(raw, ok):
    assert (checker.parse_address(raw) is not None) is ok


def test_parse_address_normalises_domain():
    local, domain = checker.parse_address("someone@Ω.com")
    assert domain.startswith("xn--")


# ---------------------------------------------------------------- verdicts
def test_classify_basics():
    assert checker.classify(250, "ok", None)[0] == checker.DELIVERABLE
    assert checker.classify(251, "user not local, forward", None)[0] == checker.DELIVERABLE
    assert checker.classify(550, "5.1.1 mailbox not found", None)[0] == checker.BOUNCE
    assert checker.classify(252, "cannot VRFY", None)[0] == checker.UNKNOWN
    assert checker.classify(451, "Greylisted, try again", None)[0] == checker.UNKNOWN
    assert checker.classify(550, "spamhaus mxlookup", 250)[0] == checker.UNKNOWN
    assert checker.classify(550, "no such mailbox", None)[0] == checker.BOUNCE


def test_reputation_guard():
    verdict, _ = checker.classify(550, "see https://www.spamhaus.org/query", None)
    assert verdict == checker.UNKNOWN


# ---------------------------------------------------------------- plumbing
@pytest.fixture()
def client():
    with app_module.app.test_client() as c:
        yield c


def test_verify_requires_json(client):
    r = client.post("/api/verify", data="not json", content_type="application/json")
    assert r.status_code == 400


def test_verify_rejects_oversized_batch(client):
    emails = [f"user{i}@example.com" for i in range(60)]
    r = client.post("/api/verify", json={"emails": emails})
    assert r.status_code == 413


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["smtp_available"] in (True, False)


def test_verify_batches_and_bad_inputs(client):
    payload = {"emails": ["ok@bad..com", "", " ", "ok@bad..com", "a@b.co"]}
    r = client.post("/api/verify", json=payload)
    if r.status_code == 429:  # if the rate limiter fires mid-suite, that's fine
        pytest.skip("rate-limited")
    data = r.get_json()
    rows = data["results"]
    assert len(rows) == 2  # dedup + whitespace handling
    bad = rows[0]
    assert bad["verdict"] == checker.BOUNCE
    assert bad["reason"]


def test_syntax_only_bounce(client):
    r = client.post("/api/verify", json={"emails": ["plainaddress"]})
    data = r.get_json()
    row = data["results"][0]
    assert row["verdict"] == checker.BOUNCE
    assert "syntax" in row["notes"][0] or "format" in row["reason"]
