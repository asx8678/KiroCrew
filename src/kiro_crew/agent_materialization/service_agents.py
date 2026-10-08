"""The agents Kiro Crew's own services drive: lite, guest, knowledge and research.

``kirocrew-lite`` is the cheap background helper; ``kirocrew-guest`` is the tool-less
agent a non-operator channel sender talks to, a trust boundary that mounts nothing;
``kirocrew-knowledge`` runs the Knowledge Library's extraction; ``kirocrew-research``
is the Research Lab's per-cycle worker, derived from the default template so it
inherits the governance ceiling; ``kirocrew-step`` is the slim agent an unnamed
subagent spawn or workflow step runs as (builtins plus a small ``kirocrew-core``
base). Each is rewritten on every rebuild.
"""

from __future__ import annotations

from kiro_crew import agent as agent_mod
from kiro_crew import agent_state
from kiro_crew.agent_files import GUEST_AGENT_FILENAME as _GUEST_AGENT_FILENAME
from kiro_crew.agent_files import KNOWLEDGE_AGENT_FILENAME as _KNOWLEDGE_AGENT_FILENAME
from kiro_crew.agent_files import LITE_AGENT_FILENAME as _LITE_AGENT_FILENAME
from kiro_crew.agent_files import RESEARCH_AGENT_FILENAME as _RESEARCH_AGENT_FILENAME
from kiro_crew.agent_files import STEP_AGENT_FILENAME as _STEP_AGENT_FILENAME
from kiro_crew.agent_materialization import auto_approve


def _install_guest_agent() -> None:
    """Write the tool-less ``kirocrew-guest`` config a non-operator sender talks to.

    Separate from ``kirocrew-lite`` on purpose: the lite agent is the background
    helper (titles, extraction) and may one day need a tool; this one is a trust
    boundary and never may. Same model as the operator's chat so an admitted
    sender gets an ordinary answer, never a background worker's minimal default.
    """
    from kiro_crew.config.loader import KiroCrewConfig

    try:
        model = KiroCrewConfig.load().agent.model or "auto"
    except Exception:
        model = "auto"
    guest_path = agent_mod.kiro_agents_dir_path() / _GUEST_AGENT_FILENAME
    guest_config = {
        "name": "kirocrew-guest",
        "model": model,
        "tools": [],
        "mcpServers": {},
        # Pinned: kiro-cli defaults this to True and would spawn every server in
        # the user-level mcp.json for a session that must mount nothing.
        "includeMcpJson": False,
        "prompt": agent_mod.GUEST_AGENT_PROMPT,
    }
    agent_mod._atomic_json_write(guest_path, guest_config)


def _install_lite_agent_fallback() -> None:
    """Write a bare kirocrew-lite config (cheap background agent)."""
    lite_path = agent_mod.kiro_agents_dir_path() / _LITE_AGENT_FILENAME
    lite_config = {
        "name": "kirocrew-lite",
        "model": agent_mod._background_agent_model(),
        "tools": [],
        "mcpServers": {},
        "prompt": "",
        # kiro-cli defaults an absent key to True and would spawn every
        # server in the user-level mcp.json; the lite agent has no tools,
        # so those processes are pure startup cost on every background call.
        "includeMcpJson": False,
    }
    agent_mod._atomic_json_write(lite_path, lite_config)
    # Cheap model for the claude_code (CC) provider. kiro-cli resolves the lite
    # model from `model` via --agent; the CC backend can't, so the provider
    # factory reads this cc_model for the lite agent. The kiro spec above uses
    # the resolved background role model (default "auto", entitlement-safe on
    # every tier); the CC seam needs a concrete model, so it falls back to the
    # cheap default when the role is unpinned. Stored in the sidecar (kiro spec
    # stays schema-clean).
    agent_state.set_cc_model("kirocrew-lite", agent_mod._background_cc_model())


