# CareCompass — Security Review

> **Scope:** This document covers the security posture of the CareCompass MVP at the
> commit it was reviewed on.  The application is designed for **local demonstration
> only**; it is not a production deployment.  Each accepted finding that requires action
> before any deployment is called out explicitly below.

---

## 1. Threat model

### 1.1 Assets

| Asset | Sensitivity | Where it lives |
|---|---|---|
| User's typed health concern | High — potentially sensitive health text | Streamlit `st.session_state` in RAM; `logs/audit.jsonl` only when `LOG_USER_TEXT=true` |
| Audit log | Medium — contains session IDs, tiers, step timings, and (by default) only hashes of user text | `logs/audit.jsonl` on local disk; never committed |
| Emergency / service-recommendation responses | High — wrong output could lead to harm | Generated in-process; grounded and checked before display |
| `WATSONX_API_KEY` | High | `.env` (never committed) |
| Hospital data (`hospitals_clean.csv`) | Public | `data/` directory; sourced from public Kaggle dataset |

### 1.2 Actors and threat vectors

| Actor | Vector | Notes |
|---|---|---|
| Casual user | Accidental off-topic input | Handled by the input gate |
| Attacker (local) | Prompt injection via health-concern text | Handled by input gate + off-topic patterns |
| Attacker (local) | Script / HTML injection via typed text | Handled by sanitiser + `html.escape` throughout the UI |
| Attacker (local) | Jailbreak ("ignore your rules") | Handled by off-topic patterns in the input gate |
| Attacker (local) | Flooding / denial of service | Handled by per-session rate limiter |
| Attacker (local) | Bad environment config (invalid DATA_SOURCE, bad EMERGENCY_NUMBER) | Handled by startup config validation |
| Deployment attacker | Cross-origin requests from malicious page | Handled by Streamlit's `enableCORS=true` (default) |
| Deployment attacker | Per-IP flooding | **Accepted risk F-10** — reverse proxy with per-IP limits needed for deployment |
| Supply chain | Vulnerable dependency | **Accepted risk F-16** — versions pinned; `pip-audit` required before deployment |

### 1.3 Out of scope for this MVP

- Multi-tenant isolation (only one user at a time on a local machine)
- Authentication / authorisation (no login)
- TLS / HTTPS (managed by a reverse proxy in any deployment)
- Database security (no database; all state is in-process RAM and local files)

---

## 2. Controls in place

### C-01 Input sanitiser (`src/sanitize.py`)

Every message passes through `sanitize_user_input()` before anything else:

1. Rejects non-string input and strings containing NUL bytes (`\x00`).
2. Unicode NFKC normalisation (collapses look-alike characters).
3. Strips all Unicode bidirectional override code points (U+202A–U+202E, U+2066–U+2069,
   U+200E, U+200F, U+061C) — prevents "Trojan Source"-style attacks.
4. Strips Unicode control characters (categories Cc, Cf); tabs, LF, and CR become spaces.
5. Collapses internal whitespace to single spaces.
6. Hard caps at `MAX_INPUT_CHARS = 1 000` characters before any regex runs, preventing
   catastrophic-backtracking attacks on the pattern matchers.
7. Rejects empty or whitespace-only results after the above steps.

### C-02 Input gate (`src/gate.py`)

After the safety check (step 3 in the pipeline), the gate classifies the cleaned message:

- **`offtopic`** — direct rule-override attempts (`ignore your instructions`), persona
  hijacking (`you are now a doctor`), jailbreak strings (`[INST]`, `do anything now`),
  and off-topic requests (code writing, cooking, sports, etc.). Checked first, before the
  health-vocabulary pass, so an attacker cannot smuggle a jailbreak string past the gate
  by appending a health word.
- **`smalltalk`** — greetings, thanks, social phrases.  A non-health-specific greeting.
- **`unclear`** — symbol-only input, single ambiguous words, short vague phrases.
- **`health`** — default; anything containing a health term from
  `data/health_terms.json`, a body-part-symptom pattern, or any multi-word input that
  doesn't match the above categories.  The gate always defaults to `"health"` when in
  doubt, so a genuine health concern is never silently blocked.

**Safety always runs before the gate.** If any red-flag rule fires (even on a string
that also matches an off-topic pattern), the emergency/urgent path takes precedence and
the gate is bypassed entirely.

#### Accepted gate behaviours

Two edge cases are accepted by design:

