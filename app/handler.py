"""The daily paid-analytics pull, as a pure-Python AWS Lambda (ZIP — no container, no ECR).

Orchestrates the deterministic pipeline end-to-end, headless:
  history restore (S3) → Brain pulls (demos/opps/ARR + Bing spend) → Google/Meta anchor fetches →
  update_history.py + stores → build.py → port-frontend.py → publish site (S3) → history push (S3).
Triggered daily by EventBridge (see template.yaml). No Claude in the loop.

Runtime layout inside the ZIP (CodeUri = app/, unpacked to /var/task):
  /var/task/handler.py, brain_client.py, spend_to_clicks.py, emit.py   this service
  /var/task/pipeline/multi-channel/   vendored renderer (build.py, update_history.py, port-frontend.py,
                                       adapters.py, engine.js, *_store.py)   ← PIPELINE_DIR
  /var/task/pipeline/{Google Ads,Microsoft Ads,meta-ads}/…   sibling artifacts   ← SK = pipeline/
Working data goes in /tmp (the only writable path in Lambda).

Secrets (Secrets Manager names via env): BRAIN_SECRET_ARN (issuer/client_id/client_secret/
refresh_token — the rotated refresh token is written BACK each run), GOOGLE_ADS_SECRET_ARN,
META_SECRET_ARN, SF_SECRET_ARN. history/ persists across runs via S3 (HISTORY_S3_PREFIX).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional


from brain_client import BrainClient
import emit
from spend_to_clicks import SIB_SUBDIR, keyword_rows_to_clicks_30d

BASE = os.path.dirname(os.path.abspath(__file__))
PIPELINE_ROOT = os.environ.get("PIPELINE_ROOT", os.path.join(BASE, "pipeline"))   # SK (siblings root)
PIPELINE_DIR = os.environ.get("PIPELINE_DIR", os.path.join(PIPELINE_ROOT, "multi-channel"))
WORK = os.environ.get("WORK_DIR", "/tmp/pa")
OUT = os.path.join(WORK, "out")
PY = os.environ.get("PYTHON_BIN", sys.executable)

_sm_client = None


def _sm():
    global _sm_client
    if _sm_client is None:
        import boto3
        _sm_client = boto3.client("secretsmanager")
    return _sm_client


# ---------------------------------------------------------------- helpers
def _run(cmd: List[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> str:
    """Run a subprocess, raising with the tail of stderr on failure."""
    p = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})},
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} exit {p.returncode}\n{p.stderr[-1500:]}")
    return p.stdout


def yesterday_ny() -> str:
    """America/New_York calendar date, minus one day."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = datetime.utcnow() - timedelta(hours=5)  # ET fallback (no DST) if tzdata missing
    return (now.date() - timedelta(days=1)).isoformat()


def _days_before(iso: str, n: int) -> str:
    return (date.fromisoformat(iso) - timedelta(days=n)).isoformat()


def window_start(anchor: str) -> str:
    return _days_before(anchor, 29)


def get_secret_json(name: Optional[str]) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    try:
        r = _sm().get_secret_value(SecretId=name)
    except _sm().exceptions.ResourceNotFoundException:
        return None
    s = r.get("SecretString")
    return json.loads(s) if s else None


def put_secret_json(name: str, value: Any) -> None:
    _sm().put_secret_value(SecretId=name, SecretString=json.dumps(value))


def make_brain() -> BrainClient:
    name = os.environ.get("BRAIN_SECRET_ARN")
    sec = get_secret_json(name)
    if not sec:
        raise RuntimeError("BRAIN_SECRET_ARN missing or empty")
    edge = os.environ.get("BRAIN_EDGE_HEADERS")

    def on_rotate(new_refresh: str) -> None:
        if name:
            put_secret_json(name, {**sec, "refresh_token": new_refresh})

    return BrainClient(
        secrets={
            "issuer": sec.get("issuer"),
            "client_id": sec["client_id"],
            "client_secret": sec.get("client_secret"),
            "refresh_token": sec["refresh_token"],
        },
        on_refresh_token_rotated=on_rotate,
        edge_headers=json.loads(edge) if edge else None,
    )


