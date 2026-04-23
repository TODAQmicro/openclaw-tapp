#!/usr/bin/env python3
"""TODAQ MCP client utility — register, authenticate, and interact with the TODAQ MCP server."""

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

MCP_BASE = "https://mcp.m.todaq.net"
MCP_ENDPOINT = f"{MCP_BASE}/mcp"
AS_BASE = "https://pay.m.todaq.net"
REDIRECT_PORT = 3333
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
SCOPES = "openid profile email twin offline_access"
TOKEN_FILE = os.path.expanduser("~/.toda-mcp-token.json")
REG_FILE = os.path.expanduser("~/.toda-mcp-client.json")


def _rand_b64(length: int = 32) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(length)).rstrip(b"=").decode()


def _sha256_b64(data: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data.encode()).digest()).rstrip(b"=").decode()


def load_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_json(path: str, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def register_client() -> dict:
    existing = load_json(REG_FILE)
    if existing:
        return existing

    resp = httpx.post(
        f"{AS_BASE}/v4/oidc/reg",
        json={
            "client_name": "toda-mcp-cli",
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": SCOPES,
        },
    )
    resp.raise_for_status()
    client = resp.json()
    save_json(REG_FILE, client)
    print(f"Registered client: {client['client_id'][:20]}...")
    return client


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None
    error: str | None = None

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        if "code" in qs:
            _CallbackHandler.code = qs["code"][0]
            _CallbackHandler.state = qs.get("state", [None])[0]
            self._respond("Authorization successful! You can close this tab.")
        elif "error" in qs:
            _CallbackHandler.error = qs["error"][0]
            self._respond(f"Authorization failed: {qs['error'][0]}")
        else:
            self._respond("Unexpected request")

    def _respond(self, msg: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(f"<html><body><h2>{msg}</h2></body></html>".encode())

    def log_message(self, format, *args):
        pass


def authenticate(client: dict) -> dict:
    existing = load_json(TOKEN_FILE)
    if existing and "access_token" in existing:
        refreshed = _try_refresh(client, existing)
        if refreshed:
            return refreshed

    verifier = _rand_b64(32)
    challenge = _sha256_b64(verifier)
    state = _rand_b64(16)

    params = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    auth_url = f"{AS_BASE}/v4/oidc/auth?{urlencode(params)}"
    print(f"Opening browser for authorization...")
    print(f"If browser doesn't open, visit:\n  {auth_url}\n")

    server = HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
    webbrowser.open(auth_url)

    server.handle_request()

    if _CallbackHandler.error:
        print(f"Auth error: {_CallbackHandler.error}")
        sys.exit(1)

    if _CallbackHandler.state != state:
        print("State mismatch — possible CSRF.")
        sys.exit(1)

    code = _CallbackHandler.code or ""
    print("Exchanging authorization code for token...")
    token = _exchange_code(client, code, verifier)
    save_json(TOKEN_FILE, token)
    return token


def _exchange_code(client: dict, code: str, verifier: str) -> dict:
    resp = httpx.post(
        f"{AS_BASE}/v4/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client["client_id"],
            "code_verifier": verifier,
        },
    )
    resp.raise_for_status()
    return resp.json()


def _try_refresh(client: dict, token: dict) -> dict | None:
    if "refresh_token" not in token:
        return None
    try:
        resp = httpx.post(
            f"{AS_BASE}/v4/oidc/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
                "client_id": client["client_id"],
            },
        )
        resp.raise_for_status()
        new_token = resp.json()
        save_json(TOKEN_FILE, new_token)
        return new_token
    except httpx.HTTPStatusError:
        return None


