# Kiro Crew: validated findings (merged)

This folder is the merged list of findings from four files, kept only where the
claim was verified against the code. It replaces them as the work order.

- **Sources merged:** `FIX_PLAN.md`, `REVIEW_FINDINGS.md`,
  `claude_verify_needed.md`, `claude_verification_needed.md`.
  Snapshot taken 2026-10-07 09:53:08 UTC. Anything added to them later is not covered.
- **Code verified:** commit `397f4be`. Line numbers are at that commit;
  re-locate by symbol if the code moves.
- **Claims checked:** 258 distinct claims, after merging duplicates
  across the four files.
- **How each was checked:** reading the code at that commit, and re-running
  the measurement scripts (or new ones) against throwaway homes. Earlier
  verdicts were treated as leads, not proof.
- **Second reader:** an independent reviewer re-checked 68 items: every
  not-confirmed claim, the top 20 by severity, 8 of 10 random confirmed items
  (CTX-17 and CTX-24 could not be re-read), and
  the outcomes of all security items. 8 were corrected; each item
  says so under "Second reader".
- **Not checked:** GitHub issue and PR states. The repo is not reachable
  without auth from this machine, so every upstream reference below says
  "state unverified".

**Reading this cheaply:** start with the ranked list below; its File column
says where each item lives. Each item is about 1k tokens; open just the ones you
need with `grep -n "^### <ID> \[" validated-findings/0*.md`.
The measurement scripts lived in a temporary scratchpad that is deleted when
this session ends, so "ran a script" means re-measure with a fresh one.

**Verdicts**

| Verdict | Count | Where |
|---|---|---|
| CONFIRMED | 133 | the ranked list below, and files 01–05 |
| PARTLY | 68 | the ranked list below, and files 01–05, in corrected form |
| NOT_CONFIRMED | 19 | 07-not-validated.md |
| CANT_VERIFY_HERE | 17 | 07-not-validated.md |
| DECISION | 21 | 07-not-validated.md |

**How to read an item**

- **Severity** is 1–100.
- **Scope:** `default` hits every install with active use; `armed` only when a
  feature, loop or job is set up; `pinned` only when the user pins a model or
  effort level.
- **Effort:** S is up to a day, M a few days, L a week or more.
- **IDs** keep the `FIX_PLAN.md` scheme. `08-id-crosswalk.md` maps every old ID
  from all four files to the ID used here.

## 1. Rules for whoever fixes these

- **Read the owning doc first.** AGENTS.md is the router. Read the doc its
  "Read before you touch" table names for the subsystem, and update that doc
  in the same commit.
- **Scope of a PR:** one logical change per commit, at most two commits per PR.
  Do not commit or push unless asked.
- **Models and harnesses:** never hardcode a model id; resolve it through
  `role_models` or `resolve_usable_model`. Gate on the harness positively
  (`is_kiro_backend`), never as "not Claude".
- **Sandbox:** its scope is the operator's to widen. Never add a seal, mask or
  regex spelling-chase that a task did not ask for; security outcomes below
  that touch scope are marked as decisions.
- **Changed defaults:** changing a default someone relies on (caps, cadences,
  activation) is a take-away change. Read
  `docs/system-specs/common/take-away-changes.md` and grep `docs/decisions/`
  first.
- **Tests** are deterministic: fake clocks, no sleeps, no real ports, no network.
- **What Kiro Crew controls:** on the default path kiro-cli makes the model
  call and replays history. Kiro Crew's levers are the text it injects, the
  agent specs and tools it exposes, MCP result size and format, session
  lifecycle, and loop cadence.
- **Upstream first:** many items cite upstream issues or PRs (state
  unverified). Check with `gh pr view <n>` before writing a second fix.

### Run these checks on a real install first

This review had no live install and no kiro-cli. Three facts could not be
settled here, and each one moves items up or down:

1. **The context window `model=auto` is served with (CTX-20).**
   - **How to check:** after one turn, read `provider.context_window_tokens()`,
     or the "used / size" figure in the context popover.
   - **If it is 1M:** CTX-1 rises to 75/default, and TOOL-2 and SPEC-1 go back
     to 65 and 55.
   - **If it is 200k:** CTX-1 stays at 45, TOOL-2 drops to about 40, and Tool
     Search already defers the conductor tools.
