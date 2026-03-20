# tapp — OpenClaw Skill

An [OpenClaw](https://openclaw.ai) skill for the [Tapp](https://tapp.todaq.net) marketplace — a payment layer built on the TODAQ protocol and integrity network.

## What it does

- **Call paywalled tools** — access marketplace APIs using your Tapp twin wallet. Payments flow automatically via the TODA protocol, no manual steps required.
- **Become a provider** — register any API as a marketplace tool and earn per call, automatically, every time an agent uses it.

## Install

```bash
clawhub install tapp
```

Or manually: copy the `SKILL.md` into your OpenClaw skills directory.

## Requirements

- [OpenClaw](https://openclaw.ai)
- [mcporter](https://mcporter.dev) (`npm i -g mcporter`)
- A Tapp account — your agent can walk you through setup, just ask: *"Help me register as a Tapp provider."*

## Usage

Once installed, your OpenClaw agent will automatically use this skill when you:

- Ask to call a tool from the Tapp marketplace
- Ask to check your wallet balance
- Ask to register and publish your own tool

## MCP Server

This skill connects to the [TODAQ MCP server](https://github.com/todaqmicro/mcp) at `mcp.m.todaq.net` — a hosted, multi-tenant MCP server that exposes the Tapp marketplace to any MCP-compatible agent.

## License

MIT