# ---------------------------------------------------------------- pulls
def pull_brain(brain: BrainClient, anchor: str) -> None:
    """cohort / funnel / keyword_funnel → demos, opps, ARR, ad-group funnel."""
    start = window_start(anchor)
    for name, fname in (
        ("gtm-paid-media-daily-cohort", "cohort.json"),
        ("gtm-paid-media-daily-funnel", "funnel.json"),
        ("gtm-paid-media-daily-keyword-funnel", "keyword_funnel.json"),
    ):
        res = brain.run_metric_query(name, start_date=start, end_date=anchor, limit=10_000)
        with open(os.path.join(OUT, fname), "w") as f:
            json.dump(res, f)
        print(f"brain {name}: {res.get('row_count')} rows")


def pull_spend(brain: BrainClient, anchor: str, sk: str) -> None:
    """Google+Bing spend (paid-media-daily-keyword) → per-platform clicks_30d.json at the SIB paths."""
    res = brain.run_metric_query("paid-media-daily-keyword", start_date=window_start(anchor), end_date=anchor, limit=10_000)
    rows = res.get("rows") if isinstance(res.get("rows"), list) else []
    by_plat = keyword_rows_to_clicks_30d(rows)
    for plat in ("google", "microsoft"):
        out = os.path.join(sk, SIB_SUBDIR[plat], "data", anchor, "clicks_30d.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            json.dump(by_plat[plat], f)
        print(f"brain spend {plat}: {len(by_plat[plat])} ad-group rows")


def run_optional_pulls(anchor: str, sk: str) -> None:
    """Google Ads + Meta anchor-day fetches (both stdlib Python, run verbatim). Each degrades
    independently — a missing secret leaves only that source's tail stale, the report still builds.

    NOTE: the Salesforce drill-down pulls were Node scripts (sf_*.js); a pure-Python Lambda can't run
    them, so the SF tails (drill-down funnel, ad-group pipeline, Meta first-touch demos) stay stale
    until those queries are ported to Python (v2). Everything the report leads with — demos, funnel,
    ARR, spend, Meta/Google leaderboards — comes from the Brain + the Python fetches.
    """
    env: Dict[str, str] = {"HOME": WORK}

    # Google Ads OAuth creds come from env vars (DeployBay injects them). The fetch reads them from a
    # synthetic ~/.claude.json (mcpServers['google-ads'].env) and reads GADS_CUSTOMER_ID from the env.
    _gkeys = ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN",
              "GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_LOGIN_CUSTOMER_ID")
    gads = ({k: os.environ[k] for k in _gkeys if os.environ.get(k)}
            if os.environ.get("GOOGLE_ADS_REFRESH_TOKEN") and os.environ.get("GADS_CUSTOMER_ID") else None)
    mcp_servers: Dict[str, Any] = {}
    if gads:
        mcp_servers["google-ads"] = {"env": gads}
    os.makedirs(WORK, exist_ok=True)
    with open(os.path.join(WORK, ".claude.json"), "w") as f:
        json.dump({"mcpServers": mcp_servers}, f)

    if gads:
        gdir = os.path.join(sk, "Google Ads", "google-ads-daily-artifact")
        try:
            _run([PY, os.path.join(gdir, "fetch_data.py"), "daily", anchor, "--force"], cwd=gdir, env=env)
            print("pull google-ads (anchor sections + exact spend): ok")
        except Exception as e:  # noqa: BLE001
            print(f"pull google-ads FAILED (Google anchor sections empty, spend stays Brain): {str(e).splitlines()[0]}")
    else:
        print("no GOOGLE_ADS_* / GADS_CUSTOMER_ID env — Google spend = Brain baseline, anchor sections empty")

    # Meta spend/clicks (ad-set grain) — the Meta Graph fetch. Reads ACCESS_TOKEN + AD_ACCOUNT_ID
    # from META_MCP_DIR/.env (patched at vendor time). Signature is `daily <anchor>`.
    meta = ({"access_token": os.environ["META_ACCESS_TOKEN"], "ad_account_id": os.environ["META_AD_ACCOUNT_ID"]}
            if os.environ.get("META_ACCESS_TOKEN") and os.environ.get("META_AD_ACCOUNT_ID") else None)
    if meta:
        meta_mcp = os.path.join(WORK, "meta-mcp")
        os.makedirs(meta_mcp, exist_ok=True)
        with open(os.path.join(meta_mcp, ".env"), "w") as f:
            f.write(f"ACCESS_TOKEN={meta['access_token']}\nAD_ACCOUNT_ID={meta['ad_account_id']}\n")
        mdir = os.path.join(sk, "meta-ads", "meta-ads-daily-artifact")
        try:
            _run([PY, os.path.join(mdir, "fetch_data.py"), "daily", anchor], cwd=mdir, env={**env, "META_MCP_DIR": meta_mcp})
            print("pull meta-spend (graph): ok")
        except Exception as e:  # noqa: BLE001
            print(f"pull meta-spend FAILED (Meta tail stays stale): {str(e).splitlines()[0]}")
    else:
        print("no META_ACCESS_TOKEN / META_AD_ACCOUNT_ID env — Meta spend tail stays stale")


