# Kiro Crew validated findings: Context window, compaction, memory and sessions

Part of the validated findings set; start at [00-index.md](00-index.md). Code verified at commit `397f4be`; sources snapshot 2026-10-07 09:53:08 UTC.

## 4. Context window, compaction, memory and sessions

### CTX-3 [50, default, effort S] Preferences block has no startup cap — CONFIRMED
- **Verified claim:** Confirmed and re-measured at HEAD. At session start, MemoryStore.get_context(include_activity=False) returns the whole preferences.md. The only bound is the model-safe protected ceiling (caps.protected_context, 500K chars on the 1M reference), and past it the head is kept with a notice. Re-measured on throwaway homes with 200 lessons: 50 prefs give a 6,264 B block; 400 prefs give 47,656 B (first turn 122,211 B); 1,500 prefs give 178,412 B (first turn 252,977 B). Two corrections. (1) add_preference has no production caller. The file actually grows through history consolidation's whole-file 'preferences_update', which runs by default on V1 because memory.migrated defaults to False, and through the dashboard Save. (2) The missing cap is a documented invariant, not an oversight: context-management.md:93 says 'injected complete, not capped', and memory-skills-hooks.md:5820 says 'Preferences are never trimmed to make room for anything else'. The pref.* semantic twin already has the 12,700-char _PREFS_STARTUP_CAP; budget.py:59-62 records a 47.7K block measured on one real store. Capping the markdown file is therefore a take-away change.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/memory.py:974-979 — '{_cap_text(prefs, prefs_cap) if include_activity else prefs}' (startup passes include_activity=False -> raw file)
  - src/kiro_crew/context_assembly/store_admission.py:223-256 — get_context(..., include_activity=False, prefs_startup_cap=caps.prefs_startup); only bound is 'room = caps.protected_context - protected_so_far', head kept with notice
  - src/kiro_crew/context_assembly/budget.py:205-208 — protected_context = max(99_000, window*4.0*0.125) = 500,000 on the 1M reference
  - src/kiro_crew/context_assembly/budget.py:56-63 — _PREFS_STARTUP_CAP = 12_700 applies to pref.* semantic rows only; '47.7K measured on one real store'
  - src/kiro_crew/memory.py:420-425 — add_preference only blocks exact substrings; grep finds no production caller
  - src/kiro_crew/history_consolidation.py:1507, :1916-1943 — V1 consolidation writes the LLM's COMPLETE preferences_update when not migrated; config/memory_sections.py:273-276 migrated default False
  - docs/architecture/context-management.md:93-94 — 'Preferences — injected complete, not capped'
  - docs/system-specs/modules/memory-skills-hooks.md:5820 — 'Preferences are never trimmed to make room for anything else'
  - …and 1 more in the verification results.
- **Checked by:** ran-existing-script (copied to merge/scripts/B/scale_B.py with homes under merge/homes/B)
- **Required outcome:** The startup preferences block is at most _PREFS_STARTUP_CAP (12,700 chars), the same allowance as the pref.* rows. It keeps the head, cut at a line boundary, and ends with the existing '[Context budget: omitted N chars of preferences …; read <file> for the complete file.]' notice. The operator gets a warning once the file passes the cap. The spec and the doc state the new rule. The user confirms the cap, because this changes a documented 'never trimmed' guarantee.
- **Solution:**
  1. Take-away first. Read docs/system-specs/common/take-away-changes.md and grep docs/decisions/ (no entry on preferences today). Get the user to confirm the 12,700 figure, and list the Reader: preferences.md users with large files.
  2. In store_admission.py:233-256, compute the room as min(caps.prefs_startup, caps.protected_context - protected_so_far) for the markdown block. Reuse the existing head-plus-notice code, and cut at the last newline before the room.
  3. Warn at write time: in memory.write_preferences (the shared write seam for consolidation, the dashboard Save and add_preference), log a warning once when the content exceeds _PREFS_STARTUP_CAP. Have the dashboard memory handler (dashboard/handlers/memory.py:446) return a size hint.
  4. Optionally tell the consolidation prompt (history_consolidation.py:1708) to merge near-duplicates when the file exceeds the cap.
  5. In the same commit, update context-management.md:93-94, memory-skills-hooks.md:5820 and the :5872/:5929 rows.
- **Done when:** A test seeds a throwaway KIROCREW_HOME with 1,500 preference lines (MemoryStore.write_preferences), runs ContextBuilder().build_message('fix the failing pytest', True, session_key='dashboard:x'), and asserts the preferences block is ≤ 12,700 chars + notice, carries '[Context budget: omitted' and the file path, and keeps the first line. With 50 prefs the block is byte-identical to today.
- **Changed from the source claim:** Severity is lowered from 60 to 50. 1,500 entries is synthetic; the realistic growth path is V1 consolidation's whole-file rewrite, and a 47.7K real-store reading exists for the semantic twin. add_preference is not the growth path, because it has no production caller. Missing from the source: the uncapped block is a documented 'never trimmed' invariant, so the fix is a take-away change that needs user confirmation. The measured numbers hold at HEAD.
- **Sources:** FIX_PLAN:CTX-3, REVIEW_FINDINGS:M1

### CTX-7 [50, default, effort M] Default subagents and workflow steps pay the full main-agent first turn — CONFIRMED
- **Verified claim:** Re-measured at HEAD, and the mechanism is confirmed. A spawn with no agent= runs as agent = info.agent or execution.template_id. The template comes from the parent's execution record, or from get_agent_selection(parent) when no record exists, so a default dashboard chat's child gets the default (kirocrew) contract. build_message then assembles the full new-session context. Measured on an empty home: 49,325 chars for agent=None, against 4,721 for kirocrew-worker and 4,719 for kirocrew-lite. Turning off every context group saves only 395 chars, because the roughly 40 KB agent prompt is not in a switchable group. Workflow pool workers re-arm _is_new = True in reset() before new_conversation(). Every reused step therefore rebuilds the full session start through workflow_memory.prompt → build_message(is_new), using default_agent=None, which is the default spec and its full tool set. One sub-claim (N10) is overstated: chat-thread replies cold-start a new kiro-cli session on every reply under a read-only derivative of the base spec, which keeps tools, mcpServers, resources and prompt. Crew injects only the short build_thread_message envelope, not the 40 KB session context, so the fixed cost per reply is the base spec's tool schemas plus its prompt.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/subagent_manager/run.py:2105 — agent = info.agent or execution.template_id
  - src/kiro_crew/subagent_manager/admission/gate.py:130-150 — no record: inherited = get_agent_selection(parent_session_key); template_id=agent or inherited[1]
  - src/kiro_crew/subagent_manager/run.py:2383-2386 — no named agent: message = _SYSTEM_PREFIX + raw_task (full build_message follows at :2443)
  - src/kiro_crew/mcp_tools/spawn.py:262, :728 — include_memory defaults True
  - src/kiro_crew/workflows/agent_pool.py:188 — reset(): self._is_new = True before new_conversation()
  - src/kiro_crew/workflow_memory.py:360-394 — prompt() → context.build_message(text, is_new, key, agent=agent, runtime_source='workflow', ...)
  - src/kiro_crew/workflows/service.py:794-800 — build_pooled_agent_fn(...) with no default_agent (None → default spec)
  - src/kiro_crew/dashboard/chat_threads.py:932-954 — thread turn publishes '<base>--readonly' spec and get_or_create()s a fresh session every reply ('A thread turn never reuses a session')
  - …and 3 more in the verification results.
- **Checked by:** ran-existing-script (copied to merge/scripts/B; empty home under merge/homes/B)
- **Required outcome:** A subagent spawned with no named agent, and a ctx.agent() workflow step with no agent=, get a first turn of at most about 10 KB. They run a base tool set without cron, workflow or monitor tools. Memory and lessons are included only when the call opts in. An explicit agent= still gets the full agent.
- **Solution:**
  1. Prerequisites: SPEC-2 (the prompt reaches the session once) and SPEC-3. Do not default to kirocrew-worker: it is the default tool set plus @kirocrew-work (agent_materialization/worker_agent.py:3, :527).
  2. Add a derived 'kirocrew-step' spec next to kirocrew-lite in agent_materialization/ (service_agents.py), with a short step contract and a base tool set. In run.py:2105, default the agent to it only when info.agent is empty AND the inherited template is the default kirocrew contract. A parent on a custom agent keeps inheriting that agent, which keeps the documented session_control inheritance rule intact. Identity is positive: compare against the kirocrew constant, not 'not custom'.
  3. Default include_memory=False for spawns with no named agent (mcp_tools/spawn.py:262 schema default and :728). Advertise it in the tool description. This is a take-away change: read take-away-changes.md and get user confirmation.
  4. Workflows: pass default_agent='kirocrew-step' from workflows/service.py:794, and add opts.memory, false by default, read by workflow_memory.prompt (:360).
  5. Chat threads (separate PR): derive the read-only thread spec from kirocrew-step rather than the base agent (chat_threads.py:932), or keep one warm session per (agent, project, spec digest).
  6. Update subagent.md, workflows.md and context-management.md §3 in the same commit.
- **Done when:** A ContextBuilder/run.py unit test: a spawn with no agent builds a first turn of ≤ 10,000 chars, and its spec lists no cron_*, workflow_* or monitor_* tools. agent='kirocrew' still builds the full ~49K-char turn. A workflow pool reuse test asserts the second step's prompt is ≤ 10,000 chars.
- **Changed from the source claim:** The figures hold for the default subagent (49.3K vs 49.2K chars; the 395-char group saving matches exactly). kirocrew-worker now measures 4.7K, not 7.0K. Template resolution is now traced: the parent's record or get_agent_selection. The chat-thread sub-claim is corrected: Crew does not inject the session context there, and the per-reply cost is the cold start plus the base spec's tools and prompt. Severity is lowered from 55 to 50.
- **Sources:** FIX_PLAN:CTX-7, REVIEW_FINDINGS:C4, REVIEW_FINDINGS:W5, REVIEW_FINDINGS:H-P8, REVIEW_FINDINGS:M8, REVIEW_FINDINGS:N10, verify_needed:O4, verify_needed:corr-W5, verify_needed:X14, verify_needed:design-rec-3, verify_needed:delta-3