class MCPClient:
    def __init__(self, token: str):
        self.token = token
        self._next_id = 1
        self._client = httpx.Client(timeout=30)
        self.initialized = False
        self._session_id: str | None = None

    def _id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def _headers(self) -> dict:
        h = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _request(self, method: str, params: dict | None = None) -> dict:
        msg: dict = {
            "jsonrpc": "2.0",
            "id": self._id(),
            "method": method,
        }
        if params is not None:
            msg["params"] = params

        resp = self._client.post(MCP_ENDPOINT, headers=self._headers(), json=msg)

        if not resp.is_success:
            print(f"  [{method}] {resp.status_code}: {resp.text[:500]}")
            resp.raise_for_status()

        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return self._parse_sse(resp.text)
        return resp.json()

    def _parse_sse(self, text: str) -> dict:
        data = ""
        for line in text.splitlines():
            if line.startswith("data: "):
                data += line[6:]
        return json.loads(data) if data else {}

    def _notify(self, method: str, params: dict | None = None):
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        try:
            self._client.post(MCP_ENDPOINT, headers=self._headers(), json=msg)
        except httpx.HTTPStatusError as e:
            print(f"  (notification {method} got {e.response.status_code}: {e.response.text[:200]})")

    def initialize(self) -> dict:
        result = self._request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "toda-mcp-cli", "version": "0.1.0"},
        })
        self._notify("notifications/initialized")
        self.initialized = True
        return result

    def list_tools(self) -> dict:
        return self._request("tools/list", {})

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self._request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })

    def list_resources(self) -> dict:
        return self._request("resources/list", {})

    def read_resource(self, uri: str) -> dict:
        return self._request("resources/read", {"uri": uri})

    def list_prompts(self) -> dict:
        return self._request("prompts/list", {})

    def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        return self._request("prompts/get", {
            "name": name,
            "arguments": arguments or {},
        })

    def close(self):
        self._client.close()


def cmd_auth():
    client = register_client()
    token = authenticate(client)
    print(f"Authenticated! Token saved to {TOKEN_FILE}")


def _print_tools(tools: dict):
    tool_list = tools.get("result", {}).get("tools", [])
    if not tool_list:
        print("No tools available.")
        return
    print(f"\n{'='*72}")
    print(f" {len(tool_list)} Tools Available")
    print(f"{'='*72}\n")
    for t in tool_list:
        name = t.get("name", "?")
        desc = t.get("description", "")
        schema = t.get("inputSchema", {}).get("properties", {})
        required = t.get("inputSchema", {}).get("required", [])
        price_match = re.search(r"Price:\s*([\d.]+)\s*USD TDN/call", desc)
        price = price_match.group(1) if price_match else None
        provider_match = re.search(r"Provider:\s*([^|\]]+)", desc)
        provider = provider_match.group(1).strip() if provider_match else None
        print(f"  {name}")
        if price:
            print(f"    Price: {price} USD TDN/call")
        if provider:
            print(f"    Provider: {provider}")
        if schema:
            params = []
            for pname, pdef in schema.items():
                ptype = pdef.get("type", "?")
                pdesc = pdef.get("description", "")
                req = " (required)" if pname in required else ""
                params.append(f"{pname}: {ptype}{req}" + (f" — {pdesc}" if pdesc else ""))
            print(f"    Params: {'; '.join(params)}")
        clean_desc = re.sub(r"\s*\[Provider:.*?\]", "", desc)
        clean_desc = re.sub(r"\s*\[.*?Price:.*?\]", "", clean_desc).strip()
        if clean_desc:
            print(f"    {clean_desc}")
        print()


def cmd_tools():
    client_info = register_client()
    token = load_json(TOKEN_FILE)
    if not token:
        print("No token found. Run: toda_mcp.py auth")
        sys.exit(1)
    refreshed = _try_refresh(client_info, token) or token
    mcp = MCPClient(refreshed["access_token"])
    init = mcp.initialize()
    info = init.get("result", {}).get("serverInfo", {})
    print(f"Server: {info.get('name', '?')} v{info.get('version', '?')}")
    tools = mcp.list_tools()
    _print_tools(tools)


