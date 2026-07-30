#!/usr/bin/env python3
"""DeployBay app: serve the Multi-Channel Paid Analytics report + rebuild it on a schedule.

One long-running process (DeployBay runs it as a web service on $PORT):
  * on startup, and daily at ~07:40 ET, and on GET/POST /refresh → run the FULL pull (Brain OAuth+MCP
    + Google/Meta fetches) → build.py → port-frontend → a local site dir, then serve it.
  * serves index.html + engine.js + boot.js + data/*.json from that dir; /healthz reports status.

Creds come from ENV VARS (DeployBay injects them) — no Secrets Manager, no AWS:
  BRAIN_ISSUER, BRAIN_CLIENT_ID, BRAIN_CLIENT_SECRET, BRAIN_REFRESH_TOKEN   (required)
  GOOGLE_ADS_CLIENT_ID/_CLIENT_SECRET/_REFRESH_TOKEN/_DEVELOPER_TOKEN/_LOGIN_CUSTOMER_ID  (optional — Google spend + anchor sections)
  META_ACCESS_TOKEN, META_AD_ACCOUNT_ID   (optional — Meta spend)
Access is gated by DeployBay's ingress grant (no app-level password).

The rotating Brain refresh token is persisted to BRAIN_TOKEN_WRITEBACK (a file on the container's
disk) so restarts within a container's life reuse the latest token; a fresh container falls back to
the env var (still valid until its first use).
"""
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(BASE, "app")
sys.path.insert(0, APP)

# Point the pull's module globals at the container's own pipeline + a writable work dir BEFORE import.
os.environ.setdefault("WORK_DIR", "/tmp/pa")
os.environ.setdefault("PIPELINE_ROOT", os.path.join(APP, "pipeline"))
os.environ.setdefault("PIPELINE_DIR", os.path.join(APP, "pipeline", "multi-channel"))
os.environ.setdefault("BRAIN_TOKEN_WRITEBACK", "/tmp/pa/.brain-refresh-token")

import handler as H  # noqa: E402  (reads the env above at import)
from brain_client import BrainClient  # noqa: E402

PORT = int(os.environ.get("PORT", "8080"))
SITE = os.environ.get("SITE_DIR", "/tmp/pa/site")
CONTENT_TYPE = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8", ".css": "text/css; charset=utf-8"}

STATE = {"building": False, "last_ok": None, "last_error": None, "anchor": None}
_lock = threading.Lock()


def _persist_token_to_deploybay(new_token: str) -> None:
    """Write the rotated Brain refresh token back to THIS app's DeployBay env var, so it survives a
    container restart (the running container already holds the token in memory; this keeps the env var
    current for the next cold start). Solves refresh-token rotation without any external store — the
    token lives only in DeployBay env vars. No-op unless the DeployBay API is configured.

    Env: DEPLOYBAY_API_BASE (the platform API base URL), DEPLOYBAY_API_KEY (a dlp_… key), and
    DEPLOYBAY_APP_NAME (this app's name). Uses the set_env_var route: POST /apps/{name}/env.
    """
    api = os.environ.get("DEPLOYBAY_API_BASE")
    key = os.environ.get("DEPLOYBAY_API_KEY")
    app = os.environ.get("DEPLOYBAY_APP_NAME")
    if not (api and key and app):
        return
    try:
        import requests
        r = requests.post(f"{api.rstrip('/')}/apps/{app}/env",
                          json={"key": "BRAIN_REFRESH_TOKEN", "value": new_token},
                          headers={"authorization": f"Bearer {key}"}, timeout=30)
        print(f"[token] persisted rotated BRAIN_REFRESH_TOKEN to DeployBay env: {r.status_code}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[token] DeployBay env update failed (in-memory token still works this run): {e}", flush=True)


