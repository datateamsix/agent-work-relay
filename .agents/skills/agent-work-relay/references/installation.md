# Installing the shared skill and MCP connection

The canonical portable skill is the complete `agent-work-relay/` directory.
Install that same directory everywhere. Do not maintain divergent planner,
Cursor, Claude Code, Gemini, or reviewer copies.

The skill provides behavior, templates, and guardrails. MCP provides
authenticated tools. OAuth provides identity and scopes. Credentials never
live in the skill, templates, fixtures, or examples.

## ChatGPT and Codex

Official Codex/ChatGPT skills are a directory with `SKILL.md` (name and
description required). Codex loads name and description first, then the
full file when the skill is selected.

Repository discovery: `.agents/skills/` from the working directory upward
to the repository root. Personal skills: `~/.agents/skills/` or
`$CODEX_HOME/skills`.

Keep this bundle at `.agents/skills/agent-work-relay/` in the destination
repository. Mention it with `$agent-work-relay` or let the description
trigger it. Connect the AWR Streamable HTTP MCP server separately and
complete OAuth. The `agents/openai.yaml` file declares the MCP dependency;
it does not create the connection or store a token.

If a Codex/OpenAI skill linter is available in the environment, run it on
this directory. This repository also runs
`scripts/validate_skill_bundle.py`.

## Cursor and Cursor Cloud

Keep the project-level copy at:

```text
.agents/skills/agent-work-relay/
```

Remote or cloud agents cannot see a laptop global skill directory.
Configure AWR MCP in project or managed settings. Do not commit bearer
tokens.

Cursor Cloud workers usually cannot call AWR MCP. Use adapter-return mode
from [worker-workflows.md](worker-workflows.md). Do not tell a cloud
worker it must open outbound MCP.

## Claude Code

Claude Code discovers Agent Skills from `.claude/skills/`. Do not copy
this bundle into a second maintained tree. Symlink or otherwise point
`.claude/skills/agent-work-relay` at this canonical directory:

```text
.agents/skills/agent-work-relay/
```

If the client does not load the symlink, point `CLAUDE.md` at this
`SKILL.md`. Configure the same AWR MCP endpoint through Claude Code MCP
settings and OAuth.

## Gemini CLI

Point `GEMINI.md` or project settings at this canonical `SKILL.md`. Add
AWR as a remote MCP server in project settings. Do not copy the skill
into a second maintained tree.

## Other MCP-capable agents

If a client needs a shim, the shim must reference this directory. Do not
fork templates per vendor.

## Connection checks

Before a live relay:

1. Discover AWR tools.
2. Complete OAuth without writing tokens into the repo.
3. Call a read-only tool.
4. Confirm expected scopes (`awr:plan`, `awr:read`, `awr:refresh`,
   `awr:response`, `awr:decide`).
5. Submit an idempotent planning message only if asked.
6. Retrieve the receipt and timeline.
7. Confirm an unauthorized decision is rejected.
