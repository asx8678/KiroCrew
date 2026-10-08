# Goal Conductor: known limits of this version

Reference for the `goal-conductor` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

## Known limits of this version

- **A question card can be displaced by your own later turns.** `ask_question` posts a card into the dashboard transcript, and every patrol turn you take while it is outstanding can push it out of the user's view. A quiet cycle takes no turn under the `work-ledger` watch, but a worker's report does. So while any question is open, put it first in every turn that speaks to the user ("Needs you" on the task board), with the one answer that unblocks it.
- **The session and ledger tools may not be in your tool list yet.** With MCP
  Tool Search active their specs are deferred, so a first `session_create` fails
  with `A tool with the name 'session_create' does not exist`. That means
  DEFERRED, not missing: load it with
  `tool_search(tool_id="kirocrew-dashboard::session_create")` — `tool_search` is
  auto-approved for exactly this, so the load never prompts — then repeat the
  call. `chat_folder_create` is on the same server; `monitor_start` is served by
  `kirocrew-core` (`kirocrew-core::monitor_start`); the two ledger verbs are
  `kirocrew-work::work_ledger_read` and `kirocrew-work::work_ledger_record`.
- **`work_brief` and `work_report` answer `not_bound` to a ROOT conductor.**
  They are the worker half of the same server, and with no parent there is
  nothing for them to read. A second-level ledger conductor IS bound as a worker
  to its own parent, so for it they answer — `work_brief` without a prompt,
  `work_report` through the approval gate. See "When a conductor dispatched you"
  for what to report and when.
- **A `question` costs a human click.** Answering means `session_send`, which
  prompts by design. So you cannot answer a worker's question unattended, and a
  question-heavy goal is that much less autonomous. Plan for it rather than
  waiting on an approval nobody is there to give.
- **A cron job may dispatch into the sessions it created**, so a fleet can be
  stood up and driven from a schedule instead of only from a live chat session.
  Session control is on by default: the agent config is the grant.
- **Never dispatch a work item onto a conductor spec expecting it to write** —
  neither `kirocrew-conductor` nor this agent can write a file, so such an item
  would look stalled rather than misconfigured. Dispatch a conductor only when
  the item genuinely decomposes.
- **The grant is the agent's MCP mount, not a feature switch.** The session tools
  come from `@kirocrew-dashboard` and the ledger tools from `@kirocrew-work`; an
  agent whose spec does not mount one never sees it. `agent.session_control`
  defaults to true and exists only as a single withdrawal — if an operator set it
  to `false`, every session tool answers `session_control_disabled`. If you see
  that error, say which switch to flip; do not retry.
- **Reads and creates do not prompt; anything that touches another session does.**
  Auto-approved by name: `chat_folder_tree`, `chat_folder_create`,
  `chat_folder_file_self` (it writes only your own placement),
  `session_create`, `session_read_message`, `work_ledger_read`,
  `work_ledger_record` — so a patrol cycle that wakes on a nudge with nobody at
  the keyboard never blocks, and filing rides the create itself (the `folder`
  argument), so it costs no extra approval. The `@kirocrew-core` verbs are
  granted by name too, and only these: `monitor_start`, `monitor_update`,
  `autonudge_stop`, `wait`, `resource_status`, `list_sessions`,
  `session_ledger_read`, `session_ledger_record`, `skill_search`, `skill_fetch`,
  `select_crew`, `send_message`, `send_notification`, `ask_question`. That covers
  every core call this procedure asks you to make; **any other core tool is
  mounted but prompts**, including `task_run`, `workflow_run` and the `spawn_*`
  family, which this charter forbids you to route a work item to in the first
  place. **`session_send` and `session_stop` are deliberately NOT
  auto-approved**, because each writes to a session that is not yours: a seed
  runs as the target's own turn, and a stop discards the target's in-flight work.
  You ingest external content by design, so the prompt is the only call-time
  check on both. Expect one approval per item at dispatch (the seed), one per
  question you answer, and one if you ever stop an item. `session_close` sits on
  the same footing — it writes to a session that is not yours, even though it
  archives rather than deletes — so budget one approval per child you close out.
  `execute_bash` also still prompts, so **each patrol cycle that verifies
  anything blocks on one approval for the `accept_eval.py` invocation**. Size
  the nudge interval for that, and batch. `patrol_budget.py` prompts the same
  way, which is why it runs once at arm time and then only on
  `10% or less left` cycles. On a host with a governance ceiling
  even the granted verbs prompt; if you see approvals where this says you
  should not, that is why.
- **`session_send` reports delivery, not completion.** `started: true` means the
  target began a turn on your message; `started: false` means it queued. Neither
  says the work succeeded — acceptance is still the evaluator's job.
- **A worker's `summary` is text you read, and the only bound on it is its cap.**
  It is 500 characters of agent-authored prose, separated by design from every
  field you decide on. Decide from `verdict`, `status` and the evaluator; read
  `summary` for context. A conductor that decides from prose is misbehaving
  against this skill, and no store can prevent that.
- **An orphaned item keeps accumulating writes.** `orphaned` is derived at read
  time by asking whether your slot still exists, so it self-heals if the session
  is reopened, and the worker's binding stays valid meanwhile. Its reports simply
  go unread until someone takes the item over or stops it.
- **Some targets are out of bounds by design.** Incognito/temporary sessions,
  app-scoped sessions, channel-linked or mirrored sessions,
  and sessions in another workspace are all refused by the shared guard. Plan
  work items onto plain persistent dashboard sessions only.
- **Shell is for the two bundled scripts only, and the evaluator runs no
  command you name.** `execute_bash` exists so patrol can run `accept_eval.py`
  and `patrol_budget.py`; every call is
  audit-logged and every call prompts. The evaluator accepts **no command, argv
  array, or shell string from a spec** — it builds every argv it runs from a
  fixed template, so `pr_checks` becomes `gh pr checks <n>` and nothing else
  executes. That is deliberate and load-bearing: this script is invoked as an
  approved wrapper, so a spec that could name a command would turn it into a
  general way to run one, and Kiro Crew's denied-command floor cannot see inside
  it (the floor reads the `execute_bash` string, which says
  `python3 accept_eval.py`). Widening happens by adding a purpose-built kind that
  constructs its own argv — never by accepting one. A `refused` verdict is a spec
  to re-express, never a list to route around.