def _brain_from_env() -> BrainClient:
    for k in ("BRAIN_CLIENT_ID", "BRAIN_REFRESH_TOKEN"):
        if not os.environ.get(k):
            raise RuntimeError(f"missing required env var {k}")
    writeback = os.environ.get("BRAIN_TOKEN_WRITEBACK")
    # Prefer an already-rotated token from a prior build in this container.
    refresh = os.environ["BRAIN_REFRESH_TOKEN"]
    if writeback and os.path.exists(writeback):
        try:
            refresh = open(writeback).read().strip() or refresh
        except OSError:
            pass

    def on_rotate(new_refresh: str) -> None:
        if writeback:
            os.makedirs(os.path.dirname(writeback), exist_ok=True)
            with open(writeback, "w") as f:
                f.write(new_refresh)
        _persist_token_to_deploybay(new_refresh)  # keep the DeployBay env var current → survives restarts

    edge = os.environ.get("BRAIN_EDGE_HEADERS")
    return BrainClient(
        secrets={"issuer": os.environ.get("BRAIN_ISSUER"), "client_id": os.environ["BRAIN_CLIENT_ID"],
                 "client_secret": os.environ.get("BRAIN_CLIENT_SECRET"), "refresh_token": refresh},
        on_refresh_token_rotated=on_rotate,
        edge_headers=json.loads(edge) if edge else None,
    )


# The history seed lives in a PRIVATE Drive file (the repo is public + data-free). We download it at
# startup so the pipeline has history to append to, and push the updated history back after each build.
SEED_NAME = os.environ.get("GDRIVE_SEED_NAME", "paid-analytics-history.tar.gz")
SEED_FOLDER = os.environ.get("GDRIVE_SEED_FOLDER_ID") or os.environ.get("GDRIVE_FOLDER_ID")
HIST_DIR = os.path.join(H.PIPELINE_DIR, "history")


def _ensure_seed() -> None:
    """Restore history/ from the private Drive seed if we don't already have it this container's life."""
    if os.path.exists(os.path.join(HIST_DIR, "google.json")):
        return
    if not SEED_FOLDER:
        print("[seed] no GDRIVE_SEED_FOLDER_ID — history starts empty (report builds; trend is short)", flush=True)
        return
    import tarfile
    import drive_upload
    tmp = "/tmp/pa-seed.tar.gz"
    try:
        if drive_upload.download(SEED_FOLDER, SEED_NAME, tmp):
            os.makedirs(HIST_DIR, exist_ok=True)
            with tarfile.open(tmp) as t:
                t.extractall(os.path.dirname(HIST_DIR))  # tarball holds history/…
            print("[seed] restored history from Drive", flush=True)
        else:
            print("[seed] no seed in Drive yet — history starts empty (first run seeds it on push)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[seed] restore failed (history starts empty): {e}", flush=True)