1. **Off-topic input that contains a health term.**  For example,
   `"write a poem about chest pain"` is classified `"health"` (not `"offtopic"`)
   because the gate checks health vocabulary before off-topic patterns (except for
   jailbreak-style strings, which are caught by the off-topic pass that runs first).
   This is intentional: the gate errs toward letting genuine health concerns through
   rather than blocking them.

2. **Prior messages combined with the next message.**  `app.py` concatenates all
   previous user turns with the new message and passes the combined string to
   `run_session()`.  This means the gate sees the concatenation, not just the latest
   message.  A prior off-topic message combined with a new health concern will classify
   as `"health"` and proceed.  This is the correct behaviour: once the user has sent a
   health concern, the prior context is relevant and should not be discarded.

### C-03 Rule-based safety path (`src/safety.py`)

`check_red_flags()` is purely deterministic: reads `data/red_flags.json` at module
import time (never at runtime from user input), applies substring phrase matching and
negation-aware term-group conjunction matching.  No LLM.  No network.  The emergency
exit in `run_session()` is structurally before any LLM or network call.

### C-04 Output escaping (`app.py`)

All data-derived values inserted into HTML strings are passed through `html.escape()`
before insertion.  This covers hospital name, address, city, state, ZIP, phone,
facility type, trauma level, bed count, and validation year.

User messages are displayed via `st.text()` (plain text, no markdown rendering) so that
typed markdown, links, or HTML tags are never rendered.

Assistant messages stored in the chat history are passed through `html.escape()` before
being rendered via `st.markdown()`.

`tel:` URLs are constructed by stripping all characters except `[0-9+\-() ]` from the
raw phone string before building the href.  Google Maps URLs use a fixed
`https://www.google.com/maps/search/` base with the query `urllib.parse.quote()`-encoded.

### C-05 Final check (`src/checks.py`)

Before any answer is displayed, `final_check()` runs 13 sub-checks:

- Every hospital ID, name, and phone cited in the answer must match
  `data/hospitals_clean.csv` (hospitals_us path) or `data/services.json` (demo path).
- The answer must contain the validation year.
- The answer must contain a "call ahead to confirm" line.
- The answer must not contain any phrase from `data/banned_phrases.json`
  (diagnosis language, treatment/dosage instructions, serious condition names).
- Urgent-tier answers must contain urgency-action language.

On failure the answer is revised and re-checked up to 2 times.  On persistent failure
`needs_human_review=True` is set and the human-review notice is shown instead of the
answer.

### C-06 Log redaction (`src/audit.py`)

By default (`LOG_USER_TEXT=false`):

- **No typed health text is ever written to the audit log.**  Free-text fields
  (`user_text`, `concern`, `who`, `area` in error steps) are replaced by
  `{"len": N, "hash": "xxxxxxxx"}` (first 8 hex chars of SHA-256).
- `user_text_preview` is dropped entirely.
- List-of-string fields (`severity_cues`, `denied_symptoms`) have each element hashed
  individually; the list length is preserved.
- Location value fields (`state`, `county`, `city`, `zip`) are replaced by a single
  `location_level` key recording only the finest level present (not the values).
- Error strings are truncated to the exception class / error-type token; the full
  message is dropped.
- Phone-number-like strings (exactly 10 or 11 digits with common separators) are
  replaced with `[REDACTED]` in all remaining string fields.
- Newlines are stripped from all logged strings (log-injection prevention).

When `LOG_USER_TEXT=true` (development/walkthrough only): raw text is retained, but
phone numbers are still redacted and newlines are still stripped.

### C-07 Config validation (`src/config.py`)

`validate_config()` is called once at Streamlit startup before `main()` runs.  It
raises `ConfigError` (displayed to the user; app stopped) if:

- `DATA_SOURCE` is not exactly `"hospitals_us"` or `"demo"`.
- `EMERGENCY_NUMBER` (if set) is not 3–6 ASCII digits.
- Any required data file is absent from `data/`.

Data file paths are fixed (`Path(__file__).parent.parent / "data" / "filename"`);
user input never influences which file is opened.

### C-08 Per-session rate limiter (`src/rate_limit.py`)

`RateLimiter` (stored in `st.session_state`) enforces:

- **10 messages per 60-second sliding window** per browser session.
- **100 messages per session lifetime.**

The check runs before the sanitiser and before any pipeline step.  When the limit is
exceeded the user sees a polite message and the pipeline does not run.

### C-09 Streamlit server hardening (`.streamlit/config.toml`)

