#!/usr/bin/env python3
"""ms_conversion_check.py — health check for the Microsoft Ads 'Demo_Scheduled' OFFLINE
conversion goal. Run with the MCP-repo venv python (bingads SDK), like ms_quality.py:

  <MS-venv-python> ms_conversion_check.py <ANCHOR YYYY-MM-DD>

WHY: Demo_Scheduled is an OFFLINE conversion (NOT the website pixel). It flows Zapier →
Google Sheet → Microsoft Ads offline-conversion import — a fragile pipe that has silently
broken before (Zapier paused / out of monthly credits).

THE LAG (why the old "anchor == 0" rule was WRONG — false-alarmed 2026-06-19): Microsoft
imports these offline conversions with a DELAY that fills in over hours/1-2 days. The SAME
platform pull for a given day grows as the day settles — verified 2026-06-19: 6/18 read
*0* at 08:48 AM and *5* by that afternoon (identical CampaignPerformance pull, just later).
So "yesterday == 0 at build time" is a NORMAL transient state, not a broken pipe. The old
gate ("built ≥5 AM AND anchor == 0 → broken") fired on that fresh-data zero and Slacked the
whole channel for nothing.

RULE (David, 2026-06-19 — "wait more than 1 day"): judge the day BEFORE the anchor
(eval_day = anchor - 1), which has had a full extra day to import. The pipe is BROKEN only
when, past the 5:00 AM ET gate, BOTH the anchor day AND eval_day show 0 Demo_Scheduled
conversions (a ≥2-day drought) → urgent banner (build.py paints it on the All Sources +
Microsoft tabs) + a Slack alert to David's DM AND #marketing-alerts. A single fresh-data
zero on the anchor never fires. Built before 5 AM ET (pre-sync) we don't judge; a failed MS
pull never judges. The banner is data-driven → auto-clears when conversions reappear. One
Slack per anchor day (idempotent on rebuilds; re-fires the next morning if still broken).

GENUINE-ZERO GUARD (David 2026-07-13 — after a false alarm on the 07-11/12 weekend): even a
real 2-day MS drought can be legitimate — on a low-volume weekend there may simply be no Bing
demos to sync. Because Salesforce is UPSTREAM of Zapier (demo booked in SF → Zapier → Sheet →
Microsoft), we cross-check the pipe's source: `sf_bing_demos_count.js` counts Bing demos booked
(Original_Demo_Scheduled_At__c, UTM_Source__c='bing') over the same eval_day..anchor window.
If SF shows 0, the zero is GENUINE (nothing to sync) → suppress. If SF shows >0, those demos
never reached Microsoft → real outage → still fire (and the Slack names the count). SF is only
queried when the MS side is already in a 2-day drought (no daily SF call on healthy days); if
SF can't be reached the check stays conservative and fires. This makes the test a real
discriminator, not a heuristic.

Writes history/ms_conv_health.json:
  {anchor, anchor_conversions, eval_day, eval_conversions, sf_bing_demos, ok, gate_hour,
   built_after_gate, checked_at, last_fired, series:[[date,conv]...], alerted_for, note}
"""
import sys, os, json, re, datetime, urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(HERE)
CLAUDE_ROOT = os.path.dirname(SKILLS)
SLACK_MEMORY = os.path.join(CLAUDE_ROOT, "Master Context", "Slack", "MEMORY.md")
OUT = os.path.join(HERE, "history", "ms_conv_health.json")
GATE_HOUR = 5                       # ET hour at/after which yesterday MUST be populated
GOAL = "Demo_Scheduled"             # the OFFLINE goal (not the website pixel)
ET = ZoneInfo("America/New_York")
NETLIFY = "https://abcsad9823ndi2n92nd12.netlify.app/"

MS = "/opt/mcp/Skills - Claude Code/Microsoft Ads/microsoft-ads-daily-artifact"
# NOTE: fetch_data (bingads SDK) is imported lazily inside pull_series() so this module can be
# imported by the eval suite (qa_conv_alert.py) under plain python3, without the MS venv.

