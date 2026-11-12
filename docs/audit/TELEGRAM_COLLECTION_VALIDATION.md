# Telegram Collection Validation (Step 3)

**Date:** 2026-11-11
**Branch:** `qa/system-integration-verification`
**Scope:** collect posts from registered public channels; verify duplicate prevention and checkpoint recovery.

---

## 1. Verdict

| Area | Result |
|---|---|
| Collector pipeline against live PostgreSQL | **28/28 checks passed** |
| Duplicate prevention (checkpoint filter) | **Verified** |
| Duplicate prevention (database constraint replay) | **Verified** |
| Checkpoint advance and recovery | **Verified** |
| Failure handling (checkpoint must not advance) | **Verified** |
| Backoff, skip, and recovery | **Verified** |
| Live Telegram message read | **BLOCKED — requires a user account session.** Telegram forbids history reads for bot accounts (`BotMethodInvalidError`), confirmed twice. |
| Bot session reuse for publishing | **Verified** — the authorized bot session is reused via `TELEGRAM_BOT_SESSION_PATH`. |

---

## 2. Account model (hard platform constraint)

Two live probes against `AFROSUIT_bot` confirmed the restriction:

| Probe | Result |
|---|---|
| `iter_messages` / `GetHistoryRequest` on a public channel | `BotMethodInvalidError: The API access for bot users is restricted. The method you tried to invoke cannot be executed as a bot (caused by GetHistoryRequest)` |
| `get_dialogs` (enumerate the bot's channels) | `BotMethodInvalidError` |

Telegram bots may **send** messages but may not **read** channel history or enumerate dialogs. The service therefore uses two separate accounts:

| Purpose | Account | Session path |
|---|---|---|
| Collect messages | User account (phone + login code + optional 2FA) | `TELEGRAM_SESSION_PATH` (`var/telegram_user`) |
| Publish messages | Bot token (already authorized as `AFROSUIT_bot`) | `TELEGRAM_BOT_SESSION_PATH` (`var/telegram_collector`) |

A Telethon session file is bound to one account identity, so the two paths deliberately use different files.

### To enable live collection

```bash
python -m app.integrations.telegram.login --as user
```

This needs the account phone number, the login code, and 2FA if enabled. It cannot be automated.

---

## 3. Pipeline verification against live PostgreSQL

`scripts/verify_collector_pipeline.py` exercises the real collector against the real database using the fake Telegram client (no network, no account needed):

| Check | Result |
|---|---|
| Run 1 collects 3 messages and ingests 3 opportunities | PASS |
| Checkpoint advances to the highest message id (103) | PASS |
| `last_success_at` recorded, `consecutive_failures` zero, backoff cleared | PASS |
| Database holds exactly 3 posts and 3 opportunities | PASS |
| Run 2 fetches nothing new because of the checkpoint | PASS |
| Run 2 creates no duplicate rows | PASS |
| Replay (checkpoint rewound to 100) counts all 3 messages as duplicates | PASS |
| Replay ingests nothing; the unique constraint prevents duplicate rows | PASS |
| Checkpoint recovers to the highest seen id (103) after a replay | PASS |
| Run 3 ingests only the genuinely new message and advances to 104 | PASS |
| A failed fetch is reported as failed | PASS |
| **The checkpoint does not advance on failure** | PASS |
| `consecutive_failures` incremented, backoff scheduled, error stored | PASS |
| A run inside the backoff window is skipped and makes no Telegram request | PASS |
| Recovery run succeeds, failure counter reset, `last_error` cleared | PASS |

**Result: 28/28 passed.**

The replay case is the important one: it proves the guarantee does not depend on the checkpoint alone. Even when Telegram re-delivers messages, the unique `(source_id, external_id)` constraint rejects them and the collector counts them as duplicates instead of inserting rows.

---

## 4. Live collection attempt (expected failure, actionable error)

With a `telegram` source registered and no user session, the collector fails cleanly — no stack trace, and the failure is recorded in source health:

```
$ python -m app.integrations.telegram.collector --source-id 17
[{"source_id": 17, "status": "failed", "fetched": 0, "ingested": 0, "duplicates": 0,
  "error": "TelegramNotConfigured: Telegram session is not authorized. Collection needs a
            user account session: run 'python -m app.integrations.telegram.login --as user'
            once, then keep the session file outside version control."}]
```

`consecutive_failures` was incremented and a backoff window scheduled, so the next scheduled run waits instead of hammering Telegram.

---

## 5. Operational constraint discovered: provider latency dominates collection

With NVIDIA NIM active, **each message costs roughly 105 seconds** of extraction (measured; see `NVIDIA_NIM_VERIFICATION.md`). The collector extracts synchronously per message, so:

| Messages in a backfill | Approximate time at ~105 s/message |
|---|---|
| 10 | ~18 minutes |
| 50 | ~88 minutes |
| 100 | ~3 hours |

Consequences:

1. `COLLECT_INTERVAL_SECONDS` (default 900) is shorter than a typical collection run. The next scheduled run is rejected by the job runner (`JobAlreadyRunning`) rather than duplicating work, so the outcome is safe but the cadence is unrealistic.
2. Bulk ingestion should either use a faster model, use the rules extractor for the bulk path and refine at review time, or collect on a much longer schedule.

The pipeline verification script defaults to the rules extractor (`--provider rules`) so it stays fast and deterministic; pass `--provider configured` to exercise the provider configured in `.env`.

---

## 6. Changes made

| File | Change |
|---|---|
| `scripts/verify_collector_pipeline.py` | New: 28-check verification of the collector pipeline against the live database. |
| `app/integrations/telegram/client.py` | Authorization error now names the exact `--as user` command. |
| `pyproject.toml` | Added `cryptg` so Telethon uses native AES instead of the slow pure-Python fallback. |
| `.env` (git-ignored) | `TELEGRAM_SESSION_PATH=var/telegram_user`, `TELEGRAM_BOT_SESSION_PATH=var/telegram_collector` so the user and bot sessions do not collide. |

---

## 7. Remaining work for full step 3

- [ ] Run `python -m app.integrations.telegram.login --as user` (requires phone + code).
- [ ] Register at least one `telegram` source with a public channel URL, with the bot as a member if publishing is also required.
- [ ] Run the collector against it and confirm `extraction_method`, checkpoint advance, and duplicate prevention on real messages.
- [ ] Decide the extraction strategy for bulk collection given the ~105 s/message provider latency.

---

## Cloudflare routing for local development

Telegram MTProto traffic can be routed through a SOCKS5 or HTTP proxy. This is the supported way to front Telegram with Cloudflare for local development, and it is applied identically to the collector client, the publisher, and the login command.

```env
TELEGRAM_PROXY_TYPE=socks5
TELEGRAM_PROXY_HOST=127.0.0.1
TELEGRAM_PROXY_PORT=40000
TELEGRAM_PROXY_RDNS=true
```

| Setup | Settings | Notes |
|---|---|---|
| Cloudflare WARP in proxy mode | `socks5` / `127.0.0.1` / `40000` | Simplest local option; changes the egress IP for Telegram. |
| Cloudflare Tunnel (`cloudflared`) in front of a SOCKS5 endpoint | `socks5` / `<tunnel host>` / `<tunnel port>` | Same settings. |

Enforcement: `tests/test_telegram_proxy.py` (6 checks) covers the settings mapping, an invalid proxy type, an incomplete configuration being ignored, and the proxy being forwarded to Telethon by both the collector client and the publisher.

**Proxy status at the time of this report:** the WARP proxy was not listening on the common ports (40000, 40001, 1080, 7890, 7897, 8118), so the live collection was performed over the direct connection, which works from this host.

**Note:** the sibling Yekis project's Telegram integration could not be inspected from this environment (the directory is outside the authorized project scope). The implementation above follows the standard Telethon proxy contract. If that project uses a different mechanism — for example a Cloudflare Worker relay, a custom MTProto transport, or a fixed proxy host — paste the relevant snippet and the code will be aligned to it.

---

## Live collection verified (2026-11-12)

With the user session authorized as `yona64`, a real collection ran end to end against the public channel `https://t.me/telegram`:

| Step | Result |
|---|---|
| Connect | `authorized: True`; entity resolved as `Telegram News @telegram` |
| Read history | 3 messages read from the channel |
| Collect (run 1) | `fetched=1 ingested=1 duplicates=0 last_id=3`; NVIDIA extraction returned `HTTP/1.1 200 OK` |
| Collect (run 2) | `fetched=1 ingested=1 last_id=5`; checkpoint advanced, no duplicate rows |
| Replay (checkpoint rewound to 0) | `fetched=2 ingested=0 duplicates=2 last_id=5`; database still held exactly **2 posts and 2 opportunities** |

**Live duplicate prevention is proven**: re-delivering already-ingested messages produced zero new rows.

### Two defects found and fixed by the live run

1. **Missing proxy transport.** With `TELEGRAM_PROXY_*` configured, Telethon failed with `ModuleNotFoundError: No module named 'socks'` — the `python-socks[asyncio]` dependency was absent, so a configured proxy could not be used at all. Added to `pyproject.toml` and installed.

2. **Empty messages consumed the fetch limit.** Media-only posts carry no text and are skipped, but they still counted against the requested limit, so `--limit 1` returned nothing when the oldest posts were empty. The client now requests a larger window and stops once it has the requested number of text messages. Covered by `tests/test_telegram_client_fetch.py` (4 checks).

Also hardened: an empty `TELEGRAM_PROXY_PORT` value no longer crashes the application at import (it is treated as "no port").

### Container networking

Inside a container, `127.0.0.1` is the container itself, so a host-local proxy must be reached as `host.docker.internal`. Compose now overrides the proxy host for `api`, `worker`, and `beat`:

```yaml
TELEGRAM_PROXY_HOST: ${TELEGRAM_PROXY_HOST_CONTAINER:-host.docker.internal}
```

| Context | Proxy host |
|---|---|
| Host CLI | `127.0.0.1` (from `.env`) |
| Containers | `host.docker.internal` (from Compose) |

### Enabling the proxy

Start Cloudflare WARP in proxy mode (SOCKS5 on `127.0.0.1:40000`), then recreate the containers so they inherit it:

```bash
docker compose up -d --force-recreate api worker beat
python -m app.integrations.telegram.collector --source-id SOURCE_ID --limit 5
```
