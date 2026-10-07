# Kiro Crew validated findings: Model routing, usage, budgets, workflows, knowledge and UI

Part of the validated findings set; start at [00-index.md](00-index.md). Code verified at commit `397f4be`; sources snapshot 2026-10-07 09:53:08 UTC.

## 7. Model routing, usage, budgets, workflows, knowledge and UI

### USE-1 [70, default, effort M] Usage recorded only on the background helpers and the cold workflow path — CONFIRMED
- **Verified claim:** Every listed path makes model calls without writing a usage row. stream_and_collect itself records nothing; of its 15 call sites, rows are written only by the cold workflow path (on success), the heartbeat and Slack nudge paths, and cron (by its caller). No row is written by: pooled workflow steps (agent_pool._run_step, the default because pool_agents=True), a cold workflow step that raises or times out (the row is written after stream_and_collect returns, not in a finally), the nudge judge (decisions/impl_llm.py), meetings translation and meeting agent turns, workflow authoring (service.author), side chat, chat-thread replies, Issue Radar AI summaries, Code Review Sage reviews (runtime.create_session + handle.prompt, no usage code in the app), knowledge extraction and knowledge agent fetch (LLMPool workers). Newly found by a second sweep of direct provider.stream()/stream_and_collect_json loops (scripts/E/direct_stream_sweep.py): interactive CHANNEL turns — Slack messages (slack/handler.handle_message) and Discord/Telegram turns through messaging/dispatch → TurnDriver.run — write no usage row and no crew-log turn-close (only the dashboard chat runner emits on_turn_completed); also task-runner decomposition (task_planner.decompose), task-runner lesson extraction (taskrunner._call_llm_for_lesson), task-runner refine (dashboard/handlers/taskrunner._run_refine), the prompt optimizer (dashboard/handlers/optimizer._optimize), channel.py _stream_task, the CLI chat, and native /compact turns (session_handle.compact). And the Slack gateway's subagent-completion injection into the parent session (_inject_with_retry, used for Slack threads and cron parents) records nothing either. Correction to the framing: 'only run_bg_oneliner/background_turn and the cold workflow path' is too strong — the dashboard chat runner, cron, heartbeat, monitor, subagent runs, task-runner steps and webhooks also write rows; the gap is the callers above.
- **Evidence (at `397f4be`):**
  - scripts/E/usage_sites.out — 15 stream_and_collect sites; 12 with no usage writer in the enclosing function; ai.py:235, meetings session.py:230, translate.py:116, chat_threads.py:963, handlers/side.py:622, decisions/impl_llm.py:211, workflows/agent_pool.py:90, workflows/service.py:1002 have no usage…
  - src/kiro_crew/llm_helpers.py:1800-1845 (run_bg_oneliner) and :2316-2340 (background_turn) — the only helper-level persist_token_record_async calls; stream_and_collect (:2354) has none
  - src/kiro_crew/workflows/agent_pool.py:76-98 — _run_step: stream_and_collect + redact, no row
  - src/kiro_crew/workflows/service.py:211 — pool_agents: bool = True (pooled is the default path)
  - src/kiro_crew/workflows/agent_exec.py:203-259 — persist_token_record_async runs after `text = await stream_and_collect(...)`; an exception skips it
  - src/kiro_crew/apps/builtins/code_review_sage/sage_lib/review_pool.py:613-640 — create_session + handle.prompt; grep 'persist_token_record|usage' over the app: no match
  - src/kiro_crew/knowledge/llm_pool.py:1285-1296 send → worker.send_message; knowledge/agent_fetch.py:55 pool.send; no usage code in knowledge/
  - src/kiro_crew/slack/gateway.py:8971-8984 — _inject_with_retry: stream_and_collect(client, msg) with no row; callers :9936, :10156
  - …and 6 more in the verification results.
