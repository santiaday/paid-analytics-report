#!/usr/bin/env python3
"""runlog.py — machine-readable run ledger + Slack alert for the multi-channel daily routine.

The orchestrator (DAILY-ROUTINE.md) calls this at each step boundary so "did the run
work?" is a one-line read instead of inferring from file timestamps. build.py reads the
SAME ledger to paint an alert banner on Paid-Analytics.html (see build.py health_banner).

  python3 runlog.py <ANCHOR> start                              # init run-log/<ANCHOR>.jsonl + MS-venv + gws/netlify auth health checks
  python3 runlog.py <ANCHOR> log <step> <ok|fail|stale|skip> ["detail"]   # append one entry
  python3 runlog.py <ANCHOR> summary                            # print summary (no Slack); exit 1 if any fail/stale
  python3 runlog.py <ANCHOR> finish                             # summary + ALWAYS Slack David's DM (success heartbeat OR failure alert), naming the machine

Ledger = one JSON object per line in run-log/<ANCHOR>.jsonl: {ts, step, status, detail}.
status: ok | fail | stale | skip. fail → red, stale → amber (banner + Slack). skip/ok are quiet.

The run NEVER stops on a failure — steps log and continue; this script only RECORDS and
NOTIFIES. finish ALWAYS Slacks: a ✅ heartbeat on a clean run, a ⚠️ alert on any fail/stale or
missing report. The heartbeat is deliberate — if Claude hangs/errors mid-run, finish never
runs and NO message arrives, so a missing Slack is itself the "something's off" signal. Every
message names the machine that ran it (David runs this on 2 laptops). Slack reuses
ms_conversion_check.load_webhooks/slack_post (single source for the webhook); it posts ONLY to
David's DM (the first webhook), never #marketing-alerts. Idempotent: finish writes a
run-log/<ANCHOR>.notified marker so a rebuild won't re-Slack (start clears it for a new run).
"""
import sys, os, json, socket, datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.path.join(HERE, "run-log")
ET = ZoneInfo("America/New_York")
NETLIFY = "https://abcsad9823ndi2n92nd12.netlify.app/"
REPORT = os.path.join(HERE, "outputs", "Paid-Analytics.html")
# The Microsoft Ads MCP venv python (bingads SDK) — the MS keyword/ad-group MCP tools are
# broken, so several step-2/step-5 sub-steps shell out to this interpreter. If it can't
# import bingads, those sub-steps silently fail — so we health-check it at run start.
MSPY = "/opt/mcp/MCP/microsoft-ads-mcp/repo/.venv/bin/python"

BAD = ("fail", "stale")  # statuses that trigger the banner + Slack


def now_ts():
    return datetime.datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S %Z")


def machine_name():
    """Which laptop/device ran this — so David knows which of his 2 machines did the run."""
    n = socket.gethostname()
    return n[:-6] if n.endswith(".local") else n


def logpath(anchor):
    return os.path.join(LOGDIR, f"{anchor}.jsonl")