def is_broken(pull_ok, built_after_gate, anchor_conv, eval_conv, sf_bing_demos=None):
    """THE decision. Broken ⟺ we actually pulled, it's past the 5 AM gate, BOTH the anchor
    day AND the day before it (eval_day, which has had >1 day for Microsoft to import) show 0
    Demo_Scheduled conversions, AND Salesforce (the pipe's UPSTREAM source) confirms Bing demos
    actually existed to sync. A lone fresh-data zero on the anchor is normal import lag and must
    NEVER alert (2026-06-19 false alarm).

    GENUINE-ZERO GUARD (David 2026-07-13): the pipe is demo-booked-in-SF → Zapier → Sheet →
    Microsoft, so SF is upstream of Zapier. When both MS days read 0, sf_bing_demos tells the two
    cases apart: 0 Bing demos in SF for the same window ⇒ nothing to sync ⇒ GENUINE zero, NOT
    broken (suppresses the false alarm — this is the 2026-07-11/12 weekend case). >0 ⇒ SF had
    demos Microsoft is missing ⇒ real outage ⇒ broken. None ⇒ SF couldn't be checked ⇒ stay
    conservative and treat as broken (better to over-alert than mask a real outage). NOTE: use
    TOTAL Bing demos booked, not just the Day-0 cohort — Day-0 can be 0 while lagged demos exist
    that SHOULD have synced, and suppressing on that would hide a real outage. Pure +
    side-effect-free so the eval suite exercises the REAL trigger, not a copy."""
    if not (bool(pull_ok) and bool(built_after_gate) and anchor_conv == 0 and eval_conv == 0):
        return False
    if sf_bing_demos == 0:
        return False   # genuine zero: SF shows no Bing demos in the window → nothing to sync
    return True        # SF has demos MS is missing (real outage), or SF unknown → conservative

