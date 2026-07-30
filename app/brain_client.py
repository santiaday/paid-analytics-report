"""Headless access to the DoorLoop Brain metric cube (brain_run_metric_query).

Ports the proven TypeScript client (src/brain/oauth.ts + src/mcp-http.ts + src/brain/client.ts) to
Python so the pull can run as a pure-Python ZIP Lambda (no container, no ECR). Behaviour is
identical:

  * OAuth 2.0 refresh_token grant against the Brain's authorization server (30-min JWT access
    tokens; a human mints the first refresh token once via the bootstrap script).
  * Refresh tokens ROTATE on every grant — the caller MUST persist the returned token
    (on_refresh_token_rotated → Secrets Manager) or the next run's token is stale.
  * MCP over Streamable HTTP: initialize handshake, notifications/initialized, tools/call, the
    mcp-session-id header, and both buffered-JSON and SSE (text/event-stream) response bodies.
  * edge_headers are sent on EVERY request (discovery, token, mcp) — an Envoy/edge service token
    that gates the Brain before OAuth (the allowlist the NAT egress IP / NordVPN satisfies).

Only the standard library + `requests` (bundled) are used; boto3 is provided by the Lambda runtime.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

import requests

DEFAULT_ISSUER = "https://brain.doorloop.com"


def discover_metadata(issuer: str = DEFAULT_ISSUER, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """RFC 8414 authorization-server metadata (public, but still behind the Brain's edge gate)."""
    url = issuer.rstrip("/") + "/.well-known/oauth-authorization-server"
    r = requests.get(url, headers={"accept": "application/json", **(extra_headers or {})}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"oauth metadata {r.status_code} from {url}: {r.text}")
    return r.json()


def refresh_access_token(
    token_endpoint: str,
    client_id: str,
    refresh_token: str,
    client_secret: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Exchange a refresh token for a fresh access token (and the rotated refresh token)."""
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    if client_secret:
        form["client_secret"] = client_secret
    r = requests.post(
        token_endpoint,
        data=form,
        headers={"content-type": "application/x-www-form-urlencoded", **(extra_headers or {})},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"refresh_token grant {r.status_code}: {r.text}")
    body = r.json()
    if not body.get("access_token"):
        raise RuntimeError("refresh_token grant returned no access_token")
    return {
        "access_token": body["access_token"],
        # If the server didn't rotate (some don't), keep the current one.
        "refresh_token": body.get("refresh_token") or refresh_token,
        "expires_at": int(time.time()) + int(body.get("expires_in") or 1800),
        "scope": body.get("scope"),
    }


def _parse_body(content_type: str, text: str, want_id: int) -> Any:
    """Extract the JSON-RPC result for want_id from a buffered-JSON or SSE response body."""
    if "text/event-stream" in content_type:
        for line in text.replace("\r\n", "\n").split("\n"):
            data = line[5:].strip() if line.startswith("data:") else ""
            if not data:
                continue
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue  # skip non-JSON data lines
            if msg.get("id") == want_id:
                if msg.get("error"):
                    raise RuntimeError(f"mcp error: {msg['error'].get('message', 'unknown')}")
                return msg.get("result")
        raise RuntimeError("mcp: no matching response in SSE stream")
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"mcp: non-JSON response (content-type={content_type or 'none'}): {text[:400]}")
    if msg.get("error"):
        raise RuntimeError(f"mcp error: {msg['error'].get('message', 'unknown')}")
    return msg.get("result")


class McpHttpClient:
    """Minimal MCP client over Streamable HTTP. Initializes lazily on first call()."""

    def __init__(
        self,
        endpoint: str,
        get_token: Callable[[], str],
        extra_headers: Optional[Dict[str, str]] = None,
        protocol_version: str = "2025-03-26",
        client_info: Optional[Dict[str, str]] = None,
    ) -> None:
        self.endpoint = endpoint
        self.get_token = get_token
        self.extra_headers = extra_headers or {}
        self.proto = protocol_version
        self.client_info = client_info or {"name": "paid-analytics-pull", "version": "0.2.0"}
        self._id = 0
        self._initialized = False
        self._session_id: Optional[str] = None
        self._session = requests.Session()

    def _send(self, method: str, params: Any, is_notification: bool = False) -> Any:
        self._id += 1
        rid = self._id
        headers = {
            **self.extra_headers,
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "authorization": f"Bearer {self.get_token()}",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not is_notification:
            payload["id"] = rid
        r = self._session.post(self.endpoint, data=json.dumps(payload), headers=headers, timeout=120)
        sid = r.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        if is_notification:
            return None
        if not r.ok:
            raise RuntimeError(f"mcp http {r.status_code}: {r.text}")
        return _parse_body(r.headers.get("content-type") or "", r.text, rid)

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        self._send("initialize", {"protocolVersion": self.proto, "capabilities": {}, "clientInfo": self.client_info})
        self._send("notifications/initialized", {}, is_notification=True)
        self._initialized = True

    def call(self, tool: str, args: Dict[str, Any]) -> Any:
        self._ensure_init()
        return self._send("tools/call", {"name": tool, "arguments": args})


def parse_tool_result(raw: Any) -> Dict[str, Any]:
    """Unwrap an MCP tools/call result into the tool's JSON payload (a metric result dict)."""
    r = raw if isinstance(raw, dict) else {}
    payload: Any = None
    text_part = None
    for c in r.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str):
            text_part = c["text"]
            break
    if text_part:
        # On an unhandled server-side failure FastMCP sets isError with a human message (NOT JSON).
        if r.get("isError"):
            raise RuntimeError(f"brain tool error: {text_part}")
        try:
            payload = json.loads(text_part)
        except json.JSONDecodeError:
            raise RuntimeError(f"brain tool returned non-JSON content: {text_part[:600]}")
    elif r.get("structuredContent") is not None:
        payload = r["structuredContent"]
    else:
        raise RuntimeError("brain tool returned no content")

    obj = payload
    # Unwrap a {result: …} envelope (FastMCP wraps non-dict returns this way).
    if isinstance(obj, dict) and "rows" not in obj and "result" in obj:
        inner = obj["result"]
        obj = json.loads(inner) if isinstance(inner, str) else inner
    if isinstance(obj, dict) and "error" in obj:
        err = obj["error"] if isinstance(obj["error"], dict) else {}
        raise RuntimeError(f"brain tool error [{err.get('code', 'unknown')}]: {err.get('message', json.dumps(obj))}")
    return obj


class BrainClient:
    def __init__(
        self,
        secrets: Dict[str, Any],
        on_refresh_token_rotated: Optional[Callable[[str], None]] = None,
        edge_headers: Optional[Dict[str, str]] = None,
        refresh_skew_seconds: int = 60,
    ) -> None:
        self._secrets = secrets
        self._on_rotate = on_refresh_token_rotated
        self._edge = edge_headers or {}
        self._skew = refresh_skew_seconds
        self._issuer = (secrets.get("issuer") or DEFAULT_ISSUER).rstrip("/")
        self._current_refresh = secrets["refresh_token"]
        self._token: Optional[Dict[str, Any]] = None  # {value, expires_at}
        self._meta: Optional[Dict[str, Any]] = None
        endpoint = secrets.get("mcp_endpoint") or (self._issuer + "/mcp")
        self._mcp = McpHttpClient(endpoint, self.get_access_token, extra_headers=self._edge)

    def _metadata(self) -> Dict[str, Any]:
        if self._meta is None:
            self._meta = discover_metadata(self._issuer, self._edge)
        return self._meta

    def get_access_token(self) -> str:
        now = int(time.time())
        if self._token and self._token["expires_at"] - self._skew > now:
            return self._token["value"]
        meta = self._metadata()
        res = refresh_access_token(
            token_endpoint=meta["token_endpoint"],
            client_id=self._secrets["client_id"],
            refresh_token=self._current_refresh,
            client_secret=self._secrets.get("client_secret"),
            extra_headers=self._edge,
        )
        self._token = {"value": res["access_token"], "expires_at": res["expires_at"]}
        if res["refresh_token"] != self._current_refresh:
            self._current_refresh = res["refresh_token"]
            if self._on_rotate:
                self._on_rotate(res["refresh_token"])
        return res["access_token"]

    def run_metric_query(
        self,
        name: str,
        filters: Optional[Dict[str, Any]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        refresh: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Run a metric's pinned query.

        The tool signature is brain_run_metric_query(params: RunMetricQueryInput) — a single
        Pydantic-model arg — so the real arguments nest under `params`. Passing {name, filters} at
        the top level fails validation ("params Field required").
        """
        params: Dict[str, Any] = {"name": name, "filters": filters or {}}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if refresh is not None:
            params["refresh"] = refresh
        raw = self._mcp.call("brain_run_metric_query", {"params": params})
        return parse_tool_result(raw)