def write_campfunnel(anchor: str) -> Optional[str]:
    """Synthesize the SF campfunnel file that the All-Sources scorecard reads, FROM the Brain funnel.

    The report's All-Sources funnel normally comes from Salesforce (sf_campfunnel.js — a Node script we
    don't run). The Brain's gtm-paid-media-daily-funnel carries the identical fields per day+campaign
    (MQLS/DEMOS_SCHEDULED/DEMOS_COMPLETED/OPPS/ACCOUNTS/NEW_ARR, SOURCE ∈ google-ads/bing/meta), so we
    aggregate those into the raw shape update_history's --campfunnel expects. This keeps the scorecard
    current instead of frozen at the seed's last SF date.
    """
    fp = os.path.join(OUT, "funnel.json")
    if not os.path.exists(fp):
        return None
    rows = json.load(open(fp)).get("rows") or []
    SRC_OK = {"google-ads", "bing", "meta"}
    agg: Dict[tuple, List[float]] = {}
    for r in rows:
        src = r.get("SOURCE")
        if src not in SRC_OK:
            continue
        key = (r.get("DT"), src, r.get("CAMPAIGN") or "")
        v = agg.setdefault(key, [0, 0, 0, 0, 0, 0.0])
        v[0] += int(r.get("MQLS") or 0);            v[1] += int(r.get("DEMOS_SCHEDULED") or 0)
        v[2] += int(r.get("DEMOS_COMPLETED") or 0); v[3] += int(r.get("OPPS") or 0)
        v[4] += int(r.get("ACCOUNTS") or 0);        v[5] += float(r.get("NEW_ARR") or 0)
    out = [{"DT": dt, "SOURCE": src, "UTM_CAMPAIGN": camp, "MQLS": v[0], "DEMOS": v[1],
            "COMPLETED": v[2], "OPPS": v[3], "ACCTS": v[4], "ARR": v[5]}
           for (dt, src, camp), v in agg.items()]
    path = os.path.join(OUT, f"sf_campfunnel_{window_start(anchor).replace('-', '')}_{anchor.replace('-', '')}.json")
    with open(path, "w") as f:
        json.dump(out, f)
    return path