def load_webhooks():
    """Read the two Slack incoming webhooks from the shared Slack MEMORY.md (single source of
    truth — we never copy them into this folder). Returns (david_dm, marketing_alerts) in the
    order they appear (DM first, #marketing-alerts second)."""
    try:
        with open(SLACK_MEMORY) as f:
            txt = f.read()
    except Exception:
        return None, None
    seen, urls = set(), []
    for u in re.findall(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+", txt):
        if u not in seen:
            seen.add(u); urls.append(u)
    dm = urls[0] if len(urls) >= 1 else None
    mkt = urls[1] if len(urls) >= 2 else None
    return dm, mkt

def slack_post(url, text):
    if not url:
        return False
    try:
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception as e:
        sys.stderr.write(f"  ⚠ slack post failed: {str(e)[:140]}\n")
        return False

def pull_series(anchor):
    """Daily Demo_Scheduled conversions for the trailing 8 days through ANCHOR."""
    if MS not in sys.path:
        sys.path.insert(0, MS)
    import fetch_data as fd  # bingads SDK harness (needs the MS venv; lazy on purpose)
    a = datetime.date.fromisoformat(anchor)
    start = a - datetime.timedelta(days=7)
    jobs = {"g": fd.Job("CampaignPerformance", ["TimePeriod", "CampaignName", "Goal", "Conversions"],
                        start, a, "Daily")}
    fd.run_jobs(jobs)
    byday = {}
    for r in jobs["g"].rows:
        if (r.get("Goal") or "").strip() == GOAL:
            d = (r.get("TimePeriod") or "").strip()
            byday[d] = byday.get(d, 0.0) + fd._f(r.get("Conversions"))
    return byday

def sf_bing_demos_in_window(eval_day, anchor):
    """READ-ONLY total Bing demos (UTM_Source__c='bing', Original_Demo_Scheduled_At__c) booked
    on the eval_day..anchor NY days, via sf_bing_demos_count.js (jsforce; creds from ~/.claude.json).
    Returns an int, or None if the check couldn't run — None keeps is_broken() conservative."""
    import subprocess
    js = os.path.join(HERE, "sf_bing_demos_count.js")
    try:
        p = subprocess.run(["node", js, eval_day, anchor], capture_output=True, text=True, timeout=120)
        if p.returncode != 0 or not p.stdout.strip():
            sys.stderr.write(f"  ⚠ SF genuine-zero check failed (rc={p.returncode}): {(p.stderr or '').strip()[:160]}\n")
            return None
        return int(json.loads(p.stdout.strip()).get("total", 0))
    except Exception as e:
        sys.stderr.write(f"  ⚠ SF genuine-zero check error: {str(e)[:160]}\n")
        return None

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    anchor = args[0] if args else (datetime.datetime.now(ET).date() - datetime.timedelta(days=1)).isoformat()
    now = datetime.datetime.now(ET)
    built_after_gate = now.hour >= GATE_HOUR

    try:
        byday = pull_series(anchor)
        pull_ok = True
    except Exception as e:
        byday, pull_ok = {}, False
        sys.stderr.write(f"  ⚠ MS pull failed: {str(e)[:160]}\n")

    anchor_conv = int(round(byday.get(anchor, 0.0)))
    a = datetime.date.fromisoformat(anchor)
    eval_day = (a - datetime.timedelta(days=1)).isoformat()       # the day we actually judge
    eval_conv = int(round(byday.get(eval_day, 0.0)))
    last_fired = max([d for d, v in byday.items() if v > 0], default=None)
    # "Wait more than 1 day" (David, 2026-06-19): Microsoft backfills these offline conversions
    # with a lag, so the anchor (yesterday) is routinely 0 at build time and fills in later.
    # Only call the pipe BROKEN when BOTH the anchor AND the day before it (eval_day — which has
    # had a full extra day to import) show 0. A lone fresh-data zero on the anchor never fires.
    # Only touch Salesforce when the MS side is actually in a 2-day drought (the only case where
    # the genuine-zero guard matters) — no daily SF call on healthy days.
    ms_drought = pull_ok and built_after_gate and anchor_conv == 0 and eval_conv == 0
    sf_bing_demos = sf_bing_demos_in_window(eval_day, anchor) if ms_drought else None
    broken = is_broken(pull_ok, built_after_gate, anchor_conv, eval_conv, sf_bing_demos)
    ok = not broken

    prev = {}
    if os.path.exists(OUT):
        try:
            with open(OUT) as f: prev = json.load(f)
        except Exception:
            prev = {}
    alerted_for = prev.get("alerted_for")

    if not pull_ok:
        note = "MS report pull failed — health not evaluated (banner suppressed)"
    elif not built_after_gate:
        note = "pre-sync (before %d:00 ET) — not evaluated" % GATE_HOUR
    elif not ms_drought:
        note = "OK — offline conversions present in the last 2 days"
    elif ok:   # 2-day MS zero, but Salesforce also shows 0 Bing demos → genuine zero, suppressed
        note = ("OK — 0 MS conversions on both %s and %s, but Salesforce shows 0 Bing demos in "
                "that window too (genuine zero — nothing to sync, pipe NOT broken)" % (eval_day, anchor))
    else:      # broken: SF has demos MS never received (or SF couldn't be checked)
        sfd = "unknown" if sf_bing_demos is None else sf_bing_demos
        note = ("BROKEN — 0 Demo_Scheduled conversions for both %s and %s (2-day drought); "
                "Salesforce shows %s Bing demo(s) that did NOT reach Microsoft" % (eval_day, anchor, sfd))

    if broken and alerted_for != anchor:
        dm, mkt = load_webhooks()
        lf = last_fired or "—"
        sf_line = (f"Salesforce shows *{sf_bing_demos} Bing demo(s)* booked in this window that never "
                   "reached Microsoft — confirming the pipe (not just a quiet day). "
                   if isinstance(sf_bing_demos, int) and sf_bing_demos > 0 else
                   "(Salesforce couldn't be checked for corroboration.) " if sf_bing_demos is None else "")
        msg = (":rotating_light: *Microsoft Ads offline conversions DOWN* — the `Demo_Scheduled` "
               f"goal shows *0 conversions for both {eval_day} and {anchor}* (the last 2 days). "
               f"{eval_day} has had over a day to import, so this is NOT just the normal import "
               f"lag. {sf_line}This is the OFFLINE conversion (NOT the website pixel): Zapier → Google "
               "Sheet → Microsoft Ads import. It looks broken — most likely *Zapier is paused or "
               f"out of monthly credits*. Last conversion imported: *{lf}*. Check Zapier now.\n"
               f"Report: {NETLIFY} (pwd dl)")
        if dry_run:
            sys.stderr.write("  [DRY-RUN] would Slack DM+#marketing-alerts:\n  " + msg.replace("\n", "\n  ") + "\n")
        else:
            sent_dm = slack_post(dm, msg)
            sent_mkt = slack_post(mkt, msg)
            if sent_dm or sent_mkt:
                alerted_for = anchor
            sys.stderr.write(f"  Slack alert sent — DM:{sent_dm} #marketing-alerts:{sent_mkt}\n")

    health = {"anchor": anchor, "anchor_conversions": anchor_conv,
              "eval_day": eval_day, "eval_conversions": eval_conv, "ok": ok,
              "sf_bing_demos": sf_bing_demos,
              "gate_hour": GATE_HOUR, "built_after_gate": built_after_gate,
              "checked_at": now.strftime("%Y-%m-%d %H:%M %Z"), "last_fired": last_fired,
              "series": [[d, int(round(byday[d]))] for d in sorted(byday)],
              "alerted_for": alerted_for, "note": note}
    json.dump(health, open(OUT, "w"), separators=(",", ":"))
    flag = "🟢 OK" if ok else "🔴 BROKEN"
    print(f"{flag} — {GOAL}: anchor {anchor}={anchor_conv}, eval {eval_day}={eval_conv} | "
          f"built {now.strftime('%-I:%M %p %Z')} (gate {GATE_HOUR}:00, after_gate={built_after_gate}) "
          f"| last_fired {last_fired} | {note}")

if __name__ == "__main__":
    main()