| Setting | Value | Effect |
|---|---|---|
| `server.address` | `"localhost"` | Binds only to the loopback interface; not reachable from the network |
| `server.enableXsrfProtection` | `true` | Streamlit's built-in XSRF token protection enabled |
| `client.showErrorDetails` | `"none"` | No stack traces or internal paths in the browser UI |
| `browser.gatherUsageStats` | `false` | No telemetry sent to Streamlit |

`enableCORS` is left at Streamlit's **default `true`** (see F-18 below).

---

## 3. Security findings

### F-01 — Prompt injection via health-concern text

**Status: Fixed (C-02)**

The input gate runs off-topic pattern matching before health-vocabulary matching, so
classic prompt-injection strings (`ignore your instructions`, `[INST]`, `you are now a
doctor`, `do anything now`) are caught even when they contain health words.  Tests in
`TestPromptInjection` (`tests/test_safety_proof.py`) cover 8 injection variants.

### F-02 — Script/HTML injection in displayed output

**Status: Fixed (C-04)**

All data-derived values are passed through `html.escape()` before insertion into HTML
strings.  User messages are displayed via `st.text()`, not `st.markdown()`.  Assistant
messages in the chat history are escaped before rendering.

### F-03 — Phone number injection in `tel:` URLs

**Status: Fixed (C-04)**

`_safe_tel_url()` strips all characters except `[0-9+\-() ]` before building the href.
A crafted phone number in the hospital CSV cannot inject `javascript:` or arbitrary
scheme content.

### F-04 — Maps URL injection

**Status: Fixed (C-04)**

`_safe_maps_url()` uses a fixed `https://www.google.com/maps/search/` base and
`urllib.parse.quote()`-encodes the query string.  The scheme and authority are not
user-influenced.

### F-05 — Long inputs causing slow regex matching

**Status: Fixed (C-01)**

The 1 000-character hard cap in `sanitize_user_input()` runs before any regex pattern
matching, preventing catastrophic-backtracking attacks on `safety.py`, `gate.py`, and
`intake.py`.

### F-06 — NUL bytes and bidirectional override characters

**Status: Fixed (C-01)**

`sanitize_user_input()` rejects NUL bytes explicitly and strips all Unicode
bidirectional override code points before any further processing.

### F-07 — Internal stack traces visible to the user

**Status: Accepted (local demo only)**

`client.showErrorDetails = "none"` prevents Streamlit from showing stack traces in the
browser.  Unhandled exceptions in `_process_user_input` are caught, logged to the local
terminal (stderr) at `ERROR` level via Python's standard `logging`, and replaced with a
generic user-facing message.

*Accepted risk:* Stack traces go only to the local terminal.  **For any deployment,
`stderr` must not be exposed** — ensure it is redirected to an internal log sink only,
not forwarded to users or included in HTTP responses.

### F-08 — Secrets committed to version control

**Status: Fixed**

`.env` and `.streamlit/secrets.toml` are in `.gitignore`.  `.env.example` provides a
template with no real values.  No secret is hard-coded in the source files.

### F-09 — Audit log contains typed health text

**Status: Fixed (C-06)**

By default (`LOG_USER_TEXT=false`) the audit log contains no typed health text — only
lengths, hashes, and redacted phone numbers.  See §2 C-06 for the full list of what
is and is not logged.

### F-10 — Rate limiting is per-session only, not per-IP

**Status: Accepted (local demo only)**

The `RateLimiter` (C-08) operates on a per-browser-session basis (state stored in
`st.session_state`).  A single physical machine could open many Streamlit sessions and
bypass the per-session limit.

*Accepted risk:* The per-session limit is appropriate for a local demonstration where
only one operator is expected.  **For any deployment, add a reverse proxy (e.g. Nginx,
Caddy) with per-IP rate limiting** in front of the Streamlit server.

### F-11 — No authentication or authorisation

**Status: Accepted (no action)**

CareCompass is designed for unauthenticated local demo use.  Adding authentication is
out of scope for this MVP.  Any deployment that needs access control must add an
authentication layer at the reverse proxy or application level.

### F-12 — Location values not re-validated before pipeline

**Status: Fixed**

`_validate_location_values()` in `app.py` re-checks all non-None location values against
the loaded state/county/city lists before calling `run_session()`, defending against
session-state tampering or future widget changes that bypass the selectbox constraints.
ZIP codes are validated as exactly 5 ASCII digits.