def run_pipeline(anchor: str) -> str:
    """update_history → stores → build.py. Returns the rendered HTML path."""
    campfunnel = write_campfunnel(anchor)  # feed the Brain funnel to the All-Sources scorecard
    args = [PY, os.path.join(PIPELINE_DIR, "update_history.py"), anchor]
    if campfunnel:
        args += ["--campfunnel", campfunnel]
    for flag, fname in (
        ("--brain-cohort", "cohort.json"), ("--brain-funnel", "funnel.json"), ("--kw-funnel", "keyword_funnel.json"),
    ):
        p = os.path.join(OUT, fname)
        if os.path.exists(p):
            args += [flag, p]  # omit inputs whose pull didn't run (update_history warns, never blocks)
    print(_run(args, cwd=PIPELINE_DIR))

    for store in ("quality_store.py", "ms_conversion_check.py", "changelog_store.py"):
        try:
            print(_run([PY, os.path.join(PIPELINE_DIR, store), anchor], cwd=PIPELINE_DIR))
        except Exception as e:  # noqa: BLE001
            print(f"store {store} skipped: {str(e).splitlines()[0]}")

    print(_run([PY, os.path.join(PIPELINE_DIR, "build.py"), anchor], cwd=PIPELINE_DIR))
    return os.path.join(PIPELINE_DIR, "outputs", "Paid-Analytics.html")


# ---------------------------------------------------------------- entrypoint
def handler(event: Optional[Dict[str, Any]] = None, context: Any = None) -> Dict[str, Any]:
    event = event or {}

    # Egress-IP diagnostic. Reports THIS function's public egress IP (the NAT EIP the Brain edge must
    # allowlist) — no Brain call, no secret. Two ways to reach it, both with NO CLI:
    #   * direct invoke `{"whoami": true}`, or
    #   * ANY HTTP request to this function's Function URL (open it in a browser).
    # An HTTP request ALWAYS returns just the egress IP and NOTHING else — the full pull runs only via
    # EventBridge / a non-HTTP invoke, so exposing the URL can't trigger a real (costly) run.
    is_http = bool((event.get("requestContext") or {}).get("http"))
    if event.get("whoami") or is_http:
        import urllib.request
        ip = urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10).read().decode().strip()
        print(f"egress_ip={ip}")
        if is_http:
            return {"statusCode": 200, "headers": {"content-type": "application/json"},
                    "body": json.dumps({"egress_ip": ip})}
        return {"egress_ip": ip}

    anchor = event.get("anchor") or yesterday_ny()
    dry_run = bool(event.get("dryRun"))
    os.makedirs(OUT, exist_ok=True)
    print(f"paid-analytics pull: anchor={anchor}")
    sk = PIPELINE_ROOT

    # 1. Restore history/ from S3 (Lambda fs is ephemeral).
    hist_s3 = os.environ.get("HISTORY_S3_PREFIX")
    hist_dir = os.path.join(PIPELINE_DIR, "history")
    if hist_s3:
        try:
            n = emit.history_pull(hist_s3, hist_dir)
            print(f"history restore: {n} files")
        except Exception as e:  # noqa: BLE001
            print(f"history restore skipped: {str(e).splitlines()[0]}")

    # 2. Pulls: Brain (demos/funnel/ARR + Bing spend) + Google/Meta anchor fetches.
    brain = make_brain()
    pull_brain(brain, anchor)
    pull_spend(brain, anchor, sk)
    run_optional_pulls(anchor, sk)

    # 3. update_history + stores + build.py
    html_path = run_pipeline(anchor)

    # 4. Regenerate the DAILY site (shell + engine + boot + per-platform data) and publish to S3.
    site = os.path.join(WORK, "site")
    _run([PY, os.path.join(PIPELINE_DIR, "port-frontend.py"),
          "--baseline", html_path, "--engine", os.path.join(PIPELINE_DIR, "engine.js"), "--out", site])
    file_count = 0
    data_s3 = os.environ.get("DATA_S3_URL")
    if not dry_run and data_s3:
        file_count = emit.upload_site_dir(site, data_s3, gz=True)
        print(f"published site → {data_s3} ({file_count} files)")

    # 5. Persist updated history/ back to S3 for tomorrow's run.
    if not dry_run and hist_s3:
        pushed = emit.history_push(hist_dir, hist_s3)
        print(f"history push: {pushed} files")

    print(f"done: {file_count} files published, anchor {anchor}")
    return {"ok": True, "anchor": anchor, "files": file_count}
