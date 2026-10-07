# Lessons from automatic writers stay auto-admitted, tagged and advisory

Decided by: the operator (session instruction; option (b) of SEC-11)
Date: 2026-10-07

## Decision

Lessons written by non-human writers — the agent's own `learn_add` MCP tool
(source `agent`), history consolidation's LLM extraction (`consolidation`) and
task-runner extraction (`taskrunner`) — are admitted to the store exactly as
before, with no review gate and no change to any write path's success semantics.
The session-start lessons block renders each such row with a provenance tag
(`[auto: <source>]`) and its header frames tagged rows as advisory, never as
instructions. Human-authored lessons (`user_explicit`, the dashboard, the CLI)
and every legacy row render untagged. This is option (b) of the SEC-11 decision;
option (a) (a pending-review sink) and option (c) (removing `learn_add` from
the default allowedTools) were not taken.

## Why

- Holding auto-written lessons for review is a take-away change for users who
  rely on silent learning, and option (c) changes the default approval surface
  for every install. The operator chose the framing-only path.
- The tag is honest about what a row is: a lesson the agent recorded from a
  conversation that may have carried third-party content is not the same thing
  as a rule a human typed, and the block said "ALWAYS follow these" about both.
- The tag rides the DISPLAY text only: the embedding text, the stored row and
  every ranking/dedup key are untouched, so no existing store's behavior
  shifts. `learn_add` is the only writer that newly names its source; every
  legacy row reads as human rather than being silently re-framed.