def cmd_call(args: list[str]):
    if len(args) < 1:
        print("Usage: toda_mcp.py call <tool_name> [json_arguments]")
        sys.exit(1)
    tool_name = args[0]
    arguments = json.loads(args[1]) if len(args) > 1 else {}
    client_info = register_client()
    token = load_json(TOKEN_FILE)
    if not token:
        print("No token found. Run: toda_mcp.py auth")
        sys.exit(1)
    refreshed = _try_refresh(client_info, token) or token
    mcp = MCPClient(refreshed["access_token"])
    mcp.initialize()
    result = mcp.call_tool(tool_name, arguments)
    print(json.dumps(result, indent=2))


def cmd_resources():
    client_info = register_client()
    token = load_json(TOKEN_FILE)
    if not token:
        print("No token found. Run: toda_mcp.py auth")
        sys.exit(1)
    refreshed = _try_refresh(client_info, token) or token
    mcp = MCPClient(refreshed["access_token"])
    mcp.initialize()
    result = mcp.list_resources()
    print(json.dumps(result, indent=2))


def cmd_prompts():
    client_info = register_client()
    token = load_json(TOKEN_FILE)
    if not token:
        print("No token found. Run: toda_mcp.py auth")
        sys.exit(1)
    refreshed = _try_refresh(client_info, token) or token
    mcp = MCPClient(refreshed["access_token"])
    mcp.initialize()
    result = mcp.list_prompts()
    print(json.dumps(result, indent=2))


def cmd_shell():
    client_info = register_client()
    token = load_json(TOKEN_FILE)
    if not token:
        print("No token found. Run: toda_mcp.py auth")
        sys.exit(1)
    refreshed = _try_refresh(client_info, token) or token
    mcp = MCPClient(refreshed["access_token"])
    init = mcp.initialize()
    server_info = init.get("result", {}).get("serverInfo", {})
    print(f"Connected to {server_info.get('name', 'MCP server')} v{server_info.get('version', '?')}")
    print("Commands: tools, resources, prompts, call <name> [args], quit")

    while True:
        try:
            line = input("mcp> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line or line in ("quit", "exit", "q"):
            break
        parts = line.split(maxsplit=2)
        cmd = parts[0]
        try:
            if cmd == "tools":
                _print_tools(mcp.list_tools())
            elif cmd == "resources":
                print(json.dumps(mcp.list_resources(), indent=2))
            elif cmd == "prompts":
                print(json.dumps(mcp.list_prompts(), indent=2))
            elif cmd == "call" and len(parts) >= 2:
                name = parts[1]
                args = json.loads(parts[2]) if len(parts) > 2 else {}
                print(json.dumps(mcp.call_tool(name, args), indent=2))
            elif cmd == "read" and len(parts) >= 2:
                print(json.dumps(mcp.read_resource(parts[1]), indent=2))
            elif cmd == "prompt" and len(parts) >= 2:
                name = parts[1]
                args = json.loads(parts[2]) if len(parts) > 2 else {}
                print(json.dumps(mcp.get_prompt(name, args), indent=2))
            elif cmd == "init":
                print(json.dumps(mcp.initialize(), indent=2))
            else:
                print("Unknown command. Use: tools, resources, prompts, call <name> [args], read <uri>, quit")
        except Exception as e:
            print(f"Error: {e}")

    mcp.close()
    print("Bye.")


def main():
    commands = {
        "auth": (cmd_auth, "Register client & authenticate via browser"),
        "tools": (cmd_tools, "Initialize & list available MCP tools"),
        "call": (cmd_call, "Call a tool: call <name> [json_args]"),
        "resources": (cmd_resources, "List available MCP resources"),
        "prompts": (cmd_prompts, "List available MCP prompts"),
        "shell": (cmd_shell, "Interactive MCP shell"),
    }

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: toda_mcp.py <command> [args...]\n")
        for name, (_, desc) in commands.items():
            print(f"  {name:12s} {desc}")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

    _, desc = commands[cmd]
    fn = _
    if cmd == "call":
        fn = lambda: cmd_call(sys.argv[2:])
    else:
        fn = fn

    fn()


if __name__ == "__main__":
    main()
