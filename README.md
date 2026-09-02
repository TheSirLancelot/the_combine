# The Combine

Read-only fantasy football co-owner. Live league state over MCP plus a blended
projection layer. It advises, I make every move. Nothing writes back to ESPN or Yahoo.

Design: `fantasy-copilot-brief.md`. Build order and mechanics: `BUILD_GUIDE.md`.

## Setup

```bash
cp .env.example .env
openssl rand -hex 32                  # -> COMBINE_BEARER_TOKEN
uv sync
uv run combine init                   # create data/combine.db
uv run combine doctor                 # env + league registry check
uv run combine doctor --live          # actually hit ESPN and Yahoo
uv run combine serve                  # http://127.0.0.1:8787/mcp
```

League slugs live in `src/combine/config.py`. Rename them to whatever you call
the leagues, since you type them into Claude on every query.

## Rules

Read-only. No write or transaction methods, ever, not even unused ones.
Secrets in `.env` only. Never in code, commits, or the knowledge doc.
Tool outputs stay small. Every list tool takes a server-enforced `limit`,
and every tool is scoped to one league.