- **Checked by:** ran-new-script ; ran-new-script
- **Required outcome:** Every model call writes exactly one usage row with a surface label and the served model — on success, on exception and on cancellation — so the usage dashboard shows all spend.
- **Solution:**
  1. Add a keyword `usage_surface: str` (and optional `usage_session_key`) to stream_and_collect (llm_helpers.py:2354); persist one row in a finally using provider_last_turn_usage(provider, since=stats_before) and usage_has_billing, exactly as run_bg_oneliner does at :1800-1845 (served model, model_source=provider).
  2. Make run_bg_oneliner/background_turn pass their label and drop their own persist blocks so nothing double-counts; remove the cold-workflow block at agent_exec.py:221-259 in favour of the label.
  3. Pass a label at every call site: workflows/agent_pool.py:90 ('workflow'), service.py:1002 ('workflow_author'), decisions/impl_llm.py:211 ('nudge_judge'), meetings translate.py:116/session.py:230, handlers/side.py:622, chat_threads.py:963, issue_radar ai.py:235, slack/gateway.py:8984 ('subagent_completion'); keep the existing rows of cron/heartbeat/monitor by either passing the label or opting out explicitly.
  4. For non-stream_and_collect paths add the same finally-persist (one shared helper, e.g. record_turn_usage(provider, surface, since)): messaging/driver.TurnDriver.run (covers Discord/Telegram/Slack-transport turns; keep the gateway monitor's existing row by passing its surface instead of double-writing), slack/handler.handle_message, task_planner.decompose, taskrunner._call_llm_for_lesson, dashboard/handlers/taskrunner._run_refine, dashboard/handlers/optimizer._optimize, channel._stream_task, Code Review Sage review_pool run_task, knowledge LLMPool AcpWorker.send_message, session_handle.compact.
  5. Add an AST test (the shape of scripts/E/direct_stream_sweep.py): every stream_and_collect(/stream_and_collect_json( call and every `async for … in provider.stream(` loop in src/kiro_crew either passes usage_surface= or sits in a function listed as writing its own row.
  6. Update the usage / learn-cron-dashboard spec in the same commit.
- **Done when:** pytest with a fake provider whose last-turn usage reports 10 credits: stream_and_collect(..., usage_surface='x') writes one row on success, one when the fake raises mid-stream, and one when the task is cancelled (tmp KIROCREW_HOME usage shard has 3 lines); pooled workflow _run_step, side chat, chat thread, nudge judge and the Slack completion injection each produce one row under their surface; the AST call-site test passes. A Slack message turn and a Telegram turn driven through fakes each write one row with surface slack/telegram.
- **Upstream:** #12225 #11214 #17112 #8945 PR #12234 PR #17663 PR #17659 PR #16123 PR #16023 PR #17159 PR #17556 PR #16886 PR #17482 (state unverified)
- **Changed from the source claim:** All listed gaps confirmed; a second sweep found more unmetered paths, the largest being interactive channel turns (Slack handle_message, Discord/Telegram via TurnDriver), plus task-runner decompose/lesson/refine, the prompt optimizer, channel.py, CLI chat, native /compact and the Slack gateway subagent-completion injection. Severity raised 65→70 for the channel turns. The 'only the two helpers and the cold workflow path' framing is too strong: chat runner, cron, heartbeat, monitor, subagents, task-runner steps and webhooks do write rows.
- **Second reader:** top-20 check: agreed.
- **Sources:** FIX_PLAN:USE-1, REVIEW_FINDINGS:W2, REVIEW_FINDINGS:W3, REVIEW_FINDINGS:B2, REVIEW_FINDINGS:N9, REVIEW_FINDINGS:N10, REVIEW_FINDINGS:N-lower(issue-radar-ai), FIX_PLAN-old:T1, FIX_PLAN-old:A-8, verify_needed:A3, verify_needed:G15(#17112), verification_needed:Part3(#17112), verify_needed:G37

### MOD-1 [55, pinned, effort M] Unpinned subagent, workflow, cron, task runs inherit chat model/effort; background doesn't — PARTLY
- **Original claim:** Unattended work inherits the chat model and effort (corrected below)
- **Verified claim:** Unpinned SUBAGENT, workflow-step, cron and task-runner sessions inherit the chat model (agent.model) and the chat effort (agent.reasoning_effort) through the provider factory's last precedence tier; the BACKGROUND role does not (resolve_model('background') returns the role pin or 'auto', and background worker agents take role_efforts.background or the provider default). The docs disagree with each other and with the code: model-selection.md says roles 'deliberately do NOT inherit agent.model', while the role_models help text and the ROLE_MODEL_KEYS comment say an unpinned role 'defers to the chat default (agent.model)' — the latter is what the subagent path does. run.py records requested_model='auto' for an unpinned spawn even when the factory will request the pinned agent.model. Default install (agent.model='auto', reasoning_effort='') is unaffected.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/config/sections.py:270 — ROLE_MODEL_KEYS = ("background", "subagent")
  - src/kiro_crew/config/sections.py:263-269 — comment: every role defaults to "" which 'resolves down to agent.model'
  - src/kiro_crew/config/sections.py:815-826 — role_models help: 'An empty value or auto defers to the chat default (agent.model)'
  - src/kiro_crew/config/sections.py:1850-1860 — resolve_model(role): role pin or DEFAULT_MODEL; 'deliberately does NOT inherit agent.model' (only background callers use it)
  - src/kiro_crew/subagent.py:1316-1333 — _subagent_default_model returns "" when unpinned so the caller OMITS the model kwarg
  - src/kiro_crew/subagent_manager/run.py:2158 — info.requested_model = eff_model or "auto"; :2159-2160 model kwarg only when eff_model
  - src/kiro_crew/config/loader.py:4272-4277 — acp_effective_model candidates: model_override > named agent spec > (global_model, 'agent.model')
  - src/kiro_crew/config/loader.py:4596-4612 — factory comment: an agent that pins nothing 'inherits the user's configured default'
  - …and 6 more in the verification results.
- **Checked by:** read
- **Required outcome:** Subagents, workflow steps, cron jobs and task-runner steps default to their role model ('auto' unless pinned via agent.role_models.<role>) and their role effort, never silently to the interactive chat model/effort; the recorded requested model is the one the factory actually requested; model-selection.md, the role_models help text and the ROLE_MODEL_KEYS comment state one rule.
- **Solution:**
  1. Decide (maintainer) whether an unpinned subagent should inherit agent.model (current factory comment, loader.py:4596-4612) or the role default (model-selection.md:376-379). This is a take-away change for operators who pinned agent.model expecting subagents to follow it: list it under `Reader:` per take-away-changes.md.
  2. If role default wins: add `cron`, `workflow`, `taskrunner` to ROLE_MODEL_KEYS (sections.py:270) or map them to `subagent`; coerce_role_models/coerce_role_efforts pick them up automatically.
  3. Pass the role model as model_override at the unattended sites: subagent_manager/run.py:2152-2160 (use cfg.agent.resolve_model('subagent') instead of omitting the kwarg), workflows/service.py:794/814 (default_model=cfg.agent.resolve_model('workflow')), slack/gateway.py:4668 (job.model or cfg.agent.resolve_model('cron')), session_allocation.open_task_session. Never a literal id; 'auto' reaches the wire only through resolve_usable_model / pick_served_default.
  4. Pass the role effort the same way (reasoning_effort_override=cfg.agent.resolve_effort(role)) or extend resolve_session_effort (loader.py:4366-4371) with the role.
  5. run.py:2158: record the model the factory requested (acp_effective_model(agent, override)) rather than the literal 'auto'.
  6. Same commit: reconcile model-selection.md:376-379, sections.py:263-269 and :815-836, and config.md.
- **Done when:** pytest with a fake SessionManager capturing get_or_create kwargs: config agent.model='X', reasoning_effort='high', role_models={} → a spawn_run, a workflow agent_fn call, a cron fire and a task-runner step each resolve (via KiroCrewConfig.acp_effective_model / resolve_session_effort) to '' (auto) and '' effort; with role_models.cron='Y' the cron fire resolves to 'Y'; info.requested_model equals the factory's resolved request. A docs test greps that model-selection.md and the role_models help text no longer contradict.
- **Upstream:** #13504 #10094 (state unverified)
- **Changed from the source claim:** Narrowed: the background role does NOT inherit agent.model/effort (resolve_model and resolve_effort('background') are independent); only subagent/workflow/cron/task-runner do. Task-runner inheritance added from open_task_session. The factory comment shows the inheritance is a deliberate current design, so the fix starts with a maintainer decision and is a take-away change.
- **Second reader:** top-20 check: agreed.
- **Sources:** FIX_PLAN:MOD-1, REVIEW_FINDINGS:S1, verify_needed:A3, verify_needed:G39(#10094), verify_needed:G39(#13504)

### USE-2 [55, armed, effort M] Workflow budget_total is never enforced — CONFIRMED
- **Verified claim:** Budget.charge() has no call site in src/kiro_crew, so spent() stays 0 and remaining() reports the full budget_total for the whole run; the only enforcement is would_exceed() before each call, which can only fire when budget_total is 0 (spent never grows). budget_update has an event builder but no emitter. The workflow authoring prompt tells scripts to read ctx.budget.remaining(), and the workflows spec itself records this as an open question. budget_total is optional (default None), so this matters only when a caller sets it; the effective limits are the run timeout and the 1000-call AgentCounter cap.
- **Evidence (at `397f4be`):**
  - grep '\.charge(' over src/kiro_crew — no match
  - src/kiro_crew/workflows/context.py:36-75 — Budget: spent/remaining/would_exceed/charge; DEFAULT_MAX_AGENTS_PER_RUN = 1000 at :33
  - src/kiro_crew/workflows/runner.py:413-420 — only `if self.budget.would_exceed(): raise BudgetExceeded(...)` before each call
  - src/kiro_crew/workflows/events.py:118-119 — budget_update builder; grep shows no caller
  - src/kiro_crew/workflows/service.py:126-131 — authoring prompt: 'Read ctx.budget.total, ctx.budget.spent(), or ctx.budget.remaining()'
  - src/kiro_crew/workflows/service.py:414, :1064, :1126 — budget_total=None default, passed through
  - docs/system-specs/modules/workflows.md:292-296 — 'Open question: nothing in the shipped engine calls Budget.charge() … no budget_update event is ever emitted'
  - src/kiro_crew/workflows/agent_pool.py:76-98 — pooled _run_step returns text only (no usage to charge)
- **Checked by:** read
- **Required outcome:** Each agent call charges its input+output tokens (retries and schema re-asks included) against Budget; a budget_update event is emitted after each charge; once the ceiling is reached no new call starts and the run ends with where='ceiling'. Alternatively (maintainer choice) remove budget_total and the ctx.budget guidance and document the timeout and call cap as the only limits.
- **Solution:**
  1. Decide enforce vs remove (workflows.md open question). If enforce:
  2. Make agent_fn return usage alongside text (agent_exec.agent_fn and agent_pool._run_step read provider_last_turn_usage(provider, since=...) after stream_and_collect); keep the public ctx.agent() return value unchanged.
  3. In runner._RunContext.agent (runner.py:405-480) call self.budget.charge(input_tokens + output_tokens) after each call (including each schema retry) and emit events.budget_update(spent, remaining).
  4. Charge in a finally so a failed/timed-out step's partial usage counts.
  5. Update workflows.md (remove the open question) and the authoring prompt text in the same commit.
- **Done when:** pytest with a fake agent_fn reporting 100 tokens per call and budget_total=250 running a script that loops ctx.agent() 10 times: the fake records exactly 3 calls (the 3rd call's charge reaches 300>=250 and raises BudgetExceeded; no 4th call starts), the run ends run_failed where='ceiling', budget_update events report spent=100 then 200 (then the clamped 250 if emitted before the raise), and ctx.budget.remaining() read in the script after call 1 equals 150.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** none — also the spec already documents it as an open question (workflows.md:292-296); severity kept at the corrected 55-60 because budget_total is opt-in.
- **Second reader:** top-20 check: agreed.
- **Sources:** FIX_PLAN:USE-2, REVIEW_FINDINGS:W1, verify_needed:A2

### MOD-2 [50, armed, effort M] Knowledge extraction: agent.model at 'high', 20 prompts per chat; edits re-extract all — PARTLY
- **Original claim:** Knowledge extraction on the chat model at 'high' effort, piling prompts into one conversation (corrected below)
- **Verified claim:** Knowledge extraction runs on the kirocrew-knowledge agent whose model is knowledge.extraction_model, else agent.model (the chat default: 'auto' on a default install, the pinned chat model otherwise) — not the background role — and at effort 'high' unless knowledge.extraction_effort is set. Each AcpWorker keeps one conversation and recycles it only at 50% context or after 20 prompts, although every prompt is self-contained, so up to 20 chunks' prompts accumulate. No knowledge path writes a usage row (no persist_token_record in knowledge/). Artifact auto-ingest (opt-in via knowledge.auto_ingest_artifacts) skips a save whose whole-file sha256 is unchanged, but any edit re-chunks and re-extracts EVERY chunk (no per-chunk hash cache) and each upsert is handled immediately (no debounce, only a lock).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/agent_materialization/service_agents.py:82-88 — model = knowledge.extraction_model or cfg.agent.model or 'auto'
  - src/kiro_crew/config/memory_sections.py:470-479 — extraction_model default '' = 'uses the default model (agent.model)'
  - src/kiro_crew/knowledge/llm_pool.py:81-83 — 'Knowledge extraction is deliberately high-effort by default'; DEFAULT_EXTRACTION_EFFORT = "high"
  - src/kiro_crew/knowledge/llm_pool.py:245-279 — _get_workload_effort: explicit pin else fallback 'high'; 'deliberately NO role-policy leg'
  - src/kiro_crew/knowledge/llm_pool.py:91-105 — prompts are self-contained, transcript is 'pure cost'; WORKER_RECYCLE_PCT=50.0, WORKER_RECYCLE_CALLS=20
  - src/kiro_crew/knowledge/llm_pool.py:1300-1320 — _maybe_recycle resets only when pct>=50 or calls_since_reset>=20
  - grep persist_token_record|provider_last_turn_usage over src/kiro_crew/knowledge/ — no match
  - src/kiro_crew/knowledge/artifact_ingest.py:6 — 'Off by default, opt in with knowledge.auto_ingest_artifacts'
  - …and 3 more in the verification results.
- **Checked by:** read
- **Required outcome:** Knowledge extraction runs on the background role model (role_models.background → 'auto') at a low/default effort unless the operator pins knowledge.extraction_model/effort; each chunk prompt runs in a fresh conversation; every extraction call writes one usage row; an edited artifact re-extracts only chunks whose hash changed, after a 30–60 s quiet period.
- **Solution:**
  1. service_agents.py:82-88: resolve as knowledge.extraction_model or cfg.agent.resolve_model('background') (never agent.model, never a literal). Update the extraction_model help text (memory_sections.py:470-479). Take-away change for users relying on the chat model: list under Reader:.
  2. llm_pool.py:83: DEFAULT_EXTRACTION_EFFORT = '' (provider default) or 'low' — a maintainer decision since the comment calls 'high' deliberate; update extraction_effort help (memory_sections.py:491-499).
  3. llm_pool.py:1300-1320: reset the conversation before each prompt (WORKER_RECYCLE_CALLS=1) or open session/new per prompt on the same client.
  4. Record usage per call in AcpWorker.send_message via the shared USE-1 seam (surface 'knowledge:extract' / 'knowledge:fetch').
  5. artifact_ingest.py:935-1000: debounce upserts per slug (coalesce within 30–60 s) and add a chunk-hash → extraction cache in ingestion.py:1390-1394 so unchanged chunks reuse stored items.
  6. Same commit: update the knowledge spec/doc that describes the pool.
- **Done when:** pytest with a fake AcpClient: 25 send() calls each see a prompt with no prior turns (fake records history length 0 per call); each call writes exactly one usage row (tmp KIROCREW_HOME usage shard has 25 lines); with role_models.background='cheap' and no extraction_model the installed kirocrew-knowledge spec has model 'cheap'; an artifact edited 5 times within the debounce window triggers 1 extract_batch, and an edit changing 1 of 10 chunks calls the extractor for 1 chunk.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** Corrected: the model is agent.model ('auto' by default, chat model only when pinned); unchanged artifact saves ARE skipped by a whole-file hash — only edited saves re-extract everything; artifact ingest is opt-in. Effort 'high' and the 20-prompt accumulation are confirmed as deliberate current design (comments), so steps 2-3 need maintainer sign-off.
- **Sources:** FIX_PLAN:MOD-2, REVIEW_FINDINGS:N3, REVIEW_FINDINGS:N4, REVIEW_FINDINGS:N8

### WF-1 [50, armed, effort M] Schema re-ask re-runs the whole workflow step — CONFIRMED
- **Verified claim:** When a workflow step's reply fails schema validation, run_with_schema calls the same agent_fn again with the ORIGINAL task prompt plus schema plus the validation errors, up to DEFAULT_SCHEMA_RETRIES=2 more times (3 attempts). agent_fn starts each call in a fresh conversation — an ephemeral session per call on the cold path, and a reset (fresh session/new) warm worker on the pooled path — with AUTO_APPROVE and up to 200 tool turns, so each re-ask re-runs the whole task, tools included, to fix only the JSON.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/workflows/schema.py:37 — DEFAULT_SCHEMA_RETRIES = 2
  - src/kiro_crew/workflows/schema.py:195-221 — for _attempt in range(retries + 1): text = await produce(attempt_prompt); re-ask prompt = augmented ORIGINAL prompt + 'Your previous reply was invalid'
  - src/kiro_crew/workflows/runner.py:457-467 — _produce(p) = self._agent_fn(p, opts); run_with_schema(_produce, prompt, schema)
  - src/kiro_crew/workflows/agent_exec.py:46 — _MAX_TURNS_PER_STEP = 200; :168-169 per-call ephemeral session by default
  - src/kiro_crew/workflows/agent_pool.py:178-188 — pooled worker 'Cheap clean slate before REUSE — fresh conversation' before each step
- **Checked by:** read
- **Required outcome:** A schema failure re-asks with only the validation error and the previous reply, in the same conversation (or a tool-free reformat call on the previous text), never re-running the task; at most one reformat attempt before giving up.
- **Solution:**
  1. Extend the agent_fn contract (agent_exec.build_agent_fn / agent_pool) with an optional follow-up mode: opts['_continue']=True sends a prompt into the SAME session the previous call used (keep the ephemeral session until the schema loop settles; release it after).
  2. schema.run_with_schema (schema.py:195-221): on failure send only `Your last reply failed validation: {errors}. Reply with only the corrected JSON.` via the follow-up mode; if that fails, run one tool-free reformat call through run_bg_oneliner(model=cfg.agent.resolve_model('background')) on the previous text.
  3. runner.py:457-467: pass the follow-up-capable producer; the per-call budget/usage (USE-1/USE-2) counts each attempt.
  4. Update workflows.md (structured output section) in the same commit.
- **Done when:** pytest with a fake agent_fn that records (prompt, session) per call and returns invalid JSON once then valid: exactly 2 calls, the second in the same session with a prompt that does NOT contain the original task text; a stub returning invalid JSON 3 times yields None after 1 follow-up + 1 reformat call and zero full re-runs.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** Upgraded from 'plausible' to confirmed: attempts are 3 total (retries=2) and each starts a fresh conversation on both the cold and pooled paths.
- **Sources:** FIX_PLAN:WF-1, REVIEW_FINDINGS:W4

### USE-3 [45, armed, effort M] Auto-Improvement $ cap never trips on the Kiro backend — CONFIRMED
- **Verified claim:** Auto-Improvement's spend ceiling (maxCostUsd, default $5) is checked against runner.total_cost_usd, which only accumulates cost_usd read from stream events (ev.cost_usd or ev.usage.cost_usd). The Kiro (acp) provider bills in TurnUsage.credits and leaves cost_usd at 0, so on the default backend the meter stays 0 and the dollar cap never trips; nothing in the app reads credits. The run is still bounded by maxCycles (25) and maxHours (2.0).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/apps/builtins/auto_improvement/spine/agent_runner.py:1507-1524 — _finish folds `cost` into _total_cost_usd 'so the driver's live --max-cost meter … sees real spend'
  - src/kiro_crew/apps/builtins/auto_improvement/spine/agent_runner.py:1544-1548 — reported_cost = ev.cost_usd or ev.usage.cost_usd; credits never read
  - src/kiro_crew/apps/builtins/auto_improvement/backend/runner.py:70-72 — DEFAULT_MAX_CYCLES = 25, DEFAULT_MAX_HOURS = 2.0, DEFAULT_MAX_COST_USD = 5.0; :617 max_cost_usd from config maxCostUsd
  - src/kiro_crew/apps/builtins/auto_improvement/spine/driver.py:378 — gate fires when cost_meter() > caps.max_cost_usd
  - src/kiro_crew/acp/types.py:659-673 — TurnUsage: 'kiro (acp) fills credits'; cost_usd and credits separate
  - grep 'credit' over apps/builtins/auto_improvement (non-test): no match
- **Checked by:** read
- **Required outcome:** On a credits-billing backend the Auto-Improvement run has a cap that actually trips (in credits), the dollar cap is hidden or marked inactive when the backend reports no USD, and the run warns if its spend meter is still 0 after N turns.
- **Solution:**
  1. agent_runner.py:1544-1548: also read credits (ev.usage.credits or provider_last_turn_usage) and accumulate a _total_credits counter with a total_credits() accessor (same lock).
  2. backend/runner.py:617: add maxCredits (config key, a named default constant), passed into BudgetCaps; driver.py:378 checks both meters.
  3. Frontend app settings: show the credit cap; hide or grey the USD cap when the active backend bills credits (capability of the provider, not a backend-name check — harness parity: positive identity).
  4. Warn (log + run event) when both meters are 0 after N completed agent turns.
  5. Update the app's README/spec in the same commit.
- **Done when:** pytest with a fake provider whose EVENT_COMPLETE carries TurnUsage(credits=2.0, cost_usd=0.0): with maxCredits=5 the driver stops before the 4th agent turn (credit meter 6.0 > 5); with only maxCostUsd set the warning event is emitted after N turns.
- **Upstream:** #6338 (state unverified)
- **Changed from the source claim:** none (severity 50→45: maxCycles/maxHours still bound the run).
- **Sources:** FIX_PLAN:USE-3, REVIEW_FINDINGS:N7

### MOD-6 [40, armed, effort S] Dictation sends the whole growing transcript every segment — CONFIRMED
- **Verified claim:** With stt.endpointing on (default OFF), every stable dictation final appends to _finals and schedules a classification whose prompt carries the WHOLE transcript so far (' '.join(self._finals)); calls are debounced 0.35 s and single-flight (a final arriving mid-call is latched and re-run), so input grows roughly with the square of dictation length (N finals × growing transcript). Each call runs through run_bg_oneliner with model 'auto' (which also overrides a role_models.background pin, see MOD-3) and start_priority FOREGROUND. No local silence/punctuation pre-check exists before the model call.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/stt_stream.py:147 — '# ── Semantic endpointing (stt.endpointing, default off) ──'
  - src/kiro_crew/dashboard/stt_stream.py:155-157 — _ENDPOINT_MODEL = "auto"; _ENDPOINT_DEBOUNCE_SECS = 0.35; _ENDPOINT_TIMEOUT_SECS = 5.0
  - src/kiro_crew/dashboard/stt_stream.py:385-398 — note_final: self._finals.append(text); transcript = " ".join(self._finals).strip(); self._schedule(gen, transcript)
  - src/kiro_crew/dashboard/stt_stream.py:405-431 — _classify: debounce, single-flight latch, run_bg_oneliner(_ENDPOINT_PROMPT.format(transcript=transcript), model=self._model, start_priority=StartPriority.FOREGROUND)
  - src/kiro_crew/dashboard/stt_stream.py:519-531 — _Endpointer built only when cfg.stt.endpointing
- **Checked by:** read
- **Required outcome:** Each endpointing call sends a bounded tail of the transcript (last N words) on the background role model, and a cheap local heuristic skips the model call when the tail clearly is or is not finished.
- **Solution:**
  1. stt_stream.py:385-398: build the classifier input from the last N words (e.g. a named constant _ENDPOINT_TAIL_WORDS = 40) instead of the whole _finals join; keep _finals for nothing else or cap it.
  2. stt_stream.py:155/:335: default the model to cfg.agent.resolve_model('background') (via the factory at :519-531), never a literal.
  3. Add a local pre-check before run_bg_oneliner: e.g. tail ends with a sentence terminator and no trailing conjunction → COMPLETE without a call; tail ends with a conjunction/preposition → INCOMPLETE without a call. Keep FOREGROUND priority (a person is waiting).
  4. Document in stt-streaming.md (same commit).
- **Done when:** pytest with a fake sessions whose run_bg_oneliner records prompts: feeding 50 finals of 10 words each (after each debounce with an injected sleep stub) produces prompts whose transcript part is ≤ 40 words every time, and a final ending in 'and' produces no model call; with role_models.background='cheap' the recorded model is 'cheap'.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** Scope corrected to armed: stt.endpointing is off by default. Debounce + single-flight dampen call count but not the per-call transcript growth.
- **Sources:** FIX_PLAN:MOD-6, REVIEW_FINDINGS:B7

### WF-2 [40, armed, effort S] No cap on step output passed between workflow steps — CONFIRMED
- **Verified claim:** ctx.agent() returns the step's full text (or schema value) to the workflow script with no per-call or run-wide size cap; the script can pass it verbatim into later steps' prompts, and it is also kept whole in agent_results for resume. Only the error string (500 chars) and the agent_finished event summary (120 chars) are truncated. agent_fn's stream_and_collect has no output bound on this path (run_bg_oneliner's max_output_bytes is not used here).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/workflows/runner.py:399-519 — _RunContext.agent: result = await self._agent_fn(prompt, opts) … self.agent_results[call_index] = result … return result
  - src/kiro_crew/workflows/runner.py:500 — result_summary=str(result)[:120] (event only)
  - src/kiro_crew/workflows/runner.py:89 — MAX_AGENT_ERROR_CHARS = 500 (errors only)
  - src/kiro_crew/workflows/agent_pool.py:76-98 and agent_exec.py:203-207 — stream_and_collect with no output limit; redact(text) returned whole
  - grep 'max_output|MAX_RESULT' over src/kiro_crew/workflows — no output cap
- **Checked by:** read
- **Required outcome:** A workflow step's output handed back to the script is bounded by an optional per-call max_output_chars with a run-wide default, and truncation is marked so the script/model knows the text was cut.
- **Solution:**
  1. Add `max_output_chars: Optional[int]` to ctx.agent (runner.py:399-412) and a run-wide default on WorkflowRunner (a named constant, e.g. DEFAULT_MAX_OUTPUT_CHARS), threaded from service.py's run start.
  2. After the call (runner.py:~470) truncate string results head/tail with an explicit '[… N chars truncated …]' marker; leave schema values alone or apply the cap to their JSON.
  3. Note: the frozen ctx Protocol — check workflows.md for whether ctx.agent's signature may grow; if frozen, use the run-wide default only.
  4. Document in workflows.md and the authoring prompt (service.py:120-135).
- **Done when:** pytest: a fake agent_fn returning 200 000 chars with run default 20 000 → the script receives ≤ 20 000 chars plus the marker; max_output_chars=None on the call with run default None leaves the text unchanged.
- **Upstream:** none (state unverified)
- **Sources:** FIX_PLAN:WF-2, REVIEW_FINDINGS:W6

### WF-3 [40, armed, effort M] Workflow call cap of 1000; dependency retries re-run whole steps — CONFIRMED
- **Verified claim:** The per-run agent-call cap is DEFAULT_MAX_AGENTS_PER_RUN = 1000. When the gateway attaches task admission (it does at runtime), each workflow step runs inside admitted_agent_fn, which on any exception classified as a retryable dependency signal parks and then re-runs the WHOLE agent_fn (`continue`) — up to DEPENDENCY_MAX_ATTEMPTS = 20 waits — with no check whether the failed attempt had already streamed text or run tools. This stacks on stream_and_collect's own transient retries inside each attempt.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/workflows/context.py:33 — DEFAULT_MAX_AGENTS_PER_RUN = 1000
  - src/kiro_crew/workflows/agent_pool.py:527-566 — while True: result = await agent_fn(prompt, opts) … except Exception: signal = classify_exception(exc); waits += 1; … yield_dependency … continue
  - src/kiro_crew/workflows/agent_pool.py:48 + src/kiro_crew/taskq/dependency.py:129 — DEPENDENCY_MAX_ATTEMPTS = DEFAULT_MAX_ATTEMPTS = 20
  - src/kiro_crew/workflows/service.py:823-831 — agent_fn wrapped by admitted_agent_fn when _task_admission is set
  - src/kiro_crew/slack/gateway_runtime/admission.py:353 — workflow_service.attach_task_admission(admission) at gateway start
- **Checked by:** read
- **Required outcome:** A lower default call cap (or one derived from budget_total), and dependency retries that re-run a step only when the failure happened before the first token/tool call (spawn, connect, session/new); a post-activity failure fails the step (or resumes it) instead of paying for the whole step again.
- **Solution:**
  1. context.py:33: lower DEFAULT_MAX_AGENTS_PER_RUN (maintainer to pick, e.g. 200) or derive it from budget_total when set; keep it overridable per run. This is a take-away change for scripts that legitimately fan out wider — list under Reader:.
  2. agent_pool.py:527-566: have agent_fn report whether activity began (e.g. raise a typed StepFailedAfterActivity wrapping the cause, set by _run_step/agent_exec when stream_and_collect saw text or tool events); in the except arm, retry only when no activity was recorded.
  3. Reuse the same pre-activity predicate stream_and_collect already uses for its transient/fallback walk (model-fallback.md: 'no result text or tool activity') rather than a new spelling.
  4. Update workflows.md and taskrunner.md (dependency waits) in the same commit.
- **Done when:** pytest with a fake agent_fn that streams text then raises a retryable dependency error: admitted_agent_fn calls it exactly once and fails the row; a fake that raises before any activity is retried until success (2 calls). A run whose script loops ctx.agent() 300 times stops at the new cap with run_failed where='ceiling'.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** none; added the concrete retry bound (20 dependency waits) and that admission is attached by the gateway at runtime.
- **Sources:** FIX_PLAN:WF-3, REVIEW_FINDINGS:W7

### MOD-4 [35, default, effort M] Rejected-model fallback picks the first listed model regardless of cost — CONFIRMED
- **Verified claim:** When a model (typically 'auto') is rejected on the wire, the reactive fallback picks the FIRST advertised id that is neither the rejected id nor 'auto' (first_advertised_fallback), with no regard to role_models.background, the background session's current model, or cost; the rejection is not remembered, so every later call tries the rejected model again and pays the failed attempt. The same selector serves three sites: run_bg_oneliner, stream_and_collect's opt-in model_fallback, and the dashboard's interactive model-access fallback. The same 'first advertised' rule also lives in acp.runtime_models.pick_served_default (session start when the backend default is unserved and 'auto' is not advertised). Frequency is reduced at HEAD because the substitute set_model path resolves through resolve_usable_model first, so the reactive path fires only when the advertised set was unknown at send time or the backend refuses an advertised id.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/llm_helpers.py:293-310 — first_advertised_fallback: returns the first advertised m with m != rejected and m != 'auto'
  - src/kiro_crew/llm_helpers.py:1776-1788 — run_bg_oneliner retries once with first_advertised_fallback(advertised, rejected)
  - src/kiro_crew/llm_helpers.py:2752 — stream_and_collect model_fallback uses the same selector
  - src/kiro_crew/dashboard/chat_runner.py:17936-17938 — interactive reactive model-access fallback uses first_advertised_fallback
  - src/kiro_crew/acp/runtime_models.py:335-340 — pick_served_default: 'auto' if advertised 'else the FIRST advertised id'
  - grep: no rejection cache (no module state for a rejected model in llm_helpers / acp)
  - ran scripts/E/mod3_mod4.py — advertised ['auto','claude-opus-4.8','claude-haiku-4.5'], rejected 'auto': two consecutive calls each sent ['auto','claude-opus-4.8'] (first listed, no memory)
- **Checked by:** ran-new-script
- **Required outcome:** A reactive fallback for a background/unattended call prefers role_models.background (resolved through resolve_usable_model) or the session's served model before any other advertised id, and a rejection is cached per (backend, namespace, model) so later calls skip the doomed attempt until the advertised list changes. An explicit user pick still raises (never swapped).
- **Solution:**
  1. Give first_advertised_fallback (llm_helpers.py:293) an ordered preference argument: [resolve_usable_model(cfg.agent.resolve_model('background'), advertised), session.served_model] before the advertised order; keep 'auto' and the rejected id excluded.
  2. Pass the preference from run_bg_oneliner (:1779) and stream_and_collect (:2752); leave the dashboard interactive path (chat_runner.py:17936) on the user's own advertised list unless a maintainer decides otherwise.
  3. Add a small rejection memo keyed by (backend, model_registry namespace, rejected id) with the advertised-list fingerprint, consulted before set_model in run_bg_oneliner; invalidate when the advertised fingerprint changes. No hardcoded ids.
  4. Review overlap with PR #15124 (ordered failover) before writing new code.
  5. Document in model-fallback.md (same commit).
- **Done when:** pytest with a fake session: role_models.background='cheap', advertised ['expensive','cheap'], the first call raises AcpError(rejected_model='auto') → fallback set_model('cheap'); a second run_bg_oneliner call sends no 'auto' attempt (memo hit) and a changed advertised list clears the memo.
- **Upstream:** PR #15124 (state unverified)
- **Changed from the source claim:** Confirmed and widened: the selector also drives stream_and_collect and the interactive model-access fallback, and pick_served_default applies the same first-listed rule at session start. Severity lowered 50→35 because resolve_usable_model now keeps most unserved ids off the wire, making this a backstop path.
- **Sources:** FIX_PLAN:MOD-4, REVIEW_FINDINGS:B6, verify_needed:G37

### UI-1 [35, default, effort M] Link-label resolution fires on mount even when the Links tab is hidden, uncached — CONFIRMED
- **Verified claim:** ChatPage calls useChatNavigation unconditionally (its sections feed the minimap), and that hook issues one POST /api/chat/nav/resolve-links for all bare (non-markdown) URLs in the transcript whenever that set is non-empty — keyed by the joined URL list (staleTime Infinity, default gcTime 5 min), so it refires on mount/reload/new window, after 5 min unmounted, and on any change to the set — even with the side panel closed; the labels are only rendered in the side panel's Links tab, which also hides URLs that have a richer Changes/Issues entry. The backend keeps links[:20] (the oldest 20) and makes one uncached run_bg_oneliner(model='auto') call, so past 20 URLs every new URL re-labels the same 20 and the new one never gets a label. Usage is recorded (bg:chat_nav).
- **Evidence (at `397f4be`):**
  - website/src/hooks/useChatNavigation.ts:59-79 — linksToResolve = non-fromMarkdown links; batchKey = urls.join('|'); useQuery(['nav-link-summaries', batchKey], staleTime: Infinity, enabled: linksToResolve.length > 0); no gcTime
  - website/src/pages/ChatPage.tsx:3905 — const chatNav = useChatNavigation(...) (unconditional); :6649/:6690 navLinks passed to the side panel only; :5884 sections used by the minimap
  - website/src/pages/chat/sidePanelMount.ts:52-55 — panel subtree mounted only when activityOpen (or app/browser tab)
  - website/src/pages/chat/ActivityViewer.tsx:464-465 — richUrls (sources/issues) filtered out of the Links list
  - src/kiro_crew/dashboard/chat_nav.py:186 — links = [_normalize_link(x) for x in links[:20]]
  - src/kiro_crew/dashboard/chat_nav.py:106-112 — run_bg_oneliner(state.sessions, prompt, model=_LINK_SUMMARY_MODEL ('auto'), sel_source='chat_nav'), no cache
  - scratchpad ui/UI_REPORT.txt — measured prompt sizes 951 / 2,379 / 7,755 chars for 1 / 5 / 20 links (prior run of the real _build_link_summary_prompt)
- **Checked by:** read
- **Required outcome:** Link labels are requested only while the Links tab is visible, only for URLs that tab will show and that have no label yet (≤20 per batch), with a backend per-URL cache; links beyond the 20th get labels; model choice follows MOD-3.
- **Solution:**
  1. Split useChatNavigation: keep sections/links extraction at ChatPage; move the resolve query into the Links tab component (or gate `enabled` on the tab being open).
  2. Exclude fromMarkdown links and richUrls (sources/issues) before requesting.
  3. Key the query per URL (or keep a label map) and send only unlabelled URLs, ≤20 per POST; merge by URL, not by position.
  4. Backend chat_nav.py:164-200: LRU/TTL cache keyed by sha256(url without query + context) returning {url: label}; accept and return a url→label map.
  5. Set gcTime: Infinity or persist labels per slot.
  6. Model: see MOD-3 (resolve_model('background')). Read website/AGENTS.md; strings via the i18n catalog.
- **Done when:** vitest: side panel closed → 0 POSTs to /api/chat/nav/resolve-links; opening Links → 1; adding a second bare URL → a POST containing only the new URL; the 21st and later URLs receive labels. pytest: two POSTs with the same url+context → one run_bg_oneliner call.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** none; 'the embeds' are the same ChatPage call site (only one useChatNavigation caller in website/src).
- **Sources:** FIX_PLAN:UI-1, REVIEW_FINDINGS:Part6/UI-1

### LOOP-10 [30, default, effort S] Welcome suggestions regenerate each 30 min while open, even if unchanged; background model — PARTLY
- **Original claim:** Welcome suggestions regenerate every 30 min even when unchanged, on 'auto' (corrected below)
- **Verified claim:** While a Welcome view is mounted (it refetches /api/suggestions every 10 min, staleTime 5 min), the gateway regenerates the suggestions whenever the single gateway-wide cache is older than 30 min — with no check whether the assembled context changed — sending up to ~9-10k chars of context (preferences ≤2000, projects ≤3000, recent activity ≤4000, 5 recent sessions, 5 cron names). The cache is global, so several windows do not multiply calls. The model sub-claim is wrong: generate_suggestions passes no model to run_bg_oneliner, so it runs on the _bg session's own default, i.e. the kirocrew-lite spec model = role_models.background ('auto' only when unpinned). Usage IS recorded (bg:suggestions).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/suggestions.py:28 — _REFRESH_INTERVAL_SECS = 30 * 60
  - src/kiro_crew/suggestions.py:97-170 — _build_context: prefs[:2000], projects[:3000], recent_history[:4000], sessions[:5], cron_jobs[:5]
  - src/kiro_crew/suggestions.py:248 — run_bg_oneliner(state.sessions, prompt, sel_source='suggestions', timeout=60) — no model kwarg
  - src/kiro_crew/suggestions.py:270-283 — maybe_refresh: only an age check, no context hash
  - src/kiro_crew/suggestions.py:300-337 — api_suggestions: GET calls maybe_refresh; global cache via get_suggestions_cache(state)
  - website/src/components/WelcomeView.tsx:75-81 — useQuery(['suggestions']) staleTime 5 min, refetchInterval 10 min, refetchOnWindowFocus false
  - src/kiro_crew/llm_helpers.py:1676 + agent_materialization/service_agents.py:66 — no model → no set_model; _bg session runs the lite spec model (resolve_model('background'))
- **Checked by:** read
- **Required outcome:** Suggestions regenerate only when the built context changed since the last generation (and the cache is older than the interval); the model stays the background role (no change needed).
- **Solution:**
  1. suggestions.py: store sha256(_build_context(state)) on SuggestionsCache with the result; in maybe_refresh/refresh_suggestions build the context first and skip the model call (just bump generated_at) when the hash is unchanged.
  2. Keep the 30-min interval as the upper bound; force=1 still regenerates.
  3. No model change: it already inherits role_models.background; if MOD-3 lands, keep passing no model or pass resolve_model('background') explicitly.
- **Done when:** pytest with a fake run_bg_oneliner counter and a frozen clock: two refreshes 31 min apart with an unchanged context make 1 model call; changing the recent-activity input before the second makes 2.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** Model sub-claim refuted: suggestions use the background role model (no 'auto' literal); cadence/no-hash claim confirmed; cache is gateway-global.
- **Sources:** FIX_PLAN:LOOP-10, REVIEW_FINDINGS:H5

### MOD-3 [30, pinned, effort S] Literal model="auto" overrides a pinned background model — CONFIRMED
- **Verified claim:** Eight background call sites pass the literal 'auto' to run_bg_oneliner: chat titles (chat_title.py:180, used at :809 and :843), link labels (chat_nav.py:74/:112), folder icons (chat_folders.py:115/:200), the lesson-contradiction check (handlers/cron.py:245/:269), session summarize (handlers/sessions.py:1804/:1957), STT polish (handlers/core.py:1747), the dictation endpoint classifier (stt_stream.py:155 default, used at :427) and tips (dashboard.tips_model default 'auto', tips.py:1117/:1124). run_bg_oneliner calls set_model('auto') for any truthy model (it does NOT skip 'auto'), and AcpSessionHandle.set_model sends 'auto' whenever the backend advertises it (the recorded kiro session/new fixture advertises exactly ['auto']), replacing the kirocrew-lite spec's role_models.background pin for that call. Measured with a fake session: model='auto' → set_model calls ['auto']. Suggestions (suggestions.py:248) and folder suggestions pass NO model, so they correctly inherit the pinned _bg default; card generation and session summaries already use resolve_model('background'). The comments at chat_title.py:173-179 and stt_stream.py:150-152 ('run_bg_oneliner skips the override for auto') are stale.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/llm_helpers.py:1676-1678 — `if model_to_use and set_model is not None: await set_model(model_to_use)` (no 'auto' skip)
  - src/kiro_crew/acp/session_handle.py:2511-2519 — resolve_usable_model(model_id, advertised); only "" is a no-op
  - src/kiro_crew/acp/runtime_models.py:318-319 — 'auto' → 'auto' when not model_is_unusable('auto', ids)
  - test/fixtures/acp_frames/kiro/session.jsonl:3 — kiro session/new availableModels [{modelId:'auto'}], currentModelId 'auto'
  - src/kiro_crew/agent_materialization/service_agents.py:66 + agent.py:565-583 — lite spec model = resolve_model('background') (the pin a literal 'auto' overrides)
  - src/kiro_crew/dashboard/chat_title.py:173-180 — stale comment 'run_bg_oneliner skips the per-session set_model override for auto'; _TITLE_MODEL = "auto"
  - src/kiro_crew/dashboard/chat_nav.py:74, chat_folders.py:115, handlers/cron.py:245, handlers/sessions.py:1804, handlers/core.py:1747, stt_stream.py:155, config/sections.py:3227-3228 (tips_model default 'auto')
  - src/kiro_crew/dashboard/chat_summary.py:412 — correct pattern: model = cfg.agent.resolve_model(_SUMMARY_ROLE)
  - …and 2 more in the verification results.
- **Checked by:** ran-new-script
- **Required outcome:** Every background one-liner honours an operator's agent.role_models.background pin; with no pin, behaviour is unchanged ('auto' resolved at the wire).
- **Solution:**
  1. Replace the literal at each site with cfg.agent.resolve_model('background') (or pass model=None to inherit the _bg session's spec model): chat_title.py:180 (+:809/:843), chat_nav.py:74/:112, chat_folders.py:115/:200, handlers/cron.py:245/:269, handlers/sessions.py:1804/:1957, handlers/core.py:1747, stt_stream.py:155/:335.
  2. tips: make dashboard.tips_model default '' meaning 'background role' and resolve it with resolve_model('background') when empty (sections.py:3227, tips.py:1117); keep an explicit tips_model honoured.
  3. Fix the stale comments at chat_title.py:173-179 and stt_stream.py:150-152.
  4. Optional guard: an AST test that fails when a run_bg_oneliner/background_turn call passes a string literal model.
- **Done when:** pytest: with agent.role_models.background='cheap' and a fake get_bg_session whose set_model records calls, generating a title, a link label, a folder icon, a contradiction check, a summarize, an STT polish, a dictation classify and tips records set_model('cheap') or no set_model call — never 'auto'. With role_models empty, each still resolves to 'auto' (unchanged). AST test: zero run_bg_oneliner calls with a str-literal model= in src/kiro_crew.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** suggestions.py:248 is wrongly listed (passes no model, honours the pin); STT endpoint (stt_stream.py:155) and STT polish (core.py:1747, not :1750) confirmed; a second stale comment exists at stt_stream.py:150-152. Overriding requires the backend to advertise 'auto' (kiro's recorded session/new does).
- **Sources:** FIX_PLAN:MOD-3, REVIEW_FINDINGS:B1, FIX_PLAN-old:T4

### WF-4 [30, armed, effort M] Workflow completion turn and edited reruns cost full turns/runs — CONFIRMED
- **Verified claim:** When a workflow bound to a dashboard chat slot finishes, the gateway injects its result (JSON body cut at 4000 chars) and then automatically enqueues a full parent agent turn on that slot (slot.enqueue_or_run_prompt(prompt, _run_chat)), which runs on the slot's chat model with the full session context; there is no setting to skip it or move it to the background model. An EDITED rerun sets replay_before=0 and an empty replay cache, so every ctx.agent() call runs again even when its (prompt, opts) is unchanged; an unedited rerun does replay the cached prefix by call index.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/server_runtime/workflow_startup.py:50-81 — _wf_on_done → _auto_turn: prompt '[Workflow … finished …] answer it directly'; slot.enqueue_or_run_prompt(prompt, _run_chat, state)
  - src/kiro_crew/dashboard/server_runtime/workflow_startup.py:83-89 — inject_bound_workflow_result(…, on_injected=_auto_turn)
  - src/kiro_crew/dashboard/workflow_inject.py:69 — 'Result: ```json' + body[:4000]
  - src/kiro_crew/workflows/service.py:1574-1577 — 'An edited script can't safely replay the old prefix (call indices shift), so force a fresh run'; replay_before = 0 if edited; replay_results = {} if edited
- **Checked by:** read
- **Required outcome:** The automatic completion turn is optional (a setting, default unchanged unless a maintainer decides otherwise) or answered from the injected result on the background role model; an edited rerun replays cached results for calls whose (prompt, opts) hash is unchanged.
- **Solution:**
  1. workflow_startup.py:50-81: gate _auto_turn on a new config key (e.g. workflows.completion_turn: 'chat'|'off'), default 'chat' to avoid a take-away change; document in workflows.md and config.md. Changing the default is a maintainer decision (take-away: users rely on the auto answer).
  2. workflows/runner.py: key the replay cache by sha256(prompt + canonical opts) in addition to call index; on an edited rerun, service.py:1574-1577 passes the prior results keyed by hash and the runner replays a call whose hash matches, otherwise calls the model.
  3. Record the hash in agent_results metadata so resume and rerun share it.
- **Done when:** pytest: a 3-call script rerun with only the 3rd prompt edited calls the fake agent_fn once (calls 1-2 replayed by hash); with workflows.completion_turn='off', a finished bound run injects the result and enqueue_or_run_prompt is not called.
- **Upstream:** none (state unverified)
- **Sources:** FIX_PLAN:WF-4, REVIEW_FINDINGS:W8

### USE-7 [25, default, effort M] Crew logs injected block sizes and occupancy but never derives the replay or image share — PARTLY
- **Original claim:** Native history replay cost is not measured or broken down in Crew turn stats (corrected below)
- **Verified claim:** Crew's context budgets (context_assembly/budget.py, _CONTEXT_BUDGET_BASE = 33 000 chars and section caps) govern only what Crew injects; kiro-cli owns the native conversation replay, and acp/prompt_blocks.py states the total a conversation's replayed history (including ~1,600 tokens per replayed image) carries 'is not measured here'. Kiro reports per-turn credits and (via usage_update) total context occupancy, not a breakdown. Overstated: Crew DOES record, per turn, the size of each block it injected (crew-log 'context/composed', served by usage.context_trace as injected_chars per block) beside the backend's context occupancy (peak used/window), so the non-Crew share is derivable — but it is not computed or shown, images are not separated, and chars vs tokens are not reconciled.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context_assembly/budget.py:33 — _CONTEXT_BUDGET_BASE = 33_000 (injected background only)
  - src/kiro_crew/acp/prompt_blocks.py:90-99 — 'each replayed image costs about 1,600 tokens on every later turn … what a conversation's replayed history carries in total is not measured here'
  - src/kiro_crew/dashboard/handlers/usage.py:721-770 — context_trace: per-turn injected block sizes (chars) from the crew log + occupancy (tokens)
  - src/kiro_crew/dashboard/handlers/usage.py:889-898 — returns injected_chars, user_chars, peak_context_used, context_window
  - src/kiro_crew/acp/session_handle.py:6446-6458 — usage_update gives used/size only; acp/_dispatch.py:431-460 metadata gives credits + pct only
- **Checked by:** read
- **Required outcome:** Each turn's stats show context occupancy split into Crew-injected (blocks, measured), images (count × per-image estimate) and 'native history + system + tools' (occupancy minus the two), labelled as derived, so the uncontrolled share is visible.
- **Solution:**
  1. In context_trace (usage.py:721-900) add derived fields per turn: injected_tokens_est (injected chars / the existing chars-per-token constant), replayed_images (count from the session's image ledger when one exists, else from prompt_blocks' inlined counts), other_tokens = context_used − injected_tokens_est − images×per-image estimate, floored at 0 and flagged 'derived'.
  2. Surface it in the session context panel (frontend, i18n strings).
  3. Do not add caps here; this is measurement (feeds CTX-* and USE-5 decisions).
  4. Document in context-management.md (cost ownership boundary).
- **Done when:** pytest: a slot with crew-log context/composed entries totalling 40 000 chars and a usage_update of 60 000/200 000 tokens yields a turn row with injected_tokens_est ≈ 10 000 and other_tokens ≈ 50 000 (derived:true); vitest renders the three segments.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** Crew already records per-turn injected block sizes and occupancy (context_trace); the gap is the derived split and image accounting, not a total absence of measurement. Whether kiro-cli could report a breakdown is not checkable here.
- **Sources:** verification_needed:crit-risk-1, verification_needed:sink#1, verification_needed:refactor(native-history-measurement)

### MOD-7 [25, pinned, effort S] Switching to 'auto' keeps the spec model pin only for frozen or sidecar-less main agents — PARTLY
- **Original claim:** Switching back to 'auto' does not clear the model pin the UI wrote (corrected below)
- **Verified claim:** The Settings default-model picker writes config agent.model; a rebuild propagates a concrete pick into ~/.kiro/agents/kirocrew.json, and with agent.model='auto' the factory and the chip fall through to that spec's model (KiroCrewConfig._resolve_agent_model). For a TRACKED spec (sidecar model_managed=True, which every fresh install seeds) switching back to 'auto' DOES clear it: _refresh_dynamic_fields resets the spec to the shipped default and the agent.model live applier rebuilds the spec. The pin survives only when the kirocrew sidecar entry is model_managed=False (a model set in the agent-template editor, which deliberately freezes it) or absent (an install whose sidecar never recorded the main agent, e.g. upgraded from before the sidecar): then the propagated/edited concrete model stays in the spec and 'auto' resolves to it. It is not invisible: the model chip (resolve_effective_model tier 4) and `kirocrew doctor` show the spec pin and suggest `kirocrew agent reset-model`, but the Settings default still reads 'auto'. Measured with the real _refresh_dynamic_fields: managed=True → 'auto' after switch; managed=False → pin kept; no entry → pin kept.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/config/loader.py:4188-4216 — _resolve_agent_model reads kirocrew.json 'model' when agent.model is 'auto'
  - src/kiro_crew/config/loader.py:4265-4268 — acp_effective_model: global 'auto' collapses through _resolve_agent_model()
  - src/kiro_crew/config/loader.py:7132-7134 — resolve_effective_model falls through to the installed kirocrew.json (chip shows the pin)
  - src/kiro_crew/agent.py:2300-2319 — managed spec reset to shipped default; comment: needed so 'auto' is reachable; 'Agents with no sidecar entry are grandfathered and left untouched'
  - src/kiro_crew/agent.py:2321-2336 — concrete agent.model propagated into the spec; 'auto' defers
  - src/kiro_crew/agent.py:3619-3622 — model_managed seeded True only on a fresh install / clean regen
  - src/kiro_crew/dashboard/agent_admin/agent_detail.py:395-400 — template editor model → set_model_managed(name, False); empty → clear_model_pin
  - src/kiro_crew/dashboard/server_runtime/config_watch.py:116-160 — agent.model change rebuilds kirocrew.json ('auto can never clear a prior concrete pin' without it)
  - …and 3 more in the verification results.
- **Checked by:** ran-new-script
- **Required outcome:** After the user switches the Settings default back to 'auto', a new default-agent session requests 'auto' unless the user deliberately pinned the kirocrew template in the template editor — and in that case the Settings panel names the template pin that overrides it.
- **Solution:**
  1. agent.py:3619-3622: seed model_managed=True for the main agent on ANY rebuild where the sidecar has no entry AND the spec's model equals the current config agent.model (i.e. propagation wrote it), so grandfathered installs track again; never flip an existing False.
  2. Settings ChatPanel (website/src/pages/settings/ChatPanel.tsx ~931-960): when resolve_effective_model differs from agent.model because of a kirocrew template pin, show it next to the default picker with a 'clear template pin' action that calls the existing clear_model_pin path (PATCH /api/agent/kirocrew {model: ''}). i18n strings via the catalog.
  3. Do not auto-clear a model_managed=False pin: it is an explicit user pick (model-selection.md: never silently swap a user's pick).
  4. Update config.md (model tracking) in the same commit.
- **Done when:** pytest in a tmp KIROCREW_HOME: sidecar without a kirocrew entry, config agent.model='X' → rebuild → spec model 'X'; set agent.model='auto' → rebuild → spec model 'auto' and resolve_effective_model(cfg) == ''. With model_managed=False and spec model 'X', the same switch leaves 'X' and GET /api/models (or the effective-model endpoint) reports the template pin as the deciding tier; vitest: ChatPanel renders the override notice when effective != configured.
- **Upstream:** #16270 #11800 (state unverified)
- **Changed from the source claim:** Narrowed: HEAD already clears the propagated pin for tracked specs (the #16270 shape), and the chip/doctor surface the pin. Residual is limited to frozen (template-editor) or sidecar-less main-agent specs; the Settings panel itself does not name the override.
- **Sources:** FIX_PLAN:MOD-7, verify_needed:G25(#16270), verify_needed:G43, verify_needed:G44

### KNOW-1 [25, armed, effort M] Knowledge agent sync has the model fetch and echo the page — CONFIRMED
- **Verified claim:** A Knowledge URL source with no registered connector is synced by 'agent-assisted sync': the gateway sends the URL-fetch LLM pool a prompt asking the model to fetch the page with any web/URL tool and 'Return ONLY the raw document text', so the whole document is paid for as model OUTPUT tokens (plus the tool-result input) on every sync; there is no local HTTP attempt first. These calls also write no usage row (USE-1).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/knowledge/agent_fetch.py:22-27 — FETCH_PROMPT_TEMPLATE: 'Fetch the full text content from this URL … Return ONLY the raw document text'
  - src/kiro_crew/knowledge/agent_fetch.py:42-58 — fetch_url_content: response = await pool.send(prompt, timeout=FETCH_TIMEOUT)
  - src/kiro_crew/dashboard/handlers/knowledge.py:1655-1716 — connector path first; local_file re-ingest; otherwise 'Agent-assisted sync' → _background_agent_sync
  - src/kiro_crew/dashboard/handlers/knowledge.py:1761 — content = await fetch_url_content(url, pool)
- **Checked by:** read
- **Required outcome:** Agent sync first tries a governed local HTTP fetch (egress allow/deny policy and SSRF guard applied) and converts HTML to text locally; the model is used only when the local fetch is refused (auth, non-200, unsupported content), and then it is asked to fetch to a file / return a short status rather than echo the page.
- **Solution:**
  1. In _background_agent_sync (knowledge.py:1720-1770) try a local fetch first through the same egress governance the agent's fetch tools obey (platform/governance URL check — mind SEC-1..3) and the existing SSRF/private-address guard; size-cap the body; HTML→text via the existing ingestion readers.
  2. Fall back to fetch_url_content only on a refused or failed local fetch; change FETCH_PROMPT_TEMPLATE (agent_fetch.py:22-27) to have the model save the content to a scratch file and reply with the path, so the document is not paid for as output tokens.
  3. Record usage on the fallback (USE-1).
  4. Document the order in the knowledge doc in the same commit.
- **Done when:** pytest with a local aiohttp test server (bound to port 0): syncing a URL source whose page is reachable makes 0 pool.send calls and ingests the text; a 401 page makes exactly 1 pool.send call; a URL denied by the egress policy makes neither a local request nor a pool call.
- **Upstream:** none (state unverified)
- **Sources:** FIX_PLAN:KNOW-1, REVIEW_FINDINGS:N-lower(knowledge-agent-sync), FIX_PLAN-old:A-14

### USE-8 [20, default, effort S] Background maintenance usage rows written with an empty model — CONFIRMED
- **Verified claim:** background_turn (memory consolidation, auto-title, skill dedupe/merge) writes its usage row with model='' and relies on model_source; the resolver skips an 'auto' _resolved_model_id and only records 'auto' when the provider's _model is literally 'auto'. A kiro background session that inherits its default has _model='' and _resolved_model_id='auto' (kiro's currentModelId), so the row's model is '' and the Spend panel files it under 'unknown' — while the same function, two lines earlier, hands served_model ('auto' or the concrete id) to the crew log. run_bg_oneliner does not have this gap (it passes the served model). Traced on the real default path (factory provider → AcpSessionProvider → AcpSessionHandle → AcpRuntime): no node carries _model='auto', so _source_requests_auto is False. Measured with the real _resolve_model: (_resolved 'auto', _model '') → ''; (_model 'auto') → 'auto'; concrete → concrete. The reporter's 458/5,895 rows figure is not checkable here.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/llm_helpers.py:2323-2341 — crew log gets model=served_model; persist_token_record_async(key, "", usage, …, model_source=client)
  - src/kiro_crew/dashboard/handlers/usage.py:1310-1341 — read_effective_model skips 'auto' in _resolved_model_id/_model
  - src/kiro_crew/dashboard/handlers/usage.py:1344-1400 — _source_requests_auto checks _model == 'auto' only; _resolve_model returns '' otherwise
  - src/kiro_crew/acp/session_handle.py:1327 — self._model: str = ""; :3677-3682 _resolved_model_id = currentModelId; :3541-3551 served_model = _model or _resolved_model_id
  - test/fixtures/acp_frames/kiro/session.jsonl:3 — kiro currentModelId 'auto'
  - ran scripts/E/use8_model.py — kiro inherit (_resolved 'auto', _model '') → row model ''
  - src/kiro_crew/llm_helpers.py:2269-2271 — background_turn acquires via sessions.get_or_create(BACKGROUND_KEY[, agent]) (the factory provider, not the _bg one-liner handle)
  - src/kiro_crew/providers/acp.py:1234 + :1462-1464 — on the kiro runtime path configured_model 'auto' (== DEFAULT_MODEL) is never pushed, so the handle's _model stays '' (session_handle.py:1327)
  - …and 3 more in the verification results.
- **Checked by:** ran-new-script
- **Required outcome:** Every background usage row names the served model (a concrete id when the backend reports one, else 'auto'), never an empty string, consistent with the crew log entry for the same turn.
- **Solution:**
  1. llm_helpers.py:2332-2341: pass the served model computed for the crew log (str(getattr(client, 'served_model', '') or '').strip()) as the row's model, keeping model_source=client as the fallback — the same pattern run_bg_oneliner uses at :1817-1845.
  2. Optionally make _source_requests_auto (usage.py:1344) also treat _resolved_model_id == 'auto' with an empty _model as an Auto turn, so every caller gets 'auto' instead of ''.
  3. Review PR #12234 against this before writing new code.
- **Done when:** pytest: background_turn with a fake client (_model '', _resolved_model_id 'auto', usage 1.0 credit) writes a row whose model is 'auto'; with _resolved_model_id 'x' the row model is 'x'; no row has model ''. Also fix test_usage_blank_model_8531's fixture (or add a case) with handle._model='' and _resolved_model_id='auto', the real default shape.
- **Upstream:** #12225 PR #12234 PR #14320 (state unverified)
- **Changed from the source claim:** Mechanism confirmed at HEAD and narrowed: only background_turn rows (not run_bg_oneliner) and only when the backend reports 'auto' as the inherited default; the 'known' model at write time is served_model (often 'auto').
- **Second reader:** random-sample check: agreed.
- **Sources:** verify_needed:G34(#12225), verify_needed:G38, verify_needed:G41

### USE-11 [20, armed, effort S] Credit popover can show the wrong balance only for no-ARN accounts with another IDE login — PARTLY
- **Original claim:** Credit popover shows the wrong account's balance (corrected below)
- **Verified claim:** HEAD has an explicit identity invariant for the credit readout: whoami is taken before and after the credential read, a reading is dropped when the two disagree (account switch), an unproven reading (no identity either time) is dropped in favour of kiro-cli's own /usage scrape, and an identity is attached only when the API's profile ARN equals whoami's. Residual matching the issue: fetch_usage_limits tries several stored credentials (IDE cache first, then the kiro-cli store) and keeps whichever the API accepts; for accounts with no profile ARN (Builder ID / individual) nothing can prove which account answered, and the code deliberately 'publishes the numbers alone' — so with the IDE and kiro-cli signed into different no-ARN accounts the popover can show the IDE account's balance (unlabelled).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/handlers/sessions.py:890-910 — _identity_matches_account: 'fetch_usage_limits tries several candidate credentials (IDE cache first, then the kiro-cli store) … The ONLY accepted proof is a matching profile ARN'
  - src/kiro_crew/dashboard/handlers/sessions.py:1036-1056 — expected_arn from whoami passed to fetch_usage_limits (None for no-ARN accounts)
  - src/kiro_crew/dashboard/handlers/sessions.py:1086-1099 — 'The no-ARN (Builder ID) case carries no proof and publishes the numbers alone.'
  - src/kiro_crew/dashboard/handlers/sessions.py:1106-1116 — unproven identity → API reading dropped, /usage scrape used
- **Checked by:** read
- **Required outcome:** The credit popover never shows numbers from a credential other than the one kiro-cli is signed in with: when the account cannot be proven (no ARN) the reading comes from kiro-cli's own /usage panel, or the API is restricted to kiro-cli's own credential store.
- **Solution:**
  1. In _fetch_usage_bg (sessions.py:~1056-1100): when expected_arn is None, call fetch_usage_limits restricted to the kiro-cli credential store only (add a parameter in kiro_usage_api to skip the IDE cache), or treat the no-ARN API reading as unproven and use the /usage scrape (which 'describes the signed-in account by construction').
  2. Keep the ARN-match path unchanged.
  3. Update the account-modal doc text if behaviour changes for Builder ID users.
- **Done when:** pytest with fakes: whoami (no ARN) for account B, IDE cache token for account A accepted by the fake API → the published reading is from the scrape/kiro-cli store (B), never A; ARN accounts unchanged.
- **Upstream:** #11872 (state unverified)
- **Changed from the source claim:** Mostly guarded at HEAD (ARN proof, whoami bracketing, switch discard); the wrong-balance case survives only for no-ARN accounts with a different IDE sign-in.
- **Sources:** verify_needed:G25(#11872)

### WF-6 [20, armed, effort S] Task-runner loop check compares raw error text, so notice/label miss; retries are capped — PARTLY
- **Original claim:** Task-runner loop detection compares raw error strings (corrected below)
- **Verified claim:** The task runner's repeat-error detector compares task.error by byte equality (three sites), and run_tests keeps only the last 2000 chars of failing output, so volatile text (timestamps, durations, ports, PIDs, a shifting tail window) makes consecutive identical failures look different: the 'Possible loop' notice (2nd identical error) and the 'Loop detected' fail-fast (3rd) never fire. But the retries are NOT unbounded at HEAD: each step's loop is `while attempt < MAX_RETRIES + stop_recoveries` with MAX_RETRIES = 3; stall recoveries are capped by STOP_RECOVERY_MAX_RETRIES (= 3), process-death/compaction recoveries by MAX_RECOVERIES (= 2), dependency waits by 20, and re-plans by MAX_REPLAN (= 2) per run (reset when a run is resumed). Because the fail-fast threshold (3rd identical error) equals MAX_RETRIES, a normalized fingerprint mostly restores the notice and the accurate 'Loop detected' label rather than saving attempts; the bigger lever is a re-plan that does not repeat the same failing step.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/task_executor.py:1124, :1194, :1229 — `if previous_error and task.error == previous_error:`
  - src/kiro_crew/task_executor.py:1581-1582 — `if len(output) > 2000: output = "...\n" + output[-2000:]`
  - src/kiro_crew/task_executor.py:180-197 — _check_error_loop: notify at consecutive>=1, fail at consecutive>=2
  - src/kiro_crew/task_executor.py:642-643 — `while attempt < MAX_RETRIES + stop_recoveries: attempt += 1`; task_models.py:12 MAX_RETRIES = 3
  - src/kiro_crew/task_models.py:13-14 — MAX_RECOVERIES = 2; MAX_REPLAN = 2
  - src/kiro_crew/acp/types.py:479 + recovery/ladder.py:122 — STOP_RECOVERY_MAX_RETRIES = SESSION_RECOVERY_MAX_ATTEMPTS = 3
  - src/kiro_crew/taskrunner.py:1957-1968 — _try_replan bounded by _MAX_REPLAN; :1429 replan_count reset on resume
- **Checked by:** read
- **Required outcome:** Repeated failures are recognised by a normalized fingerprint (digits/hex/ports/durations/timestamps masked, keyed on FAILED/ERROR lines), shared with repeat_loop.py, so the loop notice and fail-fast fire on real loops; a re-plan does not re-issue a step whose fingerprint already failed MAX_RETRIES times.
- **Solution:**
  1. Add a pure helper error_fingerprint(text) (new small module or in repeat_loop.py) that masks volatile tokens and keeps FAILED/ERROR/assert lines; unit-test it.
  2. task_executor.py:1124/:1194/:1229: compare error_fingerprint(task.error) to the stored previous fingerprint (keep task.error itself for display).
  3. taskrunner._try_replan (:1957): pass the failed step's fingerprint into the replan spec and refuse a replan that reproduces the same fingerprint.
  4. Review PR #17187 first; it reportedly implements step 1-2.
  5. Update taskrunner.md in the same commit.
- **Done when:** pytest: three failures whose outputs differ only by a timestamp and a port trigger 'Possible loop' after the 2nd and FAILED 'Loop detected' after the 3rd; two genuinely different failures do not; error_fingerprint('took 1.23s on :8080 pid 4242') == error_fingerprint('took 9.87s on :9090 pid 77').
- **Upstream:** #17186 PR #17187 (state unverified)
- **Changed from the source claim:** Corrected: retries are bounded (3 attempts per step plus capped recoveries and ≤2 re-plans), so 'retries unbounded' is false; the byte-equality defect only defeats the notice and the fail-fast label, whose threshold equals the existing attempt cap.
- **Sources:** verify_needed:G10(#17186), verification_needed:Part3(#17186), verify_needed:G36

### MOD-11 [15, default, effort S] Model picker may offer an unentitled model on a cold gateway (deliberate); hiding is fixed — PARTLY
- **Original claim:** Model picker offers a model the account cannot run and hides one it can (corrected below)
- **Verified claim:** Both halves have dedicated code at HEAD. 'Hides one it can run': GET /api/models now serves each kiro catalog row under its model_id (display name kept separately), and the entitlement narrowing re-probes a suspect session/new snapshot (catalog_row_would_drop + maybe_refresh_available_models, 3 s shielded deadline, 503 model_list_revalidating) before dropping a row. 'Offers one it cannot run': the --list-models catalog is narrowed to the newest live kiro session's advertised list via model_is_unusable — but it deliberately FAILS OPEN when no live kiro session exists, the backend advertises nothing, or the advertised set does not intersect the catalog, so on a cold gateway (no session yet) the picker can still offer an unentitled model; an explicit pick of it is then refused at the wire (AcpModelUnavailable) rather than silently swapped. Whether the reporter's case is one of these fail-open windows cannot be decided without the issue body and a live account.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/handlers/agents.py:558-600 — _entitled_kiro_models: narrow catalog to live advertised; 'Fails open in every unknowable case … no live session in this namespace'
  - src/kiro_crew/dashboard/handlers/agents.py:620-640 — maybe_refresh_available_models(catalog_ids) before narrowing
  - src/kiro_crew/dashboard/handlers/agents.py:645-695 — `return models` on the fail-open branches
  - src/kiro_crew/dashboard/handlers/agents.py:1012-1030 — _fetch_kiro_catalog serves rows under model_id
  - src/kiro_crew/acp/runtime_models.py:170 — catalog_row_would_drop
  - docs/system-specs/common/model-selection.md:185-222 — documents the model_id fix and read-path revalidation
  - src/kiro_crew/acp/client.py:6826-6832 — explicit unusable pick raises AcpModelUnavailable
- **Checked by:** read
- **Required outcome:** The picker never offers a model the account cannot run without saying its entitlement is unverified, and never hides an entitled one.
- **Solution:**
  1. In the fail-open branches of _entitled_kiro_models (agents.py:645-695) mark the response (e.g. entitlement: 'unverified') instead of silently returning the full catalog; the frontend picker shows an 'availability not yet confirmed' hint (i18n catalog strings).
  2. Optionally kick one probe_advertised_models (existing single-flight probe) when no live kiro session exists so the next poll can narrow.
  3. Re-test upstream #11757 against v0.9.0 with the live check before further work.
- **Done when:** pytest: GET /api/models with no live kiro provider returns the catalog with entitlement:'unverified'; with a fake provider advertising ['a'] and catalog ['a','b'] returns only 'a' (+auto); vitest: picker renders the unverified hint when the flag is set.
- **Upstream:** #11757 (state unverified)
- **Changed from the source claim:** Most of the reported shape is addressed at HEAD (model_id row fix, read-path revalidation, live-list narrowing); the remaining 'offers' case is a deliberate fail-open when no live kiro session exists.
- **Sources:** verify_needed:G39(#11757)

### LOOP-14 [15, armed, effort S] Dynamic cards regenerate for every restored session on restart — CONFIRMED
- **Verified claim:** With dashboard.dynamic_dashboard_cards on (default OFF), every gateway start calls seed_open_sessions(), which notifies each restored slot with reason RESTORED; the card publisher's entries live only in memory, so every eligible slot (root session, has messages, crew log on, not exempt; up to capacity 128) gets a model card generation again even if nothing changed since the last card. There is no stored source hash to skip an unchanged session. Generation is bounded by the publisher budget (one at a time, 2 s debounce, ≥120 s per session, ≤60 per gateway hour), runs on resolve_model('background') and records usage via run_bg_oneliner.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/server_runtime/session_restore.py:58-59 — state._dynamic_cards.seed_open_sessions() after restore
  - src/kiro_crew/dashboard/card_lifecycle.py:896-903 — seed_open_sessions: for slot in state._slots.values(): self.notify(slot, RESTORED)
  - src/kiro_crew/dashboard/dynamic_cards.py:57-62 — CardBudget debounce 2.0, per_session 120.0, per_hour 60, capacity 128
  - src/kiro_crew/dashboard/dynamic_cards.py:103, :108-127 — entries OrderedDict in memory; a new entry (no prior payload) is created and queued
  - src/kiro_crew/dashboard/card_lifecycle.py:906-929 — _eligible: _derived_allowed, crew_log_enabled, is_root_session
  - src/kiro_crew/dashboard/card_lifecycle.py:1195-1205 — run_bg_oneliner(model=cfg.agent.resolve_model('background'), …)
  - src/kiro_crew/config/sections.py:2543-2549 — dynamic_dashboard_cards default False
- **Checked by:** read
- **Required outcome:** After a restart, a slot whose card source (redacted evidence window) is unchanged since its last stored card is not regenerated; its stored card is shown.
- **Solution:**
  1. Persist each generated card payload with sha256 of the evidence context built in _generate (card_lifecycle.py:~1150-1190) and the slot history key.
  2. On RESTORED (seed_open_sessions, :896-903), load the stored payload; if the freshly built evidence hashes to the stored value, publish the stored card and skip run_bg_oneliner.
  3. Reuse the existing published_revision/_retired ordering rather than a second ordering scheme.
- **Done when:** pytest with a fake run_bg_oneliner counter: generate cards for 3 slots, simulate a restart (new CardLifecycle over the same store) with unchanged histories → 0 calls; append a message to one slot → 1 call.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** none (bounds added: ≤60 generations/hour, 128 entries, root sessions only).
- **Sources:** FIX_PLAN:LOOP-14, REVIEW_FINDINGS:N-lower(dynamic-cards)

### UI-3 [15, armed, effort M] Dynamic cards generate with no viewer — CONFIRMED
- **Verified claim:** With dynamic cards on (default OFF), CardLifecycle.notify() queues a model card generation on session activity events for every eligible slot with no check that any browser tab is showing a card surface; the only limits are the publisher budget (one at a time, ≥120 s per session, ≤60 per gateway hour). Number re-binding for an existing card is model-free and is not the issue.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/card_lifecycle.py:951-984 — notify(): enabled/slot/eligibility checks, then publisher.notify(...) and self._start_worker(); no viewer/subscriber check
  - src/kiro_crew/dashboard/dynamic_cards.py:57-62 — budget: per_session 120 s, per_hour 60
  - src/kiro_crew/dashboard/card_lifecycle.py:1195-1205 — each generation is a run_bg_oneliner call
  - src/kiro_crew/dashboard/card_lifecycle.py:825-826 — class docstring: 'no browsing-triggered generation' (generation is activity-triggered, viewer-blind)
  - src/kiro_crew/config/sections.py:2543-2549 — dynamic_dashboard_cards default False
- **Checked by:** read
- **Required outcome:** With dynamic cards on and no card surface open in any tab, session events mark cards dirty but trigger no model call; when a surface opens, only the changed sessions are regenerated.
- **Solution:**
  1. Add a WS subscribe_cards / unsubscribe_cards pair (dashboard WS handler) that keeps a per-gateway subscriber count; the card panel/strip subscribes on mount (website component, i18n-neutral).
  2. CardLifecycle.notify (card_lifecycle.py:951-984): when the count is 0, mark the entry dirty (keep revision bump) but do not start the worker; on the first subscribe, start the worker for dirty entries only.
  3. Keep the derived (model-free) refresh as is.
  4. Document in learn-cron-dashboard.md (cards) in the same commit.
- **Done when:** pytest: flag on, no subscriber, 20 notify() calls across 5 slots → 0 _generate calls; subscribe → exactly the 5 dirty slots generate (subject to budget, fake clock); vitest: card panel mount sends subscribe_cards and unmount sends unsubscribe_cards.
- **Upstream:** none (state unverified)
- **Sources:** FIX_PLAN:UI-3, REVIEW_FINDINGS:Part6/UI-3

### USE-4 [15, armed, effort M] API-key auth skips the account credit/limit readout by design; per-turn usage unverified — PARTLY
- **Original claim:** API-key accounts see no credit/usage data (corrected below)
- **Verified claim:** Under API-key authentication the ACCOUNT credit/limit readout is deliberately skipped: the refresh detects whoami account_type 'ApiKey', publishes {available: false, reason: 'api_key_auth'} and the account modal shows 'Credit usage isn't available for API key authentication' — because GetUsageLimits needs an SSO/OIDC bearer token such accounts do not hold, and the /usage text scrape needs the same sign-in. The cited _unavailable_reason (sessions.py:317) is the different sign-in-required path. Not established: that API-key users see NO usage data at all — per-turn usage rows come from the session's own billing metadata (credits per turn), which this skip does not touch; whether kiro-cli emits per-turn credits under API-key auth needs a live check.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/handlers/sessions.py:1019-1035 — 'Fail fast on API-key auth … such accounts hold no SSO/OIDC bearer token'; _publish_usage({'available': False, 'reason': 'api_key_auth'})
  - src/kiro_crew/dashboard/handlers/sessions.py:317-339 — _unavailable_reason returns signin_required for auth-class failures only
  - website/src/components/KiroAccountModal.tsx:514 — 'components.kiroAccountModal.credit_usage_api_key_auth'
  - website/src/i18n/locales/en.manual.json:1772 — "Credit usage isn’t available for API key authentication"
  - src/kiro_crew/llm_helpers.py:1811-1845 and dashboard/handlers/usage.py:1617-1700 — per-turn rows are built from provider turn usage, independent of the account-limits fetch
- **Checked by:** read
- **Required outcome:** API-key accounts see whatever spend Kiro Crew can measure locally (per-turn credits from the usage store: today/7-day totals per surface) in place of the account pill, with the reason the account limit is unavailable.
- **Solution:**
  1. Confirm with the live check that per-turn credits are reported under API-key auth.
  2. If yes: when reason == 'api_key_auth', have the usage pill/modal fall back to the local usage-store totals (existing usage aggregation endpoint) and label them 'measured locally; account limit unavailable for API keys' (i18n catalog).
  3. If not: document the gap in the usage doc and leave the current message.
  4. Pairs with USE-1 (all paths recorded) and USE-6 (estimate vs authoritative).
- **Done when:** pytest: with whoami account_type 'ApiKey' and two usage rows today totalling 3.5 credits, the usage status endpoint returns available:false, reason:'api_key_auth', local_credits_today:3.5; vitest: the modal renders the local total plus the reason.
- **Upstream:** #17443 (state unverified)
- **Changed from the source claim:** Cited lines point at the sign-in path; the API-key skip is sessions.py:1019-1035. It is a deliberate, labelled skip of the ACCOUNT limit only; 'no data at all' is unproven.
- **Sources:** verify_needed:G8(#17443), verification_needed:Part3(#17443)

### WF-5 [15, armed, effort S] opts.effort (and nudge) ignored by shipped agent_fn — CONFIRMED
- **Verified claim:** ctx.agent(effort=…, nudge=…) values reach agent_fn's opts dict, but neither shipped adapter (agent_exec.build_agent_fn, agent_pool.build_pooled_agent_fn) reads opts['effort'] or opts['nudge'], so a script cannot lower (or raise) a step's reasoning effort and the per-call nudge dict is a no-op; steps run at the factory's effort (the chat effort, see MOD-1). The separate ctx.nudge() method does work. workflows.md records this as an open question.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/workflows/runner.py:399-444 — agent(…, effort=None, …, nudge=None) put into opts {'effort': effort, 'nudge': nudge}
  - grep 'effort|nudge' in src/kiro_crew/workflows/agent_exec.py and agent_pool.py — only 'best-effort' comments; opts.get('effort'/'nudge') never read
  - src/kiro_crew/workflows/agent_exec.py:179 and agent_pool.py:371 — only opts.get('model'), agent, cwd, session are read
  - docs/system-specs/modules/workflows.md:157-165 — 'neither shipped agent_fn … reads them, so today they have no effect. Either wire them or document them as reserved.'
- **Checked by:** read
- **Required outcome:** opts.effort is honoured per step (passed as reasoning_effort_override to get_or_create on both cold and pooled paths, keying the pool identity on it), and opts.nudge is either wired to the nudge port or documented as reserved.
- **Solution:**
  1. agent_exec.py:~175-185: pass reasoning_effort_override=opts.get('effort') or '' to sessions.get_or_create.
  2. agent_pool.py:330-372: add effort to the pool identity key (agent, model, effort, cwd) and pass it at worker creation, so warm workers are not shared across efforts.
  3. Validate the value with is_valid_effort; drop invalid with a warning event.
  4. Per-call nudge: wire to the runner's nudge port or document as reserved; update workflows.md (remove the open question) in the same commit.
- **Done when:** pytest with a fake SessionManager capturing get_or_create kwargs: ctx.agent('x', effort='low') yields reasoning_effort_override='low' on the cold path and on the pooled path, and two steps with different efforts use two distinct pool workers.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** none (nudge included; the spec already lists both as an open question).
- **Sources:** FIX_PLAN:WF-5, REVIEW_FINDINGS:W8

### MOD-10 [10, default, effort S] Background one-liners share one runtime; only its spawn and session/new start serialize — PARTLY
- **Original claim:** All background callers serialize behind one shared runtime (corrected below)
- **Verified claim:** Background one-liner callers share ONE _bg runtime process, but they serialize only at two points: (a) the _bg_runtime_lock while the runtime is checked or (re)spawned, and (b) a separate one-permit _bg session/new start gate that is released as soon as session/new answers. After that each caller holds its own ephemeral session on the multiplexed runtime and its prompt streams concurrently with the others; there is no per-session semaphore that queues suggestions, titles, labels and cards behind each other for the whole call. The throughput ceiling is therefore session-start latency (one start at a time) plus whatever concurrency kiro-cli gives one process, not full serialization. The one-permit gate is a deliberate design (so one-liners never take a user's start permit).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/session_background.py:664-852 — `async with self._bg_runtime_lock:` covers runtime check/spawn only; `selected` pinned under the lock
  - src/kiro_crew/session_background.py:856-863 — create_session(agent=runtime_agent, bg_runtime_start=True) runs OUTSIDE the lock
  - src/kiro_crew/acp/runtime.py:6967-6970 — 'bg_runtime_start=True … takes the permit from the loop's separate one-permit _bg gate'
  - src/kiro_crew/acp/runtime.py:7174-7180 — gate = bg_runtime_session_start_gate(); 'released exactly once: on success right after the answer'
  - src/kiro_crew/llm_helpers.py:1651-1849 — each run_bg_oneliner owns its handle and destroys it in finally
- **Checked by:** read
- **Required outcome:** Maintainers know the real ceiling (one session/new at a time on _bg, prompts concurrent) before adding background callers; if contention matters, it is measured (gate queue_wait_ms is already reported via notify_start_queue) rather than assumed.
- **Solution:**
  1. Document in session.md / acp-client.md that _bg one-liners serialize on session/new only (one-permit gate) and run prompts concurrently.
  2. If a measurement shows start-gate queueing hurts titles, give titles start_priority=FOREGROUND (already supported) rather than adding permits.
  3. Decision for maintainers: whether link labels/cards (UI-1/UI-3) should be throttled so they do not occupy the start gate ahead of titles.
- **Done when:** Doc updated; a test with a fake runtime whose session/new blocks on an Event shows a second get_bg_session waits for the first start but two acquired handles can prompt concurrently.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** Overstated: callers do not queue on a per-session semaphore for the whole call; only the runtime spawn lock and the one-permit session/new gate serialize them.
- **Sources:** verify_needed:Z3

### UI-4 [10, default, effort S] Tips run maybe_refresh before the cadence check — CONFIRMED
- **Verified claim:** GET /api/tips/next calls maybe_refresh() (a background run_bg_oneliner tip generation whenever the stored tips are older than 6 h) BEFORE it checks for an outstanding offered tip or the cadence gate, so a request that will return {tip: null} — cadence closed — or re-serve an already-offered tip can still start a generation. The client fires the GET 10 s into a running turn, at most every ~20 min. Net cost is bounded by the 6 h refresh interval (≤4 generations/day while tips are enabled, the default), on the tips_model ('auto' by default, see MOD-3).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/tips.py:50 — _REFRESH_INTERVAL_SECS = 6 * 60 * 60
  - src/kiro_crew/tips.py:1157-1167 — maybe_refresh: age check only, then create_task(refresh_tips)
  - src/kiro_crew/tips.py:1197-1198 — '# Trigger background refresh if stale' / await maybe_refresh(state, cache) — before the gate
  - src/kiro_crew/tips.py:1209-1225 — offered-tip re-serve, then cadence_open check returns {tip: None}
  - src/kiro_crew/tips.py:1117-1124 — refresh uses run_bg_oneliner(model=tips_model)
  - website/src/components/TipCard.tsx:281-302 — client gate ≤20 min, 10 s timer while a turn runs, useQuery(['tips-next'])
  - src/kiro_crew/config/sections.py:3176-3177 — tips_enabled default True; :3206-3207 tips_cadence_hours default 6.0
- **Checked by:** read
- **Required outcome:** Tips regenerate only when a tip could be shown (cadence gate open, no outstanding offered tip) and the inputs (context + catalog + dismissed set) changed since the last generation.
- **Solution:**
  1. tips.py api_tips_next: move `await maybe_refresh(state, cache)` below the offered-tip re-serve and the cadence check (only when cadence_open).
  2. In refresh_tips/generate_tips store sha256(context + catalog ids + dismissed ids) with the tips and skip the model call when unchanged (bump last_generated).
  3. No frontend change needed.
- **Done when:** pytest with a fake run_bg_oneliner counter: GET with the cadence closed and stale tips → 0 calls; GET with an offered tip outstanding → 0 calls; GET with cadence open and stale tips → 1 call; a second stale refresh with unchanged inputs → 0 calls.
- **Upstream:** none (state unverified)
- **Changed from the source claim:** none (bounded to one generation per 6 h).
- **Sources:** FIX_PLAN:UI-4, REVIEW_FINDINGS:Part6/UI-4

### USE-9 [10, default, effort S] Applied credit multiplier not in usage telemetry — CONFIRMED
- **Verified claim:** Usage rows carry provider, model, token counts, cost, credits, turns, duration, surface, agent, context and stop_reason, but no credit/rate multiplier. The multiplier exists only on the model-picker path: kiro catalog rows' rate_multiplier is passed through to the frontend as rateMultiplier for a price badge. Credits recorded per turn are already the billed (multiplied) amount, so the gap is attribution — a row cannot say which multiplier produced its credits — not missing spend.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/handlers/usage.py:1474-1500 — _build_token_record fields: _type, ts, slot, app, provider, model, input, output, cache_create, cache_read, cost, credits, turns, duration_ms, surface, agent, context_used, context_window, stop_reason (no multiplier)
  - grep 'multiplier' over src/kiro_crew (non-backoff) — no usage/telemetry field
  - website/src/providers/adapters/acp.ts:166-182, :398 — rate_multiplier → rateMultiplier for the picker only
  - src/kiro_crew/builtin_skills/llm-council/SKILL.md:71 — 'rate_multiplier = credit cost'
- **Checked by:** read
- **Required outcome:** Each usage row records the rate multiplier advertised for the served model at the time of the turn (or null when unknown), so spend can be attributed to model price changes.
- **Solution:**
  1. Keep a small map model_id → rate_multiplier from the cached catalog (the GET /api/models kiro catalog cache in dashboard/handlers/agents.py).
  2. In _build_token_record (usage.py:~1474) add an additive 'rate_multiplier' field looked up by the resolved model (None when not found); additive fields are already the row's convention.
  3. Show it in the usage dashboard tooltip (i18n). Check PR #17663 first.
- **Done when:** pytest: with the catalog cache holding {'m1': 2.2}, persist_token_record for model 'm1' writes rate_multiplier 2.2; for an unknown model writes null; existing readers ignore the new field.
- **Upstream:** #11214 PR #17663 (state unverified)
- **Changed from the source claim:** none (clarified: credits are already post-multiplier; the gap is attribution).
- **Sources:** verify_needed:G25(#11214)

### UI-6 [8, default, effort S] Chat side panel polls /api/workflows/runs every 2.5 s while open, any tab or run state — PARTLY
- **Original claim:** Chat side panel polls workflow runs every 2.5 s regardless of tab (corrected below)
- **Verified claim:** The chat side panel (ActivityViewer) polls GET /api/workflows/runs every 2.5 s whenever the panel is open (`enabled: open`), whichever of its tabs is active and whether or not any run of this slot is running; React Query's default pauses it while the browser tab is in the background. Each poll lists every run and awaits a per-run scope check before serializing off-loop, so the cost grows with the number of stored runs. Correction: the endpoint is not a 'deliberately slow backstop' — the deliberately slow (15 s) backstop is the separate heal-tick reconcile in hooks/websocket/workflowRuns.ts, which makes no request when no row is running. No model calls are involved.
- **Evidence (at `397f4be`):**
  - website/src/pages/chat/ActivityViewer.tsx:1001-1006 — useQuery(['workflow-runs'], queryFn: api.workflowRuns, enabled: open, refetchInterval: 2500)
  - website/src/pages/chat/ActivityViewer.tsx:1007-1008 — results filtered to this slot after fetching all runs
  - website/src/hooks/websocket/workflowRuns.ts:11-16 — WORKFLOW_HEAL_MS = 15000, 'makes no request at all while no row is running' (the slow backstop)
  - src/kiro_crew/dashboard/handlers/workflows.py:577-593 — api_workflow_runs: for snapshot in svc.list_runs(): await _run_scope_refusal(...) per run
- **Checked by:** read
- **Required outcome:** The side panel polls workflow runs only while its Workflows view is visible and this slot has a running run (otherwise relies on workflow_run_event frames plus the 15 s heal tick).
- **Solution:**
  1. ActivityViewer.tsx:1001-1006: enabled: open && tab === 'workflows' (or the tab that renders runs); refetchInterval as a function returning 2500 only while wfRunningCount > 0, else false.
  2. Rely on the existing workflow_run_event WS frames for progress; keep the 15 s heal tick.
  3. Optional backend: let GET /api/workflows/runs accept ?session_key= so the panel fetches only its slot's runs. Read website/AGENTS.md; no new strings.
- **Done when:** vitest with fake timers: panel open on the Links tab → 0 calls to api.workflowRuns over 10 s; Workflows tab with one running run → a call every 2.5 s; after the run turns terminal → polling stops.
- **Upstream:** #15234 (state unverified)
- **Changed from the source claim:** Poll confirmed; 'deliberately slow backstop endpoint' is a misreading (the slow backstop is the 15 s heal tick); the poll is gated on the panel being open, not on its tab or on running runs.
- **Sources:** verify_needed:G40(#15234)
