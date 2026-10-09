# Kiro Crew validated findings: New findings surfaced during verification

Part of the validated findings set; start at [00-index.md](00-index.md). Code verified at commit `397f4be`; sources snapshot 2026-10-07 09:53:08 UTC.

## 8. New findings surfaced during verification

The verifiers found these while checking other claims. Each was seen by
one reviewer in the code at this commit, but none has been checked a second
time. Verify before scheduling.

### NEW-D1 Cron retry still replays a mid-turn death matched by message text

> **COMPLETED** — verified fixed on `main` by code read (no tests run): `src/kiro_crew/slack/gateway.py:5673` now gates the whole retry condition (both the typed `AcpProcessDied` arm and the `"not running"`/`"process exited"` substring arm) on `not _prompt_dispatched`. At base the substring arm had no such gate (base ~5620-5623). …
- **Finding:** The old substring arm still replays a mid-turn death whose text says 'Process exited during prompt' and ignores ambiguous_delivery, so tools can run twice (follow-up to LOOP-23).
- **Area:** Loops, cron, injected events, channels and app timers; found by the area-D verifier.

### NEW-D2 Cron parent with no dashboard tab pays an extra turn per inline-returned child
- **Finding:** mark-collected returns no_slot for a cron parent with no dashboard tab, so each child that spawn_sub_agents already returned inline costs one more turn plus a post (follow-up to EVT-9).
- **Area:** Loops, cron, injected events, channels and app timers; found by the area-D verifier.

### NEW-D3 A probe that raises never gets a blind alert
- **Finding:** Measured 0 blind alerts in 24 h for a probe that raises (only fetch_ok=False alerts) (follow-up to LOOP-7).
- **Area:** Loops, cron, injected events, channels and app timers; found by the area-D verifier.

### NEW-D4 Conductor wake turn carries no brief

> **COMPLETED** — verified fixed on `main` by code read (no tests run): `src/kiro_crew/autonudge_service/gate.py:1002-1008` now keeps `verdict.body` (`self._pending_monitor_wake_body[loop.id] = verdict.body`) so the gated wake turn carries the brief. `autonudge.py:817` comment describes the carried brief.   738ef77ec ("fix: carry …
- **Finding:** The gate discards verdict.body, so the wake turn carries neither the banner nor the item id (LOOP-17).
- **Area:** Loops, cron, injected events, channels and app timers; found by the area-D verifier.

### NEW-D5 A gated nudge is dropped when the dashboard slot is running
- **Finding:** slack/gateway.py:7262 drops a gated nudge that finds the slot running (relevant to LOOP-7/8/9).
- **Area:** Loops, cron, injected events, channels and app timers; found by the area-D verifier.

### NEW-D6 Doc errors found while verifying loops and events

> **COMPLETED** — the heartbeat, subagent and injected-messages doc fixes landed, and the Telegram forum wording in `messaging.md` matches the code predicate (supergroup + thread). The `sessions.py:4453` reference no longer appears in the repo. Closed with one residual: the `#2347` references in `learn-cron-dashboard.md:2353,2376` were left as they are, because the identity work's landing is unverified (issue closed upstream).
- **Finding:** heartbeat.md:5 says the interval is configurable (nothing configures it); subagent.md:2158/:2160 say Slack/cron parents get no model turn (the code runs one); sessions.py:4453 and subagent.md:2235 are stale on #2347; messaging.md:1669 misstates the Telegram forum activation fallback; injected-messages.md does not document the script-cron envelope.
- **Area:** Loops, cron, injected events, channels and app timers; found by the area-D verifier.

### NEW-A1 Shell audit log stores command output unredacted
- **Finding:** audit.log rotates at 8 MiB (shell_audit_log.py), but each execute_bash result is appended without redaction, so secrets printed by commands persist on disk (residual found while checking REL-11).
- **Area:** Security and reliability; found by the security verifier.
