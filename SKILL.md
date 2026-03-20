---
name: tapp
description: "Use and publish Tapp marketplace tools. Use when: calling paywalled API tools from the Tapp marketplace, checking wallet balance, or helping a user register as a Tapp provider to earn per-call payments from their own tools. Connects to the hosted Tapp MCP server at mcp.m.todaq.net via Tapp OIDC. NOT for: direct payment API calls or free tools."
homepage: https://github.com/todaqmicro/openclaw-tapp
license: MIT
metadata:
  {
    "openclaw":
      {
        "emoji": "💎",
        "requires": { "bins": ["mcporter"] }
      }
  }
---

# Tapp Marketplace Skill

Access paywalled API tools from the Tapp marketplace. Payments are handled transparently via your Tapp twin wallet — no manual payment steps required.

## When to Use

✅ **USE this skill when:**
- User asks to call a tool from the Tapp marketplace
- A tool requires Tapp wallet payment
- Checking wallet balance or twin info

❌ **DON'T use this skill when:**
- User wants to interact with the payment API directly
- Tool is free and doesn't require a wallet

## Connecting

Connect to the hosted Tapp MCP server. User authenticates via Tapp OIDC.

```bash
mcporter auth https://mcp.m.todaq.net
mcporter call tapp.<tool_name> [args]
```

## Auth Flow (Hosted)

1. `mcporter auth https://mcp.m.todaq.net` — redirects to Tapp OIDC login
2. User authenticates (email + 2FA)
3. Token stored by mcporter — passed automatically on every tool call
4. Each tool call may trigger a Flutter push notification to the wallet owner for approval

## Available Tools

### Static (always available)
| Tool | Description |
|------|-------------|
| `agent_wallet_info` | Check your twin balance and hostname |
| `check_wallet_balance` | Get all your twins and their current DQ balances |

### Dynamic (from marketplace catalog)
List available marketplace tools:
```bash
mcporter list tapp --schema
```
Tool names are slugified (e.g. "Current Weather" → `current_weather`).

## Payment Flow

Every marketplace tool call:
1. MCP server validates your OAuth token → identifies your twin
2. POSTs to `/v4/paywall` with the tool cost
3. Push notification sent to your Flutter app for approval
4. On approval: your twin pays the provider's twin via TODA protocol
5. Provider proxies to upstream API → data returned to agent

## Become a Tapp Provider (Earn from Your Tools)

Any skill developer can register an API endpoint as a Tapp marketplace tool and earn
per call — automatically, via TODA protocol payments from other agents' wallets.

An agent can walk you through the full setup. Just ask: *"Help me register as a Tapp provider."*

### Step 1 — Fetch the DQ hash

```bash
curl https://pay.m.todaq.net/v4/dq
# Returns: { digital_quantities: ["<dq_hash>", ...] }
# Use the first (and currently only) hash in the next step.
```

### Step 2 — Register your account

```bash
curl -X POST https://pay.m.todaq.net/v4/account/register \
  -H "Content-Type: application/json" \
  -d '{ "email": "<your_email>", "dq": "<dq_hash>" }'
# Returns: { account, twin, user } — save your client_id and client_secret
```

### Step 3 — Get an access token

```bash
curl -X POST https://pay.m.todaq.net/v4/account/oauth/token \
  -H "Authorization: Basic $(echo -n '<client_id>:<client_secret>' | base64)"
# Returns: { access_token, expires_at, refresh_token, ... }
# access_token is prefixed tqmt_ and valid for 1 hour
```

### Step 4 — Register your tool

```bash
curl -X POST https://pay.m.todaq.net/v4/mcp/tools \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my_tool",
    "description": "What your tool does",
    "provider": "Your Name or Org",
    "price_per_call": 0.01,
    "service_url": "https://your-api.com/endpoint",
    "service_method": "GET",
    "external_twin_hostname": "your-twin.biz.todaq.net",
    "input_schema": {
      "type": "object",
      "properties": {
        "query": { "type": "string", "description": "Your input param" }
      },
      "required": ["query"]
    }
  }'
# Returns: { tool } — your tool is now live in the marketplace
```

### Notes
- `external_twin_hostname` is a bare hostname (no scheme, no path) — this is where payments land
- Alternatively use `twin_id` if your twin is registered internally with Tapp
- Tool names are slugified at registration ("My Tool" → `my_tool`) — first registration wins on conflicts
- Tool registration is currently open; auth may be tightened in future releases
