#!/usr/bin/env python3
"""Run the pull locally / on the VPS — the same code the Lambda runs, with creds from the environment
instead of Secrets Manager and output to a local dir instead of S3.

  python3 scripts/run_local.py <ANCHOR> [--smoke]

--smoke  only exercises the Brain pulls (OAuth + MCP), printing row counts — the fast check that the
         ported Python Brain client works against the live Brain (needs VPN/allowlisted egress).
full     also stages a copy of the vendored pipeline, runs update_history → build.py → port-frontend,
         and leaves the site in <WORK>/site. The committed app/pipeline is never mutated.

Env (source your .env.brain first): BRAIN_ISSUER, BRAIN_CLIENT_ID, BRAIN_CLIENT_SECRET,
BRAIN_REFRESH_TOKEN, [BRAIN_EDGE_HEADERS], [BRAIN_TOKEN_WRITEBACK=path to persist a rotated token].
Optional for the Google/Meta tails: the same secrets the Lambda uses, via GOOGLE_ADS_SECRET_ARN-style
files are NOT read here — --smoke and the core Brain path don't need them.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # services/paid-analytics-pull
APP = os.path.join(HERE, "app")
ANCHOR = next((a for a in sys.argv[1:] if not a.startswith("--")), "2026-07-12")
SMOKE = "--smoke" in sys.argv
WORK = os.environ.get("WORK_DIR", os.path.join(HERE, ".local-run"))

# Point the handler's module globals at a WORK copy BEFORE importing it.
os.environ["WORK_DIR"] = WORK
os.environ["PIPELINE_ROOT"] = os.path.join(WORK, "report")
os.environ["PIPELINE_DIR"] = os.path.join(WORK, "report", "multi-channel")
sys.path.insert(0, APP)

from brain_client import BrainClient  # noqa: E402


def make_local_brain() -> BrainClient:
    for k in ("BRAIN_CLIENT_ID", "BRAIN_REFRESH_TOKEN"):
        if not os.environ.get(k):
            sys.exit(f"missing {k} — source your .env.brain first")
    writeback = os.environ.get("BRAIN_TOKEN_WRITEBACK")

    def on_rotate(new_refresh: str) -> None:
        if writeback:
            with open(writeback, "w") as f:
                f.write(new_refresh)
            print(f"  (rotated refresh token written to {writeback})")

    edge = os.environ.get("BRAIN_EDGE_HEADERS")
    return BrainClient(
        secrets={
            "issuer": os.environ.get("BRAIN_ISSUER"),
            "client_id": os.environ["BRAIN_CLIENT_ID"],
            "client_secret": os.environ.get("BRAIN_CLIENT_SECRET"),
            "refresh_token": os.environ["BRAIN_REFRESH_TOKEN"],
        },
        on_refresh_token_rotated=on_rotate,
        edge_headers=json.loads(edge) if edge else None,
    )


def main() -> None:
    print(f"== paid-analytics run_local · anchor={ANCHOR} · {'smoke' if SMOKE else 'full'} ==")
    os.makedirs(os.path.join(WORK, "out"), exist_ok=True)
    brain = make_local_brain()

    if SMOKE:
        # Exercise the ported OAuth + MCP path against the live Brain — the real validation.
        for name in ("gtm-paid-media-daily-cohort", "gtm-paid-media-daily-funnel",
                     "gtm-paid-media-daily-keyword-funnel", "paid-media-daily-keyword"):
            res = brain.run_metric_query(name, start_date="2026-06-13", end_date=ANCHOR, limit=5)
            n = res.get("row_count", len(res.get("rows", [])))
            print(f"  ✓ {name}: {n} rows (freshness={ (res.get('freshness') or {}) })")
        print("Brain client OK (OAuth refresh + MCP tools/call live).")
        return

    import handler  # noqa: E402  (imports after env is set so its globals point at WORK)

    # Stage a runnable copy of the vendored pipeline (code + history; data/ excluded) so the
    # committed app/pipeline is never mutated.
    report = os.path.join(WORK, "report")
    if os.path.exists(report):
        shutil.rmtree(report)
    subprocess.run(["rsync", "-a", "--exclude", "data/", "--exclude", "outputs/",
                    "--exclude", "__pycache__/", APP + "/pipeline/", report + "/"], check=True)
    os.makedirs(os.path.join(report, "multi-channel", "outputs"), exist_ok=True)

    sk = os.environ["PIPELINE_ROOT"]
    handler.pull_brain(brain, ANCHOR)
    handler.pull_spend(brain, ANCHOR, sk)
    handler.run_optional_pulls(ANCHOR, sk)     # Google/Meta if their creds happen to be in ~/.claude.json / env
    html = handler.run_pipeline(ANCHOR)
    site = os.path.join(WORK, "site")
    subprocess.run([sys.executable, os.path.join(handler.PIPELINE_DIR, "port-frontend.py"),
                    "--baseline", html, "--engine", os.path.join(handler.PIPELINE_DIR, "engine.js"),
                    "--out", site], check=True)
    print(f"\n== done. site in {site} (index.html + engine.js + boot.js + data/*.json) ==")


if __name__ == "__main__":
    main()
