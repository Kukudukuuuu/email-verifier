# Email deliverability checker

Paste a list of email addresses; get a per-address verdict:

| Verdict | Meaning |
| --- | --- |
| **Likely to be delivered** | a mail server that owns the domain confirmed this exact mailbox accepts mail |
| **Likely to bounce** | syntax is invalid, the domain cannot receive mail, or a responsible mail server rejected the mailbox with a permanent (5xx) error |
| **Unknown** | everything else — the checker could not get a trustworthy answer |

**"Unknown" is a first-class result, not an error.** Many mail servers deliberately
refuse to confirm mailboxes (greylisting, anti-harvesting rules), many
*domains* accept every local part so no answer can ever be specific, and some
hosting platforms block the SMTP connections the deep check needs. The UI
explains the reason for every row, and the top banner warns you when the
current deployment cannot run the SMTP stage at all.

The app is Flask + `dnspython` + a small hand-written SMTP client. It never
sends a message: SMTP sessions always stop right after `RCPT TO`, before any
`DATA`.

## How a verdict is produced

1. **Syntax** (RFC 5321-flavoured): structure, lengths, IDNA domain
   normalisation. Failure ⇒ *bounce* ("no mail server would accept this").
2. **DNS**: does the domain exist and have a mail exchanger? Handles `NXDOMAIN`,
   RFC 7505 *null MX* ("domain announces it accepts no mail"), and the
   implicit-MX fallback (no MX → use the A record, RFC 5321 §5.1). A domain
   that cannot receive mail ⇒ *bounce*. A DNS outage ⇒ *unknown*.
3. **Disposable hint**: throwaway-inbox domains (Mailinator, YOPmail, …) get a
   warning tag — a hint only, never a verdict on its own.
4. **SMTP mailbox probe** (the deep check): connect to the domain's own MX
   servers, `EHLO`, `MAIL FROM:<>` (null sender), `RCPT TO:<the address>`, and
   a random catch-all probe, then `QUIT`. No `DATA` is ever sent, so **no
   message is delivered to anyone**.
   - `250/251` ⇒ *deliverable* — unless the catch-all probe also succeeded
     ⇒ *unknown* (the domain accepts anything).
   - `5xx` on the address ⇒ *bounce*, except when the reply text is about
     our IP's reputation/policy (Spamhaus blocks, `HELO` checks, RDNS
     requirements) ⇒ *unknown*.
   - `4xx` (greylisting, rate limits), `252` ("won't verify"), refusals of
     the probe itself, connection resets, timeouts ⇒ *unknown*.

Batches are capped (50 by default), same-domain probes are spaced apart, and
per-IP rate limiting applies — the tool is intentionally polite to mail
servers. Heavy probing by one IP can get that IP listed; the ethics section
below applies to you more than to the tool.

## ⚠️ What the deployed version can and cannot check

Free tiers of the popular hosts block outbound SMTP, which turns off stage 4
mailbox probing. This is exactly the "host blocks connections to mail
servers" case the project brief anticipates. The app **detects** this at
runtime (`GET /api/health` → `smtp_available`), shows a banner, and labels
affected results instead of pretending.

| Environment | Outbound SMTP (port 25) | What the checker can still tell you |
| --- | --- | --- |
| This repo run locally / VPS with port 25 open | ✅ usually | everything, incl. real mailbox verdicts |
| **Render free** (since 2025-09: blocks 25, 465, 587; port 25 also blocked on paid plans) | ❌ blocked | syntax, DNS, null-MX, disposable hint; valid domains ⇒ *unknown* |
| **Railway free/hobby/pro** (SMTP blocked on Free/Trial/Hobby; Pro can enable 465/587 but **port 25 stays blocked**) | ❌ blocked | same as Render |
| **PythonAnywhere** (outbound internet only via whitelisting; direct SMTP not offered on free tiers) | ❌ blocked | same |

So on free PaaS hosting:

- *bounce* verdicts are still real: invalid syntax, `NXDOMAIN` domains, null-MX
  and no-MX/no-A domains are detected with certainty;
- *deliverable* verdicts cannot be produced (they require talking to the mail
  server) — accept-the-domain addresses show as *unknown* with an explanation;
- the disposable flag still works (DNS-based list).