def append(anchor, step, status, detail=""):
    os.makedirs(LOGDIR, exist_ok=True)
    rec = {"ts": now_ts(), "step": step, "status": status, "detail": detail}
    with open(logpath(anchor), "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return rec


def read_entries(anchor):
    p = logpath(anchor)
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def ms_venv_check():
    """Try to import the bingads SDK in the MS MCP venv. Returns (ok, detail)."""
    import subprocess
    if not os.path.exists(MSPY):
        return False, f"MS venv python missing: {MSPY}"
    try:
        r = subprocess.run([MSPY, "-c", "import bingads"], capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return True, "bingads import OK"
        return False, f"bingads import failed: {(r.stderr or r.stdout).strip()[:160]}"
    except Exception as e:
        return False, f"MS venv check errored: {str(e)[:160]}"


def auth_check():
    """Early-warn on the two PUBLISH-path credentials (Drive via `gws`, Netlify CLI) so a stale
    login is caught at run start — not at step 7/8 after all the data work. Returns (ok, detail).
    Deliberately FAIL-OPEN on ambiguity: only flags a CLEAR negative (token_valid:false / 'not
    logged in' / binary missing), so a format change can't cry wolf — the real deploy step still
    catches a genuine failure. The data steps don't need these, so this never blocks the run."""
    import subprocess, shutil, re, json as _json
    bad = []
    # gws (Google Drive upload, step 7) — `gws auth status` returns JSON with token_valid
    if not shutil.which("gws"):
        bad.append("gws not on PATH")
    else:
        try:
            r = subprocess.run(["gws", "auth", "status"], capture_output=True, text=True, timeout=45)
            m = re.search(r"\{.*\}", r.stdout or "", re.S)
            if m and _json.loads(m.group(0)).get("token_valid") is False:
                bad.append("gws Drive token expired — run: gws auth login -s drive,sheets")
        except Exception:
            pass  # ambiguous → don't alarm
    # netlify (deploy, step 8) — `netlify status` says 'Not logged in' when unauthenticated
    if not shutil.which("netlify"):
        bad.append("netlify not on PATH")
    else:
        try:
            r = subprocess.run(["netlify", "status"], capture_output=True, text=True, timeout=45)
            if "not logged in" in ((r.stdout or "") + (r.stderr or "")).lower():
                bad.append("netlify CLI logged out — run: netlify login")
        except Exception:
            pass
    return (not bad), ("; ".join(bad) if bad else "gws + netlify auth OK")


def report_status(anchor):
    """(ok, detail) for the built report: must exist and be modified today (NY)."""
    if not os.path.exists(REPORT):
        return False, "Paid-Analytics.html was never written"
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(REPORT), ET).date()
    today = datetime.datetime.now(ET).date()
    if mt != today:
        return False, f"Paid-Analytics.html last built {mt} (not today {today})"
    return True, f"built today ({os.path.getsize(REPORT)//1024} KB)"


def problems(anchor):
    """List of (step, status, detail) for every fail/stale entry, deduped on step+status+detail."""
    seen, out = set(), []
    for e in read_entries(anchor):
        if e.get("status") in BAD:
            k = (e.get("step"), e.get("status"), e.get("detail"))
            if k not in seen:
                seen.add(k)
                out.append((e.get("step", "?"), e.get("status", "?"), e.get("detail", "")))
    return out


def print_summary(anchor):
    entries = read_entries(anchor)
    probs = problems(anchor)
    rok, rdetail = report_status(anchor)
    print(f"=== run-log {anchor} — {len(entries)} entr{'y' if len(entries)==1 else 'ies'} ===")
    for e in entries:
        flag = {"ok": "🟢", "fail": "🔴", "stale": "🟡", "skip": "⚪"}.get(e.get("status"), "•")
        d = f" — {e['detail']}" if e.get("detail") else ""
        print(f"  {flag} {e.get('step','?'):<22} {e.get('status','?'):<6}{d}")
    print(f"  {'🟢' if rok else '🔴'} report                 {'ok' if rok else 'MISSING'} — {rdetail}")
    return probs, (rok, rdetail)


def slack_notify(anchor, probs, report, entries):
    """ALWAYS Slack David's DM when the run finishes — a success heartbeat OR a failure alert.
    The heartbeat matters: if Claude hangs/errors mid-run, finish never runs and NO message
    arrives — so 'no Slack today' is itself the signal that something is off. Every message
    names the machine that ran it (David runs this on 2 laptops). DM only, never #marketing-alerts.
    Idempotent per anchor via a .notified marker (start clears it for a genuine new run)."""
    rok, rdetail = report
    healthy = (not probs) and rok
    marker = os.path.join(LOGDIR, f"{anchor}.notified")
    if os.path.exists(marker):
        sys.stderr.write("  (already notified for this anchor — skipping Slack)\n")
        return
    sys.path.insert(0, HERE)
    try:
        from ms_conversion_check import load_webhooks, slack_post
    except Exception as e:
        sys.stderr.write(f"  ⚠ could not load Slack helpers: {str(e)[:140]}\n")
        return
    dm, _mkt = load_webhooks()  # David's DM only — never #marketing-alerts
    mc = machine_name()
    if healthy:
        n_ok = sum(1 for e in entries if e.get("status") == "ok" and str(e.get("step", "")).startswith("step"))
        lines = [f":white_check_mark: *Multi-channel daily report — completed OK* for {anchor}",
                 f"• Ran on *{mc}*",
                 f"• {n_ok} step{'s' if n_ok != 1 else ''} OK · report {rdetail}",
                 f"Report: {NETLIFY} (pwd dl)"]
    else:
        lines = [f":warning: *Multi-channel daily report — issues on {anchor}*",
                 f"• Ran on *{mc}*"]
        if not rok:
            lines.append(f"• :red_circle: *Report:* {rdetail}")
        for step, status, detail in probs:
            icon = ":red_circle:" if status == "fail" else ":large_yellow_circle:"
            lines.append(f"• {icon} *{step}* ({status}){' — ' + detail if detail else ''}")
        lines.append(f"Report: {NETLIFY} (pwd dl)")
    msg = "\n".join(lines)
    if slack_post(dm, msg):
        open(marker, "w").write(now_ts())
        sys.stderr.write(f"  Slack {'heartbeat' if healthy else 'alert'} sent to David's DM (machine: {mc})\n")
    else:
        sys.stderr.write("  ⚠ Slack NOT sent (no webhook / post failed)\n")


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit("usage: runlog.py <ANCHOR> start|log|summary|finish [...]")
    anchor, cmd = args[0], args[1]

    if cmd == "start":
        os.makedirs(LOGDIR, exist_ok=True)
        open(logpath(anchor), "w").close()  # truncate/init
        # clear any prior notify marker so a genuine new run sends a fresh heartbeat/alert
        m = os.path.join(LOGDIR, f"{anchor}.notified")
        if os.path.exists(m):
            os.remove(m)
        append(anchor, "run", "ok", "run started")
        ok, detail = ms_venv_check()
        append(anchor, "ms-venv", "ok" if ok else "fail", detail)
        aok, adetail = auth_check()
        append(anchor, "auth", "ok" if aok else "stale", adetail)
        print(f"started run-log {anchor} | MS venv: {'🟢' if ok else '🔴'} {detail} | "
              f"publish auth: {'🟢' if aok else '🟡'} {adetail}")

    elif cmd == "log":
        if len(args) < 4:
            sys.exit('usage: runlog.py <ANCHOR> log <step> <ok|fail|stale|skip> ["detail"]')
        step, status = args[2], args[3]
        detail = args[4] if len(args) > 4 else ""
        if status not in ("ok", "fail", "stale", "skip"):
            sys.exit(f"bad status '{status}' (use ok|fail|stale|skip)")
        rec = append(anchor, step, status, detail)
        flag = {"ok": "🟢", "fail": "🔴", "stale": "🟡", "skip": "⚪"}.get(status, "•")
        print(f"{flag} logged {step} {status}{(' — ' + detail) if detail else ''}")

    elif cmd == "summary":
        probs, _ = print_summary(anchor)
        sys.exit(1 if probs else 0)

    elif cmd == "finish":
        probs, report = print_summary(anchor)
        rok, _ = report
        slack_notify(anchor, probs, report, read_entries(anchor))  # ALWAYS — success heartbeat or failure alert
        sys.exit(1 if (probs or not rok) else 0)

    else:
        sys.exit(f"unknown command '{cmd}'")


if __name__ == "__main__":
    main()
