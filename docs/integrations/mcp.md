<!--
Copyright (c) 2026 B-Tree Labs
SPDX-License-Identifier: LicenseRef-BSL-1.1
-->

# Postrule MCP server — drive Postrule from a coding agent

Postrule ships a [Model Context Protocol](https://modelcontextprotocol.io)
server so an MCP-aware coding agent (Claude Code, Cursor, Windsurf, and
others) can analyze and instrument a codebase **without shelling out** — it
calls Postrule's tools in-process and gets structured JSON back.

## Install + run

The server lives behind an optional extra:

```bash
pip install 'postrule[mcp]'
postrule mcp           # stdio server; or: python -m postrule.mcp_server
```

## Register it with your agent

**Claude Code** — add it with one command:

```bash
claude mcp add postrule -- postrule mcp
```

Or drop it into a project's `.mcp.json` (works for any client that reads
that format):

```json
{
  "mcpServers": {
    "postrule": {
      "command": "postrule",
      "args": ["mcp"]
    }
  }
}
```

That's it — the agent now sees the Postrule tools.

## The tools

| Tool | What it does |
|------|--------------|
| `postrule_analyze` | Scan a file/dir → ranked classification sites (return-string dispatch, dict lookups, regex routers, model prompts). |
| `postrule_instrument_codebase` | **One shot:** analyze, rank sites by projected annual savings, and return the top-N with per-site savings + the diff to wrap each. The fast path instead of looping analyze→init. |
| `postrule_init` | Wrap a single function — inserts `@ml_switch` + import, returns a unified diff. |
| `postrule_refresh` | Walk `__postrule_generated__/` and report drift. |
| `postrule_doctor` | Project health check. |
| `postrule_status` | Am I connected, as whom, syncing where? |
| `postrule_connect_start` / `postrule_connect_complete` | Device-flow login so the user can create / connect a cloud account from the chat. |

**Writes are opt-in.** `postrule_init` and `postrule_instrument_codebase`
default to `dry_run=True` — the agent shows you the diffs and you apply them
(the privacy-max, copy-paste path). Pass `dry_run=false` to modify files in
place.

## The instrument loop

The shortest path from "here's my repo" to instrumented switches:

1. **`postrule_instrument_codebase(path, top_n=5)`** — the agent gets the top
   classification sites ranked by how much LLM/$ they'd retire, each with the
   diff that wraps it. (Or do it by hand: `postrule_analyze` to find sites,
   then `postrule_init` per site.)
2. **Review the diffs**, then apply (copy-paste, or re-run with
   `dry_run=false`).
3. **Each wrapped function logs outcomes** and can graduate
   RULE → MODEL_SHADOW → … → ML_PRIMARY behind a statistical gate. The rule
   stays the safety floor throughout — the wrap never changes what the caller
   sees.

## Connect a cloud account from the chat

The work tools are **account-aware**. When no account is configured, an
`analyze` / `instrument_codebase` result carries a `next_step` pointing at
`postrule_connect_start` — the agent shows you a sign-up link + code, you
authorize, and from then on your switches report verdicts to your dashboard
so you can watch them graduate after merge.

Connecting is **keyless** (RFC 8628 device flow) — no API key to paste into
agent config. See [Securing your API key](../securing-your-api-key.md#where-the-key-should-live)
for why the device flow is the recommended path for agents.

Sending data to your account is opt-in: local analysis and the copy-paste
path need no connection at all. Connecting is how you get the dashboard,
cross-session continuity, and (when you choose it) cloud-stored logs.
