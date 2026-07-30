# paid-analytics-report — Multi-Channel Paid Analytics on DeployBay

A **public-safe, data-free** DeployBay app that builds the DoorLoop Multi-Channel Paid Analytics
report every morning and uploads the self-contained `Paid-Analytics.html` to Google Drive. **No
business data lives in this repo** — the history is loaded at runtime from a private Drive file, and
account IDs come from env vars. One Python container: pull → build → Drive.

## Every ~6 hours (in-app schedule — no cron needed; 4×/day by default)
1. **Restore** the history from the private Drive seed → 2. **pull** the numbers from the Brain (paid
funnel + Google/Meta spend) **and Salesforce** (the all-channel funnel incl. ORGANIC — Blog,
Direct/Organic, Consumer Reviews) → 3. **build** the self-contained `Paid-Analytics.html` →
4. **upload** it to the shared Drive folder → 5. **save** the updated history back to the private seed.

Runs at `BUILD_TIMES_ET` (default `07:40,13:40,19:40,01:40` America/New_York). Also serves the report on
`$PORT`: **`GET /refresh`** triggers a manual pull on demand (rebuild + re-upload in ~1 min), `/healthz`
reports status, `/whoami` shows the egress IP.

## Why it's safe to be public
- No `history/` data, no backfill data — the repo ships an empty `history/` that is filled at runtime
  from a **private** Drive file (`paid-analytics-history.tar.gz`).
- No credentials (all via env vars), no hardcoded account IDs (env vars), no internal paths.

## Environment variables (set in DeployBay)

**Brain (required):**
| Var | Value |
|-----|-------|
| `BRAIN_ISSUER` | `https://brain.doorloop.com` |
| `BRAIN_CLIENT_ID` / `BRAIN_CLIENT_SECRET` / `BRAIN_REFRESH_TOKEN` | from `node scripts/brain-bootstrap.mjs` |
| `BRAIN_EDGE_HEADERS` | JSON edge-token headers, if the Brain uses a service token instead of an IP allowlist |

**Brain token — set once, never touch again.** The Brain refresh token rotates on each use, and a
container restart would otherwise fall back to the stale original and get revoked. To avoid that, the
app writes each rotated token back to its OWN DeployBay env var (via the `set_env_var` API), so a
restart always reads the latest valid token. Set these three so it can (optional but recommended):

| Var | Value |
|-----|-------|
| `DEPLOYBAY_API_BASE` | the DeployBay platform API base URL |
| `DEPLOYBAY_API_KEY` | a DeployBay API key (`dlp_…`, from `create_api_key`) |
| `DEPLOYBAY_APP_NAME` | this app's name |

Without these, the token still works while the container runs, but a restart requires re-minting +
updating `BRAIN_REFRESH_TOKEN` manually.

**Google Drive (the daily output + the private history seed):**
| Var | Value |
|-----|-------|
| `GDRIVE_FOLDER_ID` | shared folder the `Paid-Analytics.html` is uploaded to (the "All Channels" folder) |
| `GDRIVE_SEED_FOLDER_ID` | **private** folder that holds `paid-analytics-history.tar.gz` (defaults to `GDRIVE_FOLDER_ID`) |
| `GDRIVE_REFRESH_TOKEN` | a `drive.file`-scoped refresh token (your `GOOGLE_SHEETS_REFRESH_TOKEN`) |
| `GOOGLE_ADS_CLIENT_ID` / `GOOGLE_ADS_CLIENT_SECRET` | the shared Google OAuth client |

**Ads sources (optional — light up Google/Meta spend + anchor sections):**
| Var | Value |
|-----|-------|
| `GADS_CUSTOMER_ID` | Google Ads customer id (digits) |
| `GOOGLE_ADS_REFRESH_TOKEN` / `_DEVELOPER_TOKEN` / `_LOGIN_CUSTOMER_ID` | Google Ads pull |
| `META_ACCESS_TOKEN` / `META_AD_ACCOUNT_ID` | Meta spend (`act_…`) |
| `META_DEMO_CONVERSION_ID` | Meta custom-conversion id used for the demo filter (optional) |

**Salesforce (the All-Sources all-channel funnel — organic + paid; read-only JWT Bearer):**
| Var | Value |
|-----|-------|
| `SF_PROD_CLIENT_ID` | read-only Connected App consumer key |
| `SF_PROD_USERNAME` | integration-user email (read-only permission set) |
| `SF_PROD_LOGIN_URL` | login endpoint (default `https://login.salesforce.com`) |
| `SF_PROD_JWT_KEY_B64` | base64 of the RSA private-key PEM (or `SF_PROD_JWT_KEY` raw PEM / `SF_PROD_JWT_KEY_PATH` file for local dev) |

The Brain funnel is paid-only (google-ads/bing/meta); the report's All-Sources tab also shows ORGANIC
channels, which live only in Salesforce (bucketed by `UTM_Source__c`). `sf_allsources.py` pulls the
9-slice all-channel funnel via **read-only SELECTs** (JWT Bearer, same as the Revops platform) → a
`series.json` that `update_history.py` merges. Without these vars the report still builds — only the
All-Sources funnel tail stays stale. First-click UTM basis (intentionally different from the Brain-cohort
per-channel tabs, which the report footnote explains).

## One-time setup
1. **Upload the seed once.** Put `paid-analytics-history.tar.gz` (provided separately — the current
   2-year history) into a **private** Drive folder, and set `GDRIVE_SEED_FOLDER_ID` to that folder.
   After that the app maintains the seed itself (pushes the updated history back daily).
2. **Share the output folder** (Editor) with the Google account that owns `GDRIVE_REFRESH_TOKEN`.
3. **Brain:** mint creds with `node scripts/brain-bootstrap.mjs` (on an allowlisted network) and get
   DeployBay's egress IP allowlisted on the Brain (open `/whoami` to read it), or use a service token.

If the seed is ever missing, the report still builds — only the multi-day trend starts short and
rebuilds over ~2 weeks.

## Layout
```
Dockerfile        python:3.12-slim → CMD python server.py
server.py         serve + daily rebuild + /refresh + /healthz + /whoami + seed round-trip
app/
  handler.py      pull orchestration (Brain + Google/Meta) → update_history → build.py
  brain_client.py Brain OAuth 2.0 + MCP Streamable-HTTP
  drive_upload.py Google Drive upload/download (report HTML + history seed)
  emit.py         S3 helpers (unused here; boto3 is lazy so it's never imported)
  spend_to_clicks.py  keyword→clicks_30d transform
  requirements.txt    requests, tzdata
  pipeline/       vendored report renderer (empty history/, filled from the Drive seed at runtime)
scripts/brain-bootstrap.mjs   one-time Brain credential mint
tests/            pytest
```