### F-13 — Unresolved `{emergency_number}` placeholders reaching output

**Status: Fixed (C-07)**

`_load_messages()` replaces all placeholders at load time.  `verify_no_placeholders()`
raises `RuntimeError` if any unresolved placeholder pattern is found after replacement.
`validate_config()` checks `EMERGENCY_NUMBER` format at startup.

### F-14 — Data files loaded from user-controllable paths

**Status: Fixed (C-07)**

All data file paths are constructed from `Path(__file__).parent.parent / "data" / "filename"`.
No file path is ever derived from user input.

### F-15 — No TLS between browser and Streamlit server

**Status: Accepted (no action)**

`server.address = "localhost"` limits the server to loopback-only; no traffic leaves
the local machine in the default configuration.  **For any deployment, terminate TLS at
a reverse proxy.**

### F-16 — Dependencies may contain known vulnerabilities

**Status: Accepted (versions pinned; run `pip-audit` before deployment)**

All dependencies are pinned in `requirements.txt`.  The pinned versions were known-good
at the time of the review.  **Before any deployment, run `pip-audit` and update any
package with a known CVE.**

### F-17 — `headless = false` opens a browser window on startup

**Status: Accepted (local use by design)**

`server.headless = false` makes Streamlit open a browser tab on `streamlit run app.py`,
which is convenient for local development.  **For any deployment (container, server, CI)
set `server.headless = true`** to suppress the browser launch and remove the assumption
that a display is available.

### F-18 — Suggestion to disable CORS protection

**Status: NOT applied (F-18 is rejected)**

A suggestion was made during the review to set `enableCORS = false` in
`.streamlit/config.toml`.  **This suggestion is explicitly not applied.**
Streamlit's `enableCORS = true` is the protective default: it prevents cross-origin
requests from pages on other origins from interacting with the Streamlit WebSocket
endpoint.  Turning CORS protection off (`enableCORS = false`) would weaken protection
by allowing any origin to make cross-origin requests.  The current configuration leaves
CORS protection at its default enabled state.

---

## 4. Deployment checklist

Before exposing CareCompass to any user beyond the local developer, complete all of
the following:

1. **Reverse proxy** — place Nginx, Caddy, or a similar proxy in front of Streamlit.
   Configure per-IP rate limiting at the proxy level (see F-10).
2. **TLS** — terminate HTTPS at the reverse proxy.  Never expose the raw Streamlit port
   over the network (see F-15).
3. **`server.headless = true`** — set in `.streamlit/config.toml` or via the
   `STREAMLIT_SERVER_HEADLESS=true` environment variable (see F-17).
4. **`server.address`** — keep as `"localhost"` and let the reverse proxy forward to it,
   or change to `"0.0.0.0"` only when the reverse proxy is on a different host and the
   network is otherwise firewalled.
5. **`stderr` redirect** — ensure the process stderr is captured to an internal log sink
   only; do not expose stack traces to end users or HTTP responses (see F-07).
6. **`pip-audit`** — run against the deployed virtualenv and resolve all CVEs before
   going live (see F-16).
7. **`LOG_USER_TEXT`** — confirm this is `false` (the default) in the deployment
   environment.  Only set to `true` with explicit consent and appropriate access controls.
8. **Log retention** — enforce the 90-day maximum by scheduling
   `python scripts/clear_logs.py --delete` via cron or a task scheduler.
9. **Authentication** — add authentication at the reverse proxy or application level if
   the deployment is not restricted to a private network (see F-11).
10. **`WATSONX_API_KEY`** — store in a secrets manager; do not pass as a plain
    environment variable in shared environments.

---

## 5. References

| Document | Contents |
|---|---|
| [`docs/GOVERNANCE.md`](GOVERNANCE.md) | Safety guarantees, consent gate, red-flag rules, banned phrases |
| [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) | 8-step pipeline, component design, data flows |
| [`docs/TESTING.md`](TESTING.md) | Test suite reference including red-team test classes |
| [`src/sanitize.py`](../src/sanitize.py) | Input sanitiser implementation |
| [`src/gate.py`](../src/gate.py) | Input gate implementation |
| [`src/rate_limit.py`](../src/rate_limit.py) | Per-session rate limiter |
| [`src/audit.py`](../src/audit.py) | Audit log writer and redaction logic |
| [`src/config.py`](../src/config.py) | Startup configuration validation |
| [`.streamlit/config.toml`](../.streamlit/config.toml) | Streamlit server settings |
