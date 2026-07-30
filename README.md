# paid-analytics-report — Multi-Channel Paid Analytics on DeployBay

A self-contained DeployBay app that **pulls, builds, and serves** the DoorLoop Multi-Channel Paid
Analytics report — all in one Python container. Pulls demos/opps/ARR + spend from the Brain
(headless OAuth + MCP) and Google/Meta from their APIs, runs the report renderer (`build.py`), and
serves the result. No AWS, no Lambda, no CLI.

## How it works

`server.py` is a long-running web app on `$PORT` (default 8080):

- **Rebuilds** the report on **startup**, **daily ~07:40 ET** (an in-app scheduler — no cron needed),
  and on demand at **`GET/POST /refresh`**.
- **Serves** `index.html` + `engine.js` + `boot.js` + `data/*.json` from the latest build.
- **`GET /healthz`** → `{ok, building, last_ok, last_error, anchor}`.
- Builds a Brain client from **env vars** (below). Access is gated by DeployBay's ingress grant.

The container is `python:3.12-slim` (the whole pipeline is Python). The vendored report renderer +
sibling artifacts live in `app/pipeline/`.

```
Dockerfile          python:3.12-slim → CMD python server.py
server.py           serve + in-app scheduler + /refresh + /healthz
app/
  handler.py        pull orchestration (Brain + Google/Meta) → update_history → build.py
  brain_client.py   Brain OAuth 2.0 refresh + MCP Streamable-HTTP
  spend_to_clicks.py  paid-media-daily-keyword → clicks_30d transform
  emit.py           S3 helpers (unused on DeployBay; boto3 is lazy so it's never imported)
  requirements.txt  requests, tzdata
  pipeline/         vendored report renderer + Google/Meta/Microsoft sibling artifacts
scripts/
  brain-bootstrap.mjs  one-time: mint the Brain credentials (opens a doorloop.com login)
tests/              pytest — transform, Brain result/SSE parsing
```

## Deploy on DeployBay

1. **Connect this repo** in DeployBay (Source → GitHub). It builds the root `Dockerfile`.
2. **Set env vars** (DeployBay → project → environment):

   | Var | Required | Value |
   |-----|----------|-------|
   | `BRAIN_ISSUER` | yes | `https://brain.doorloop.com` |
   | `BRAIN_CLIENT_ID` | yes | from `node scripts/brain-bootstrap.mjs` |
   | `BRAIN_CLIENT_SECRET` | yes | ″ |
   | `BRAIN_REFRESH_TOKEN` | yes | ″ (rotates on first use; the app persists the rotation on disk) |
   | `GOOGLE_ADS_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` / `_DEVELOPER_TOKEN` / `_LOGIN_CUSTOMER_ID` | optional | exact Google spend + anchor sections |
   | `META_ACCESS_TOKEN` / `META_AD_ACCOUNT_ID` | optional | Meta spend (`act_…`) |

   Only the four `BRAIN_*` are required; without Google/Meta those tails degrade gracefully.
3. **Launch.** First load shows a "generating" placeholder for a few seconds while the startup build
   runs, then the report appears. `/healthz` shows build status; `/refresh` forces a rebuild.

### Minting the Brain credentials (one-time, ~2 min, on the allowlisted network)
```sh
node scripts/brain-bootstrap.mjs      # opens a doorloop.com Google login, prints the 4 BRAIN_* values
```

## Local run
```sh
PORT=8099 BRAIN_ISSUER=… BRAIN_CLIENT_ID=… BRAIN_CLIENT_SECRET=… BRAIN_REFRESH_TOKEN=… \
  python server.py
# open http://localhost:8099/   (needs an allowlisted network / VPN for the Brain)
python -m pytest -q               # unit tests (needs: pip install -r app/requirements.txt pytest)
```

## Static HTML → Google Drive (every morning)
`build.py` produces a **self-contained** `Paid-Analytics.html` (all data inline — the ~6 MB single
file). If a Drive folder is configured, each daily build **uploads that file to Google Drive** — it
**creates** the file on the first run and **updates it in place** thereafter, so the file and its
shared link stay stable. Reuses the existing Google OAuth (same client as Google Ads + a
`drive.file`-scoped refresh token; the Sheets adapter's token works). Purely additive — a Drive
failure never blocks the build or the served page.

Set these env vars to enable it (leave `GDRIVE_FOLDER_ID` unset to skip Drive entirely):

| Var | Value |
|-----|-------|
| `GDRIVE_FOLDER_ID` | the target Drive folder id (from its URL — e.g. the "All Channels" folder) |
| `GDRIVE_REFRESH_TOKEN` | a `drive.file`-scoped refresh token (your `GOOGLE_SHEETS_REFRESH_TOKEN`) |
| `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET` | the shared OAuth client (already set for the Google Ads pull) |
| `GDRIVE_FILE_NAME` | optional; default `Paid-Analytics.html` |

**One-time:** share the target folder (Editor) with the Google account that owns the refresh token, so
the app can write into it.

## Brain access — the one network requirement
The Brain (`brain.doorloop.com`) is behind an **Envoy IP allowlist** (separate from any platform
SQL/`revops-db` access). This app egresses from DeployBay's public IP, which must be on that allowlist
or the pull fails with `oauth metadata 403 …/.well-known/oauth-authorization-server`. Two options:
- **Allowlist the egress IP:** open `https://your-app.deploybay.io/whoami` → it returns
  `{"egress_ip": "…"}`. Send that IP to the Brain/edge owner (`@yzhang`) to add to the allowlist.
- **Edge service token:** if the Brain issues a service token instead, set env var
  `BRAIN_EDGE_HEADERS` to a JSON object of headers (e.g. `{"x-edge-token":"…"}`) — the client sends
  it on every request, no IP allowlisting needed.

## Notes
- **Persistence:** the container filesystem is ephemeral — a redeploy resets `history/` to the
  committed seed and rebuilds. Attach a DeployBay Database/volume later if durable trend history matters.
- **Refresh token = one runner.** The Brain refresh token rotates on use, so don't run a second copy
  with the same `BRAIN_*` creds — it would invalidate this one's token.