To unlock full checking, run the app where outbound TCP/25 is allowed — e.g.
a small VPS (Hetzner, OVH, Linode; request reverse DNS in their panels), a
home server with a clean residential IP, or Workflow/pro=Render paid+
(Railway Pro still blocks 25, so VPS is the better route):

```bash
git clone <this repo> && cd email-verifier
python -m venv .venv && .venv/bin/pip install -r requirements.txt
HELO_DOMAIN=mail.yourdomain.example .venv/bin/gunicorn -b 0.0.0.0:8000 -w 2 --threads 8 app:app
```

The probes honour `HELO_DOMAIN` and `MAIL_FROM`; set them to a domain you
control for the best verdict accuracy (some servers refuse mail-sounding
`HELO` names). Accuracy also depends on the egress IP's reputation — which is
why several big providers (Yahoo, iCloud, kernel.org MTAs…) may answer your
`RCPT` probes with policy 5xx; the app maps those to *unknown* rather than
falsely claiming the mailbox bounces.

## Deployment

### Render (free)
1. Push this folder to a GitHub repo.
2. In Render: **New + → Blueprint** → pick the repo → **Apply**. `render.yaml`
   in the repo drives the setup (Python runtime, `pip install`, gunicorn).
3. The public URL appears on the service page. Free instances sleep when idle
   (first request takes ~30 s to wake).

### Railway
1. `npm i -g @railway/cli && railway login`
2. In the project folder: `railway init && railway up`
   (Dockerfile detected automatically) → `railway domain` publishes a URL.

### PythonAnywhere (no CLI needed)
1. Upload the folder (or clone it in a console).
2. Web tab → Add new web app → **Manual config / Python**, set the virtualenv
   to one with `pip install -r requirements.txt`, and WSGI file to import
   `application` from `app.py` (`application = app` is already defined).
3. Expect *unknown* for every DNS-valid address — the SMTP stage is blocked
   there (see the table above).

## Running locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py                    # http://127.0.0.1:8000
.venv/bin/python -m pytest test_app.py     # unit + a few light integration tests
```

## Ethics & abuse notes

- **No message is ever sent** — the SMTP conversation always ends after
  `RCPT TO` (verifier method), well before `DATA`. Remote servers generally
  still see these as recipient-verification attempts, which many providers
  dislike; use the tool on lists you have a right to check, not to probe
  strangers.
- Don't point it at arbitrary domains in bulk: repeated probes from one IP
  can end up on DNSBLs, hurting everything else you send from that IP.
- The endpoint is intentionally rate-limited and batch-capped; if you face
  "company firewall" the per-domain throttle plus small batch size are your
  friends. All limits are env-tunable (see below).

## Configuration (env vars)

| Var | Default | Meaning |
| --- | --- | --- |
| `HELO_DOMAIN` | machine FQDN or `email-checker.local` | name announced in `EHLO`/`HELO`; use a domain you control |
| `MAIL_FROM` | `<>` (null sender) | envelope sender for the probe |
| `MAX_BATCH` | `50` | max addresses per request |
| `SMTP_MAX_WORKERS` | `8` | concurrent verification threads |
| `RATE_MAX_CALLS` / `RATE_WINDOW` | `6` / `300s` | per-client-IP verify-call allowance |

## Files

```
app.py              Flask routes UI + /api/verify + rate limiting
checker.py          syntax, DNS, SMTP probe, verdict classification
capability.py       runtime detection whether outbound SMTP is blocked
disposable.py       throwaway-domain list
templates/ static/  the single-page UI
test_app.py         pytest suite (no live SMTP in unit tests)
render.yaml         one-click Render blueprint
Dockerfile Procfile portable start commands
```

## Limitations

- Greylisted verdicts are never retried automatically (real verifiers wait
  5–15 min and try again); those stay *unknown*.
- SMTP codes vary by receiver; classification is deliberately conservative —
  when in doubt it reports *unknown* instead of guessing a bounce.
- No DNSSEC/SPF/DKIM-analysis — this is a deliverability probe, not a
  authentication audit.
- Quoted local parts (`"a b"@x.com`) and IP-literal domains are treated as
  invalid (pragmatic choice; note the tests).
- The rate limiter is in-memory per process; horizontal scaling needs a
  shared store.