def _push_seed() -> None:
    """Save the updated history/ back to the private Drive seed for tomorrow / the next container."""
    if not SEED_FOLDER or not os.path.exists(os.path.join(HIST_DIR, "google.json")):
        return
    import tarfile
    import drive_upload
    tmp = "/tmp/pa-seed-out.tar.gz"
    try:
        with tarfile.open(tmp, "w:gz") as t:
            t.add(HIST_DIR, arcname="history")
        drive_upload.upload(tmp, folder_id=SEED_FOLDER, name=SEED_NAME)
        print("[seed] pushed updated history to Drive", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[seed] push failed (report still built): {e}", flush=True)


def build_once(anchor: str | None = None) -> None:
    """Run the full pull → build.py → port-frontend into SITE. Skips if a build is already running."""
    with _lock:
        if STATE["building"]:
            return
        STATE["building"] = True
    anchor = anchor or H.yesterday_ny()
    try:
        os.makedirs(H.OUT, exist_ok=True)
        _ensure_seed()                 # restore history/ from the private Drive seed (once per container)
        brain = _brain_from_env()
        H.pull_brain(brain, anchor)
        H.pull_spend(brain, anchor, H.PIPELINE_ROOT)
        H.run_optional_pulls(anchor, H.PIPELINE_ROOT)   # Google/Meta if their env vars are set
        html = H.run_pipeline(anchor)  # the self-contained Paid-Analytics.html (all data inline)

        # If a Drive folder is configured, upload that self-contained HTML there (create-then-update
        # in place). This is the "static HTML dropped in Google Drive every morning" path — additive,
        # non-fatal: a Drive failure never blocks the build or the served site.
        if os.environ.get("GDRIVE_FOLDER_ID"):
            try:
                import drive_upload
                drive_upload.upload(html)
            except Exception as e:  # noqa: BLE001
                print(f"[drive] upload FAILED (report still built): {e}", flush=True)

        _push_seed()                   # save the updated history/ back to the private Drive seed

        # port-frontend.py renders the served site (shell + engine + boot + per-platform data).
        H._run([H.PY, os.path.join(H.PIPELINE_DIR, "port-frontend.py"),
                "--baseline", html, "--engine", os.path.join(H.PIPELINE_DIR, "engine.js"), "--out", SITE])
        STATE.update(last_ok=datetime.utcnow().isoformat() + "Z", last_error=None, anchor=anchor)
        print(f"[build] OK anchor={anchor} → {SITE}", flush=True)
    except Exception as e:  # noqa: BLE001
        STATE.update(last_error=f"{type(e).__name__}: {e}")
        print(f"[build] FAILED anchor={anchor}: {e}\n{traceback.format_exc()}", flush=True)
    finally:
        with _lock:
            STATE["building"] = False


def _seconds_until_next_run() -> float:
    """Seconds until the next ~07:40 America/New_York (the report's daily cadence)."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
        nxt = now.replace(hour=7, minute=40, second=0, microsecond=0)
    except Exception:
        now = datetime.utcnow()
        nxt = now.replace(hour=12, minute=40, second=0, microsecond=0)  # ~07:40 ET in UTC (no DST)
    if nxt <= now:
        nxt += timedelta(days=1)
    return max(60.0, (nxt - now).total_seconds())


def scheduler() -> None:
    build_once()  # build immediately on startup so the first request has a report
    while True:
        time.sleep(_seconds_until_next_run())
        build_once()


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_path(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        # prevent traversal; map to the built site dir
        rel = os.path.normpath(path).lstrip("/")
        full = os.path.join(SITE, rel)
        if not full.startswith(SITE) or not os.path.isfile(full):
            if path == "/index.html":
                # first build not ready yet (or failed)
                msg = ("<!doctype html><meta charset=utf-8><title>Paid Analytics</title>"
                       "<body style='font:16px system-ui;max-width:40rem;margin:4rem auto;padding:0 1rem'>"
                       "<h1>Paid Analytics</h1><p>The report is being generated"
                       f"{' — last error: ' + STATE['last_error'] if STATE['last_error'] else ''}. "
                       "Refresh in a moment, or hit <code>/refresh</code>.</p>")
                return self._send(200 if not STATE["last_error"] else 503, msg.encode(), "text/html; charset=utf-8")
            return self._send(404, b"Not found")
        with open(full, "rb") as f:
            body = f.read()
        ctype = CONTENT_TYPE.get(os.path.splitext(full)[1], "application/octet-stream")
        self._send(200, body, ctype)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/healthz":
            return self._send(200, json.dumps({"ok": STATE["last_error"] is None, **STATE}).encode(),
                              "application/json")
        if p == "/whoami":
            # Report this container's public egress IP — the IP to allowlist on the Brain's edge gate.
            import urllib.request
            try:
                ip = urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10).read().decode().strip()
            except Exception as e:  # noqa: BLE001
                ip = f"error: {e}"
            return self._send(200, json.dumps({"egress_ip": ip}).encode(), "application/json")
        if p == "/refresh":
            threading.Thread(target=build_once, daemon=True).start()
            return self._send(202, json.dumps({"triggered": True, **STATE}).encode(), "application/json")
        self._serve_path(p)

    do_HEAD = do_GET

    def do_POST(self):
        if self.path.split("?")[0] == "/refresh":
            threading.Thread(target=build_once, daemon=True).start()
            return self._send(202, json.dumps({"triggered": True}).encode(), "application/json")
        self._send(404, b"Not found")

    def log_message(self, *a):  # quieter logs
        pass


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main() -> None:
    os.makedirs(SITE, exist_ok=True)
    threading.Thread(target=scheduler, daemon=True).start()
    print(f"paid-analytics DeployBay app listening on :{PORT} (site={SITE})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