def _install_knowledge_agent() -> None:
    """Generate and install the kirocrew-knowledge agent config.

    This agent is used by the Knowledge Library's LLMPool for document
    extraction. By default it uses the user's configured agent.model (so
    extraction runs on the same model as chat). If the user sets
    knowledge.extraction_model explicitly, that model is used instead —
    allowing a cheaper model for extraction without changing the chat default.
    """
    from kiro_crew.config.loader import KiroCrewConfig

    path = agent_mod.kiro_agents_dir_path() / _KNOWLEDGE_AGENT_FILENAME

    # Resolve model: knowledge.extraction_model > agent.model > "auto"
    try:
        cfg = KiroCrewConfig.load()
        model = cfg.knowledge.extraction_model.strip()
        if not model:
            # Use the user's default model (same as chat).
            model = cfg.agent.model or "auto"
    except Exception:
        model = "auto"

    config: dict[str, object] = {
        "name": "kirocrew-knowledge",
        "description": (
            "Dedicated agent for knowledge extraction, categorization, " "and summarization."
        ),
        "model": model,
        "includeMcpJson": False,
        "prompt": agent_mod._KNOWLEDGE_SYSTEM_PROMPT,
        "mcpServers": {},
        "tools": [],
    }

    agent_mod._atomic_json_write(path, config)
    agent_mod.logger.info("Installed knowledge agent config: %s (model=%s)", path, model)


def _install_research_agent() -> None:
    """Generate and install the kirocrew-research agent config.

    Derives from the kirocrew agent (MCP servers, security, tools) but swaps in a
    lean research-worker prompt + identity. Used by the Research Lab app's
    autonudge loop to run one research cycle per turn.
    """
    config = agent_mod.build_agent_config()
    config["name"] = "kirocrew-research"
    config["description"] = (
        "Autonomous research worker — runs one research cycle per turn "
        "in a Research Lab campaign loop."
    )
    config["prompt"] = agent_mod._RESEARCH_SYSTEM_PROMPT
    agent_mod.kiro_agents_dir_path().mkdir(parents=True, exist_ok=True)
    path = agent_mod.kiro_agents_dir_path() / _RESEARCH_AGENT_FILENAME
    agent_mod._atomic_json_write(path, config)
    agent_mod.logger.info("Installed research agent config: %s", path)


#: The ``kirocrew-core`` verbs a step mounts, and the ONLY Crew MCP surface it has
#: (SPEC-3). Chosen for what one delegated task needs: check on spawned work
#: (``spawn_status``/``spawn_list``), find and read a procedure (the three skill
#: verbs), read and record what is learned (``memory_recall``, the knowledge
#: search, ``learn_add``/``learn_list``), reach a person (``ask_question``,
#: ``send_message``, ``send_notification``) and ``wait``. Deliberately absent:
#: ``@kirocrew-cron`` (a recurring job outlives the step), every ``workflow_*``
#: and ``monitor_*``/``autonudge_*`` verb (a step does not orchestrate), spawning
#: (``spawn_run`` and friends), artifacts, app/dev tools and session control.
#: Their mounted compact schemas total ~22 KB, against ~120 KB for the default
#: agent's whole-server mounts; the budget is 25,000 bytes.
STEP_CORE_VERBS: tuple[str, ...] = (
    "spawn_status",
    "spawn_list",
    "skill_search",
    "skill_discover",
    "skill_fetch",
    "memory_recall",
    "local_knowledge_search",
    "learn_add",
    "learn_list",
    "ask_question",
    "send_message",
    "send_notification",
    "wait",
)

