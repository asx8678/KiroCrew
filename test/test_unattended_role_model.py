"""MOD-1: unattended roles do not inherit the chat model."""

from kiro_crew.config.loader import DEFAULT_MODEL
from kiro_crew.config.sections import AgentConfig, coerce_role_models


def test_unpinned_unattended_roles_request_auto():
    agent = AgentConfig(model="chat-pin")
    for role in ("cron", "workflow", "taskrunner", "subagent"):
        assert agent.resolve_model(role) == DEFAULT_MODEL


def test_cron_pin_is_kept_and_used():
    assert coerce_role_models({"cron": "role-pin", "nope": "x"}) == {"cron": "role-pin"}
    agent = AgentConfig(model="chat-pin", role_models={"cron": "role-pin"})
    assert agent.resolve_model("cron") == "role-pin"