2. **Whether kiro-cli caps MCP tool results itself (TOOL-1).** Kiro Crew's own
   servers are cut at 100k chars; third-party servers are capped only if
   kiro-cli does it.
3. **Whether the provider caches a byte-identical prompt prefix across
   sessions.** This decides how much the cold-session items save (LOOP-1,
   LOOP-6, CTX-7) and how much CTX-4 is worth.

Then turn on per-surface usage (USE-1) and re-rank the `armed` items by what
the install actually spends.

## 2. Ranked list of validated items

Sorted by severity, then by scope (default before pinned before armed). This
is a ranking, not a schedule: check the dependencies in each item.

| # | ID | Title | Verdict | Sev | Scope | Effort | File |
|---|---|---|---|---|---|---|---|
| 1 | REL-12 | Outbox video cards stall the event loop and crash-loop the gateway | CONFIRMED | 75 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 2 | USE-1 | Usage recorded only on the background helpers and the cold workflow path | CONFIRMED | 70 | default | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 3 | SEC-1 | Egress allow-list bypass via backslash before @ | CONFIRMED | 70 | armed | M | [01-security-reliability.md](01-security-reliability.md) |
| 4 | LOOP-3 | Issue Radar crews take a full turn every 5 minutes with no end | CONFIRMED | 65 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 5 | SEC-2 | Scheme-only URLs (https:evil.com, http:/evil.com) skip the egress check | CONFIRMED | 65 | armed | S | [01-security-reliability.md](01-security-reliability.md) |
| 6 | TOOL-12 | session_ledger_read sends each event twice | CONFIRMED | 65 | armed | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 7 | EVT-1 | Queued automation events each drain as their own main-chat turn | CONFIRMED | 60 | default | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 8 | EVT-2 | Subagent completion carries the opening narration, not the answer | CONFIRMED | 60 | default | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 9 | TOOL-1 | Tool results: first-party cut at 100k chars with no spill; third-party servers uncapped | PARTLY | 60 | default | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 10 | LOOP-2 | Pipeline-conductor patrol: ungated turn after each 90 s idle; work-ledger watch won't fix | PARTLY | 60 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 11 | TOOL-8 | Computer-use actions return the full accessibility tree; screenshot is only a file path | PARTLY | 60 | armed | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 12 | SEC-5 | Standard sandbox tier leaves ~/.aws, ~/.ssh, ~/.kube readable to spawned shells | CONFIRMED | 55 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 13 | TOOL-2 | ~30k tokens of always-on tool definitions resent on every request | CONFIRMED | 55 | default | L | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 14 | TOOL-4 | spawn_status default: full transcript, cut at 100k chars, so the closing answer is lost | PARTLY | 55 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 15 | MOD-1 | Unpinned subagent, workflow, cron, task runs inherit chat model/effort; background doesn't | PARTLY | 55 | pinned | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 16 | LOOP-1 | HEARTBEAT.md tasks poll every 60 s forever with the full persona and all core schemas | CONFIRMED | 55 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 17 | LOOP-6 | LLM cron runs start cold with ~39K-char persona even on minimal context; duplicates paid | PARTLY | 55 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 18 | MSG-1 | Slack runs each queued message as its own full turn | CONFIRMED | 55 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 19 | USE-2 | Workflow budget_total is never enforced | CONFIRMED | 55 | armed | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 20 | ATT-1 | Channel attachments: no per-message inline total | CONFIRMED | 50 | default | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 21 | CTX-3 | Preferences block has no startup cap | CONFIRMED | 50 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 22 | CTX-7 | Default subagents and workflow steps pay the full main-agent first turn | CONFIRMED | 50 | default | M | [02-context-sessions.md](02-context-sessions.md) |
| 23 | OUT-1 | Empty-reply auto-continue fires after intentionally final tool calls | CONFIRMED | 50 | default | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 24 | TOOL-5 | get_chat_session has no per-message or total cap | CONFIRMED | 50 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 25 | EVT-4 | Script-cron reports wake the main chat with no dedupe or cap | CONFIRMED | 50 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 26 | LOOP-4 | Meetings: three agents each take a turn every 30 s on auto | CONFIRMED | 50 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 27 | MOD-2 | Knowledge extraction: agent.model at 'high', 20 prompts per chat; edits re-extract all | PARTLY | 50 | armed | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 28 | SEC-3 | Trailing-dot hostname bypasses egress deny list | CONFIRMED | 50 | armed | S | [01-security-reliability.md](01-security-reliability.md) |
| 29 | WF-1 | Schema re-ask re-runs the whole workflow step | CONFIRMED | 50 | armed | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 30 | ATT-2 | Large dashboard pastes go inline into the prompt | CONFIRMED | 45 | default | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 31 | EVT-3 | A spread-out subagent wave produces several parent turns | CONFIRMED | 45 | default | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 32 | OUT-2 | Subagent, workflow, task, cron turns get the hard diff rule; [OPTIONS:] only on choices | PARTLY | 45 | default | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 33 | REL-13 | Identity sweep spares live-account parents; others' running subagents are still killed | PARTLY | 45 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 34 | SEC-6 | Third-party env tokens not scrubbed from agent subprocesses | CONFIRMED | 45 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 35 | TOOL-13 | Model-facing JSON pretty-printed and ASCII-escaped at ~20 sites | CONFIRMED | 45 | default | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 36 | TOOL-3 | Tool descriptions are bloated | CONFIRMED | 45 | default | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 37 | TOOL-6 | Artifact edits rewrite the whole content | CONFIRMED | 45 | default | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 38 | CTX-1 | Compaction triggers only at 70% of the window; ~700k tokens only on sessions served at 1M | PARTLY | 45 | pinned | S | [02-context-sessions.md](02-context-sessions.md) |
| 39 | CTX-15 | Drained app context has no total cap (up to ~2M chars in one turn) | CONFIRMED | 45 | armed | S | [02-context-sessions.md](02-context-sessions.md) |
| 40 | LOOP-7 | A blind monitor probe fires a full turn on every tick | CONFIRMED | 45 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 41 | MSG-2 | Slack thread-follow never expires and the model cannot stay silent | CONFIRMED | 45 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 42 | SEC-12 | Cron vet leading-dot rule broken by bash dotglob | CONFIRMED | 45 | armed | M | [01-security-reliability.md](01-security-reliability.md) |
| 43 | SKL-1 | Skill triggers inject bodies on weak (one-word) matches | CONFIRMED | 45 | armed | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 44 | SPEC-1 | Conductor specs mount 117 tools whole | CONFIRMED | 45 | armed | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 45 | SPEC-2 | Custom and conductor agent prompts delivered twice per request | CONFIRMED | 45 | armed | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 46 | USE-3 | Auto-Improvement $ cap never trips on the Kiro backend | CONFIRMED | 45 | armed | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 47 | CTX-14 | Lessons are not re-injected after compaction | CONFIRMED | 40 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 48 | CTX-2 | Compaction can repeat with no attempt cap | CONFIRMED | 40 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 49 | CTX-8 | Whole 39 KB prompt.md reaches every session; diff rule is repeated but not contradictory | PARTLY | 40 | default | M | [02-context-sessions.md](02-context-sessions.md) |
| 50 | EVT-5 | wait ends only on user end or steer, not on pushed events; poll recipe is for external CI | PARTLY | 40 | default | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 51 | REL-15 | Opaque MCP tool read as WORKING is never cut off before the 4 h turn ceiling (by design) | PARTLY | 40 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 52 | SEC-11 | Lessons reach session-start context unreviewed; learn_add pre-approved beside web_fetch | PARTLY | 40 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 53 | SEC-18 | Batch redact() quadratic on long eyJ runs freezes the dashboard | CONFIRMED | 40 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 54 | TOOL-14 | memory_recall result is mostly retrieval diagnostics | CONFIRMED | 40 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 55 | CTX-6 | Slack thread fallback re-sends the buffered thread and duplicates the current message | CONFIRMED | 40 | armed | S | [02-context-sessions.md](02-context-sessions.md) |
| 56 | LOOP-15 | Work-ledger: staggered reports each buy a turn (no settle window, by design); bursts merge | PARTLY | 40 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 57 | LOOP-16 | Work-ledger wake can be lost while the conductor is mid-turn | CONFIRMED | 40 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 58 | MOD-6 | Dictation sends the whole growing transcript every segment | CONFIRMED | 40 | armed | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 59 | MSG-4 | Remote-crew relay never forwards tool approvals; the turn stalls until the 600 s timeout | PARTLY | 40 | armed | L | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 60 | SKL-2 | Kiro Crew dev skills lack repo_scope | CONFIRMED | 40 | armed | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 61 | SPEC-3 | kirocrew-worker is not a slim agent | CONFIRMED | 40 | armed | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 62 | TOOL-19 | Image paths in injected text are re-attached as images every nudge cycle | CONFIRMED | 40 | armed | S | [02-context-sessions.md](02-context-sessions.md) |
| 63 | TOOL-7 | workflow_result returns the full event stream uncapped | CONFIRMED | 40 | armed | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 64 | TOOL-9 | Replayed images accumulate with no per-conversation ledger | CONFIRMED | 40 | armed | M | [02-context-sessions.md](02-context-sessions.md) |
| 65 | WF-2 | No cap on step output passed between workflow steps | CONFIRMED | 40 | armed | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 66 | WF-3 | Workflow call cap of 1000; dependency retries re-run whole steps | CONFIRMED | 40 | armed | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 67 | CTX-5 | Per-turn reminders re-sent identically every follow-up turn | CONFIRMED | 35 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 68 | EVT-6 | A separate synthesis turn follows every completion batch | CONFIRMED | 35 | default | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 69 | MOD-4 | Rejected-model fallback picks the first listed model regardless of cost | CONFIRMED | 35 | default | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 70 | REL-18 | Session RSS ceiling never fires on macOS | CONFIRMED | 35 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 71 | REL-22 | Mount-source janitor waits for a new session; idle or tmpfs-full hosts never sweep | PARTLY | 35 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 72 | REL-26 | File-change snapshots retained in gateway memory per turn | CONFIRMED | 35 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 73 | REL-47 | Snapshot build/restore, import and cron jobs stage in system temp; memory backups do not | PARTLY | 35 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 74 | SEC-13 | Streaming redaction holdback too short for long key-anchored values | CONFIRMED | 35 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 75 | SEC-7 | SEL audit chain forgeable by in-sandbox code | CONFIRMED | 35 | default | L | [01-security-reliability.md](01-security-reliability.md) |
| 76 | SES-1 | One config save recycles every session including in-flight turns | CONFIRMED | 35 | default | M | [02-context-sessions.md](02-context-sessions.md) |
| 77 | SES-5 | A throttle refusal on the empty continue turn fails a long subagent run | CONFIRMED | 35 | default | M | [02-context-sessions.md](02-context-sessions.md) |
| 78 | SKL-4 | Several SKILL.md files exceed 32 KiB and prompts force whole reads | CONFIRMED | 35 | default | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 79 | TOOL-15 | List renderers repeat the same instruction on every row | CONFIRMED | 35 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 80 | TOOL-20 | repeat_loop catches only identical calls; no per-turn tool-call ceiling | CONFIRMED | 35 | default | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 81 | UI-1 | Link-label resolution fires on mount even when the Links tab is hidden, uncached | CONFIRMED | 35 | default | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 82 | LOOP-19 | Conductor bind does not arm a patrol | CONFIRMED | 35 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 83 | LOOP-21 | Wake judge fires every interval on a sustained run of partial readings | CONFIRMED | 35 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 84 | LOOP-26 | Disabling Auto-Improvement stops its run but not its PR watchers (up to 4, 6 passes each) | PARTLY | 35 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 85 | LOOP-5 | Opt-in meeting translation makes one small lite call per line, unbatched, no usage row | PARTLY | 35 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 86 | LOOP-8 | Unbounded nudge loops from the goal popover and ctx.nudge | CONFIRMED | 35 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 87 | REL-16 | Opt-in pooled kirocrew-core runs one tool call at a time; ping/initialize skip the queue | PARTLY | 35 | armed | M | [01-security-reliability.md](01-security-reliability.md) |
| 88 | REL-32 | Pooled backends orphaned across gatewayd death | CONFIRMED | 35 | armed | S | [01-security-reliability.md](01-security-reliability.md) |
| 89 | SEC-9 | No MCP tool-schema pinning after 'Trust this tool' | CONFIRMED | 35 | armed | M | [01-security-reliability.md](01-security-reliability.md) |
| 90 | SKL-6 | Triggered global skills have no byte cap; capped confined skills re-inject on every match | PARTLY | 35 | armed | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 91 | CTX-21 | Auto-compaction waits for turn end and overshoots the threshold | CONFIRMED | 30 | default | M | [02-context-sessions.md](02-context-sessions.md) |
| 92 | CTX-31 | Conversation rows uncapped in the non-native replay path | CONFIRMED | 30 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 93 | CTX-4 | Volatile values early in the prompt break the cross-session shared prefix | CONFIRMED | 30 | default | M | [02-context-sessions.md](02-context-sessions.md) |
| 94 | LOOP-10 | Welcome suggestions regenerate each 30 min while open, even if unchanged; background model | PARTLY | 30 | default | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 95 | OUT-3 | Checklist recovery block orders one call per row | CONFIRMED | 30 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 96 | REL-14 | taskq_fail announces refusal before its store write lands | CONFIRMED | 30 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 97 | REL-24 | Cron-store saves are not crash-durable | CONFIRMED | 30 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 98 | REL-33 | Closed-session crew logs expire at 30 days; team and live-session logs grow unbounded | PARTLY | 30 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 99 | REL-40 | Transport prompt timeout floor ignores a lower configured ceiling | CONFIRMED | 30 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 100 | REL-45 | augmented_path() re-prepends existing PATH dirs | CONFIRMED | 30 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 101 | SEC-16 | YOLO stays on after Trust/Reads; config-set YOLO re-arms on restart (both deliberate) | PARTLY | 30 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 102 | SEC-8 | Shell gate cannot see encoded multi-stage execution | CONFIRMED | 30 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 103 | SES-11 | Non-dashboard turns lack lost-backend-session recovery | CONFIRMED | 30 | default | M | [02-context-sessions.md](02-context-sessions.md) |
| 104 | SES-2 | Retry families stack with no per-message cap | CONFIRMED | 30 | default | M | [02-context-sessions.md](02-context-sessions.md) |
| 105 | MOD-3 | Literal model="auto" overrides a pinned background model | CONFIRMED | 30 | pinned | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 106 | CTX-12 | Non-dashboard turn loops never arm reinjection after a backend compaction | CONFIRMED | 30 | armed | S | [02-context-sessions.md](02-context-sessions.md) |
| 107 | CTX-28 | learn_list reports no lessons for a V1 named store after restart | CONFIRMED | 30 | armed | S | [02-context-sessions.md](02-context-sessions.md) |
| 108 | EVT-8 | Workflow/spawn texts invite polling pushed results; peer-session results are not pushed | PARTLY | 30 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 109 | LOOP-17 | Work-ledger wake turn has neither banner nor item id, only the standing loop message | PARTLY | 30 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 110 | LOOP-18 | Conductor 20-items-per-goal cap is prompt-only; code caps 32 open / 256 stored items | PARTLY | 30 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 111 | LOOP-27 | Auto-Improvement disable race restarts the driver | CONFIRMED | 30 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 112 | REL-37 | Broker-stub MCP bridge retries ~10 min; then that server's tools are gone until restart | PARTLY | 30 | armed | M | [01-security-reliability.md](01-security-reliability.md) |
| 113 | SEC-22 | Guest (non-operator) agent receives operator's global steering | CONFIRMED | 30 | armed | S | [01-security-reliability.md](01-security-reliability.md) |
| 114 | SEC-4 | meets_min_version: '0.3.0-rc.1' passes a 0.3.0 floor; PEP 440 '0.3.0rc1' fails every floor | PARTLY | 30 | armed | S | [01-security-reliability.md](01-security-reliability.md) |
| 115 | SKL-3 | Skill-body dedup resets on native resume | CONFIRMED | 30 | armed | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 116 | SPEC-7 | App specs ship a `skills` key kiro-cli may reject | CONFIRMED | 30 | armed | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 117 | TOOL-10 | Browser snapshot silently cut at 2000 chars | CONFIRMED | 30 | armed | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 118 | TOOL-16 | workflow_result events repeat run_id, timestamps and summaries | CONFIRMED | 30 | armed | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 119 | UI-2 | Mochi keeps planning while the pet is hidden | CONFIRMED | 30 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 120 | UI-5 | Mochi QueuePoller retries a refused freestyle-task spawn every 1 s, no backoff (~400/min) | PARTLY | 30 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 121 | WF-4 | Workflow completion turn and edited reruns cost full turns/runs | CONFIRMED | 30 | armed | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 122 | REL-46 | _apply_recent_session has no rollback: a bad title/tab_id aborts the restore loop | PARTLY | 30 | default (needs one pinned or foldered transcript with a malformed field; metadata lines are agent-writable) | S | [01-security-reliability.md](01-security-reliability.md) |
| 123 | CTX-9 | Protected-content ceiling scales with the 1M auto/unknown window | CONFIRMED | 25 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 124 | EVT-11 | Messages queued before a subagent is flagged stalled wait for the next send | CONFIRMED | 25 | default | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 125 | OUT-4 | Prompt's routine resource_status pre-check is redundant only on full-context sessions | PARTLY | 25 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 126 | REL-10 | Oversized frontend files | CONFIRMED | 25 | default | L | [01-security-reliability.md](01-security-reliability.md) |
| 127 | REL-19 | All seven mcp-* servers import numpy; per-core OpenBLAS cost is Linux-only, unmeasured | PARTLY | 25 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 128 | REL-20 | Dashboard on_tool_call blocks the loop on path resolution (2 s per target, 12 s/25 s max) | PARTLY | 25 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 129 | SEC-19 | redact() now keeps {token} and <token>; still redacts $TOKEN, %s and any 200+ char query | PARTLY | 25 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 130 | SEC-20 | Control-split credential/exfil tokens not redacted | CONFIRMED | 25 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 131 | SES-10 | Effort/env lost on a compaction restart | CONFIRMED | 25 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 132 | SES-9 | Dashboard turns fail during short network drops | CONFIRMED | 25 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 133 | SKL-7 | $skill expansion can stack five 99K bodies in one turn | CONFIRMED | 25 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 134 | SPEC-4 | Background agents load the user's global steering files; workspace globs usually find none | PARTLY | 25 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 135 | TOOL-11 | kiro_cli_logs returns up to 80k chars | CONFIRMED | 25 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 136 | TOOL-17 | Repeated policy denials sent in full | CONFIRMED | 25 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 137 | TOOL-21 | Repeat-loop steering is best-effort only | CONFIRMED | 25 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 138 | USE-7 | Crew logs injected block sizes and occupancy but never derives the replay or image share | PARTLY | 25 | default | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 139 | MOD-7 | Switching to 'auto' keeps the spec model pin only for frozen or sidecar-less main agents | PARTLY | 25 | pinned | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 140 | KNOW-1 | Knowledge agent sync has the model fetch and echo the page | CONFIRMED | 25 | armed | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 141 | LOOP-20 | Pipeline conductor's 960-cycle cap ends patrol before 72 h when turns average under 180 s | PARTLY | 25 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 142 | LOOP-24 | Cron auto-pause threshold not configurable; expired-credential failures count toward it | CONFIRMED | 25 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 143 | LOOP-32 | Cron misses a boundary the host slept through | CONFIRMED | 25 | armed | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 144 | LOOP-9 | Nudge judge: fresh tool-less lite session per judged tick, background model, no usage row | PARTLY | 25 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 145 | REL-21 | Claude Code executable resolution runs blocking `mise which` on the event loop | CONFIRMED | 25 | armed | S | [01-security-reliability.md](01-security-reliability.md) |
| 146 | SPEC-5 | pptx composer preloads 50 KB of docs as resources | CONFIRMED | 25 | armed | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 147 | CTX-10 | Post-compaction reinjection re-sends the full agent prompt and pinned skill bodies | CONFIRMED | 20 | default | M | [02-context-sessions.md](02-context-sessions.md) |
| 148 | CTX-11 | Memory dict/list values escape non-ASCII via json.dumps (4.3x for CJK); strings fine | PARTLY | 20 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 149 | CTX-25 | /compact as the first message of a new chat waits 300 s | CONFIRMED | 20 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 150 | LOOP-12 | Idle history consolidation fires on a single new prompt row | CONFIRMED | 20 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 151 | REL-27 | Transcripts rotate at 10 MB on append; dashboard whole-file save bounded only by 10k msgs | PARTLY | 20 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 152 | REL-30 | commit_runtime_teardown refusal leaves no kill-owed debt; owner kills can leak the runtime | PARTLY | 20 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 153 | REL-34 | Judge, task-refine and hook-run work folders never reclaimed; eval folders are cleaned | PARTLY | 20 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 154 | REL-42 | Backend god-files exceed review scale; orchestration lives in prose | CONFIRMED | 20 | default | L | [01-security-reliability.md](01-security-reliability.md) |
| 155 | REL-44 | importlib.reload replaces shutdown_event | CONFIRMED | 20 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 156 | REL-7 | File watch never reconnects after a connection drop | CONFIRMED | 20 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 157 | SEC-10 | Output redaction is shape-based; hex/rot13/chunked emission evades it | CONFIRMED | 20 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 158 | SEC-15 | MCP identity now per session via signed tokens; tokenless stub connections still per PID | PARTLY | 20 | default | M | [01-security-reliability.md](01-security-reliability.md) |
| 159 | SEC-21 | No structural pin that every tool-dispatch route funnels through tools._dispatch | CONFIRMED | 20 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 160 | SES-12 | Failed /compact recycle keeps only the replay excerpt, no summary; UI labels the restart | PARTLY | 20 | default | M | [02-context-sessions.md](02-context-sessions.md) |
| 161 | SES-4 | kirocrew-lite spec does not set includeMcpJson: false | CONFIRMED | 20 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 162 | SKL-5 | $skill expansion bypasses the per-session dedup | CONFIRMED | 20 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 163 | USE-8 | Background maintenance usage rows written with an empty model | CONFIRMED | 20 | default | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 164 | CTX-17 | Changed member essentials envelope re-sends whole | CONFIRMED | 20 | armed | M | [02-context-sessions.md](02-context-sessions.md) |
| 165 | CTX-30 | Pending-context drain evicts entries over the ceiling | CONFIRMED | 20 | armed | S | [02-context-sessions.md](02-context-sessions.md) |
| 166 | LOOP-13 | Auto-research has no judge but is bounded (cycles, stagnation, 24 h); fan-out is opt-in | PARTLY | 20 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 167 | LOOP-25 | One unanswered approval stops a goal loop on its next wake (deliberate); it can be resumed | PARTLY | 20 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 168 | SES-14 | Webhook runs don't report MCP servers that failed to start; same servers as chat | PARTLY | 20 | armed | S | [02-context-sessions.md](02-context-sessions.md) |
| 169 | USE-11 | Credit popover can show the wrong balance only for no-ARN accounts with another IDE login | PARTLY | 20 | armed | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 170 | WF-6 | Task-runner loop check compares raw error text, so notice/label miss; retries are capped | PARTLY | 20 | armed | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 171 | REL-3 | Notifications history rewritten non-atomically | CONFIRMED | 18 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 172 | CTX-18 | Unknown model ids get 1M caps on the first turn only, unwarned (deliberate fallback) | PARTLY | 15 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 173 | CTX-24 | Preference-only consolidation advances _prefs_offset on an empty answer | CONFIRMED | 15 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 174 | EVT-12 | Memory-queued spawns are not woken on events | CONFIRMED | 15 | default | M | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 175 | MOD-11 | Model picker may offer an unentitled model on a cold gateway (deliberate); hiding is fixed | PARTLY | 15 | default | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 176 | OUT-6 | Prompt's recall-miss path costs 2 tool calls (memory_recall, search_chat_history), not 3 | PARTLY | 15 | default | M | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 177 | REL-43 | build_message: 36 params, 932-line body; rules-gate chokepoint is a tested function | PARTLY | 15 | default | L | [01-security-reliability.md](01-security-reliability.md) |
| 178 | REL-50 | session/new gate is host-sized ('auto'); outer cold-start _start_sem is still fixed at 4+1 | PARTLY | 15 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 179 | SEC-14 | Cap-straddle token leak is in api_file_read (cuts, then redacts); office_preview is fixed | PARTLY | 15 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 180 | TOOL-18 | Non-JSON HTTP error bodies pass through uncapped | CONFIRMED | 15 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 181 | ATT-4 | Slack voice-memo transcripts join turn text uncapped; bound is 1 h per memo, none on AWS | PARTLY | 15 | armed | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 182 | CTX-16 | Operator prompt.md override has no size budget or warning below the 50 MiB read guard | PARTLY | 15 | armed | S | [02-context-sessions.md](02-context-sessions.md) |
| 183 | LOOP-11 | Channel auto-title has no attempt limit | CONFIRMED | 15 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 184 | LOOP-14 | Dynamic cards regenerate for every restored session on restart | CONFIRMED | 15 | armed | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 185 | LOOP-22 | Monitoring supplemental retry burns budget on permanent failures | CONFIRMED | 15 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 186 | REL-36 | mcp-gateway SpawnGate waiters not priority-ordered | CONFIRMED | 15 | armed | S | [01-security-reliability.md](01-security-reliability.md) |
| 187 | REL-8 | Research lab page falls back to polling forever after one SSE error | CONFIRMED | 15 | armed | S | [01-security-reliability.md](01-security-reliability.md) |
| 188 | SEC-17 | sandbox-escape-ssh-self: denies are permanent; own-address set only grows until restart | PARTLY | 15 | armed | M | [01-security-reliability.md](01-security-reliability.md) |
| 189 | SPEC-6 | App agent templates omit includeMcpJson: false | CONFIRMED | 15 | armed | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 190 | UI-3 | Dynamic cards generate with no viewer | CONFIRMED | 15 | armed | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 191 | USE-4 | API-key auth skips the account credit/limit readout by design; per-turn usage unverified | PARTLY | 15 | armed | M | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 192 | WF-5 | opts.effort (and nudge) ignored by shipped agent_fn | CONFIRMED | 15 | armed | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 193 | REL-4 | Notification trim failures swallowed silently | CONFIRMED | 12 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 194 | LOOP-28 | Opt-in Jev client: option criteria sent as null, one 1 s call, no retry (both deliberate) | PARTLY | 12 | armed | S | [04-loops-events-channels.md](04-loops-events-channels.md) |
| 195 | CTX-32 | context-management.md has no Crew-vs-harness token ownership section; only fragments | PARTLY | 10 | default | S | [02-context-sessions.md](02-context-sessions.md) |
| 196 | MOD-10 | Background one-liners share one runtime; only its spawn and session/new start serialize | PARTLY | 10 | default | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 197 | REL-6 | AppIcon leaves SVGs with an XML prolog or leading comment blank; no shipped icon has one | PARTLY | 10 | default | S | [01-security-reliability.md](01-security-reliability.md) |
| 198 | TOOL-23 | mcp_shared tools/call ignores keys outside 'arguments'; only non-conforming clients hit it | PARTLY | 10 | default | S | [03-tools-specs-skills.md](03-tools-specs-skills.md) |
| 199 | UI-4 | Tips run maybe_refresh before the cadence check | CONFIRMED | 10 | default | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 200 | USE-9 | Applied credit multiplier not in usage telemetry | CONFIRMED | 10 | default | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |
| 201 | UI-6 | Chat side panel polls /api/workflows/runs every 2.5 s while open, any tab or run state | PARTLY | 8 | default | S | [05-routing-usage-ui.md](05-routing-usage-ui.md) |


## Files in this set

- [01-security-reliability.md](01-security-reliability.md): Security and reliability items (validated).
- [02-context-sessions.md](02-context-sessions.md): Context window, compaction, memory and sessions (validated).
- [03-tools-specs-skills.md](03-tools-specs-skills.md): Tools, agent specs, skills, attachments and prompt rules (validated).
- [04-loops-events-channels.md](04-loops-events-channels.md): Loops, cron, injected events, channels and app timers (validated).
- [05-routing-usage-ui.md](05-routing-usage-ui.md): Model routing, usage, budgets, workflows, knowledge and UI (validated).
- [06-new-findings.md](06-new-findings.md): Defects the verifiers found along the way (one reviewer each; verify first).
- [07-not-validated.md](07-not-validated.md): Not confirmed, can't verify here, decisions needed, and anything unverified.
- [08-id-crosswalk.md](08-id-crosswalk.md): Every old ID from the four source files, mapped to the ID used here.