#: The step contract. Short on purpose: a step's first turn is the task, not the
#: default agent's ~40 KB operating contract.
STEP_SYSTEM_PROMPT = """# Kiro Crew Step

You are `kirocrew-step`: you run ONE delegated task -- a subagent spawn or a
workflow step -- and end with its result.

- Do the task with the tools you have: read and write files, run commands, search.
  Stay inside the task; do not widen it to problems you notice along the way.
- Your Crew tools are a small base: check spawned work, search and fetch skills
  (`skill_search`, then `skill_fetch` when a procedure applies), recall memory,
  record a durable lesson with `learn_add`, ask a person with `ask_question`, and
  `wait`. You have no scheduling, workflow, monitor or spawning tools; if the
  task needs one, say so in your result rather than working around it.
- When you are blocked on something only a person can supply, say exactly what
  and stop; do not guess at credentials or decisions.
- End with a concise final message: what you did, what came out, and where it
  is (absolute paths, commit, branch, URLs). That message IS your result.
"""


def step_spec_present() -> bool:
    """Whether ``kirocrew-step.json`` is on disk for kiro-cli to load.

    The callers that default onto the step (an unnamed spawn, an unnamed
    workflow step) check this first and keep the default agent when it is
    absent: a spawn onto a mode kiro-cli does not have fails every turn, which
    is worse than the cost the step saves. Blocking stat; call off-loop.
    """
    try:
        return (agent_mod.kiro_agents_dir_path() / _STEP_AGENT_FILENAME).is_file()
    except OSError:
        return False


def _install_step_agent() -> None:
    """Generate and install the ``kirocrew-step`` agent config (SPEC-3).

    The slim agent an unnamed subagent spawn or workflow step runs as: the
    default template's builtin tools, hooks and model, a short step contract, and
    ONLY the :data:`STEP_CORE_VERBS` of ``kirocrew-core`` -- mounted verb by verb
    as ``@kirocrew-core/<verb>``, so the whole-server ``@kirocrew-core`` mount,
    ``@kirocrew-cron`` and every other Crew server stay off it. Derived from
    ``build_agent_config`` (template + user override), so the governance ceiling
    and the security hooks it carries apply here too; a server or grant the user
    added only to ``kirocrew.json`` is deliberately not mirrored (a step is the
    narrow base, ``kirocrew-worker`` is the default-mirroring superset).
    """
    config = agent_mod.build_agent_config()
    config["name"] = "kirocrew-step"
    config["description"] = (
        "Runs one delegated task (an unnamed subagent spawn or a workflow step) "
        "with the builtin tools and a small Crew base, and ends with its result."
    )
    config["prompt"] = STEP_SYSTEM_PROMPT
    verbs = [f"@kirocrew-core/{verb}" for verb in STEP_CORE_VERBS]
    builtins = [ref for ref in (config.get("tools") or []) if isinstance(ref, str)]
    config["tools"] = [ref for ref in builtins if not ref.startswith("@")] + verbs
    # Only the core server's entry: a mounted verb needs its server, and no other
    # Crew server is referenced. Absent (a broken template), the verbs resolve to
    # nothing and the step still runs on its builtins.
    servers = config.get("mcpServers") or {}
    core = servers.get("kirocrew-core") if isinstance(servers, dict) else None
    config["mcpServers"] = {"kirocrew-core": core} if isinstance(core, dict) else {}
    # A grant is kept when it names a builtin, the core server whole (it reaches
    # only the mounted verbs) or one of those verbs; the cron grants and anything
    # naming an unmounted server go.
    allowed_refs = {"@kirocrew-core", *verbs}
    config["allowedTools"] = [
        ref
        for ref in (config.get("allowedTools") or [])
        if isinstance(ref, str) and (not ref.startswith("@") or ref in allowed_refs)
    ]
    auto_approve._apply_allowed_tools_ceiling(config, source="_install_step_agent")
    config["mcpServers"] = auto_approve._strip_ungoverned_auto_approve(config["mcpServers"])
    auto_approve._write_derived_permissions(config, config["allowedTools"], _STEP_AGENT_FILENAME)
    agent_mod.kiro_agents_dir_path().mkdir(parents=True, exist_ok=True)
    path = agent_mod.kiro_agents_dir_path() / _STEP_AGENT_FILENAME
    agent_mod._atomic_json_write(path, config)
    agent_mod.logger.info("Installed step agent config: %s", path)