### CTX-1 [45, pinned, effort S] Compaction triggers only at 70% of the window; ~700k tokens only on sessions served at 1M — PARTLY
- **Original claim:** Compaction is percentage-only (fires ~700k tokens on a 1M window) (corrected below)
- **Verified claim:** Confirmed in code: the automatic compaction trigger checks only a percentage. _compaction_gate_decision returns 'below_threshold' when pct < effective_autocompact_pct(key), whose default is DEFAULT_AUTOCOMPACT_PCT = 70.0. No absolute-token ceiling exists anywhere. The same gate serves check_context_usage and compact_if_needed, and the CLI REPL has its own percentage-only copy. pct is kiro-cli's own percentage of the window it serves, so compaction fires at 0.7 x that window: about 700k tokens on any session served at 1M. The registry lists claude-opus-4.6, claude-sonnet-4.6 and claude-opus-4.8 at 1M. model_registry.py:105-108 notes that kiro actually serves the sonnet/haiku aliases at 200K, so the registry-1M figure is reliable only for opus-4.6 and opus-4.8. Not established: that the DEFAULT model=auto runs at 1M (CTX-20 needs a live check). If auto is served at 200k, the trigger already fires at about 140k and the cap changes nothing on the default path. Two corrections to FIX_PLAN's fix. (a) autocompact_pct is not forwarded to kiro-cli: the sections.py:3670 comment describes Crew's own trigger, and no code writes a kiro-cli compaction setting. (b) provider.context_used_tokens() is not an independent count on kiro-cli 2.10+, which sends only a percentage. It is backfilled as pct x the registry window of the resolved id, and an unresolved 'auto' resolves to 200k. A cap built on it would therefore read 140k at 70% of a real 1M window and never fire. A per-slot override (slot.autocompact_pct) also exists, and the cap has to combine with it.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/config/sections.py:185 — DEFAULT_AUTOCOMPACT_PCT = 70.0
  - src/kiro_crew/config/sections.py:1902-1908 — autocompact_pct field, help 'Context usage percentage at which auto-compaction triggers (5-90)'; no token field beside it
  - src/kiro_crew/session_compaction.py:521-522 — 'if pct < self.effective_autocompact_pct(key): return "below_threshold"' (the only threshold rung)
  - src/kiro_crew/session_compaction.py:285-289 — check_context_usage: pct = provider.context_usage_pct()
  - src/kiro_crew/session_compaction.py:467-471 — effective_autocompact_pct: per-key override else cfg.session.autocompact_pct
  - src/kiro_crew/cli_chat.py:1123 — REPL: needs_compact = pct >= cfg.session.autocompact_pct
  - src/kiro_crew/dashboard/chat_handlers.py:7538, dashboard/channel_slots.py:627 — per-slot set_autocompact_pct override
  - src/kiro_crew/config/sections.py:3670-3679 — comment calls it 'the backend autocompactor' but grep finds no write of a kiro-cli compaction setting (no autoCompact/compaction_threshold outside Crew's own gate)
  - …and 4 more in the verification results.
- **Checked by:** ran-new-script
- **Required outcome:** Automatic compaction fires at whichever comes first: autocompact_pct of the served window (the per-slot override wins as today), or an absolute session.autocompact_max_tokens. The default is about 200k; it is configurable, and 0 turns it off. The absolute arm reads only an authoritative token count. That is a usage_update, or pct x a window from the live/kiro-list tier. It never reads a figure backfilled from the static 'auto' row. The context warning follows the same rule. cc_managed and harness-managed (KAS) backends are unchanged.
- **Solution:**
  1. Add autocompact_max_tokens: int next to autocompact_pct (config/sections.py:1902), with help text, and a 0..window clamp in the loader beside the AUTOCOMPACT_PCT_MIN/MAX clamp (sections.py:3680). Add a row to config.md's key table. This is a behaviour change on existing installs: compaction becomes more frequent and loses detail. Treat it as a take-away change, read docs/system-specs/common/take-away-changes.md, and get the default confirmed by the user.
  2. In SessionCompaction._compaction_gate_decision (session_compaction.py:521), keep the percentage rung and add an absolute rung. The rung reads provider.context_used_tokens() only when the stats are authoritative: context_tokens_from_usage is True, or the window came from model_registry.window_source(resolved_id) in {'kiro-list'} or a live usage_update. Otherwise it skips the rung and logs the skip once. Return None (proceed) when used >= max_tokens > 0.
  3. Mirror the rung in cli_chat.py:1123 and in the warn arm (session_compaction.py:304-309), so the warning fires CONTEXT_WARN_MARGIN_PCT of the cap below it.
  4. Drop FIX_PLAN step 4: nothing forwards autocompact_pct to kiro-cli.
  5. Land this after CTX-20's live check, or together with it. If auto is served at 200k, the cap is cheap insurance only for sessions pinned to 1M models.
  6. Update session.md ('Context compaction' bullet and the check_context_usage API row) and config.md in the same commit.
- **Done when:** In test_session_compaction (fake provider, no clock): window=1_000_000 with context_tokens_from_usage=True and used=210_000 (21%) returns None (compacts); used=150_000 returns 'below_threshold'; window=200_000, used=141_000 (70.5%) compacts through the percentage rung; autocompact_max_tokens=0 disables the absolute rung; a pct-only reading backfilled from model_id='auto' (context_tokens_from_usage False, window from the static registry) does not trigger the absolute rung. cli_chat has a matching test.
- **Upstream:** #16143 #15482 PR #15484 (state unverified)
- **Changed from the source claim:** Severity 75/default is lowered to 45/pinned, because only the mechanism is verified at HEAD. It rises to 75/default if CTX-20's live check shows model=auto served at 1M. FIX_PLAN step 2's data source is wrong as written: context_used_tokens() is pct x registry window on kiro-cli 2.10+, and 'auto' resolves to 200k there. Step 4 does not apply: nothing forwards the threshold to kiro-cli. The per-slot override path was not mentioned.
- **Sources:** FIX_PLAN:CTX-1, REVIEW_FINDINGS:S3, REVIEW_FINDINGS:Part5b§5.1, verify_needed:X11, verify_needed:G21(#16143), verify_needed:G38, verify_needed:G43, verify_needed:G44

### CTX-15 [45, armed, effort S] Drained app context has no total cap (up to ~2M chars in one turn) — CONFIRMED
- **Verified claim:** Confirmed and measured with the real helpers. POST /api/chat/slots/{slot}/context accepts entries of up to 40,000 chars (_MAX_CONTEXT_CONTENT) into a FIFO of 50 (_MAX_PENDING_CONTEXT). The per-source cap of 10 is skipped for an empty source ('a sourceless context injection is intentionally bucket-free'). An omitted maxAge means the entry never expires. When the queue is full, append_pending_context evicts the oldest entry; the cap is checked at enqueue time, not at drain. drain_pending_context concatenates every live entry into the turn's message text (chat_runner.py:10759-10761: message = _ctx_prefix + message). That text is the user's turn, outside admit_background and the 33K budget. Probe: 60 sourceless 40,000-char entries leave 50 entries, and the drain returns 2,013,500 chars for a single turn. One correction to 'body read unbounded': read_bounded_json(request, max_bytes=None) has no per-endpoint cap, but the app-wide aiohttp client_max_size of 60 MB still bounds the read.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/chat_handlers.py:10303-10304 — _MAX_CONTEXT_PER_SOURCE = 10, _MAX_CONTEXT_CONTENT = 40000
  - src/kiro_crew/dashboard/chat_handlers.py:10505-10506 — _source_cap_reached: 'if not source: return False'
  - src/kiro_crew/dashboard/chat_handlers.py:10633-10637 — '/context keeps empty-source-uncapped … a sourceless context injection is intentionally bucket-free'
  - src/kiro_crew/dashboard/chat_handlers.py:10614 — read_bounded_json(request, max_bytes=None); src/kiro_crew/dashboard/server.py:1604-1605 — web.Application(client_max_size=60 * 1024 * 1024)
  - src/kiro_crew/dashboard/state.py:1793 — _MAX_PENDING_CONTEXT = 50; src/kiro_crew/dashboard/slot_buffers.py:684-700 — evict oldest at append
  - src/kiro_crew/dashboard/chat_turn/turn_context.py:106-152 — drain concatenates all live entries, no total cap
  - src/kiro_crew/dashboard/chat_runner.py:10759-10761 — _ctx_prefix = drain_pending_context(slot); message = _ctx_prefix + message
  - measured: 50 entries kept of 60; drained 2,013,500 chars
- **Checked by:** ran-new-script
- **Required outcome:** One drain injects at most a fixed total (about 60-80K chars), newest first. It ends with an omission notice naming the sources dropped, and the dropped entries are never silently lost: they are counted and logged. Sourceless entries get a default per-turn bound and a default maxAge. The context endpoint reads its body with an explicit byte cap just above the 40K content limit.
- **Solution:**
  1. Add _MAX_DRAINED_CONTEXT_CHARS (for example 64_000) beside _MAX_PENDING_CONTEXT (dashboard/state.py:1793). In drain_pending_context (turn_context.py:136-152), walk the live entries newest first, keep whole entries until the next would cross the total, and append '[Background context omitted: N entries from <sources>]'.
  2. Give sourceless entries a default maxAge, for example 30 min, in _build_pending_context_entry (chat_handlers.py:10544). Document it in the endpoint docstring and app-kit docs, because it is a behaviour change for apps that relied on immortal entries (take-away change).
  3. Pass max_bytes=_MAX_CONTEXT_CONTENT * 4 + 4096 to read_bounded_json at chat_handlers.py:10614, which allows for UTF-8 and JSON overhead. Review the other max_bytes=None sites (:687, :5116, :9168, :10970) separately, because some carry legitimately unbounded content.
  4. Coordinate with CTX-30 / PR #16818 (refuse at the queue ceiling instead of evicting).
  5. Update app-kit-platform.md and docs/app-kit/ for the context-inject contract in the same commit.
- **Done when:** A unit test calls append_pending_context 60 times with 40,000-char sourceless entries on a stub slot, then drain_pending_context. It asserts the result is ≤ 64,000 chars plus the notice, carries the omission notice, and includes the newest entry. A request body of 200 KB to the endpoint returns 413.
- **Upstream:** #16817 PR #16818 (state unverified)
- **Changed from the source claim:** The 2M worst case is measured at 2,013,500 chars. The body read is corrected: no per-endpoint cap, but bounded by the 60 MB app-wide client_max_size. Scope is set to armed, because it needs an app or producer that posts context. Severity is lowered from 55 to 45. Probe note: turn_context.py binds its helper names from the chat_runner facade at runtime, so it must be imported through chat_runner.
- **Sources:** REVIEW_FINDINGS:X1, verify_needed:X1, verify_needed:design-rec-5, verify_needed:delta-5, verify_needed:G44

### CTX-14 [40, default, effort S] Lessons are not re-injected after compaction — CONFIRMED
- **Verified claim:** Confirmed and measured, and the gap is wider than claimed. After a confirmed compaction, post_compaction_parts calls _forget_shown_lessons but adds no lessons block. With memory.inject_lessons_per_turn off by default, no lessons reach the session again until a new session starts. The same seeded-home build also shows that the re-injection turn lacks three other blocks the first turn had: '## User Preferences' (the memory preferences file), '[CRITICAL RULES' (diff, path and [OPTIONS:] rules), and the [CURRENT DATE]/[CURRENT AGENT] identity. Those are the user's standing rules and the runtime contract, and context-management.md says kiro-cli's compaction drops session-start blocks. A channel turn's per-turn [RUNTIME] refresh partly re-asserts the diff mandate (context-management.md §2); dashboard turns get no such refresh.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context_assembly/turn.py:63-64 — builder._forget_shown_lessons(session_key) — no lessons render follows in post_compaction_parts (:33-163)
  - src/kiro_crew/config/memory_sections.py:251-252 — inject_lessons_per_turn default False
  - docs/architecture/context-management.md:297-320 — re-added after compaction: activity index, skills index, response preferences, member section, agent prompt (no lessons, no memory preferences, no critical rules)
  - measured (seeded home, reinject.py): first turn has '[Learned corrections', '## User Preferences', '[CRITICAL RULES'; the needs_reinjection=True turn has none of them
- **Checked by:** ran-new-script
- **Required outcome:** The first turn after a compaction restores the user's standing rules and runtime contract: the standing-rule lessons tier (bounded by _LESSONS_STARTUP_CAP), the memory preferences block (bounded per CTX-3), and the runtime-selected [CRITICAL RULES]. Findings-tier lessons stay on-demand.
- **Solution:**
  1. In context_assembly/turn.py:post_compaction_parts, add the critical-rules block (context._critical_rules_for(session_key, runtime_source), honouring includeCrewContext as at context.py:2580).
  2. Add the standing-rule lessons tier through the same store_admission.session_lessons_part render that session start uses, with the startup budget. Do not re-render findings.
  3. Add the memory preferences block through MemoryStore.get_context(include_activity=False, prefs_startup_cap=...), under the same memory-group and blocks_reads gate the activity index uses at turn.py:85-92. Bound it per CTX-3.
  4. Re-inject a V2 member's essentials as today, with no change.
  5. Keep the extra bytes small: lessons are about 23-37K at the cap, so pair this with CTX-10's slimming.
  6. Update context-management.md §2 'What comes back after compaction' list, and memory-skills-hooks.md, in the same commit. Extend test/test_reinjection_gate.py and the ContextBuilder re-injection tests.
- **Done when:** A ContextBuilder test on a tmp KIROCREW_HOME seeded with 3 lessons and 2 preferences: build_message('x', False, session_key='dashboard:s', runtime_source='dashboard', needs_reinjection=True) contains '[Learned corrections', '## User Preferences' and '[CRITICAL RULES'. The same call without needs_reinjection contains none.
- **Upstream:** #15399 (state unverified)
- **Changed from the source claim:** Broadened: besides lessons, the memory preferences file and the [CRITICAL RULES] block are also missing after compaction (measured). A severity is assigned (40) because this is a correctness loss on every compacting session on the default path.
- **Sources:** verify_needed:G22(#15399), verify_needed:delta-6

### CTX-2 [40, default, effort S] Compaction can repeat with no attempt cap — CONFIRMED
- **Verified claim:** Confirmed and measured. A compaction is judged ineffective when it frees less than 5 points. That arms a flat 60 s cooldown, with no backoff. One that frees 5 points or more clears the cooldown even when the reading is still at or above the threshold, so the same reading triggers again. No per-episode attempt counter exists. The only escalation is the reset when an immediate verdict is at least 95%. That escalation is unreachable on kiro-cli: a 'completed' compaction status resets the meter to unknown, so every verdict is deferred, and deferred verdicts are damping-only by design. Measured with the real SessionManager and the compact_if_needed ladder, a fake kiro-style provider and a fake clock: a session whose post-compaction floor sits at 80% (threshold 70%) compacted 11 times in 20 turns at 61 s or 120 s per turn, and 8 times at 30 s per turn. That is every second turn, indefinitely. A healthy landing at 40% compacted once. Each pass is a kiro-cli summarization of a context above the threshold, followed by the CTX-10 re-injection. The trigger needs a compacted floor that stays above the threshold, for example a small window, a low per-slot autocompact_pct override, or large pinned skills or tool sets. That condition is uncommon on a default install.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/session.py:700 — _COMPACT_FAILURE_COOLDOWN_SECS = 60.0 (flat; wired at session.py:2020)
  - src/kiro_crew/session.py:710 — _COMPACT_MIN_EFFECT_PCT_POINTS = 5.0
  - src/kiro_crew/session_compaction.py:1170-1176 — freed < min_effect -> cooldown_until = now + compact_failure_cooldown_secs
  - src/kiro_crew/session_compaction.py:1213 — effective verdict: self.state.cooldown_until.pop(key, None) regardless of pct_after vs threshold
  - src/kiro_crew/session_compaction.py:1134-1146 — unknown or non-dropping post reading -> verdict deferred (no immediate escalation)
  - src/kiro_crew/acp/client.py:14374-14376 — kiro-cli compaction 'completed' -> last_prompt_stats.reset_after_compaction() (pct 0, unknown)
  - src/kiro_crew/session_compaction.py:491-518 — deferred verdict judged at the next confirmed reading, escalation result ignored ('only safe for cooldown damping')
  - docs/system-specs/modules/session.md:978-980 — 'Deferred (next-reading) verdict settles are deliberately damping-only'
  - …and 1 more in the verification results.
- **Checked by:** ran-new-script
- **Required outcome:** An episode is a run of compactions that never brings the context below threshold - CONTEXT_WARN_MARGIN_PCT. It runs at most 2 compaction passes, with per-key exponential backoff between them. A third pass that does not improve notifies the user, or takes the existing reset path. A deferred verdict counts toward the episode, because the signal is the count of passes and not one ambiguous reading.
- **Solution:**
  1. In SessionCompactionState (session_compaction.py:95-102), add an episode counter per key and a backoff level per key. Clear both in the same places cooldown_until is cleared today: reset, remove, destroy and close_all (see the cooldown-clearing tests at test_session.py:5170-5223).
  2. In _judge_compact_effect (session_compaction.py:1167-1214), pop the cooldown and end the episode only when pct_after < effective_autocompact_pct(key) - context_warn_margin_pct. Otherwise increment the episode and arm cooldown = base x 2**level, capped at for example 15 min. Base stays _COMPACT_FAILURE_COOLDOWN_SECS.
  3. In _compaction_gate_decision (session_compaction.py:521-600), add a rung after 'cooldown': when the episode count is at least 2, return a new 'episode_exhausted' decline. Fire _fire_compact_callback with that outcome once, so surfaces can tell the user ('context stays above N% after compaction; start a new chat or /clear'). Do not auto-reset: a reset ends the conversation, and the doc deliberately limits resets to the measured >=95% immediate verdict.
  4. Add 'episode_exhausted' to the compact_if_needed outcome list in session.md (API table at session.md:1441) and to the 'Context compaction' bullet. Group this with CTX-1 if both are small.
- **Done when:** Extend TestIneffectiveCompactionCooldown in test/test_session.py. Use a fake kiro-style provider whose /compact always lands at 80% (threshold 70), drive 20 compact_if_needed turns with the module's time.monotonic monkeypatched to a fake clock advanced 120 s per turn, and assert at most 3 '/compact' stream_command calls and exactly one 'episode_exhausted' callback. A landing at 40% still compacts once and resets the episode. reset/remove/destroy clear the episode state.
- **Upstream:** #12443 (state unverified)
- **Changed from the source claim:** Severity is lowered from 55 to 40, because the loop needs a compacted floor above the threshold, which is uncommon on default settings. The source's '60 s cooldown' damping is weaker than stated. Effective and ineffective verdicts alternate, so a floor above the threshold compacts every second turn. The 95% reset escalation never fires on kiro-cli, because the meter resets to unknown and the verdict is deferred. FIX_PLAN's 'escalate to the existing reset path' must not reuse the immediate-verdict reset; notify the user instead.
- **Sources:** FIX_PLAN:CTX-2, REVIEW_FINDINGS:S2

### CTX-8 [40, default, effort M] Whole 39 KB prompt.md reaches every session; diff rule is repeated but not contradictory — PARTLY
- **Original claim:** Whole 39 KB prompt.md goes to every session; diff rules stated twice and contradictory (corrected below)
- **Verified claim:** Confirmed and measured: config/prompt.md (38,989 B) reaches every non-slim-resume session whole, whatever the surface or capability. That includes cron minimal-context runs: 39,632 B, nearly all persona. Section sizes: Computer Use 2,914 B (L165), Subagent Orchestration 7,888 B (L33), Browser 6,299 B (L141), Wait & Webhook 711 + Iterative pattern 5,406 + Webhook sessions 797 = 6,914 B (L96-140). _resolve_prompt_templates gates nothing but {{WIDGET_BLOCK}} and {{MAX_SUBAGENTS}}, and _computer_use_spec_gate only controls MCP server emission. The diff rule is stated twice, but 'contradictory' is overstated. prompt.md:5 says to show a ```diff block 'unless the latest injected critical rule or [RUNTIME] surface note relaxes it'. The runtime-selected critical rule (_DIFF_RULE_DASHBOARD) relaxes it on the dashboard, and _DIFF_RULE_CHANNEL restates it elsewhere. So it is a layered duplicate with explicit precedence, about 1.5 KB repeated, and the model must resolve two texts. Side check: subagent.py:7 'No spawn recursion: subagents cannot spawn other subagents' is stale. spawn.py:1298 waits on a 'subagent:<id>' parent's lane slot, and admission reasons about 'depth-0' spawns (fairness.py:340).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/config/prompt.md — 38,989 B; sections: Output Format L3 1,558 B, Capabilities L21 3,518 B, Subagent Orchestration L33 7,888 B, Rules L76 6,539 B, Wait & Webhook L96-140 6,914 B, Browser L141 6,299 B, Computer Use L165 2,914 B
  - src/kiro_crew/context.py:2270-2301 — _resolve_prompt_templates: only {{MAX_SUBAGENTS}}, {{VERBOSITY_BLOCK}} strip, {{WIDGET_BLOCK}}
  - src/kiro_crew/agent.py:1006 — _computer_use_spec_gate (MCP server emission only)
  - src/kiro_crew/config/prompt.md:5 — 'show a ```diff block unless the latest injected critical rule or [RUNTIME] surface note relaxes it'
  - src/kiro_crew/context.py:1176-1195 — _DIFF_RULE_DASHBOARD 'do NOT repeat them as ```diff code blocks'; :1196-1204 _DIFF_RULE_CHANNEL; :1324-1336 _critical_rules_for selects by runtime
  - src/kiro_crew/subagent.py:7 — 'No spawn recursion: subagents cannot spawn other subagents.' vs src/kiro_crew/mcp_tools/spawn.py:1298 'Only a subagent:<id> parent has a lane slot to wait for'
  - measured: dashboard 52,328 B, slack 49,287 B, cron-minimal 39,632 B, subagent 49,311 B — all contain ## Computer Use, ## Browser, ## Wait & Webhook Tools, ### Subagent Orchestration
- **Checked by:** ran-new-script
- **Required outcome:** prompt.md sections reach only the sessions whose capability and surface they apply to. Computer Use goes only where computer use is enabled. Browser and Wait/Webhook go only where those tools are mounted. Cron and minimal runs get no orchestration walkthrough. One diff rule text remains, the runtime-selected one. Non-dashboard sessions save 3-6k tokens.
- **Solution:**
  1. Add section markers to prompt.md, e.g. {{#COMPUTER_USE}}…{{/COMPUTER_USE}}, {{#BROWSER}}, {{#ORCHESTRATION}}, {{#WAIT_WEBHOOK}}.
  2. Resolve them in ContextBuilder._resolve_prompt_templates (context.py:2270), the same way {{WIDGET_BLOCK}} is resolved. Computer Use keys off the same predicate as agent._computer_use_spec_gate (agent.py:1006); Browser and Wait key off whether the session's spec mounts those tools; orchestration keys off minimal_context and the surface. Computer use is deliberately not governed, so this is presentation only: add no scopes.
  3. A copied or custom agent prompt without markers must render unchanged, and stray markers must be stripped.
  4. Remove the diff paragraph from prompt.md:5-15 and leave a one-line pointer to the runtime-selected [CRITICAL RULES] rule.
  5. Replace the monitor walkthrough at prompt.md:96-136 with a pointer to the babysit skill. Read memory-skills-hooks.md first, because the skill must live in builtin_skills/.
  6. Fix the stale docstring at subagent.py:7.
  7. Update context-management.md §1 row 1 and the post-compaction item 5, which re-injects the same resolved prompt.
- **Done when:** A ContextBuilder test on a tmp KIROCREW_HOME: a cron minimal build and a slack build contain no '## Computer Use' or '## Browser'; a dashboard build with computer use enabled (enable_state patched) contains '## Computer Use'; exactly one occurrence of 'unified diff' instruction text per build; a custom agent prompt without markers is byte-identical to today.
- **Upstream:** #16108 PR #16425 PR #17330 (state unverified)
- **Changed from the source claim:** The size and section figures hold. The cron-minimal path is confirmed: 39.6 KB, nearly all persona. 'Contradictory diff rules' is corrected to a layered duplicate with explicit precedence. The stale subagent.py docstring is confirmed.
- **Sources:** FIX_PLAN:CTX-8, REVIEW_FINDINGS:H-P2, REVIEW_FINDINGS:H-P6, verify_needed:G19(#16108), verify_needed:G37

### CTX-6 [40, armed, effort S] Slack thread fallback re-sends the buffered thread and duplicates the current message — CONFIRMED
- **Verified claim:** Re-measured at HEAD. On a Slack thread turn with no new third-party replies, thread_replies_text is None, so build_message injects channel_history.context_for(channel, thread_ts). That re-sends the thread's whole buffered tail every turn. In the 20-message thread fixture, the channel block grows from 4,014 B to 4,704 B over turns 2-10, of which 3,818-4,466 B (about 95%) was already sent the turn before. The owner's own text appears in the block on every turn, because slack/events.py pushes the incoming message into the buffer before the prompt is built, and the turn text carries it again as the request. When there are new replies, the fenced replies path replaces this leg and injects no channel block. Worst case per turn: 50 entries are 16,033 B by default; observe mode holds 200 entries (63,883 B) for 1 week. Only Slack writes channel_history; Discord, Telegram and other messaging transports never push. The whole-channel branch (old C1/T8) stays unreachable, because Slack always passes thread_ts.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context.py:3456-3460 — 'if channel_id and self.channel_history and not (thread_ts and thread_replies_text): ch_ctx = self.channel_history.context_for(channel_id, thread_ts=thread_ts)'
  - src/kiro_crew/slack/events.py:2655 (observe) and :2777 (non-observe) — channel_history.push(channel, sender_id, text, thread_ts=..., msg_ts=...) before the turn is dispatched
  - src/kiro_crew/slack/transport_dispatch.py:788 — thread_replies_text=_thread_replies.text if _thread_replies else None
  - src/kiro_crew/channel_history.py:23-29 — _DEFAULT_MAX_ENTRIES=50, TTL 300 s; OBSERVE_MAX_ENTRIES=200, OBSERVE_TTL_SECS=604800
  - src/kiro_crew/channel_history.py:53 — relative '(1m ago)' ages in the rendered lines
  - grep: channel_history.push appears only in slack/events.py
  - measured fallback turns 2-10: channel block 4,014→4,704 B, already-sent 3,818→4,466 B, user text duplicated in block every turn; replies path: channel block 0
  - measured worst case: default 50 entries 16,033 B; observe 200 entries 63,883 B
- **Checked by:** ran-existing-script + tm/observe_cap.py (copied to merge/scripts/B)
- **Required outcome:** Each Slack thread turn includes only buffered messages the session has not been shown. It never includes the current message, and it uses absolute times. A turn with no new messages carries no channel block.
- **Solution:**
  1. Keep a watermark per (session_key, thread_ts): the last msg_ts included, or the turn's own msg_ts. Store it on ContextBuilder, LRU-bounded like _SENT_SKILL_BODY_SESSIONS, and reset it on a new session and on needs_reinjection, so a compacted session sees the thread again. Mirror slack/thread_replies.py's last-turn watermark.
  2. Give channel_history.context_for (channel_history.py:138) a `since_ts` and an `exclude_ts` argument. Filter entries with msg_ts <= since_ts, and the entry whose msg_ts equals the current message's ts.
  3. Pass both from context.py:3456-3460. Commit the watermark only when the turn lands, using the same commit/rollback pairing as the skill-body record, so a failed turn re-sends.
  4. Render absolute HH:MM timestamps instead of '(Ns ago)' (channel_history.py:53). The relative form also breaks byte-identity of repeated lines.
  5. Update context-management.md §2 per-turn table row 'Channel history' and messaging.md / slack-gateway.md in the same commit.
- **Done when:** A ContextBuilder test with an in-memory ChannelHistory runs ten thread turns: 20 buffered messages, then the owner's message pushed before each build, and no thread_replies_text. Turn 2 with no new messages has no '[Recent channel messages' block. A message pushed by another user between turns 3 and 4 appears exactly once on turn 4. The owner's text never appears inside the channel block. No relative '(… ago)' strings appear.
- **Changed from the source claim:** The numbers hold (channel block about 4-4.7 KB/turn, about 95% repeated, user text duplicated; worst case 16.0 KB default and 63.9 KB observe). Severity is lowered from 45 to 40, because only Slack is affected and only when there are no new third-party replies. Added: only Slack writes channel_history, and a fix needs the commit/rollback discipline so a failed turn does not lose unseen messages.
- **Sources:** FIX_PLAN:CTX-6, REVIEW_FINDINGS:M4, REVIEW_FINDINGS:C1, FIX_PLAN-old:T8

### TOOL-19 [40, armed, effort S] Image paths in injected text are re-attached as images every nudge cycle — CONFIRMED
- **Verified claim:** Re-measured end to end at HEAD. When a monitor or auto-nudge loop's session has a non-terminal work ledger, the cycle message is prefixed with session_ledger.render_snapshot. The snapshot renders each artifact as 'artifact <k>: <path>' verbatim. The whole outgoing message then goes through build_prompt_blocks at the ACP send seam (acp/client.py:12563, acp/session_handle.py:1503), which inlines every readable image path it finds. A snapshot naming a real PNG produced blocks ['text', 'image'], with the text rewritten to 'artifact chart: [image: chart.png]'. So the artifact image is re-attached on every cycle, and each attachment then stays in native history. strip_image_refs is applied only to replay text (replay.py:311), consolidation (history_consolidation.py:441) and background prompts (llm_helpers.py:1714). No injected-message builder calls it. Not checked: other injected blocks that might carry image paths, such as cron notifications, subagent completion envelopes and rails.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/handlers/autonudge.py:97-105 — snapshot = render_snapshot(ledger_key(slot_key)); return f"{snapshot}\n\n{body}"
  - src/kiro_crew/session_ledger.py:2416 — lines.append(f"artifact {k}: {_field(v)}")
  - src/kiro_crew/acp/prompt_blocks.py:115-175 — build_prompt_blocks scans the whole message with _PATH_RE and inlines readable images
  - src/kiro_crew/acp/client.py:12563, src/kiro_crew/acp/session_handle.py:1503 — build_prompt_blocks(message, allow_image=...) on every prompt
  - grep strip_image_refs( callers: llm_helpers.py:1714, history_consolidation.py:441, context_assembly/replay.py:311 only
  - measured: block types ['text', 'image']; text 'artifact chart: [image: chart.png]'
- **Checked by:** ran-existing-script (copied to merge/scripts/B/t19_check.py; PNG under merge/homes/B/t19)
- **Required outcome:** Only the user's own typed text and explicit attachment markers can attach images. Host-injected text, including the work-ledger snapshot and other automation envelopes, never turns a path into an image block.
- **Solution:**
  1. Narrow fix: render artifact values in session_ledger.py:2416 through image_refs.strip_image_refs, or in a form _PATH_RE does not match, such as a backticked path with a non-matching prefix. Keep the path readable for the model.
  2. General fix: give build_prompt_blocks (acp/prompt_blocks.py:115) an optional image-scan span (start, end), and have the dashboard prompt assembly pass the user-text range it already tracks (context.py user_text_range / turn.py user_turn) plus the attachment markers. Text outside the span is never scanned. Default to the whole message only for callers that pass no span, so existing channel attachment flows keep working.
  3. Audit the other injected envelopes (cron notifications, [Subagent completion event], rails) with the same test.
  4. Update context-management.md §2 'The [work ledger] snapshot' in the same commit.
- **Done when:** A unit test writes an 8x8 PNG to tmp_path. A nudge message built through the autonudge prefix helper, with a fake ledger whose artifact names that PNG, yields build_prompt_blocks(...) of only text blocks. A user-typed message containing the same path still yields ['text', 'image'].
- **Upstream:** #15264 PR #15273 (state unverified)
- **Changed from the source claim:** none (the measurement reproduces at HEAD: ['text', 'image'])
- **Sources:** FIX_PLAN:TOOL-19, REVIEW_FINDINGS:Part6/TOOL-19, verify_needed:G27(#15264), verify_needed:G36

### TOOL-9 [40, armed, effort M] Replayed images accumulate with no per-conversation ledger — CONFIRMED
- **Verified claim:** Confirmed by code, and the code itself documents the gap. build_prompt_blocks caps a single prompt at 20 image blocks and 12 MiB of base64. Its own comment says 'Both caps bound this prompt alone; what a conversation's replayed history carries in total is not measured here.' The same comment records a measured backend request-body ceiling of 32 MiB: a replayed request of 30.4 MB of base64 was accepted and one of 33.8 MB was refused as improperly formed. It also cites about 1,600 tokens per replayed image. The MCP gateway relay (mcp_gateway/image_budget.py) makes each tool-result image compliant (edge and bytes), but it counts nothing per conversation. A search for any per-session image count or bytes ledger found none. So a conversation whose native history (kiro-cli or claude) accumulates tool-result or prompt images can cross the request-body ceiling while the percentage meter sits below autocompact_pct, and then every later turn is refused. #16143's '91 PNGs, ~31 MB base64, ~62% of 1M, no compaction' matches that body ceiling. Crew's own replay is text-only (§5.2 is correct), so the accumulation happens in the harness's native history. Whether image tokens move the harness's percentage meter is provider-side and not observable here. Recovery today is manual: session_image_repair is a user-invoked command, and discard_conversation runs only on typed IMAGE_FORMAT_UNSUPPORTED.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/acp/prompt_blocks.py:90-101 — '32 MiB … each replayed image costs about 1,600 tokens on every later turn. Both caps bound this prompt alone; what a conversation's replayed history carries in total is not measured here.' MAX_PROMPT_IMAGE_BLOCKS = 20, MAX_PROMPT_IMAGE_B64_BYTES = 12 MiB
  - src/kiro_crew/mcp_gateway/image_budget.py:1-45 — per-image budget on relayed tool results; 'kiro-cli replays the full history to the model on every subsequent turn'
  - src/kiro_crew/session_image_repair.py:1-30 — user-invoked repair of wedged transcripts; automatic rewriting ruled out
  - src/kiro_crew/session_compaction.py:521 — trigger is the percentage only (no byte or image term)
  - docs/system-specs/modules/session.md:1447 — discard_conversation on typed IMAGE_FORMAT_UNSUPPORTED ('the bounded Kiro Crew replay is text-only')
  - grep: no images_per_session / image_ledger / per-conversation image count anywhere in src/kiro_crew
- **Checked by:** read
- **Required outcome:** Crew keeps a per-conversation account of the image bytes and count that it inlined into prompts or relayed through the gateway. Before the account reaches the measured body ceiling (32 MiB, with headroom), new images stay as paths with the existing 'not attached' note, and the session compacts or tells the user. A conversation never wedges because of image volume Crew could have seen.
- **Solution:**
  1. Keep the ledger on the SessionManager per canonical session key (bytes, count). Never put it in a module global in the MCP gateway: AGENTS.md requires MCP tools to stay stateless, and the relay serves co-pooled tenants. Reset it on new session, reset(clear_conversation), discard_conversation and confirmed compaction.
  2. Feed it from build_prompt_blocks' result: count image blocks and base64 bytes at the dispatch seam that sends session/prompt. Feed it from mcp_gateway/image_budget.py's rewrite result, reported back with the session key the relay already knows.
  3. When the ledger passes about 24 MiB or a count threshold, pass max_prompt_image_b64_bytes=0 to build_prompt_blocks so new pictures stay as paths with PROMPT_LIMIT_NOTE. Replace further relayed images with the short text block image_budget already uses for unverifiable images. Treat it as a compaction trigger in _compaction_gate_decision as an additional rung, gated positively on ACP_BACKENDS_COMPACT.
  4. Surface a one-time notice on the dashboard.
  5. Update session.md, mcp.md (gateway relay) and the design note docs/architecture/design-notes/mcp-gateway-oversize-response.md in the same commit.
- **Done when:** Unit tests: a SessionManager ledger fed 100 synthetic 340 KB images crosses the threshold, and the next build_prompt_blocks call for that key attaches no image and carries PROMPT_LIMIT_NOTE. The gate decision for that key returns a compaction trigger even with pct below autocompact_pct. reset/discard clear the ledger. No real files or network are needed, because images are generated in-memory with Pillow.
- **Upstream:** #16143 #15264 (state unverified)
- **Changed from the source claim:** Re-upgraded from the §5.2 downgrade. Crew's replay is text-only, but the code documents that native history accumulates images and that nothing measures it per conversation. The wedge is better explained by the measured 32 MiB request-body ceiling than by 'images do not move the meter', and the latter is not verifiable here. Severity is set to 40/armed, between FIX_PLAN 35 and A8 55: it needs an image-heavy session, but it ends the conversation.
- **Sources:** FIX_PLAN:TOOL-9, REVIEW_FINDINGS:U8, verify_needed:corr-U8, verify_needed:A8, verify_needed:G21(#16143), verification_needed:P1-1, verification_needed:sink#2, verification_needed:opt(image-ledger), verification_needed:refactor(image-ledger), verify_needed:G36, verify_needed:G38, verify_needed:G41, verify_needed:G43, verify_needed:G44

### CTX-5 [35, default, effort S] Per-turn reminders re-sent identically every follow-up turn — CONFIRMED
- **Verified claim:** Re-measured at HEAD. Dashboard follow-up turns 2-10 each inject 1,915 B, of which 1,902 B are byte-identical to the previous turn: the guidance paragraphs are 1,352 B, [PROJECT] is 373 B (it carries the project path, so its size varies), [RUNTIME] is 175 B, and the [REPLY FORMAT RULES] header is 21 B. The source measured 1,899 B. Heartbeat turns call build_message without interactive=False, so they get the default interactive=True. With no session key and therefore no dashboard surface, that adds only the ~430 B [OPTIONS:] paragraph, not the ask_question or suggest_followup paragraphs. The guidance is deliberately placed just before the request header (context-management.md §2), so a fix must keep a pointer there.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context_assembly/turn.py:412-474 — interactive_guidance: [OPTIONS:] paragraph always when interactive; ask_question/suggest_followup (+ dynamic cards) when has_dashboard_surface
  - src/kiro_crew/context_assembly/turn.py:283 — f"[PROJECT] Active project directory: {project}\n" every turn
  - src/kiro_crew/context_assembly/sections.py:450-460 — follow-up [RUNTIME] refresh
  - src/kiro_crew/context.py:3010 — build_message(..., interactive: bool = True)
  - src/kiro_crew/slack/gateway.py:6245-6253 — heartbeat build_message(injected, is_new, memory_store=..., needs_reinjection=..., skill_bodies_session=session_key) — no interactive=False
  - docs/architecture/context-management.md:225-227 — guidance sits before the request header on purpose (recency edge)
  - measured (turns 2-10, dashboard): injected 1,915 B/turn, repeated 1,902 B/turn; blocks [RUNTIME] 175, [PROJECT] 373, [REPLY FORMAT RULES] 21, guidance 1,352
- **Checked by:** ran-existing-script (copied to merge/scripts/B/turns.py; homes under merge/homes/B)
- **Required outcome:** When nothing changed, a dashboard follow-up turn injects at most about 300 B. [PROJECT] and [RUNTIME] are sent only when they change, and on the first turn after a compaction. The full guidance goes on turn 1 and after a compaction; other turns get a one-line pointer kept just before the request header. Heartbeat turns get no interactive guidance.
- **Solution:**
  1. Keep a per-session digest per block in ContextBuilder, using the same pattern as _dedup_triggered_bodies (context.py) and an LRU bound like _SENT_SKILL_BODY_SESSIONS. Reset it on a new session, on needs_reinjection, and on an agent change. Write it at build time and commit or roll back with the turn (commit_skill_bodies / rollback_skill_bodies), so a failed turn re-sends.
  2. context_assembly/turn.py:283: emit [PROJECT] only when the digest changed.
  3. sections.py:450-460: emit the follow-up [RUNTIME] only when the runtime changed. A channel turn's diff-block mandate re-assertion (context-management.md §2 table) must survive; keep that line if it carries rules.
  4. turn.py:412-474: on unchanged turns, replace the paragraphs with a one-line pointer ('(Reply-format and card rules as given earlier apply.)') placed before [CURRENT USER REQUEST].
  5. slack/gateway.py:6245: pass interactive=False for heartbeat builds.
  6. Update context-management.md §2 per-turn table ('every follow-up' becomes 'when changed') in the same commit. A model that stops seeing the guidance every turn may regress on [OPTIONS:] formatting, so verify with the existing guidance tests.
- **Done when:** In a ContextBuilder test on a tmp KIROCREW_HOME, turn 2 with an unchanged project has no '[PROJECT]' body and injects ≤ 300 B beyond the user text. Changing project= on turn 3 re-sends [PROJECT]. needs_reinjection=True re-sends the full guidance. A heartbeat build (no session key) contains no '[OPTIONS:' paragraph.
- **Changed from the source claim:** 1,915 B/turn now versus 1,899 B; [PROJECT] is 373 B here because it depends on the path. The heartbeat gets only the [OPTIONS:] paragraph (about 430 B), not the whole guidance. Severity is lowered from 40 to 35: the total is about 0.5k tokens per turn, and on kiro-cli it accumulates in native history.
- **Sources:** FIX_PLAN:CTX-5, REVIEW_FINDINGS:C2, REVIEW_FINDINGS:M5

### SES-1 [35, default, effort M] One config save recycles every session including in-flight turns — CONFIRMED
- **Verified claim:** Every dashboard save that ends in `_reset_all_sessions` (agent-config PUT for ONE agent, computer-use enable, the MCP restart endpoint, MCP enable/disable when hot reload is not active) calls `reload_provider_factory()`, which clears the whole session registry and shuts down every provider with no semaphore/idle check and no scoping to the edited agent, then drains the warm pool. A turn in flight is killed mid-stream; on the dashboard the process-death ladder usually re-queues it (a cold resend with full session-start context), other surfaces just lose the turn.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/handlers/sessions.py:5038 — `async def _reset_all_sessions(request)` docstring: 'Reset all active sessions so they pick up config changes'
  - src/kiro_crew/dashboard/handlers/sessions.py:5051 — `await sessions.reload_provider_factory()` (unconditional), :5057 `drain_all_providers()`, :5060 `drain_warm_pool()`
  - src/kiro_crew/session_lifecycle.py:1281-1284 — `stale = list(owner._sessions.items())` … `owner._sessions.clear()` under the manager lock, no `skip_if_busy` / semaphore test
  - src/kiro_crew/session_lifecycle.py:1297 — `await sess.provider.shutdown()` for every stale session
  - src/kiro_crew/dashboard/agent_admin/agent_config.py:522 — `await _h._reset_all_sessions(request)` after a PUT for one agent `name` (:435)
  - src/kiro_crew/dashboard/handlers/computer_use.py:812 — `sessions_reset = await _reset_all_sessions(request)`
  - src/kiro_crew/dashboard/handlers/sessions.py:5155 — restart endpoint `count = await _reset_all_sessions(request)`
  - src/kiro_crew/dashboard/handlers/mcp.py:1712-1717 — reset skipped only when `_mcp_hot_reload_active(request)`
  - …and 2 more in the verification results.
- **Checked by:** read
- **Required outcome:** A config save recycles only the sessions the change affects (the edited agent's sessions for an agent-config PUT; all sessions only for a global surface such as an MCP-server or computer-use change), and never a session whose turn is in flight: a busy session is recycled once its turn releases the semaphore.
- **Solution:**
  1. Give `_reset_all_sessions` (dashboard/handlers/sessions.py:5038) a scope argument: `agent: str | None`. agent_config.py:522 passes the edited `name`; computer_use.py:812, sessions.py:5155 and mcp.py:1717 pass None (global).
  2. Stop calling `reload_provider_factory()` there (sessions.py:5051): it is the provider-switch path (config_watch.py:56-71 already uses it only for that) and clears the registry unconditionally (session_lifecycle.py:1281-1297). Call `refresh_defaults(cfg)` instead (keeps live sessions, refreshes factory/pool).
  3. For each registered key in scope, call `SessionManager.reset(key, skip_if_busy=True)`; for each key that declines because its semaphore is held, record it in a manager-owned `pending_config_recycle` set and perform the reset in the turn-end release path (the same place `release()` runs), so the turn completes first.
  4. Drain the warm pool as today (pool processes hold stale MCP config).
  5. Update docs/system-specs/modules/session.md (reset semantics after a config save) in the same commit; read runtime-ownership.md first because reset reaches the kill path.
- **Done when:** Unit test with a SessionManager holding two fake sessions (agent A with its semaphore held, agent B idle) and a frozen clock: an agent-config PUT for B resets B only and leaves A's provider alive; a PUT for A while A is busy does not shut A down, and after the test releases A's semaphore the pending recycle runs exactly once; a global (computer-use) save resets B immediately and A only after release. No sleeps: drive release explicitly.
- **Changed from the source claim:** none on the mechanism; added that the reach is wider than agent config (computer-use enable, MCP restart, MCP toggles without hot reload) and that the dashboard's process-death ladder usually re-queues the killed turn cold rather than losing it outright.
- **Sources:** FIX_PLAN:SES-1, REVIEW_FINDINGS:S4, verify_needed:A6, FIX_PLAN-old:A-9

### SES-5 [35, default, effort M] A throttle refusal on the empty continue turn fails a long subagent run — CONFIRMED
- **Verified claim:** In a subagent run, once any activity has been observed (text, a completed turn, or a tool call), a transient error gets exactly ONE continue turn (`_TRANSIENT_CONTINUE_MSG`). A second transient error, including a capacity throttle that the durable-queue dependency coordinator could park, raises out of `_stream_with_transient_retry`. The run's generic exception arm then records `info.error`, sets `done` and writes an 'error' tombstone, so the run ends `failed`. That arm, unlike the cancel, reap and context-overflow arms, does not promote `info.streaming_text` into `info.result`. Tool side effects stay, but the run's streamed answer is not delivered as its result. The only keep-output escape is the narrow 'failed to generate a response' text match.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/subagent_manager/run.py:2682 — `async def _stream_with_transient_retry()`; :2704 `post_activity_attempts = 0` ('post-activity recovery gets exactly ONE attempt')
  - src/kiro_crew/subagent_manager/run.py:2820 — `_had_activity = bool(result_text) or turns > 0 or info.tool_count > 0`
  - src/kiro_crew/subagent_manager/run.py:2824-2833 — the only keep-output escape: `post_activity_attempts >= 1 and … and "failed to generate a response" in str(exc).lower()`
  - src/kiro_crew/subagent_manager/run.py:2842-2845 — dependency-classified throttle: `_signal = classify_exception(exc) …; if _had_activity and post_activity_attempts >= 1: raise` (parked only before the continue is spent)
  - src/kiro_crew/subagent_manager/run.py:2854-2857 — unclassified transient: `if _had_activity: if post_activity_attempts >= 1: raise`
  - src/kiro_crew/subagent_manager/run.py:1124-1131 — generic arm: `info.error = append_fallback_story(...)`, `info.done = True`, `_write_tombstone(info, "error")`; no `info.result = info.streaming_text` (contrast :994-995 cancel arm, :1041-1042 overflow arm, :883-884 reap)
  - src/kiro_crew/subagent.py:1233 — `_TRANSIENT_CONTINUE_MSG = "[system] Your previous response was interrupted by a transient backend error…"`
  - src/kiro_crew/acp/transport_errors.py:1448-1454 — throttle text 'Bedrock is throttling requests…' (classified transient)
- **Checked by:** read
- **Required outcome:** A capacity or throttle refusal on the post-activity continue turn parks the run and retries that continue after capacity returns, through the same per-scope dependency coordinator used before the continue was spent. The run is not failed while that refusal class persists inside a bounded wait. If it does end failed, the work already streamed is delivered as the partial result (`partial=True`), as the cancel and overflow arms do.
- **Solution:**
  1. In `_stream_with_transient_retry` (subagent_manager/run.py:2842-2851), when `_signal` is a non-terminal dependency signal (throttle/capacity), do not count it against `post_activity_attempts`: yield to `_yield_for_dependency` and resend `_TRANSIENT_CONTINUE_MSG`; bound it by the coordinator's own wait budget rather than the one-shot (a continue cannot repeat a side effect the model already completed any more than the first continue could).
  2. Keep the one-shot for unclassified transients (:2854-2857).
  3. In the generic exception arm (run.py:1124-1131), mirror the cancel/overflow arms: `if not info.result and info.streaming_text: info.result = info.streaming_text; info.partial = True` so the parent receives the work.
  4. Mirror the parity note at :2700 in dashboard/chat_runner.py if its post-token ladder has the same rule. Update docs/system-specs/modules/subagent.md (recovery ladder) in the same commit.
- **Done when:** Subagent run test with a fake client: attempt 1 streams text then raises a throttle AcpError classified as a dependency signal; the continue raises the same throttle; a fake coordinator wakes immediately (no sleeps, injected clock); the third send succeeds → the run ends `completed` with both text segments. A variant where the continue raises an unclassified transient ends `failed` with `info.result` equal to the streamed text and `partial` True.
- **Upstream:** #17439 (state unverified)
- **Changed from the source claim:** Shape confirmed and the verdict site is pinned: run.py:2844 for the classified throttle and :2855 for the unclassified one. Added that the failure arm does not preserve the streamed text as the result. Not checked: the reporter's exact refusal frame (GitHub unreachable).
- **Sources:** verify_needed:G4(#17439), verification_needed:Part3(#17439)

### CTX-21 [30, default, effort M] Auto-compaction waits for turn end and overshoots the threshold — CONFIRMED
- **Verified claim:** Crew's auto-compaction trigger is evaluated only after a turn completes: every caller of SessionManager.check_context_usage (dashboard chat runner, Slack/Telegram/Discord/... transport dispatch, slack/handler, task runner) calls it once the turn has ended, and compact_if_needed is called only by the task runner between steps. The only in-turn guard is the task runner's ContextOverflow interject at a fixed 90% (_MID_STREAM_COMPACT_PCT), and it fires only on a permission request of a gated turn. A chat or channel turn that grows from below autocompact_pct (70%) to far above it inside one turn (large tool results, long agentic turn) is not compacted until it ends; past that point only kiro-cli's own native self-compaction (provider-side, not observable here; Crew notices it via the mid-turn compaction recovery in chat_runner) bounds it.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/session_compaction.py:285-289 — check_context_usage: pct = provider.context_usage_pct(); decline = owner._trigger_compaction(...)
  - src/kiro_crew/dashboard/chat_runner.py:16855 — state.sessions.check_context_usage(session_key, client) runs after the turn's stop_reason is known
  - src/kiro_crew/slack/transport_dispatch.py:976 — sessions.check_context_usage(session_key, client) after the reply is posted
  - src/kiro_crew/telegram/transport_dispatch.py:2388 — pct = self.sessions.check_context_usage(...) in the post-turn soft-threshold warning; docstring: 'The hard-compaction backstop is the backend autocompactor'
  - src/kiro_crew/task_executor.py:849 — check_context_usage after record_success (turn landed)
  - src/kiro_crew/task_executor.py:85 — _MID_STREAM_COMPACT_PCT = 90.0
  - src/kiro_crew/task_executor.py:555 — interject=tool_permission.ContextOverflow(client, threshold=_MID_STREAM_COMPACT_PCT) (task runner only)
  - src/kiro_crew/tool_permission.py:601-612 — ContextOverflow.refuse tears the request down when pct >= threshold; evaluated per permission request, not per streamed chunk
  - …and 3 more in the verification results.
- **Checked by:** read
- **Required outcome:** A chat or channel turn whose context crosses the compaction threshold mid-turn is bounded before it overshoots by more than a configured margin: either the turn yields to compaction at a safe boundary (between tool calls) or the overshoot is capped, on every surface, not only the task runner.
- **Solution:**
  1. Read docs/system-specs/modules/session.md (Context compaction; Compaction Race Handling) and docs/architecture/context-management.md first; update session.md in the same commit.
  2. Generalize the task runner's in-turn guard: extract tool_permission.ContextOverflow (tool_permission.py:601) into a policy the dashboard chat runner and messaging.dispatch ChannelTurns/drive_turn also install, with the threshold derived from effective_autocompact_pct(key) + a margin (not the fixed 90.0 at task_executor.py:85).
  3. Also evaluate it on EVENT_TOOL_CALL / tool-result boundaries (auto-approved tools never raise a permission request, so the current guard is blind on ungated turns).
  4. On trip, end the turn at the tool boundary, run compact_if_needed(key), and queue the existing compaction continuation (_COMPACTION_CONTINUE_MSG, chat_runner.py ~:15947) rather than a new mechanism.
  5. Gate positively on ACP_BACKENDS_COMPACT membership (harness identity positive); KAS/cc_managed keep their own compaction.
  6. This changes when turns end (a take-away for a user who relies on long uninterrupted turns): read docs/system-specs/common/take-away-changes.md and grep docs/decisions/ before landing.
- **Done when:** A unit test with a fake provider whose context_usage_pct() returns 60 before the turn and 85 after the 2nd tool-call event (autocompact_pct=70) shows the dashboard runner ends the turn at that tool boundary, compact_if_needed is awaited once, and one continuation is queued; the same fake on a backend outside ACP_BACKENDS_COMPACT does not trip. No sleeps: events are fed from a list.
- **Upstream:** #12443 (state unverified)
- **Changed from the source claim:** Claim holds for Crew's trigger. Added: the task runner already has a partial in-turn guard (90%, permission requests only), and kiro-cli's own native self-compaction may bound overshoot provider-side (not verifiable here).
- **Sources:** verify_needed:G25(#12443)

### CTX-31 [30, default, effort S] Conversation rows uncapped in the non-native replay path — CONFIRMED
- **Verified claim:** Confirmed, measured, and worse than claimed. The provider-agnostic session replay (build_session_replay → replay_text) applies no per-row cap to conversation rows, as the code comment itself says: 'Conversation rows are uncapped here'. Only inject rows are clipped, at 2,000 chars. Its budget loop admits a line only while 'total + len(line) <= replay_budget', but the guard is 'and lines', so the NEWEST row is always admitted whole whatever its size. Measured: a 300,000-char newest row gives a 300,006-char replay against an 80,000 budget. On a 200k window the budget is 16,000 and the same 300,006 chars still go out. A 100,000-char older row behind three small rows stops the scan, so the replay keeps only those 3 rows and loses all history before it. This output is placed OUTSIDE admit_background as '[CONVERSATION HISTORY …]' ('inject OUTSIDE the capped session context so it doesn't get truncated'). The dashboard uses it on every cold start without native history: a provider switch, a failed or Tool-Search resume, process death, or a reset after stop. The conflict is resolved this way. REVIEW_FINDINGS §5.2's per-message caps (8K/4K) belong to different paths: build_interrupted_turn_preamble (user_cap 8000, assist_cap 4000) and the fallback thread_history_text (per_message 8,000 x window factor; measured 20,668 chars for the same 300K row). 'Replayed rows uncapped' is therefore wrong for those paths and right for replay_text.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context_assembly/replay.py:199-207 — _REPLAY_BUDGET_CHARS = 80_000; 'Per-row ceiling for inject content … Conversation rows are uncapped here'
  - src/kiro_crew/context_assembly/replay.py:316-358 — replay_text: only inject rows clipped; 'if total + len(line) > replay_budget and lines: break' (first/newest line always admitted)
  - src/kiro_crew/context.py:1505-1546 — build_session_replay → _replay.replay_text(messages, model_window)
  - src/kiro_crew/context.py:3380-3389 — '[CONVERSATION HISTORY — recent session replay …]' appended outside the capped session context
  - src/kiro_crew/dashboard/chat_turn/prompt_assembly.py:44-86 — cold start without provider history → build_session_replay(..., model_window=window_for_provider_client(client))
  - src/kiro_crew/context_assembly/replay.py:361-407 — thread_history_text clips every row at per_message_cap (the capped path §5.2 described); :102-108 build_interrupted_turn_preamble user_cap=8000, assist_cap=4000
  - measured: newest 300K row → 300,006 chars (budget 80,000; 16,000 on 200k); older 100K row → replay keeps 3 rows (948 chars); fallback path same 300K row → 20,668 chars
- **Checked by:** ran-new-script
- **Required outcome:** The session replay never exceeds its window-scaled budget. Each conversation row is clipped to a per-row ceiling that scales with the budget, as thread_history_text already does, with a '…[truncated]' marker. One large older row is clipped instead of ending the scan, so the newer and older rows still fit.
- **Solution:**
  1. In replay_text (context_assembly/replay.py:316), compute per_row_cap = min(_PER_MESSAGE_CAP x factor, replay_budget - len('Assistant: ') - len('…[truncated]')), mirroring thread_history_text:373-376. Clip conversation rows to it before building the line.
  2. Keep the 'at least one row' guarantee by clipping, never by over-admitting: drop the 'and lines' exemption once rows are clipped.
  3. Optionally compress assistant rows with _compress_assistant_message as the fallback path does. That is a behaviour change to the 'lossless' replay, so note it in context-management.md §1 row 13/20 in the same commit.
  4. Update the comment at replay.py:203-207: conversation rows are capped per row, and inject rows keep their own lower cap.
- **Done when:** In test_context_replay (or a new test), replay_text([... 40 small rows, {'role':'user','content':'Z'*300_000}], None) returns ≤ 80,000 chars and ends with '…[truncated]'. With model_window=200_000 the result is ≤ 16,000. With a 100,000-char row behind 3 small rows, the result contains rows older than the large row.
- **Changed from the source claim:** Escalated: the newest row bypasses the budget entirely. The source said only that one row can consume the budget; the measured overflow is 300K chars against an 80K or 16K budget. The CONFLICT with §5.2 is resolved: the 8K/4K caps are on the interrupted-turn preamble and the fallback thread-history path, not on replay_text.
- **Sources:** verification_needed:sink#7, verification_needed:opt(replay-row-cap), verification_needed:refactor(replay-row-cap), verify_needed:A-pass2-correction(replayed-tool-outputs)

### CTX-4 [30, default, effort M] Volatile values early in the prompt break the cross-session shared prefix — CONFIRMED
- **Verified claim:** Re-measured at HEAD, and the numbers hold. Two first turns built 7 minutes apart in separate processes, with different hash seeds and session ids, share a byte-identical prefix only up to the minute in [CURRENT DATE]. That is 44,614 B: 85.3% of 52,310 B on an empty home and 49.4% of 90,315 B on a seeded home. When the adaptive subagent cap changes from 8 to 6, the shared prefix collapses to 6,105 B (11.7%) at prompt.md:41 {{MAX_SUBAGENTS}}. On a seeded home with the same clock, two different first messages diverge at 59,887 B, where the query-ranked [Learned corrections] block starts. What this is worth depends on two things this machine cannot observe. (1) Whether the provider caches a prefix across sessions (FIX_PLAN §0.4(b)). (2) Where cache boundaries fall. On Anthropic-style prompt caching, a hit needs an identical prefix up to a content-block boundary that carries a breakpoint. Crew sends the whole first turn as one ACP text block (plus images), so bytes shared inside that block earn nothing unless the stable part becomes its own block and kiro-cli keeps the boundary. Separately, query-ordered lessons are a documented design choice: the order decides which rows survive an overflow, and the header says 'relevant ones first'.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context.py:2589 — append_required(f"[CURRENT DATE] {now.strftime('%A, %Y-%m-%d %H:%M %Z')}") (minimal/cron path: :2521)
  - src/kiro_crew/config/prompt.md:41 — 'Capacity (up to {{MAX_SUBAGENTS}} active, overflow queued)'
  - src/kiro_crew/context.py:2176 — _live_cap_figure; :2274-2281 token replacement in _resolve_prompt_templates
  - src/kiro_crew/learn.py:700-701 — ranked_directives = order_by_request_relevance(entries(authored), query_text) (+ unclassified)
  - src/kiro_crew/acp/prompt_blocks.py:115-143 — build_prompt_blocks: one text block plus image blocks ('The result is always at least one text block')
  - docs/system-specs/modules/memory-skills-hooks.md:3176 — relevance ordering per tier decides which rows survive truncation; header reads 'relevant ones first'
  - measured 1a: empty home 52,310 B, shared 44,614 B (85.3%), break at '[CURRENT DATE] Wednesday, 2026-10-07 09:00'
  - measured 1b: seeded home 90,315 B, shared 44,614 B (49.4%)
  - …and 2 more in the verification results.
- **Checked by:** ran-existing-script + cap_prefix.py + query_prefix.py (consolidated as merge/scripts/B/prefix_B.py using tm/build_one.py and tm/seed.py; homes under merge/homes/B)
- **Required outcome:** For the same user and config, two new sessions share every byte up to the query-dependent tail. On the seeded home that is at least 59 KB instead of 44.6 KB, and a subagent-cap change no longer cuts the prefix to 6 KB. The stable span is emitted so that a block-boundary prompt cache can actually hit it. Do the work only once the live check shows cross-session caching exists.
- **Solution:**
  1. First, run the live check: on a real install, compare two new sessions' first-turn usage (cache-read tokens) through kiro-cli's metering, or through the Claude Code backend's usage. If neither reports cache reads across sessions, stop here and park this item.
  2. Move [CURRENT DATE], [CURRENT AGENT]/[RUNTIME] and the live subagent cap into one tail block placed just before [END OF SESSION CONTEXT] (context.py:2589, :2521 minimal path).
  3. Replace {{MAX_SUBAGENTS}} at prompt.md:41 with fixed wording that points to that tail line. Keep the post-compaction copy's session-start figure (_session_cap_figure, context.py:2225) as documented in context-management.md §'What comes back after compaction' item 5.
  4. Lessons: keep relevance ranking for SELECTION, which the spec requires. When the tier does not overflow, render the admitted rule-tier rows in stored order (id/ts), which makes the rule tier byte-stable for a given store. Put only the query-ranked tier after the tail. This changes the 'relevant ones first' header wording, so update memory-skills-hooks.md:3176 in the same commit.
  5. Emit the stable span as its own ACP text block: give build_prompt_blocks (acp/prompt_blocks.py:115) an optional split marker, so that a cache with block-boundary hits can reuse it. Confirm on the live install that kiro-cli keeps block boundaries.
  6. Update context-management.md §1 'Block order' (rows 3/4) in the same commit.
- **Done when:** A test builds two first turns with ContextBuilder.build_message in-process: kiro_crew.context.datetime patched to a frozen clock 7 minutes apart, different session keys, resource_status.adaptive_exec_cap stubbed to 8 and then 6, and different queries on a seeded tmp KIROCREW_HOME. It asserts the common byte prefix is ≥ 59,000 B, and that '[CURRENT DATE]' comes after the '[Learned corrections' standing-rule tier.
- **Changed from the source claim:** The numbers hold: 52,310/90,315 B totals versus 52,134/90,175 B in the source, a shared prefix of exactly 44,614 B, a 6,105 B cap break, and a query divergence at 59,887 B versus 59,744 B. Severity is lowered from 45 to 30, because the value is contingent on unknown (b). The source also misses that a block-boundary prompt cache cannot hit a prefix inside one text block, so the stable span must also become its own block. The lesson-order change touches a documented ranking contract.
- **Sources:** FIX_PLAN:CTX-4, REVIEW_FINDINGS:M2, REVIEW_FINDINGS:M3, REVIEW_FINDINGS:M7, REVIEW_FINDINGS:C3, FIX_PLAN-old:C3, verify_needed:design-rec-6, verification_needed:opt(stable-first-ordering)

### SES-11 [30, default, effort M] Non-dashboard turns lack lost-backend-session recovery — CONFIRMED
- **Verified claim:** Only the dashboard runner recognises a live backend's 'session not found' answer. It resets the binding, keeping the session-map sid so a fresh process session/loads the same backend id, and replays the turn once. The Slack handler, the messaging dispatcher (Telegram, Discord and other channels), the channel-agent loop, the sub-agent ladder and stream_and_collect (cron, heartbeat, task runner) all treat it as a generic AcpError. A channel thread answers '❌ …session not found' and charges the circuit breaker; nothing resets the dead binding until the breaker trips after 5 consecutive failures, so up to 5 user messages fail first. A sub-agent or cron run simply fails (not transient), losing that run's work. The breaker reset itself keeps the sid, so the eventual recovery re-loads natively rather than re-paying context. The 're-pay context' part applies only when that load also fails and falls back to replay.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/llm_helpers.py:266-279 — `acp_error_is_session_not_found`: 'the binding keeps naming a session the backend has dropped, and every later prompt draws the same answer … the remedy is a fresh process that re-loads the SAME id'
  - src/kiro_crew/dashboard/chat_runner.py:17479-17530 — the only caller: `needs_session_reset = True`, one requeue via ReplayFamily.SESSION_NOT_FOUND
  - grep acp_error_is_session_not_found / 'session not found' → no hit in slack/, messaging/, channel.py, subagent_manager/, llm_helpers.stream_and_collect
  - src/kiro_crew/slack/handler.py:2286-2291 — `except AcpError as e: … answer.accumulated = f"❌ {e}"; await sessions.record_failure(session_key)` (no reset)
  - src/kiro_crew/session.py:749 — `_CIRCUIT_BREAKER_THRESHOLD = 5`; src/kiro_crew/session_allocation.py:1479-1485 — breaker `await self._owner.reset(key)` after 5 consecutive failures
  - src/kiro_crew/messaging/dispatch.py:1326-1333 — non-death failures `await sessions.record_failure(session_key)`
  - src/kiro_crew/subagent_manager/run.py:2810-2812 — `if not acp_error_is_transient(exc): raise` → run failed
- **Checked by:** read
- **Required outcome:** Every turn loop that can see a 'session not found' answer (Slack, the messaging dispatcher, channel agents, sub-agents, the task runner and stream_and_collect callers) applies the dashboard's recovery: reset the live binding, keep the session-map sid, and replay the turn once on a fresh process (continue instead of replay when the turn already emitted output or ran tools). A second loss in the same turn surfaces as today.
- **Solution:**
  1. Lift the dashboard's decision into a shared helper next to `acp_error_is_session_not_found` (llm_helpers.py:266), e.g. `recover_session_not_found(sessions, key, *, emitted)`, returning replay/continue/give-up. The dashboard keeps its own requeue mechanics and uses the helper for the decision.
  2. Call it from slack/handler.py's `except AcpError` arm (:2286), messaging/dispatch.py's failure arm, channel.py run_channel_agent (:1656), subagent_manager/run.py `_stream_with_transient_retry` (:2810, before `raise`) and stream_and_collect's AcpError branch (llm_helpers.py ~:2590). Use `sessions.reset(key)` (keeps the sid) and one replay per turn.
  3. Keep the turn-loop contract from docs/architecture/context-management.md: `rearm_reinjection` and `rollback_skill_bodies` on the failed attempt.
  4. Update docs/system-specs/modules/session.md (Recovery ladder) and messaging.md in the same commit.
- **Done when:** Per-surface test with a fake provider that raises AcpError('Session not found') once, then answers: the Slack handler, the messaging dispatcher and a sub-agent run each deliver the answer on the same user message; `sessions.reset` was awaited once with the sid preserved in the session map; a provider that raises it twice yields one error reply and no third attempt.
- **Upstream:** #16225 PR #16428 (state unverified)
- **Changed from the source claim:** Confirmed. Clarified that channel threads recover only after the 5-failure circuit breaker, and that the breaker's reset re-loads the same backend id natively. 'Re-pay context' therefore applies only when that load fails.
- **Sources:** verify_needed:G25(#16225), verify_needed:G37

### SES-2 [30, default, effort M] Retry families stack with no per-message cap — CONFIRMED
- **Verified claim:** The dashboard chat runner keeps one counter per recovery family on the slot, each with its own budget, and resets them only when a turn lands; there is no shared per-message cap. Budgets at HEAD: transient 5xx 3 (then the throttle-fallback chain, 2 attempts per candidate; agent.fallback_model defaults to 'auto'), prompt-busy 3, process/pipe death 3, stale-turn recovery 3, tool-stall recovery 3, gateway-capacity (L1 infra) 3, compaction-failed 2, empty response 1 verbatim + session.empty_response_max_continues (default 1), promise-only 1, post-compaction continue 1, session-not-found 1, and one each for the model-access swap, image-history discard and content-filter retry. Summed, one message can be re-sent or continued about 25+ times in theory; the cold resends (fresh process + full session-start context: death 3, busy 3, session-not-found 1, image-history 1, compaction-failed 2) top out around 10, realistically 4-6 when two failure classes chain.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/chat_runner.py:17620 — `slot._transient_5xx_retries < TRANSIENT_RETRIES` (llm_helpers.py:108 `_TRANSIENT_RETRIES = 3`)
  - src/kiro_crew/dashboard/chat_runner.py:17805 — fallback swap rewinds the counter: `slot._transient_5xx_retries = fallback_rewound_transient_budget()` (llm_helpers.py:332 `FALLBACK_CANDIDATE_ATTEMPTS = 2`; config/sections.py:838-839 `fallback_model` default 'auto')
  - src/kiro_crew/dashboard/chat_runner.py:17416 — prompt busy: `_exhausted = slot._prompt_busy_retries > 3`
  - src/kiro_crew/dashboard/chat_runner.py:15621, :17284 — process death: `_death_attempts < SESSION_RECOVERY_MAX_ATTEMPTS` (recovery/ladder.py:122 = 3)
  - src/kiro_crew/dashboard/chat_runner.py:15412, :15472 — stale and tool-stall recovery each `< STOP_RECOVERY_MAX_RETRIES` (acp/types.py:479 = SESSION_RECOVERY_MAX_ATTEMPTS = 3)
  - src/kiro_crew/dashboard/chat_runner.py:15535 — `slot._compaction_failed_retries < _COMPACTION_FAILED_RETRIES` (:1916 = 2)
  - src/kiro_crew/dashboard/chat_runner.py:16009, :16033 — empty response: 1 verbatim replay + `1 + _empty_max_auto_continues()` (chat_turn/recovery.py:50, default 1)
  - src/kiro_crew/dashboard/chat_runner.py:16289 + recovery/ladder.py:132 — L1 infra retry, `max_attempts=3`, its own `slot._infra_retries` counter (:16321)
  - …and 3 more in the verification results.
- **Checked by:** read
- **Required outcome:** One counter per user message counts every re-send of that message or a continuation of it, across all recovery families, and stops at a cap (about 4 cold resends); past it the runner surfaces one give-up notice with a retry button instead of starting another family's ladder. Each family keeps its own per-family budget below the shared cap.
- **Solution:**
  1. Add `slot._message_resends` (reset where the family counters reset, chat_runner.py:16764-16813, i.e. only when a turn lands or a genuine new user message starts).
  2. Route every requeue through one helper (the `_queue_recovery` call sites at chat_runner.py:15412, :15472, :15535, :15621, :16009/:16033, :16289, :17284, :17416, :17498, :17620/:17805 and the ReplayFamily replays in dashboard/recovery_replays.py) that increments it and refuses past `_MESSAGE_RESEND_CAP` (count cold resends — a fresh process plus replay — separately from warm continuations if the maintainer wants different numbers).
  3. On refusal emit the existing terminal error row for that family (non-retry `msg-err`, no TRANSIENT_RETRY_KIND tag) so the UI offers a manual retry.
  4. This lowers how hard the runner retries before giving up, which is a take-away change: read docs/system-specs/common/take-away-changes.md, grep docs/decisions/, and get the cap number confirmed by the user. Update docs/system-specs/modules/session.md (Recovery ladder section) in the same commit.
- **Done when:** A runner test with a fake provider that fails each attempt with a scripted sequence (death, busy, death, transient, session-not-found, busy…) and a frozen clock (recovery delays patched to return immediately) sends the user message at most cap+1 times, then appends exactly one non-retry error row; a sequence that lands on attempt 2 resets the counter so the next message gets the full cap.
- **Upstream:** #16707 (state unverified; distinct claim, see LOOP-22) #8285 (state unverified; distinct claim, see SES-8) #7395 (state unverified; distinct claim, see LOOP-23) #15231 (state unverified; opposite direction, see SES-9) PR #12232 PR #7396 (state unverified)
- **Changed from the source claim:** Mechanism confirmed. The '~20 sends' figure is re-counted per family at HEAD: about 25+ theoretical re-sends or continuations, about 10 of them cold. More families exist than the source names: L1 infra, stale and tool-stall, compaction-failed, empty-response and the ReplayFamily one-shots. The upstream items attached by FIX_PLAN are separate claims.
- **Sources:** FIX_PLAN:SES-2, REVIEW_FINDINGS:S5, verify_needed:A7, FIX_PLAN-old:A-10

### CTX-12 [30, armed, effort S] Non-dashboard turn loops never arm reinjection after a backend compaction — CONFIRMED
- **Verified claim:** Confirmed by code, and the code documents it as a known soft failure. needs_reinjection is armed in four situations: by a fresh session; by Crew's own session_compaction (session_compaction.py:1307, for every surface, because Crew triggered it); by the dashboard runner when the backend reports a completed compaction mid-turn (chat_runner.py:8285, called at :14590/:15701/:15719); and by the heartbeat (slack/gateway.py:6347-6348). The shared messaging TurnDriver receives EVENT_COMPACTION_STATUS and only renders a notice (messaging/driver.py:961-963). The Slack, Telegram, Discord, Teams, WeCom, Feishu, Webex, WhatsApp, iMessage and Weixin dispatchers, the task runner and cron therefore never arm the flag after a BACKEND self-compaction, such as kiro-cli compacting mid-turn or the claude/codex equivalents. Because the managed spec prompt is a stub (turn.py:65-67), such a session then runs on without its [AGENT SYSTEM PROMPT] contract, memory index, skills index, reply-style preferences and folder steering until a new session starts. The skill-body dedup record also goes stale. REVIEW_FINDINGS §5.3 ('degrades to a pointer, never to silence') is true but describes only the skill-body side effect. test/test_reinjection_gate.py pins that compacting loops consume and re-arm the flag; it does not pin that they arm it on a backend report.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context.py:3542-3555 — 'SOFT FAILURE (known, bounded): the flag is armed by a fresh session, by Kiro Crew's own session_compaction, and by the dashboard runner and the heartbeat … Other turn loops do not watch for that report … deliberately out of scope here.'
  - src/kiro_crew/dashboard/chat_runner.py:8282-8285 _restore_skills_context_after_compaction; called at :14590 on event.text == 'completed'
  - src/kiro_crew/slack/gateway.py:6347-6348 — heartbeat: if _turn_compaction['completed']: self.sessions.mark_needs_reinjection(session_key)
  - src/kiro_crew/messaging/driver.py:961-963 — EVENT_COMPACTION_STATUS → renderer.dispatch(OutputEvent(kind=COMPACTION…)) only
  - grep mark_needs_reinjection( callers: session.py:2766, session_compaction.py:1307, dashboard/chat_runner.py:8285, dashboard/handlers/members.py:1968, slack/gateway.py:6348 — none in messaging/, */transport_dispatch.py, task_executor.py, cron
  - src/kiro_crew/context_assembly/turn.py:65-67 — managed spec prompt is a stub pointing at the [AGENT SYSTEM PROMPT] block
  - test/test_reinjection_gate.py:159 — test_every_compacting_turn_loop_consumes_needs_reinjection (consumption only)
- **Checked by:** read
- **Required outcome:** Every multi-turn loop arms needs_reinjection when the backend reports a completed compaction during its turn, as the dashboard runner and the heartbeat already do. The next turn then restores the session-start contract and resets the skill-body record. The gate test pins the arming as well as the consumption.
- **Solution:**
  1. In messaging/driver.py:961, record self.compaction_completed = True when the event's status is 'completed', whether real or synthesized at turn end by the claude path.
  2. In messaging/dispatch.py, in the shared pipeline (drive_turn / ChannelTurns), call sessions.mark_needs_reinjection(session_key) in the turn's finally when driver.compaction_completed is set, BEFORE rearm_reinjection and rollback_skill_bodies. This is the order the heartbeat uses at slack/gateway.py:6343-6352. That covers every transport_dispatch that delegates to the driver.
  3. Do the same for task_executor.py and the cron runner if they hold a session across turns. Subagent single-turn runs need it only for continuations.
  4. Extend test/test_reinjection_gate.py with an AST/behavioural check that each module observing EVENT_COMPACTION_STATUS also arms the flag.
  5. Remove the SOFT FAILURE paragraph at context.py:3542-3555 and the 'Known soft failure' bullet at context-management.md:290-293, and update the 'What comes back after compaction' section in the same commit.
- **Done when:** A messaging dispatch test with a fake provider whose stream yields EVENT_COMPACTION_STATUS('completed') and then EVENT_COMPLETE: after the turn, sessions.consume_reinjection(key) returns True. The next build_message for that key carries '[AGENT SYSTEM PROMPT]'. A turn without the event leaves the flag unset.
- **Upstream:** #17467 PR #17489 PR #15107 (state unverified)
- **Changed from the source claim:** Confirmed by code. The A4/G2 conflict is resolved: §5.3's 'pointer, not silence' covers only the skill-body record. The wider loss of the contract and context on non-dashboard loops is real and is documented in context.py itself. Severity is raised from A4's 25 to 30/armed, because channel and cron loops need setup.
- **Sources:** verify_needed:A4, verify_needed:G2(#17467), verification_needed:Part3(#17467), verify_needed:G36, verify_needed:G37, verify_needed:delta-6

### CTX-28 [30, armed, effort S] learn_list reports no lessons for a V1 named store after restart — CONFIRMED
- **Verified claim:** Confirmed by code read: GET /api/lessons (what learn_list renders) prepares only V2 member stores (_prepare_member_lesson_store calls ensure_store only when memory_store_version == 2). For a V1 named store whose vector tier has not been built since the gateway started (no turn on that store has called ContextBuilder.ensure_store yet), get_memory_for returns a MemoryStore whose vector_store is _vector_stores.get(name) == None, so vs_lessons is [] and the handler falls back to the silo's lessons JSONL file; lessons that were written to that store's vector DB are not listed and learn_list answers 'No lessons saved.' until something builds the store's vectors (_build_store_vectors then back-fills cached.vector_store). Other dashboard handlers avoid this by calling the shared helper that awaits ensure_store for any silo. Related (suspected, not verified): POST /api/lessons (learn_add) has the same gap, so a lesson added in that window goes to the JSONL tier and is hidden again once the vector tier loads, because the list prefers a populated vector tier.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_tools/learn.py:477-498 — learn_list -> GET /api/lessons; empty 'lessons' -> 'No lessons saved.'
  - src/kiro_crew/dashboard/handlers/cron.py:2732-2744 — _prepare_member_lesson_store: 'if store and memory_store_version(store) == 2: ContextBuilder.ensure_store(store)' (V1 never prepared)
  - src/kiro_crew/dashboard/handlers/cron.py:3697-3705 — api_lessons: _lesson_mem = ContextBuilder.get_memory_for(memory_store=_lesson_silo); vs = _lesson_mem.vector_store
  - src/kiro_crew/dashboard/handlers/cron.py:3714-3780 — vs_lessons = [] if vs is None; vs_populated False -> rows = _lesson_jsonl_store(state, _lesson_silo).load_all() (an empty silo answers 'no lessons')
  - src/kiro_crew/context.py:1733-1744 — get_memory_for(named): MemoryStore(..., vector_store=_vector_stores.get(store_name)); 'Prepared stores come from ensure_store, which the caller must await first'
  - src/kiro_crew/context.py:1787-1808 — ensure_store builds _vector_stores[name] via _build_store_vectors (V1 'legacy initialization')
  - src/kiro_crew/context.py:~635-643 (_build_store_vectors) — _vector_stores[name] = store; cached.vector_store = store (only once built)
  - src/kiro_crew/dashboard/handlers/_shared.py:3869-3886 — the shared silo helper other handlers use awaits ContextBuilder.ensure_store(store) for any silo
  - …and 1 more in the verification results.
- **Checked by:** read
- **Required outcome:** learn_list (and learn_add / learn_remove) on a V1 named store answer from that store's vector tier immediately after a gateway restart, without waiting for a turn on that store; a store whose vectors cannot be built is reported as unavailable, not as empty.
- **Solution:**
  1. Read docs/system-specs/modules/memory-skills-hooks.md (named V1 stores, lessons) first and update it in the same commit.
  2. In dashboard/handlers/cron.py replace _prepare_member_lesson_store's V2-only branch (:2739-2741) with the shared silo helper in dashboard/handlers/_shared.py (~:3869-3886), i.e. await ContextBuilder.ensure_store(store) for every named store, keeping the V2 'unavailable -> 503' refusal and returning a 503 store_unavailable (not an empty list) when a V1 store's vector tier cannot be built.
  3. This covers api_lessons (:3697), api_lessons_create (:2849) and the delete handler (:3184), which share the helper.
  4. Check whether lessons already written to the silo JSONL during the gap need a one-time merge into the vector tier (write_lesson dedups).
- **Done when:** Test: create a V1 named store in a throwaway KIROCREW_HOME, write one lesson through its VectorMemoryStore, clear context._vector_stores/_memory_stores (simulated restart), then call api_lessons via the aiohttp test client (no real port) with a session bound to that store: the response lists the lesson and total == 1. Same for POST then GET in the restarted state: the posted lesson lands in the vector tier.
- **Upstream:** #15513 (state unverified)
- **Changed from the source claim:** Confirmed with mechanism located (V2-only ensure_store in the lesson handlers); added the suspected learn_add write-side twin.
- **Sources:** verify_needed:G40(#15513)

### CTX-9 [25, default, effort S] Protected-content ceiling scales with the 1M auto/unknown window — CONFIRMED
- **Verified claim:** Confirmed and measured. The model-safe ceiling for protected content is protected_context = max(_PROTECTED_CONTEXT_FLOOR = 99,000, window x 4.0 x 0.125). The window comes from _effective_window(resolve_model_window(model)), and auto, empty and unregistered ids all resolve to the 1M reference, so the ceiling is 500,000 chars. Registry 200k models get 100,000. In practice the ceiling binds only protected blocks that lack their own cap. Pinned skill bodies have a 99K capacity and fail closed. Lessons have the 37K startup tier and pref.* rows the 12.7K cap. The preferences file (CTX-3) is the main block that can grow toward 500K. On a session whose live window is 200k but whose id resolves to the reference (auto if served at 200k, or an unknown id), 500K chars (about 125k tokens) of protected text could alone exceed the 70% compaction point.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context_assembly/budget.py:121-124 — _PROTECTED_CONTEXT_CHARS_PER_TOKEN = 4.0, _PROTECTED_CONTEXT_WINDOW_FRACTION = 0.125, _PROTECTED_CONTEXT_FLOOR = _CONTEXT_BUDGET_BASE * 3
  - src/kiro_crew/context_assembly/budget.py:205-208 — protected_context=max(_PROTECTED_CONTEXT_FLOOR, int(window * 4.0 * 0.125))
  - src/kiro_crew/context_assembly/budget.py:158-170 — _effective_window(None) -> 1_000_000; :213-229 resolve_model_window('auto'|'') -> None
  - src/kiro_crew/context.py:816-821 — _resolve_caps(window) = _resolve_caps_cached(_effective_window(window))
  - src/kiro_crew/context_assembly/store_admission.py:241-256 — preferences bounded only by caps.protected_context
  - measured: auto/''/'some-new-model-x'/'gpt-5' -> protected 500000; claude-opus-4.5/sonnet-4.5/haiku-4.5 (200k) -> 100000
- **Checked by:** ran-new-script
- **Required outcome:** The protected ceiling is a fixed absolute cap, the 99K floor, independent of the resolved window. A warning is logged, and a notice placed in the prompt, whenever a protected block is cut.
- **Solution:**
  1. In budget.py:205-208, set protected_context=_PROTECTED_CONTEXT_FLOOR, or min(floor, window x 4 x 0.125) for windows known to be smaller. Keep the two factor constants only if a smaller-window floor is still wanted.
  2. Check every reader of caps.protected_context (store_admission.py:241, :426, :466-478) still yields a complete-entry render. The lessons fallback re-render path relies on the ceiling.
  3. Land together with CTX-3, which removes the main block that can approach the ceiling.
  4. Update context-management.md:164-170 and memory-skills-hooks.md:5820, which spell out the formula, in the same commit. This is a take-away change for anyone with a protected set between 99K and 500K: note it in the PR's Reader: list.
- **Done when:** A unit test: _resolve_caps(None).protected_context == 99_000 and _resolve_caps(1_000_000).protected_context == 99_000. A ContextBuilder test with a 150K-char preferences file on a tmp home keeps the head plus the omission notice under the 99K ceiling and logs the 'Preferences exceed model-safe ceiling' warning.
- **Changed from the source claim:** The formula and the 500K figure hold. Added: the ceiling in practice binds only the preferences file, because other protected blocks carry their own caps, so this item is mostly subsumed by CTX-3. Severity is lowered from 35 to 25.
- **Sources:** FIX_PLAN:CTX-9, REVIEW_FINDINGS:C5

### SES-10 [25, default, effort S] Effort/env lost on a compaction restart — CONFIRMED
- **Verified claim:** When a kiro-cli in-place /compact fails or times out, `_restart_held` starts the successor from `allocation_identity`. The recycling reset successor (session_lifecycle.py:1604) is built the same way. That identity carries agent, approval policy, cwd, channel, the requested model and the crew member, but no reasoning-effort override and no extra_env; the helper's docstring admits extra_env 'cannot be carried'. The successor's factory therefore resolves effort from `resolve_session_effort` (crew pin or role default) instead of the dashboard slot's or sub-agent's override. A later turn claims the live successor as-is and does not re-push effort; effort is re-pushed only on pool claims or a user change. Env is lost for every caller that passes it: a cron's job.env plus KIROCREW_APPROVAL_MODE (so the cron's spawn_run children can fall back to interactive approval with no responder), workflow pool env, and app env. On kiro-cli the effort can survive by accident, because the predecessor's level is still in the workspace cli.json overlay keyed by (work_dir, model). Adapter harnesses that take effort as a live set_config_option push lose it.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/session_lifecycle.py:404-420 — `allocation_identity`: returns agent, approval_policy, cwd, channel_id, model, crew_agent; docstring 'A caller's extra_env is not recorded on a session, so it cannot be carried.'
  - src/kiro_crew/session_compaction.py:1091-1106 — 'in-place /compact failed … recycling' → `return await self._restart_held(key, session, pct)`
  - src/kiro_crew/session_compaction.py:922-926 — `await owner.get_or_create(key, **allocation_identity(owner, key, session), speculative=True)` (no reasoning_effort_override, no extra_env)
  - src/kiro_crew/config/loader.py:4662 — factory: `_eff = reasoning_effort_override or self.resolve_session_effort(agent, crew_agent)`
  - src/kiro_crew/dashboard/chat_runner.py:10033 — the dashboard passes `reasoning_effort_override=slot.reasoning_effort or None` only at allocation; src/kiro_crew/subagent_manager/run.py:2166 the sub-agent's resolved effort likewise
  - src/kiro_crew/session_allocation.py:2510-2514 — the only in-allocation `change_effort` re-push is the warm-pool post-claim arm
  - src/kiro_crew/slack/gateway.py:4550-4565, :4669, :4704 — cron passes `extra_env=_cron_extra_env()` (job.env + KIROCREW_APPROVAL_MODE); workflows/agent_pool.py:141 and apps/routes.py:1581 also pass extra_env
  - src/kiro_crew/providers/acp.py:1791-1805 — `_apply_effort_overlay` writes the kiro workspace cli.json only for ACP_BACKENDS_KIRO_SLASH_COMMANDS and only when a level resolves (why kiro may keep the predecessor's level)
- **Checked by:** read
- **Required outcome:** A session restarted for compaction, and the recycling-reset successor, keep the effort override and caller env their predecessor was allocated with, on every harness.
- **Solution:**
  1. Record the allocation's `reasoning_effort_override` and `extra_env` on the session entry at registration (session_allocation.py, beside `session.requested_model = model or ""` at :2752), the way `requested_model` is stamped. Store env as-is in memory only; never persist it, since cron job.env can hold secrets.
  2. Add both to `allocation_identity` (session_lifecycle.py:404-420) and drop the 'cannot be carried' sentence.
  3. Both successors (`_restart_held`, session_compaction.py:922; `_respawn_as`, session_lifecycle.py:1604) then pass them through unchanged.
  4. Update docs/system-specs/modules/session.md: the reset row in the APIs table says 'a caller's extra_env is not recorded on a session and is not carried'. Update it in the same commit, and read runtime-ownership.md because the respawn is a kill path.
- **Done when:** SessionManager test with a fake provider factory that records its kwargs: allocate key K with reasoning_effort_override='high' and extra_env={'KIROCREW_APPROVAL_MODE': 'auto'}, make the fake /compact raise, run compact_if_needed(K) → the successor factory call received reasoning_effort_override='high' and the same extra_env; same assertion for `reset(K)` with a queued entry (the successor path).
- **Upstream:** #14442 (state unverified)
- **Changed from the source claim:** Confirmed from code. It is wider than compaction: the recycling-reset successor uses the same identity. The env loss includes the cron approval-mode variable. On kiro-cli, effort may survive via the shared workspace cli.json overlay; adapter harnesses lose it.
- **Sources:** verify_needed:G25(#14442)

### SES-9 [25, default, effort S] Dashboard turns fail during short network drops — CONFIRMED
- **Verified claim:** A connection-class error on a dashboard turn (dispatch failure, connection reset, ECONNRESET), arriving before any token or tool call, is re-prompted on the live session at most TRANSIENT_RETRIES = 3 times. The backoff is 2/4/8 s plus up to 25% jitter: 14.2-17.4 s in total, unless the dependency coordinator's shared cooldown floors it longer. Then the turn ends with 'Connection unstable — please try again.' On the default fallback_model 'auto' with an 'auto' slot model, the fallback swap has no other candidate, so a network drop longer than about 15 s fails the turn. 'operation timed out' is not classified transient at all.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/chat_runner.py:17617-17620 — `elif (not _turn_emitted and acp_error_is_transient(exc) and slot._transient_5xx_retries < TRANSIENT_RETRIES)`
  - src/kiro_crew/llm_helpers.py:108-109 — `_TRANSIENT_RETRIES = 3`, `_TRANSIENT_DELAY = 2.0`; :282-290 exponential + jitter
  - ran fork2/ses9_backoff.py — total backoff before give-up min 14.2 s, max 17.4 s over 2000 draws; 'dispatch failure', 'connection reset by peer', 'econnreset' → transient True; 'operation timed out' → False
  - src/kiro_crew/dashboard/chat_turn/recovery.py:282 — `_shared_dependency_delay` floors the local delay by the scope's shared `retry_at` (can only lengthen)
  - src/kiro_crew/dashboard/chat_runner.py:17745-17751 — fallback swap after exhaustion via `_fallback_swap_for_turn`; llm_helpers.py:1070-1094 default chain `("auto",)`
  - src/kiro_crew/dashboard/chat_utils.py:3777 — `TRANSIENT_GIVE_UP_TEXT = "⟳ Connection unstable — please try again."`
- **Checked by:** ran-new-script merge/scripts/B/fork2/ses9_backoff.py
- **Required outcome:** A pre-token connection-class failure on the dashboard survives a short network drop (on the order of a minute) without failing the turn. The budget is time-based for connection errors, not a flat 3, because these re-prompts fail before any token streams and cost nothing. Throttles and 5xx keep their current budget. All of these count toward SES-2's per-message cap.
- **Solution:**
  1. Split the classifier result: connection-class markers (llm_helpers.py `_TRANSIENT_MARKERS` 'dispatch failure', 'connection reset', 'econnreset', the client._RE_CONNECTION tokens) vs throttle/5xx.
  2. In the dashboard transient arm (chat_runner.py:17617-17660), give connection-class errors a wall-clock budget (e.g. `session.connection_retry_secs`, default about 60-90 s, capped per-attempt backoff), read through an injected clock; throttle/5xx keep TRANSIENT_RETRIES.
  3. Mirror in stream_and_collect Case 2 (llm_helpers.py:2641) and subagent `_stream_with_transient_retry` (subagent_manager/run.py:2682) per the parity note there.
  4. Coordinate with SES-2: these pre-token warm re-prompts should not count as cold resends.
  5. Update docs/system-specs/modules/session.md (Recovery ladder) and the config help text in the same commit. It raises retry tolerance, so it is not a take-away change, but grep docs/decisions/ for a recorded retry budget first.
- **Done when:** Runner test with an injected monotonic clock and recovery delays patched to advance it: a fake provider that raises 'dispatch failure' for 45 simulated seconds then succeeds lands the turn; one that fails for longer than the budget ends with TRANSIENT_GIVE_UP_TEXT; a 5xx sequence still gives up after 3 retries.
- **Upstream:** #15231 (state unverified)
- **Changed from the source claim:** Confirmed, with measured numbers: 3 retries over 14-17 s. The default fallback chain adds no rescue when the slot model is already 'auto'. 'operation timed out' is not transient at all. This pulls in the opposite direction from SES-2, as the inventory notes.
- **Sources:** verify_needed:G35(#15231), verify_needed:G38, verify_needed:G41

### CTX-10 [20, default, effort M] Post-compaction reinjection re-sends the full agent prompt and pinned skill bodies — CONFIRMED
- **Verified claim:** Confirmed and measured. After a confirmed compaction, post_compaction_parts re-injects five things in full: the agent prompt (about 40.3 KB), the memory activity index plus the [Memory tools] line (about 1.8 KB), the skills index together with every pinned always:true body (bounded by the 99,000-byte pinned capacity), the reply-style preferences, and folder steering. The member section goes through the caller. No digest or pointer is used for blocks that have not changed. Measured with the real builder on a seeded home (no pinned bodies): a follow-up turn is 1,933 B, and the re-injection turn is 49,054 B, so each compaction pass adds about 47 KB (about 12k tokens). Pinned bodies add up to 99K more (about 25k tokens), which gives the 11-35k-token range. The original C6 claim ('whole session-start context') is wrong, and X3 has the correct subset. Memory preferences, lessons, [CRITICAL RULES], [CURRENT DATE]/identity, the user profile and thread history are all NOT re-injected (see CTX-14). Two constraints limit the fix. The agent prompt cannot be replaced by a pointer: the managed spec prompt is a stub, so without the block the session has no contract, and the real fix is SPEC-2, which puts the prompt where kiro-cli keeps it. Pinned bodies are 'required instructions' by contract, so turning them into pointers is a take-away change.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context_assembly/turn.py:33-163 — post_compaction_parts: _resolve_agent_prompt(session_start=False) → '[AGENT SYSTEM PROMPT]…'; activity_index + [Memory tools]; skill_parts(required_skills + skills_ctx); response preferences; folder steering
  - src/kiro_crew/context_assembly/turn.py:65-67 — 'The managed spec prompt is a stub pointing at this block, so a compaction that drops it leaves the session with no contract.'
  - src/kiro_crew/skills.py:313 — PINNED_SKILL_BODIES_CAP = 99_000
  - docs/architecture/context-management.md:467-470 — always:true bodies are required instructions with a 99,000-byte capacity; 'never silently truncated or deferred'
  - measured (seeded home): followup 1,933 B; reinjection turn 49,054 B (+47,121 B): agent prompt 175→40,449, activity index 40,476→42,247, skills index 42,363→47,257
  - measured: reinjection turn lacks '## User Preferences', '[Learned corrections', '[CRITICAL RULES' present in the first turn
- **Checked by:** ran-new-script
- **Required outcome:** A compaction pass re-sends only what the session needs and cannot get back another way. The agent contract is restored without re-sending 40 KB as user text where the backend can hold it natively (SPEC-2). Optional discovery (the skills index) becomes a pointer to skill_search. Whether pinned bodies become pointers is decided explicitly as a take-away change.
- **Solution:**
  1. Land SPEC-2 first: once kiro-cli carries the agent prompt in the spec, which survives compaction natively, drop the [AGENT SYSTEM PROMPT] re-send for those backends in turn.py:70-80. Gate it positively on the harness capability set (H-invariants), never on 'not claude'.
  2. Replace the re-injected skills index (turn.py:95-118) with a one-line skill_search pointer when the session already received the index at start. Keep required always:true bodies as they are unless the user confirms the take-away change (take-away-changes.md; required instructions 'never silently deferred').
  3. Keep the activity index (1.8 KB) and reply-style preferences, which are small.
  4. Pair with CTX-2 (cap passes) so the cost cannot repeat unbounded.
  5. Update context-management.md §2 'What comes back after compaction' in the same commit.
- **Done when:** A ContextBuilder test on a seeded tmp KIROCREW_HOME: build_message(..., is_new_session=False, needs_reinjection=True) for a backend in the native-prompt capability set carries no '[AGENT SYSTEM PROMPT]' and ≤ 8,000 B beyond the follow-up baseline. For other backends, the agent prompt is still present.
- **Upstream:** PR #17489 (state unverified)
- **Changed from the source claim:** The subset is corrected as X3 had it, and the size is measured at +47 KB/pass without pinned bodies. Severity is lowered from 30 to 20: it fires once per compaction and mostly restores the contract the session needs. The fix is re-scoped: the agent prompt cannot become a pointer (SPEC-2 is the lever), and pinned bodies are a take-away decision.
- **Sources:** FIX_PLAN:CTX-10, REVIEW_FINDINGS:C6, REVIEW_FINDINGS:X3, verify_needed:X3

### CTX-11 [20, default, effort S] Memory dict/list values escape non-ASCII via json.dumps (4.3x for CJK); strings fine — PARTLY
- **Original claim:** Semantic memory values injected with JSON escapes (~6x) (corrected below)
- **Verified claim:** The escape overhead is real but narrower than '~6x'. get_semantic_context renders dict and list values with json.dumps(val), whose default ensure_ascii=True turns every non-ASCII character into a 6-character \uXXXX escape. Plain string values go through str(val) and get no escapes. The startup pref.* block (get_preferences_context) already uses ensure_ascii=False. Measured with the same expressions: a CJK dict renders at 4.30x its content, a mixed-accent list at 2.46x, and ASCII strings, ASCII dicts and CJK strings at 1.00x. The path is reached on the default install, because memory.inject_activity defaults to True ([Memory activity] task facts). memory_recall reuses it through recall.py:84. The impact is limited to non-ASCII content in structured fact values.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/vector_memory_runtime/semantic.py:1027 — val_str = json.dumps(val) if isinstance(val, (dict, list)) else str(val) (get_semantic_context; ensure_ascii default True)
  - src/kiro_crew/vector_memory_runtime/semantic.py:857-859 — get_preferences_context: json.dumps(value, ensure_ascii=False) … else str(value)
  - src/kiro_crew/memory.py:995, :1128 and src/kiro_crew/vector_memory_runtime/recall.py:84 — callers of get_semantic_context (activity block, memory_recall)
  - src/kiro_crew/config/memory_sections.py:261-262 — inject_activity default True
  - measured: CJK dict 47 → 202 chars (4.30x); mixed list 41 → 101 (2.46x); ASCII string/dict and CJK string 1.00x
- **Checked by:** ran-new-script
- **Required outcome:** Structured semantic values reach the prompt and memory_recall unescaped (ensure_ascii=False), as pref.* rows already do. A non-ASCII dict value is injected at ≤ 1.1x its content length.
- **Solution:**
  1. vector_memory_runtime/semantic.py:1027: change to json.dumps(val, ensure_ascii=False). Optionally use compact separators (',', ':'), matching TOOL-13's compact helper.
  2. Check _is_degenerate_value_json and any lone-surrogate handling (semantic.py:249 notes json.dumps(ensure_ascii=False) accepts lone surrogates). Make the render path tolerate a surrogate the same way the write path does, so the prompt build never raises.
  3. Update memory-skills-hooks.md where the semantic render is described, in the same commit.
- **Done when:** A unit test seeds a VectorMemoryStore (tmp KIROCREW_HOME, no embedder) with key 'fact.x' and value {'偏好': '用户喜欢简洁的回答'}. It asserts get_semantic_context(query_text='') contains '用户喜欢简洁的回答' verbatim, and that the rendered value length is ≤ 1.1x len(json.dumps(value, ensure_ascii=False)).
- **Upstream:** #15570 #10378 #10391 (state unverified)
- **Changed from the source claim:** '~6x' is narrowed: up to 6x per non-ASCII character, only in dict/list values of the activity/recall render (4.3x measured for a CJK dict). There is no overhead on string values or on the startup pref.* block. Severity is lowered from 35 to 20.
- **Sources:** FIX_PLAN:CTX-11, verify_needed:G26(#15570), verify_needed:G38, verify_needed:G41

### CTX-25 [20, default, effort S] /compact as the first message of a new chat waits 300 s — CONFIRMED
- **Verified claim:** Crew side confirmed: on the dashboard, a /compact sent as the first message of a brand-new chat on kiro-cli is dispatched as a turn, its streamed text is purged, and the runner then awaits client.wait_for_compaction(timeout=compact_wait_budget_secs()) - 300 s by default - which only ends on a _kiro.dev/compaction/status 'completed'/'failed'; nothing recognises kiro-cli's 'Conversation too short to compact.' answer. The only short-circuit is the replay-pending case (COMPACT_REPLAY_PENDING_NOTICE), which is armed only when the session owes a Kiro Crew history replay or switched provider - not for a fresh chat with no history - so the user waits the whole budget and gets 'Compaction timed out.' Crew's own comment states kiro-cli 'ends the turn with "Conversation too short to compact." and sends no compaction status' on an empty provider session; that kiro-cli behaviour itself was not observed here (no kiro-cli installed).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/chat_runner.py:15656 — if first_word == '/compact' and not saw_compaction: slot.purge_chunks() (the 'too short' text is discarded)
  - src/kiro_crew/dashboard/chat_runner.py:15692-15699 — if _replay_pending: ... 'kiro-cli ends the turn with "Conversation too short to compact." and sends no compaction status. Awaiting one would hold the chat for the whole compaction timeout.' -> COMPACT_REPLAY_PENDING_NOTICE
  - src/kiro_crew/dashboard/chat_runner.py:15712-15715 — else: client.wait_for_compaction(timeout=state.sessions.compact_wait_budget_secs()); :15752 'Compaction timed out.'
  - src/kiro_crew/dashboard/chat_runner.py:10180 — _replay_pending = state.sessions.provider_switch_replay_pending(session_key) is True
  - src/kiro_crew/session_allocation.py:2763-2764 — provider_switch_replay armed only if provider_switched or replay_needed (a brand-new chat has neither)
  - src/kiro_crew/acp/client.py:14584-14627 — wait_for_compaction loops until completed/failed status or the deadline; returns {'type':'timeout'}
  - src/kiro_crew/constants.py:203 — COMPACT_WAIT_TIMEOUT_SECS = 300.0 (session.compact_wait_secs default 0 -> built-in)
  - grep -i 'too short|nothing to compact' over src/kiro_crew — only the comment above and the replay-pending notice; no text match on the reply
- **Checked by:** read
- **Required outcome:** A /compact on a provider session that holds no conversation answers immediately with the existing 'nothing to compact yet' wording and never waits for a compaction status.
- **Solution:**
  1. Read docs/system-specs/modules/session.md (Context compaction, manual /compact) and update it in the same commit.
  2. In dashboard/chat_runner.py near :15692, widen the short-circuit from _replay_pending to 'the provider session holds no landed turn': e.g. the session's first-turn observation is still FRESH / the provider reports context_usage_unknown() or a 0-token reading after the slash turn. Prefer a state predicate over matching kiro-cli's sentence; if the sentence is used at all, use it only as a secondary signal in the ACP layer (acp/client.py) and translate it to a 'failed: too short' compaction status so every surface benefits.
  3. Better still, answer before dispatch (near :9506): if no landed turn exists on this session key, reply with the shared 'nothing to compact' text (channels already do this: webex/transport_dispatch.py:2017, whatsapp/commands.py:159) and do not send /compact.
  4. Gate positively on the backend being one whose status arrives asynchronously (not compacts_inline), as the existing arms do.
- **Done when:** Unit test: dashboard runner with a fake kiro-cli client on a brand-new session (no replay lease, no landed turn) handling '/compact' appends the 'nothing to compact' notice and never calls wait_for_compaction (assert the fake's call count is 0); the same with a landed prior turn still calls wait_for_compaction once. No sleeps or real timeouts.
- **Upstream:** #15722 (state unverified)
- **Changed from the source claim:** Confirmed on the dashboard path; kiro-cli's reply text is taken from Crew's own code comment, not observed live. Channel surfaces answer 'no conversation to compact yet' only when no session exists at all.
- **Sources:** verify_needed:G29(#15722)

### LOOP-12 [20, default, effort S] Idle history consolidation fires on a single new prompt row — CONFIRMED
- **Verified claim:** Confirmed by code. check_idle_sessions starts a full history consolidation (_consolidate(key, include_history=True)), which is one LLM call, for any session idle at least memory.history_idle_hours (default 3 h) that has at least one unconsolidated row (the 'unconsolidated < 1' gate). _consolidate then skips the model only when the span has no prompt rows at all. So one new user or assistant row after an idle window bills a whole consolidation pass. That pass carries the fixed prompt (current preferences and projects files plus instructions) no matter how small the span is. Seeded sessions after a restart are forced eligible ((0, 1)), bounded by _SEEDED_PER_SWEEP. The in-memory throttle and the durable retry backoff only stop a repeat within the same idle window.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/history_consolidation.py:1150-1176 — 'if now - last < self._history_idle_secs: continue' … 'unconsolidated < 1 or …' → asyncio.create_task(self._consolidate(key, include_history=True))
  - src/kiro_crew/history_consolidation.py:1161 — total, unconsolidated = (0, 1) if seeded else self._log.consolidation_counts(key)
  - src/kiro_crew/history_consolidation.py:1420-1439 — early returns only for an empty span or a span with no _prompt_rows
  - src/kiro_crew/history.py:3206-3221 — consolidation_counts = (len(messages), len(messages) - last_consolidated)
  - src/kiro_crew/config/memory_sections.py:195-196 — history_idle_hours default 3.0
- **Checked by:** read
- **Required outcome:** An idle consolidation pass runs only when the unconsolidated span carries at least a minimum number of prompt rows (about 4). A shorter tail waits for more rows, or for the session-end consolidation, which still flushes any tail, so nothing is lost permanently.
- **Solution:**
  1. In check_idle_sessions (history_consolidation.py:1163), replace 'unconsolidated < 1' with a constant _IDLE_MIN_PROMPT_ROWS = 4, counted on prompt rows. Prompt rows need a cheap count: extend HistoryLog.consolidation_counts (history.py:3206) to return the prompt-row count of the tail from the same single read. Do not add a second transcript read on the loop.
  2. Keep consolidate_session (the session-end hooks: dashboard close, Slack end, idle expiry) unconditional, so a short tail is flushed when the session ends.
  3. Seeded sessions keep the (0, 1) shortcut, because their transcript is read off-loop. Apply the minimum inside _consolidate for include_history idle passes: return None without marking when there are fewer than 4 prompt rows and the call came from the idle sweep.
  4. Changing when consolidation runs is a cadence change. Read take-away-changes.md, grep docs/decisions/ (no entry today), and get the user to confirm the number. Update memory-skills-hooks.md (consolidation triggers) in the same commit.
- **Done when:** A HistoryConsolidator test with a fake log and a fake clock (_time.time monkeypatched): a session idle 3 h with 1 new prompt row starts no _consolidate task; with 4 rows it starts exactly one; consolidate_session on a 1-row tail still consolidates.
- **Changed from the source claim:** The mechanism is now confirmed by code; the source rated it 'plausible'. Added: seeded sessions are forced eligible, and a fix must keep the session-end flush so short tails are not lost.
- **Sources:** FIX_PLAN:LOOP-12, REVIEW_FINDINGS:H7

### SES-12 [20, default, effort M] Failed /compact recycle keeps only the replay excerpt, no summary; UI labels the restart — PARTLY
- **Original claim:** Failed /compact recycles the ACP process with no turn checkpoint (corrected below)
- **Verified claim:** The recycle exists: when a kiro-cli in-place /compact errors or exhausts its wait budget, `_compact_in_place` falls to `_restart_held`. That starts a successor, hands it the queue, clears the native resume sid ('the overflowed conversation must not resume') and shuts the old process down. No summary is produced, so the successor's next turn starts from Crew's bounded replay, the recent excerpt of the transcript, plus reinjection. Two parts of the claim are refuted. First, there is no user turn in flight: the automatic compaction runs after the turn landed, holding the session semaphore for its own /compact turn, and queued user messages are handed to the successor. Second, the UI does distinguish the outcome: `COMPACT_OUTCOME_RECYCLED` is a separate value, and both the dashboard and the channel surfaces render a dedicated 'Compaction didn't succeed … so the session was restarted instead … starts from a recent excerpt' notice. The real gap is narrower: on a long session everything outside the replay window is discarded, with no gateway-side summary or checkpoint written before the sid is cleared.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/session_compaction.py:1091-1106 — 'in-place /compact failed after %.0fs — recycling (semaphore held…)' → `return await self._restart_held(key, session, pct)`
  - src/kiro_crew/session_compaction.py:903-960 — `_restart_held`: `owner._session_map.clear_sid(key) # the overflowed conversation must not resume`; successor via `get_or_create(..., speculative=True)`; `successor.queue.extendleft(reversed(live))` hands queued messages over
  - src/kiro_crew/session_compaction.py:52 — `COMPACT_OUTCOME_RECYCLED = "recycled"` ('telling a user they were compacted when they were replaced is the exact class of untruth this vocabulary exists to prevent'); :1295-1302 outcome selection from the `_recycling` marker
  - src/kiro_crew/dashboard/state.py:1380-1387 — `_AUTO_RECYCLE_NOTICE = "♻️ Compaction didn't succeed at {pct:.0f}%, so the session was restarted instead. The conversation above is still here, and the agent's next reply starts from a recent excerpt of it…"`; :6366 selects it; dashboard/chat_compaction…
  - src/kiro_crew/session_compaction.py:1311-1314 — `if success and key not in self._owner._recycling: mark_needs_reinjection` (a recycled successor gets full startup context as a new session instead)
  - src/kiro_crew/context_assembly/budget.py — thread-history replay cap `_HISTORY_BUDGET_CHARS` × window factor = 34,650 chars at 1M (8,910-44,550 depending on path/window): the 'recent excerpt' size
- **Checked by:** read
- **Required outcome:** Before a failed compaction discards the native conversation, the gateway keeps a durable digest of what the replay window will not carry: open tasks, decisions and the work-ledger state. The successor's first turn receives it beside the bounded replay, so a recycle loses detail but not the thread of the work.
- **Solution:**
  1. In `_restart_held` (session_compaction.py:903), before `clear_sid`, snapshot what Crew already owns without a model call: the session's work-ledger snapshot (`session_ledger.render_snapshot`), the open todo checklist, and the session summary panel's intents if present (docs/system-specs/modules/session-summary.md). Persist it next to the session-map entry as a one-shot 'recycle carry-over'.
  2. On the successor's first build (`build_message(is_new_session=True)`), add it as a protected block just before the thread-history replay, then clear it.
  3. Do not add a model-generated summary here: the compaction just failed, often on throttle or timeout, and a second model call would fail the same way.
  4. Update docs/architecture/context-management.md ('What comes back after compaction') and session.md in the same commit.
- **Done when:** SessionManager test with a fake provider whose /compact raises and a ledger holding goal 'X' and next 'Y': after compact_if_needed returns 'recycled', the successor's first built message contains the carry-over block naming X and Y before `[THREAD CONVERSATION HISTORY`, and a second turn does not contain it; outcome passed to the callback is 'recycled'.
- **Changed from the source claim:** Corrected. There is no in-flight user turn to checkpoint, and the UI already labels a recycle distinctly. What remains is that a recycle keeps only the bounded replay excerpt, with no carry-over of ledger or task state. Severity 40 → 20.
- **Sources:** verify_needed:B2

### CTX-17 [20, armed, effort M] Changed member essentials envelope re-sends whole — CONFIRMED
- **Verified claim:** Confirmed by code. On every non-fresh turn, build_message rebuilds a private V2 member's essentials envelope and appends it (context.py:3181-3182). EssentialDelivery.stream replaces the envelope with '' only when the acknowledged receipt matches this incarnation, this digest and this epoch. Otherwise it sends the whole envelope, or its native variant. Nothing diffs per source. Conditional (trigger-matched) documents enter the envelope whenever the provider lacks native steering (conditional_index = ... and not context_provider.native_steering), and the trigger text is the current user's message. So a turn that matches a different document, or any edit to one source, changes the digest and re-sends the full envelope, up to ESSENTIAL_MAX_CHARS = 64,000 chars. An unchanged envelope is stripped on the wire, as §5.2 found.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/essential_delivery.py:118-145 — stream(): replacement = '' only if acknowledged == (identity, candidate.digest, epoch); else native_envelope or the full envelope
  - src/kiro_crew/context.py:3153-3182 — _build_v2_essentials(..., trigger_text=_trigger_text, conditional_index=context_provider is not None and delivery is not None and not context_provider.native_steering); 'if _essentials and not is_new_session: parts.append(_essentials)'
  - src/kiro_crew/context_assembly/member.py:491-591 — build_v2_essentials carries conditional_index / trigger_text into the envelope
  - src/kiro_crew/member_essential_context.py:20 — ESSENTIAL_MAX_CHARS = 64_000
- **Checked by:** read
- **Required outcome:** When the acknowledged envelope differs from the new one only in some sources, the turn sends only the changed sources, plus a short line saying the unchanged sources from the acknowledged snapshot still apply. A full re-send happens only after invalidation (compaction, /clear, agent switch, new incarnation).
- **Solution:**
  1. Make the envelope structured: have build_v2_essentials (context_assembly/member.py:491) return per-source sections with their own digests, alongside the rendered text.
  2. Have EssentialReceipt (essential_delivery.py:55-91) store the per-source digest map of the acknowledged snapshot.
  3. In stream() (:131-145), when the acknowledged identity and epoch match but the digest differs, replace the envelope with a delta: the changed or added sources, plus 'removed: <names>' and 'unchanged sources as previously provided'. Acknowledge the new map only on a productive, completed turn, as today.
  4. Keep the full-envelope path for any mismatch in identity or epoch.
  5. Update context-management.md §5 and crew-mode.md in the same commit. The envelope declares itself 'the complete replacement for all prior snapshots' (turn_context.py comment), so the delta wording must keep that contract explicit.
- **Done when:** A unit test drives EssentialDelivery.stream with a fake send(). Turn 1 sends envelope A (3 sources) and completes productively. Turn 2's envelope differs only in source 2. The message sent on turn 2 contains source 2's text, not sources 1 and 3, and is shorter than the full envelope. Turn 3 after invalidate() sends the full envelope.
- **Changed from the source claim:** none (the mechanism is confirmed at HEAD; the conditional index applies only to providers without native steering)
- **Sources:** REVIEW_FINDINGS:X6, verify_needed:X6

### CTX-30 [20, armed, effort S] Pending-context drain evicts entries over the ceiling — CONFIRMED
- **Verified claim:** At HEAD a slot's pending-context queue (app-kit /context inject, /note, artifact companion, Slack thread backfill share it) is a 50-entry FIFO, and appending to a full queue silently evicts the OLDEST live entries (pop(0)) - no refusal, no log, no notice to the producer or the model. The eviction happens at enqueue (SlotBuffers.append_pending_context), not at the drain; the drain concatenates whatever survived, with no total cap (that is CTX-15). Named sources are refused with 429 at 10 live entries each, but sourceless entries bypass that cap, so one sourceless producer can push out every other source's entries. The app-kit API reference does not document the 50-entry eviction. PR #16818 (refuse over the ceiling instead of evicting) is the upstream proposal.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/state.py:1791-1793 — '# FIFO ceiling on a slot's pending-context queue (app-kit context inject + Slack thread backfill)'; _MAX_PENDING_CONTEXT = 50
  - src/kiro_crew/dashboard/slot_buffers.py:684-699 — append_pending_context: prune expired; while len(slot._pending_context) >= max_pending_context: slot._pending_context.pop(0); append
  - src/kiro_crew/dashboard/chat_handlers.py:10505-10514 — _source_cap_reached: 'if not source: return False' (sourceless entries are bucket-free)
  - src/kiro_crew/dashboard/chat_handlers.py:10516-10541 — _enqueue_pending_context returns 429 only for the per-source cap, else slot.append_pending_context(entry)
  - src/kiro_crew/dashboard/chat_turn/turn_context.py:106-152 — drain_pending_context: concatenates every live entry, clears the queue (no eviction here, no total cap)
  - other enqueue sites: dashboard/chat_handlers.py:11085 (/note), dashboard/slot_buffers.py:821 (deferred-note flush)
  - grep -i 'oldest|FIFO|evict' docs/app-kit/api-reference.md — no hits (eviction undocumented)
- **Checked by:** read
- **Required outcome:** A producer posting context past the queue ceiling learns it was not queued (a 429 with a capacity code) instead of silently displacing older entries from other producers; the ceiling is documented in the app-kit API reference.
- **Solution:**
  1. Review/re-land PR #16818 before writing a second fix (gh pr view 16818).
  2. Read docs/system-specs/modules/app-kit-platform.md and docs/app-kit/api-reference.md; update both in the same commit.
  3. In dashboard/chat_handlers.py _build_pending_context_entry (~:10545-10571) add a total-queue check (live entries + held deferred notes >= _MAX_PENDING_CONTEXT -> 429 code 'capacity_reached'), mirroring _source_cap_reached; keep SlotBuffers.append_pending_context's FIFO only as a last-resort guard for internal producers (Slack backfill) and log a WARNING naming the evicted source when it fires.
  4. Give sourceless entries a bucket ('app') for the per-source cap, or document that they share the total cap only.
  5. This is a take-away for any app relying on fire-and-forget posting: read docs/system-specs/common/take-away-changes.md and grep docs/decisions/ (nothing found for pending context today); coordinate with CTX-15's total byte cap so the two limits do not disagree.
- **Done when:** aiohttp test-client test (no real port): post 50 sourceless entries to /api/chat/slots/{slot}/context -> all 200; the 51st -> 429 capacity_reached and the queue still holds the first 50 unchanged; drain then returns 50 frames. Separately, an internal append past the ceiling logs one WARNING naming the evicted source (caplog).
- **Upstream:** #16817 PR #16818 (state unverified)
- **Changed from the source claim:** Confirmed; corrected location: eviction is at enqueue (append_pending_context), not in the drain. Added that sourceless entries bypass the per-source cap and the ceiling is undocumented.
- **Sources:** verify_needed:G37(#16817)

### SES-14 [20, armed, effort S] Webhook runs don't report MCP servers that failed to start; same servers as chat — PARTLY
- **Original claim:** Webhook sessions silently start without MCP servers (corrected below)
- **Verified claim:** The silence half is confirmed. The webhook runner never reads the session's MCP report, so a hook run whose MCP servers failed to initialize records a normal outcome, and neither the run history nor the delivered result says a server was missing. Sub-agents log the problem and prepend a spawn notice to the run's first turn; the dashboard renders the report. The 'webhook sessions start without the servers chat sessions have' half is not a Crew-side difference. A hook session is allocated through the same `get_or_create(session_key, agent=agent)` and agent spec as a chat session, and the `hook:` prefix is only a caller label in the MCP gateway. A gateway-managed instance failure such as the missing keytar binary would remove the server from chat and sub-agent sessions too; only the surfacing differs. The keytar trigger itself needs a live install.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/handlers/hooks.py:1174, :1230 — `_run_hook_inner` allocates with `await state.sessions.get_or_create(session_key, agent=agent)`; no `mcp_session_report` read anywhere in dashboard/handlers/hooks.py (grep 0 hits)
  - src/kiro_crew/dashboard/handlers/hooks.py:1595-1611 — the run record carries outcome/duration/result_chars/delivered/detail only (`webhooks.run_store().record(...)`)
  - src/kiro_crew/subagent_manager/run.py:1679-1693 — contrast: sub-agent reads `client.mcp_session_report()`, logs 'MCP servers unusable in this session — %s'; :1695 `_spawn_mcp_notice` puts it in the run's first turn
  - src/kiro_crew/dashboard/chat_runner.py:2480-2496 — dashboard reads the same report via `provider.mcp_session_report()`
  - src/kiro_crew/acp/mcp_session_report.py:658 — `problem_summary(...)` already renders the failed/unreported servers
  - src/kiro_crew/mcp_gateway/claim.py:88-97 — `classify_session_type`: 'hook:' → 'hook' is a label only; no server set differs by session type
- **Checked by:** read
- **Required outcome:** A webhook run whose MCP servers failed or were not reported says so: the run record's detail names the servers, using `problem_summary(include_reasons=False)`, and the agent's turn carries the same notice sub-agents get, so the hook agent does not hunt for tools that are not there.
- **Solution:**
  1. In `_run_hook_inner` (dashboard/handlers/hooks.py:1230), after `get_or_create`, read `client.mcp_session_report()` through the provider contract (as subagent_manager/run.py:1679 does) and, when `problem_summary()` is non-empty, prepend the same notice text `_spawn_mcp_notice` builds (lift it to a shared helper rather than copy it).
  2. Return the names-only summary to the caller and append it to the run record's `detail` at hooks.py:1595-1611 ('MCP unavailable: <names>'), and log a WARNING naming the hook id.
  3. Update docs/system-specs/modules/messaging.md or the webhook section of the owning doc in the same commit.
- **Done when:** Webhook handler test with a fake provider whose `mcp_session_report().problem_summary()` returns 'kirocrew-core (init failed)': the recorded run's `detail` contains 'kirocrew-core' and the message streamed to the provider starts with the MCP notice; with an empty summary neither changes.
- **Upstream:** #17148 (state unverified)
- **Changed from the source claim:** Narrowed to the surfacing gap. Crew does not give webhook sessions fewer servers than chat; the server loss is environmental and hits every surface, but only webhook runs stay silent about it. Keytar trigger not reproduced (needs a live install).
- **Sources:** verification_needed:Part3(#17148)

### CTX-18 [15, default, effort S] Unknown model ids get 1M caps on the first turn only, unwarned (deliberate fallback) — PARTLY
- **Original claim:** Unknown model ids silently get 1M-window budgets (corrected below)
- **Verified claim:** The fallback exists, and it is deliberate. resolve_model_window returns None for '', 'auto' and any id that model_window cannot place. model_window looks the id up in this order: kiro-list cache, then registry, then the supplementary map, then the [1m] heuristic. _effective_window(None) then yields the 1M reference. Measured: 'some-new-model-x' and 'gpt-5' get history_fallback 34,650, per_message 8,000, compressed_history 44,550 and protected_context 500,000 chars. A registry 200k id gets 6,930 / 1,600 / 8,910 / 100,000. No warning is logged for an unknown window. Two parts of the claim are overstated. (1) 'No re-resolution after the first turn' is wrong. window_for_provider_client prefers the provider's live context_window_tokens() whenever it is greater than 0 (a usage_update size, or a kiro-list or registry backfill), so later rebuilds such as post-compaction re-injection use the real window. The kiro-list cache (refresh_kiro_catalog, fed by dashboard/handlers/agents.py:1181) learns windows from 'kiro-cli chat --list-models'. The supplementary map pins the currently served non-Anthropic kiro ids. Only the first-turn build of a session on a genuinely unlisted model uses the reference. (2) The design states the trade-off on purpose: 'an unresolved window never silently shrinks the default deployment' (budget.py:162-170, model_registry.py:955-962). The affected caps are small: thread replay at most about 45K chars, plus the protected ceiling (CTX-9).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context_assembly/budget.py:158-170 — _effective_window: None → _REFERENCE_WINDOW_TOKENS, 'an unresolved window never silently shrinks the default deployment to 20%'
  - src/kiro_crew/context_assembly/budget.py:213-249 — resolve_model_window delegates to model_registry.model_window; unknown → None
  - src/kiro_crew/context_assembly/budget.py:252-283 — window_for_provider_client prefers live context_window_tokens() > 0, else resolved model id
  - src/kiro_crew/model_registry.py:939-978 — model_window order: live > kiro-list > registry > supplementary > [1m] > None ('Callers treat None as REFERENCE_WINDOW_TOKENS (never a silent 200k)')
  - src/kiro_crew/model_registry.py:142-158 — supplementary windows for served kiro ids (deepseek-3.2 164k, gpt-5.6-* 272k, qwen3-coder-next 256k …)
  - src/kiro_crew/dashboard/handlers/agents.py:1181 — refresh_kiro_catalog seeds the kiro-list window cache
  - measured: unknown ids → effective 1,000,000; caps 34,650 / 8,000 / 44,550 / 500,000; 200k ids → 6,930 / 1,600 / 8,910 / 100,000
- **Checked by:** ran-new-script
- **Required outcome:** An unknown model window is visible: one log line per (model id, process) when a session start falls back to the reference, and the dashboard's model chip can show 'window unknown'. Optionally, the protected ceiling uses a conservative window when unknown (CTX-9's fixed cap does this). The replay caps keep the reference fallback that the design chose.
- **Solution:**
  1. In budget.resolve_model_window (or window_for_provider_client), when model_registry.window_source(model) == 'unknown' and the id is not ''/'auto', log one INFO per id per process naming the id and that budgets use the 1M reference until the backend reports a window.
  2. Land CTX-9's fixed protected cap, which removes the only budget whose size matters on a small unknown window.
  3. Do not change the replay-cap fallback: it is the documented fail-safe. If it is to change, it is a take-away change.
  4. Update context-management.md §1 'Budgets and caps' to say unknown ids log once.
- **Done when:** A unit test: resolve_model_window('some-new-model-x') returns None and logs exactly one INFO naming the id across two calls; resolve_model_window('claude-opus-4.5') logs nothing.
- **Changed from the source claim:** Corrected: windows ARE re-resolved after the first turn (live usage window, kiro-list cache, supplementary map), and the fallback is a documented design choice. What remains is the missing warning and the first-turn caps on genuinely unlisted ids. A severity of 15 is assigned.
- **Sources:** verification_needed:P1-3, verification_needed:refactor(unknown-model-budgets)

### CTX-24 [15, default, effort S] Preference-only consolidation advances _prefs_offset on an empty answer — CONFIRMED
- **Verified claim:** A preference-only consolidation pass (maybe_consolidate -> _consolidate(include_history=False)) whose LLM answer is empty returns None, and maybe_consolidate's done-callback treats any non-refused, non-exception result as a completed pass and advances _prefs_offset to the message count, so the next prefs pass waits for another _CONSOLIDATION_THRESHOLD (30) messages. The same advance happens when nothing was dispatched at all (_ConsolidationNotDispatched also returns None). The history path (include_history=True) charges the attempt via _note_failed_attempt and leaves the durable marker unwritten; the prefs path has neither. Impact is a delay, not a permanent loss: the idle/session-end history pass re-reads the same unconsolidated span and also asks for preferences_update/semantic facts.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/history_consolidation.py:1108-1110 — prefs_off = self._prefs_offset.get(key, 0); if total - prefs_off < _CONSOLIDATION_THRESHOLD: return
  - src/kiro_crew/history_consolidation.py:66 — _CONSOLIDATION_THRESHOLD = 30
  - src/kiro_crew/history_consolidation.py:1125-1136 — _on_done advances self._prefs_offset[k] = off unless cancelled / exception / _CONSOLIDATION_REFUSED
  - src/kiro_crew/history_consolidation.py:1795-1804 — if not result: ... if include_history: await self._note_failed_attempt(...); return None (prefs-only: nothing recorded, returns None)
  - src/kiro_crew/history_consolidation.py:1784-1793 — _ConsolidationNotDispatched: return None (also advances the prefs offset)
  - ran fork1/prefs_offset.py: 31 messages, fake _consolidate returning None -> include_history=[False], _prefs_offset {'k': 31}; immediate second call schedules no pass (needs 30 new messages)
  - callers: dashboard/chat_utils.py:2062, slack/handler.py:3066, slack/transport_dispatch.py:989, taskrunner.py:1525/:1707
- **Checked by:** ran-new-script
- **Required outcome:** An empty or undispatched preference-only pass leaves _prefs_offset where it was, so the next eligible turn retries that window (bounded by the existing durable backoff), matching the history path's handling.
- **Solution:**
  1. Read docs/system-specs/modules/memory-skills-hooks.md (consolidation section) first and update it in the same commit.
  2. In history_consolidation.py _consolidate (:1795-1804 and :1784-1793) return a distinct sentinel (e.g. _CONSOLIDATION_EMPTY) for 'billed but empty' and 'not dispatched' when include_history is False, instead of None.
  3. In maybe_consolidate._on_done (:1125-1136) advance _prefs_offset only on a real completed result, not on that sentinel; keep retry_eligible (:1117) as the bound so an always-empty model cannot re-bill every turn (charge the prefs attempt to the same durable backoff the history path uses, or a small in-memory per-key backoff).
  4. Check whether PR #17552 (sub-chunking) touches the same callback before writing.
- **Done when:** Test with a HistoryConsolidator whose _consolidate is replaced by an async fake returning the empty sentinel: after maybe_consolidate('k') with 31 messages and the task awaited, _prefs_offset.get('k', 0) == 0; with a fake returning a normal result it is 31; with retry_eligible returning False no task is scheduled. No sleeps beyond awaiting the task.
- **Upstream:** #15539 PR #2038 PR #17552 (state unverified)
- **Changed from the source claim:** Confirmed as stated; added that the undispatched (_ConsolidationNotDispatched) case advances the offset too, and that the loss is a delay (the idle history pass re-reads the span), not permanent.
- **Sources:** verify_needed:G29(#15539), verify_needed:G37

### CTX-16 [15, armed, effort S] Operator prompt.md override has no size budget or warning below the 50 MiB read guard — PARTLY
- **Original claim:** Operator prompt override read with no size cap (corrected below)
- **Verified claim:** _read_prompt_file reads the operator override (~/.kiro/crew/prompt.md, which _prompt_path prefers over the shipped prompt) through safe_read_file. That read is not unbounded at HEAD: safe_read_file passes max_bytes=MAX_FILE_BYTES, which is 50 MiB, the same ceiling safe_read_file_bytes uses. The docstring X5 cited no longer contrasts the two helpers. The substance holds, though. Below that 50 MiB allocation guard there is no prompt-size budget and no warning, so an override of any realistic size is injected whole as [AGENT SYSTEM PROMPT] at every session start, and again after every compaction (CTX-10). The shipped prompt is 38,989 B.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context.py:1623-1660 — _read_prompt_file: 'return safe_read_file(str(pp))' (no size check, no warning)
  - src/kiro_crew/hook_runtime/safe_reads.py:106-157 — safe_read_file: 'The read is bounded by MAX_FILE_BYTES, the same ceiling safe_read_file_bytes applies' → open_regular_nofollow(resolved, max_bytes=MAX_FILE_BYTES)
  - src/kiro_crew/hooks.py:2114 — MAX_FILE_BYTES = 50 * 1024 * 1024
  - src/kiro_crew/context_assembly/turn.py:70-80 — the same resolved prompt is re-sent after compaction
- **Checked by:** read
- **Required outcome:** An override prompt larger than a documented budget (for example 100K chars, about 2.5x the shipped prompt) is still used, but a WARNING names its measured size once per process and once in the dashboard health surface. Optionally, past a hard ceiling, the override is refused with the measured size and the shipped prompt is used, matching how unreadable overrides already degrade.
- **Solution:**
  1. In context.py:_read_prompt_file, after the read, compare len(text) with a new constant _PROMPT_OVERRIDE_WARN_CHARS = 100_000. Log one WARNING per (path, size) naming the file and the size, and do not truncate: the contract must stay whole.
  2. Optionally add _PROMPT_OVERRIDE_MAX_CHARS. Above it, degrade to the shipped prompt with the same 'Ignoring prompt override' WARNING the OSError path already emits (context.py:1651-1653). This is a take-away change for anyone running a larger override, so it needs user confirmation.
  3. Document the budget in src/kiro_crew/docs/agents.md and in context-management.md §1 row 1 in the same commit.
- **Done when:** A unit test writes a 150,000-char prompt.md under a tmp KIROCREW_HOME and asserts _read_prompt_file returns it whole and logs exactly one WARNING containing '150000'. A second call logs nothing new, and a 30,000-char override logs nothing.
- **Changed from the source claim:** 'Unbounded safe_read_file' is wrong at HEAD: the read carries the 50 MiB MAX_FILE_BYTES ceiling, and X5's docstring contrast is gone. The real gap is that no prompt-size budget or warning exists below that ceiling. Severity is lowered from 20 to 15.
- **Sources:** REVIEW_FINDINGS:X5, verify_needed:X5

### CTX-32 [10, default, effort S] context-management.md has no Crew-vs-harness token ownership section; only fragments — PARTLY
- **Original claim:** No documented Crew-vs-harness token ownership boundary (corrected below)
- **Verified claim:** docs/architecture/context-management.md has no section that names the boundary between what Crew's budgets bound and what the harness owns. A search for 'cost ownership' or 'token ownership' across docs/ finds nothing. The boundary is stated only in fragments. context-management.md:201-203 says 'After the first turn build_message injects no transcript — the ACP session carries its history natively'. budget.py:29-32 says the base 'is NOT a bound on the provider's full model input or a token estimate'. memory-skills-hooks.md:5820 says the 33,000 allowance 'is not falsely reported as a full-input ceiling'. No single place tells a contributor what Crew does not control and therefore cannot budget on the kiro-cli path: native history replay, tool schemas, tool results, and the image bytes in history (TOOL-9). The tradeoff FIX_PLAN §0.2 spells out (levers Crew has and does not have) is absent from the owned doc.
- **Evidence (at `397f4be`):**
  - docs/architecture/context-management.md:119-173 — 'Budgets and caps' section covers Crew allowances only
  - docs/architecture/context-management.md:201-203 — 'the ACP session carries its history natively'
  - src/kiro_crew/context_assembly/budget.py:29-32 — 'this is NOT a bound on the provider's full model input or a token estimate'
  - docs/system-specs/modules/memory-skills-hooks.md:5820 — 'not falsely reported as a full-input ceiling'
  - grep -ri 'cost ownership|token ownership' docs/ → no match
- **Checked by:** read
- **Required outcome:** context-management.md states in one short subsection which context Crew budgets (injected session-start and per-turn blocks, the session replay, drained app context and MCP results it formats), and which the harness owns per backend (native history replay, tool schemas, tool-result retention and image bytes, its own compaction). It names the levers Crew has: what it injects, which spec and tools it mounts, the size of its MCP results, the session lifecycle, and cadence.
- **Solution:**
  1. Add a subsection to docs/architecture/context-management.md §1 after 'Budgets and caps', for example '### What Crew's budgets do not cover', in the existing file. AGENTS.md forbids new markdown files. Cite symbols, not lines.
  2. Per backend, cite the positive capability sets (acp_backends ACP_BACKENDS_COMPACT, ACP_BACKENDS_HARNESS_MANAGED_COMPACTION) rather than 'not claude'.
  3. Link it from docs/README.md's index entry if the index lists subsections.
  4. Run scripts/docs-lint.sh.
- **Done when:** scripts/docs-lint.sh passes, and the new subsection exists. A docs test (or the docs-lint symbol check) confirms every cited symbol resolves.
- **Changed from the source claim:** Partly documented in fragments, which the source missed. There is no dedicated section. A severity of 10 is assigned.
- **Sources:** verification_needed:refactor(cost-ownership-doc), verification_needed:crit-risk-1
