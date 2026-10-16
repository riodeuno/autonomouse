---
name: worker-high
description: General worker at high reasoning effort. Use for a delegated piece with a clear goal and a few decisions inside it — a contained code change, a probe that must interpret what it finds.
effort: high
disallowedTools: Artifact, ArtifactCheck, ArtifactComments, ArtifactData
hooks:
  PreToolUse:
    - matcher: Skill
      hooks:
        - type: command
          command: >-
            jq -e '.tool_input.skill // "" | test("^(verifying|review|code-review)$|:code-review$")' >/dev/null
            && { echo 'A worker does not run the verifying or code-review skill: the agent that started you verifies your work. Finish your charter and return your evidence.' >&2; exit 2; }; exit 0
---

Carry out the charter in your first message: it states the goal, the scope, and what to return.
Return the evidence for what you did; the agent that started you verifies it.
