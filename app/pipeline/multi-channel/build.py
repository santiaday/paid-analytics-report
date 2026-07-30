#!/usr/bin/env python3
"""build.py — render the unified multi-channel report.

Usage: python3 build.py [ANCHOR]          (default: yesterday America/New_York)

Inputs:  history/*.json (2-year fact tables) + the sibling artifacts'
         data/<ANCHOR>/ files (anchor-day sections, via adapters.py).
Output:  outputs/<ANCHOR>.html — self-contained, no network at view time.
"""
import json, os, re, sys, datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import adapters, adapters_assets, theme

CHARTJS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.min.js"
CHARTJS_SRI = "sha384-XcdcwHqIPULERb2yDEM4R0XaQKU3YnDsrTmjACBZyfdVVqjh6xQ4/DCMd7XLcA6Y"

def yesterday_ny():
    return (datetime.datetime.now(ZoneInfo("America/New_York")).date() - datetime.timedelta(days=1)).isoformat()

def load_history(name):
    p = os.path.join(HERE, "history", name + ".json")
    with open(p) as f:
        return json.load(f)

def trim_flags(h, platform):
    """attach per-platform UI flags + drop fields the client doesn't need"""
    h["flags"] = {
        "google":    {"branded": "branded"},
        "microsoft": {"branded": "brand"},
        "meta":      {},
    }.get(platform, {})
    return h

# ── Keyword Quality-Score monitoring (Google + Microsoft) ────────────────────
# The store (history/quality_<plat>.json, built by quality_store.py) holds the latest
# live keyword QS snapshot + a 14-day list of per-day CHANGES (QS is output-only/current —
# there is no historical QS to query, so movement is detected by diffing daily snapshots).
# build.py does two things with it: (1) embeds the whole store for the collapsed
# "Quality Scores" browse table (engine.js initQualitySection); (2) renders the latest
# day's ACTIVE-keyword changes as a table appended into that tab's Daily Alerts card, so
# it inherits the day-dropdown + reviewed-checkboxes + hide-reviewed for free.
QS_FIELD = {"qs": "Overall Quality Score", "ctr": "Expected CTR",
            "rel": "Ad Relevance", "lpx": "Landing Page Experience"}
QS_COMP = {"AA": "Above average", "AV": "Average", "BA": "Below average", "?": "Unknown"}

def load_quality(plat):
    try:
        with open(os.path.join(HERE, "history", f"quality_{plat}.json")) as f:
            return json.load(f)
    except Exception:
        return None

def _qs_val(field, v):
    return str(v) if field == "qs" else QS_COMP.get(v, "Unknown")

def quality_alert_html(store, anchor):
    """A Daily-Alerts table fragment for the anchor day's ACTIVE-keyword QS changes.
    Returns "" when there's no store. A baseline/no-change day shows a muted row so the
    section still renders (and explains why)."""
    if not store:
        return ""
    day = next((d for d in store.get("daily", []) if d.get("date") == anchor), None)
    changes = [c for c in (day or {}).get("changes", []) if c.get("active")] if day else []
    head = ('<h3>Quality Score Changes <span class="dt">— '
            + (f"{len(changes)} on active keywords" if changes else "none") + "</span></h3>")
    if not changes:
        why = ("first snapshot — change tracking begins tomorrow"
               if not store.get("prev_keywords") else "no movement on active keywords")
        return (head + '<table><thead><tr><th>Keyword</th><th>Campaign / Ad group</th>'
                '<th>Metric</th><th>Change</th></tr></thead><tbody>'
                f'<tr><td colspan="4" class="muted">No quality-score changes ({why}).</td>'
                '</tr></tbody></table>')
    rows = []
    for c in changes:
        cls = "qs-down" if c["dir"] < 0 else "qs-up"
        arrow = "▼" if c["dir"] < 0 else "▲"
        mt = f' <span class="qs-mt">{esc(c["mt"])}</span>' if c.get("mt") else ""
        loc = esc(c["c"]) + " › " + esc(c["g"])
        chg = f'{_qs_val(c["f"], c["from"])} → {_qs_val(c["f"], c["to"])} {arrow}'
        rows.append(f'<tr class="{cls}"><td>{esc(c["kw"])}{mt}</td><td>{loc}</td>'
                    f'<td>{QS_FIELD.get(c["f"], c["f"])}</td><td class="qs-chg">{esc(chg)}</td></tr>')
    return (head + '<table><thead><tr><th>Keyword</th><th>Campaign / Ad group</th>'
            '<th>Metric</th><th>Change</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>')

def esc(s):
    return (str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

# ── Microsoft offline-conversion health (Demo_Scheduled) → urgent banner ─────
# ms_conversion_check.py writes history/ms_conv_health.json with ok=False when the
# Demo_Scheduled OFFLINE conversion (Zapier → Sheet → Microsoft import) shows 0 for the
# day before AND the build ran past the 5 AM ET sync gate. We paint a red banner on the
# All Sources + Microsoft tabs; it's data-driven so it auto-clears once conversions return.
def load_conv_health():
    try:
        with open(os.path.join(HERE, "history", "ms_conv_health.json")) as f:
            return json.load(f)
    except Exception:
        return None

def conv_health_banner(health):
    if not health or health.get("ok", True):
        return ""
    lf = health.get("last_fired") or "—"
    eday = health.get("eval_day", "")
    anc = health.get("anchor", "")
    return (f'<div class="alert-top">🚨 <b>Microsoft Ads offline conversions DOWN.</b> '
            f'The <b>Demo_Scheduled</b> offline conversion (Zapier → Google Sheet → Microsoft Ads '
            f'import — <b>not</b> the website pixel) recorded <b>0 conversions for both '
            f'{esc(eday)} and {esc(anc)}</b> (the last 2 days). {esc(eday)} has had over a day to '
            f'import, so this is not just the normal import lag. The pipe looks broken — most '
            f'likely <b>Zapier is paused or out of monthly credits</b>. Last conversion imported: '
            f'<b>{esc(lf)}</b>. Check Zapier now — an alert was sent to #marketing-alerts.</div>')

def health_banner(anchor, errors):
    """Top-of-report banner summarizing run health, so a degraded build is OBVIOUS rather
    than silently published. Two sources: (a) the run-log ledger (run-log/<anchor>.jsonl —
    runlog.py records each step's ok/fail/stale/skip; steps 1-4 are logged before this
    build runs) and (b) build.py's OWN section/adapter errors. The run never stops — this
    only surfaces. Red (.alert-top) if any ledger fail; amber (.warn-banner) otherwise.
    Post-build failures (step 6 / publish) land after this build, so they hit Slack only
    (runlog finish), not this banner. Returns '' when everything is healthy."""
    fails, stales = [], []
    seen = set()
    try:
        p = os.path.join(HERE, "run-log", f"{anchor}.jsonl")
        if os.path.exists(p):
            for line in open(p):
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                st = e.get("status")
                if st not in ("fail", "stale"):
                    continue
                txt = e.get("step", "?") + (f" — {e['detail']}" if e.get("detail") else "")
                if txt in seen:
                    continue
                seen.add(txt)
                (fails if st == "fail" else stales).append(txt)
    except Exception:
        pass
    # build.py's own degradations (section/adapter errors) → amber notes
    for e in (errors or []):
        if e not in seen:
            seen.add(e)
            stales.append(e)
    if not fails and not stales:
        return ""
    items = "".join(f"<li>{esc(t)}</li>" for t in (fails + stales))
    if fails:
        head = (f"⚠️ <b>This report ran with {len(fails)} failure"
                f"{'s' if len(fails) != 1 else ''}"
                + (f" and {len(stales)} stale/partial item{'s' if len(stales) != 1 else ''}" if stales else "")
                + ".</b> Some numbers may be missing or out of date:")
        cls = "alert-top"
    else:
        head = (f"⚠️ <b>{len(stales)} item{'s' if len(stales) != 1 else ''} ran on stale or "
                f"partial data.</b> Most of the report is current; these tails may lag:")
        cls = "warn-banner"
    return f'<div class="{cls}">{head}<ul style="margin:6px 0 0;padding-left:20px;font-weight:500">{items}</ul></div>'

MC_CSS = """
/* ===== multi-channel additions on top of the original theme ===== */
header.mc{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:2px 0 4px}
header.mc h1{font-size:21px;margin:0}
/* "Last Updated" + the 3 chrome icons ride together on the tab row (right side) so they
   stay sticky with the tabs as you scroll. */
.tabs .tab-right{margin-left:auto;display:flex;align-items:center;gap:12px;padding-bottom:6px}
.tabs .mc-updated{color:var(--text-muted,#5A6B8C);font-size:12px;white-space:nowrap}
.tabs .mc-btnrow{display:flex;align-items:center;gap:8px}
/* header icon buttons (theme · expand-all · hamburger TOC) — match the HTML template glyphs */
.hbtn.icon{appearance:none;display:inline-flex;align-items:center;justify-content:center;width:34px;height:30px;
  padding:0;border:1px solid var(--border,#E4E8EE);border-radius:8px;background:var(--bg-card,#fff);
  color:var(--text-primary,#162050);font-size:15px;line-height:1;cursor:pointer;transition:border-color .12s,color .12s,background .12s}
.hbtn.icon:hover{border-color:var(--core-blue,#3185FC);color:var(--core-blue,#3185FC)}
.menu-wrap{position:relative}
.menu-panel{display:none;position:absolute;right:0;top:115%;z-index:1100;background:var(--bg-card,#fff);
  border:1px solid var(--border,#E4E8EE);border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.16);
  min-width:240px;max-height:70vh;overflow:auto;padding:6px}
.menu-panel.open{display:block}
.menu-panel a{display:block;padding:7px 11px;border-radius:7px;color:var(--text-primary,#162050);
  text-decoration:none;font-size:13px;cursor:pointer}
.menu-panel a:hover{background:var(--bg-page,#F5F7FA)}
.menu-panel a.mp-tab{font-weight:700}
.menu-panel a.mp-sec{padding-left:22px;font-size:12.5px;color:var(--text-muted,#5A6B8C)}
.menu-sep{border-top:1px solid var(--border,#E4E8EE);margin:4px 0}
/* tabs — sticky, browser-tab look */
.tabs{display:flex;gap:4px;border-bottom:2px solid var(--border,#E4E8EE);flex-wrap:wrap;
  position:sticky;top:0;z-index:60;background:var(--bg-page,#F5F7FA);padding-top:6px;margin-bottom:0}
.tabbtn{appearance:none;border:1px solid var(--border,#E4E8EE);border-bottom:none;background:#E9EDF3;
  color:var(--text-muted,#5A6B8C);font:600 13.5px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  padding:9px 20px;border-radius:10px 10px 0 0;cursor:pointer;position:relative;top:2px;transition:background .12s,color .12s}
.tabbtn:hover{background:#F0F3F8;color:var(--text-primary,#162050)}
.tabbtn.on{color:var(--text-primary,#162050);background:var(--bg-card,#fff);box-shadow:inset 0 3px 0 var(--core-blue,#3185FC);
  border-bottom:2px solid var(--bg-card,#fff);margin-bottom:-2px;top:0;padding-top:11px}
[data-theme=dark] .tabbtn{background:#1d2740}
[data-theme=dark] .tabbtn.on{background:var(--bg-card)}
.tabpane{display:none}
.tabpane.on{display:block;padding-top:10px}
/* sticky date row (only the top-of-tab one, not the meta-leaderboard one) */
.rangehost>.rangebar{position:sticky;top:45px;z-index:55;background:var(--bg-page,#F5F7FA);
  padding:8px 0 30px;margin:0;
  box-shadow:0 -6px 0 6px var(--bg-page,#F5F7FA)}
/* separator line sits 20px above the rangebar's bottom edge — 20px of grey below the line */
.rangehost>.rangebar::after{content:'';position:absolute;bottom:20px;left:0;right:0;height:1px;background:var(--border,#E4E8EE)}
.rangebar .rb-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.rangesel,.rangebar input[type=date]{padding:6px 10px;border:1px solid var(--border,#E4E8EE);border-radius:8px;
  background:var(--bg-card,#fff);color:var(--text-primary,#162050);font:600 12.5px -apple-system,"Segoe UI",Roboto,sans-serif;cursor:pointer;transition:border-color .12s}
.rangesel:hover,.rangebar input[type=date]:hover{border-color:var(--core-blue,#3185FC)}
.rangelbl{color:var(--text-muted,#5A6B8C);font-size:12px;margin-top:4px}
.grainbtns{display:inline-flex;gap:4px}
.gbtn{padding:5px 12px;border:1px solid var(--border,#E4E8EE);border-radius:8px;background:var(--bg-card,#fff);
  color:var(--text-muted,#5A6B8C);font:600 12px -apple-system,"Segoe UI",Roboto,sans-serif;cursor:pointer;transition:all .12s}
.gbtn:hover{border-color:var(--core-blue,#3185FC);color:var(--core-blue,#3185FC)}
.gbtn.on{background:var(--core-blue,#3185FC);border-color:var(--core-blue,#3185FC);color:#fff}
/* KPI cards (optimizer style) */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:10px 0 14px}
.kpi{background:var(--bg-card,#fff);border:1px solid var(--border,#E4E8EE);border-radius:10px;padding:10px 13px;min-width:0}
.klabel{font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--text-muted,#5A6B8C);font-weight:600}
.kval{font-size:21px;font-weight:700;margin-top:3px}
.kdelta{font-size:11.5px;margin-top:3px;font-weight:600}
.kdelta.good{color:#15803d}.kdelta.bad{color:#b91c1c}.kdelta.flat{color:var(--text-muted,#5A6B8C)}
.kdelta .vsprior{color:var(--text-muted,#5A6B8C);font-weight:400}
.kval a.kpilink{color:inherit;text-decoration:none;border-bottom:1.5px dotted var(--core-blue,#3185FC)}
.kval a.kpilink:hover{color:var(--core-blue,#3185FC)}
/* hover effects on the original controls */
.mbtn:hover,.chip:hover,.vbtn:hover{filter:brightness(.96);box-shadow:0 1px 4px rgba(22,32,80,.12)}
.mbtn,.chip,.vbtn{transition:all .12s}
/* AS metric buttons not covered by the theme's data-m rules */
.metric-toggle .mbtn.on{background:var(--asc,#3185FC);border-color:var(--asc,#3185FC);color:#fff}
/* Month-end spend run-rate card (All-Sources, above Daily Alerts) */
#runrate-allsources .rr-tbl{border-collapse:collapse;width:100%;margin:8px 0 4px}
#runrate-allsources .rr-tbl th,#runrate-allsources .rr-tbl td{padding:7px 12px;border-bottom:1px solid var(--border,#E4E8EE);font-size:13px;text-align:right;white-space:nowrap}
#runrate-allsources .rr-tbl th:first-child,#runrate-allsources .rr-tbl td:first-child{text-align:left}
#runrate-allsources .rr-tbl thead th{background:var(--thead,#EFF3F9);font-size:10.5px;text-transform:uppercase;letter-spacing:.4px;color:var(--text-muted,#5A6B8C)}
#runrate-allsources .rr-rate{color:var(--text-muted,#5A6B8C);font-size:12px}
#runrate-allsources .rr-note{font-size:11.5px;color:var(--text-muted,#5A6B8C);margin:6px 0 0}
/* Tooltips (ⓘ info popovers) on every tab: regular weight, no italic/bold — overrides any <b>/<i> in the tip text */
.tip .tipbox,.tip .tipbox *{font-weight:400 !important;font-style:normal !important;font-size:12px !important;line-height:17px !important}
/* Attribution-basis banner (just above the KPI widgets) — all channel tabs */
.basisbanner{background:rgba(49,133,252,.08);border:1px solid rgba(49,133,252,.28);color:var(--text-primary,#162050);
  border-radius:10px;padding:9px 14px;font-size:12.5px;font-weight:600;margin:0 0 12px;display:flex;align-items:center;gap:8px}
.basisbanner::before{content:"ⓘ";color:var(--core-blue,#3185FC);font-size:14px}
/* KPI-label info tooltip: keep the ⓘ icon small/aligned next to the uppercase label */
.klabel .tip.kpitip{width:13px;height:13px;font-size:9px;margin-left:5px;vertical-align:baseline}
/* narrow + right-align the KPI tooltip box so it never clips off the right edge */
.klabel .tip.kpitip .tipbox{width:auto;min-width:170px;max-width:240px;left:auto;right:0;white-space:normal;text-transform:none;letter-spacing:normal}
.clk-pipenote{margin:0 0 10px}
.gridscope{margin:0 0 8px}
section.mcsec{margin-bottom:14px}
.demo-note{font-size:12px;margin:2px 0 10px}
.acc-body{min-width:0}
/* Daily-Metrics consolidated control row (grain · view · moving-avg) */
.clk-ctrls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:2px 0 10px}
.clk-ctrls .clk-views{margin:0}
/* multi-select dropdown (top-bar Campaign/Ad-group filters + meta leaderboards) */
.ms{position:relative;display:inline-block}
.mspanel{position:absolute;z-index:80;top:115%;left:0;background:var(--bg-card,#fff);border:1px solid var(--border,#E4E8EE);
  border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.16);padding:8px;min-width:300px}
.mssearch{width:100%;padding:5px 8px;border:1px solid var(--border,#E4E8EE);border-radius:7px;margin-bottom:6px;
  background:var(--bg-card,#fff);color:var(--text-primary,#162050);box-sizing:border-box}
.msacts{font-size:12px;margin-bottom:6px}
.msacts a{color:var(--core-blue,#3185FC);cursor:pointer}
.mslist{max-height:300px;overflow:auto;display:flex;flex-direction:column;gap:3px}
.mslist label{font-size:12.5px;display:flex;align-items:center;gap:2px;cursor:pointer;white-space:nowrap}
.mslist label:hover{background:var(--bg-page,#F5F7FA)}
.mssub{color:var(--text-muted,#5A6B8C);margin-left:auto;font-size:11px;padding-left:10px}
tr.tot td{font-weight:700;background:var(--thead,#EFF3F9)}
/* metrics-over-time matrix: frozen first column + header */
.matrixwrap{overflow:auto;max-height:540px;border:1px solid var(--border,#E4E8EE);border-radius:8px}
table.matrix{border-collapse:separate;border-spacing:0;min-width:100%}
table.matrix th,table.matrix td{white-space:nowrap;padding:6px 10px;border-bottom:1px solid var(--border,#E4E8EE);font-size:12.5px}
table.matrix thead th{position:sticky;top:0;background:var(--thead,#EFF3F9);z-index:2}
table.matrix td:first-child,table.matrix th:first-child{position:sticky;left:0;background:var(--bg-card,#fff);z-index:1;font-weight:600;border-right:1px solid var(--border,#E4E8EE)}
table.matrix thead th:first-child{z-index:3;background:var(--thead,#EFF3F9)}
table.matrix td:not(:first-child){text-align:right}
.matrixchips{margin-bottom:8px}
/* drill-down expanders */
tr.drill-more{display:none}
tr.drill-more.show{display:table-row}
.drill-toggle{margin:4px 0 12px;display:inline-block}
/* Daily Alerts day-selector (dropdown, multi-select) + per-alert reviewed checkbox */
.alert-pick{margin:2px 0 4px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.alert-pick .dd{position:relative;display:inline-block}
.alert-hide{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--text-primary);cursor:pointer;user-select:none}
.alert-hide input{margin:0;cursor:pointer}
.alert-body.hide-done tr.alert-done{display:none}
.dd-btn{font:600 13px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:6px 14px;
  border-radius:8px;border:1.5px solid var(--core-blue);background:var(--bg-card);color:var(--text-primary);
  cursor:pointer;transition:background .12s,box-shadow .12s}
.dd-btn:hover{background:rgba(49,133,252,.10);box-shadow:0 1px 5px rgba(49,133,252,.28)}
.dd-car{color:var(--text-muted);font-size:11px;margin-left:6px}
.dd-panel{display:none;position:absolute;z-index:40;top:calc(100% + 4px);left:0;min-width:250px;
  background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:8px 10px;box-shadow:0 6px 22px rgba(0,0,0,.12)}
.dd-panel.open{display:block}
.dd-top{display:flex;gap:16px;padding-bottom:7px;margin-bottom:5px;border-bottom:1px solid var(--border)}
.dd-link{font-size:12px;color:var(--core-blue);cursor:pointer;user-select:none}
.dd-link:hover{text-decoration:underline}
.dd-row{display:flex;align-items:center;gap:8px;font-size:13px;padding:4px 2px;cursor:pointer;white-space:nowrap;color:var(--text-primary)}
.dd-row input{margin:0;cursor:pointer}
.alert-day{margin-top:16px}
.alert-day:first-child{margin-top:8px}
.alert-dayhd{font-size:13px;font-weight:600;color:var(--text-primary);border-bottom:1px solid var(--border);padding-bottom:5px;margin-bottom:6px}
th.ack,td.ack{width:26px;text-align:center;padding-left:2px;padding-right:2px}
.alert-ack{cursor:pointer}
tr.alert-done td{opacity:.45}
tr.alert-done td.ack{opacity:1}
/* ===== Changelog section (3-source change log) + chart annotation popover ===== */
.cl-count{background:var(--thead,#EFF3F9);color:var(--text-muted,#5A6B8C);font-size:11px;font-weight:700;border-radius:20px;padding:1px 9px;margin-left:2px}
.cl-filter{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:2px 0 10px}
.cl-flbl{font-size:11px;color:var(--text-muted,#5A6B8C);font-weight:600;margin-right:2px}
.fchip{appearance:none;border:1px solid var(--border,#E4E8EE);background:var(--bg-card,#fff);color:var(--text-muted,#5A6B8C);
  font:600 11.5px -apple-system,"Segoe UI",Roboto,sans-serif;border-radius:20px;padding:4px 12px;cursor:pointer;transition:all .12s}
.fchip:hover{border-color:var(--core-blue,#3185FC);color:var(--core-blue,#3185FC)}
.fchip.on{background:var(--core-blue,#3185FC);border-color:var(--core-blue,#3185FC);color:#fff}
.cl-scope{font-size:12px;color:var(--text-muted,#5A6B8C);margin:0 0 10px}
.cl-date{font-size:12px;font-weight:700;color:var(--text-primary,#162050);text-transform:uppercase;letter-spacing:.4px;
  border-bottom:1px solid var(--border,#E4E8EE);padding:11px 0 5px;margin-bottom:2px}
.cl-date:first-child{padding-top:2px}
.cl-item{padding:8px 2px;border-bottom:1px solid var(--border,#E4E8EE)}
.cl-item:last-child{border-bottom:none}
.cl-title{font-weight:600;font-size:13.5px;cursor:pointer;color:var(--text-primary,#162050)}
.cl-title:hover{color:var(--core-blue,#3185FC)}
.cl-title .arrow{color:var(--text-muted,#5A6B8C);font-size:10px;margin-right:6px;display:inline-block;transition:transform .15s}
.cl-item.open .cl-title .arrow{transform:rotate(90deg)}
.cl-body{display:none;margin:7px 0 2px}
.cl-item.open .cl-body{display:block}
.cl-detail{font-size:12.5px;color:var(--text-secondary,#3a4a6b);margin-bottom:8px;white-space:pre-wrap}
.cl-meta{font-size:11.5px;color:var(--text-muted,#5A6B8C)}
.cl-meta a,.cl-scopechip{color:inherit}
.cl-meta a{color:var(--core-blue,#3185FC);text-decoration:none}
.cl-meta a:hover{text-decoration:underline}
.cl-statustag{font-size:10px;font-weight:700;color:#9a6b00;background:#FFF6E0;border:1px solid #F0D78A;border-radius:5px;padding:1px 6px;margin-left:6px;text-transform:uppercase;letter-spacing:.3px}
#cl-pop{position:fixed;z-index:200;display:none;width:390px;max-width:calc(100vw - 24px);max-height:340px;overflow-y:auto;
  background:var(--bg-card,#fff);border:1px solid var(--border,#E4E8EE);border-radius:10px;box-shadow:0 8px 28px rgba(0,0,0,.18);
  padding:11px 13px;font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:var(--text-primary,#162050)}
#cl-pop .pop-hd{font-weight:700;font-size:12.5px;margin-bottom:7px;position:sticky;top:-11px;background:var(--bg-card,#fff);padding-top:1px}
#cl-pop .pop-row{padding:8px 0;border-top:1px solid var(--border,#E4E8EE)}
#cl-pop .pop-row:first-of-type{border-top:none}
#cl-pop .pop-t{font-weight:600;font-size:12px}
#cl-pop .pop-d{font-size:11.5px;color:var(--text-secondary,#3a4a6b);margin-top:3px;white-space:pre-wrap}
#cl-pop .pop-meta{margin-top:5px;font-size:11.5px;color:var(--text-muted,#5A6B8C)}
#cl-pop .pop-meta a{color:var(--core-blue,#3185FC);text-decoration:none}
#cl-pop .pop-meta a:hover{text-decoration:underline}
/* ===== Keyword Quality-Score monitoring (Daily-Alert rows + browse table) ===== */
tr.qs-down td.qs-chg{color:#C0392B;font-weight:600}
tr.qs-up td.qs-chg{color:#1E8E4E;font-weight:600}
.qs-mt{font-size:10px;font-weight:600;color:var(--text-muted,#5A6B8C);text-transform:uppercase;letter-spacing:.3px;
  border:1px solid var(--border,#E4E8EE);border-radius:4px;padding:0 4px;margin-left:5px;vertical-align:middle}
.qs-ctrls{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:2px 0 10px;font-size:12.5px;color:var(--text-muted,#5A6B8C)}
.qs-ctrls label{display:inline-flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
.qs-tbl td.qs-num{font-weight:700}
/* sortable + filterable column headers: click the TITLE to sort, ▾ to filter; sticky on scroll */
.qs-wrap{max-height:70vh;overflow:auto}
.qs-tbl thead th{position:sticky;top:0;z-index:3;background:var(--thead,#EFF3F9);white-space:nowrap}
.qs-tbl thead th .qs-th-t{cursor:pointer;user-select:none}
.qs-tbl thead th .qs-th-t:hover{color:var(--core-blue,#3185FC)}
.qs-tbl thead th .qs-caret{opacity:.4;font-size:10px}
.qs-tbl thead th.sorted .qs-th-t{color:var(--core-blue,#3185FC)}
.qs-tbl thead th.sorted .qs-caret{opacity:1}
.qs-fbtn{cursor:pointer;border:none;background:none;color:var(--text-muted,#5A6B8C);font-size:11px;margin-left:5px;padding:0 3px;border-radius:4px;vertical-align:middle}
.qs-fbtn:hover{color:var(--core-blue,#3185FC)}
.qs-fbtn.on{color:var(--core-blue,#3185FC)}
.qs-fpop{position:fixed;display:none;z-index:300;background:var(--bg-card,#fff);border:1px solid var(--border,#E4E8EE);border-radius:8px;box-shadow:0 6px 22px rgba(0,0,0,.16);padding:5px;min-width:150px;max-height:320px;overflow:auto}
.qs-fpop.open{display:block}
.qs-fopt{padding:5px 10px;font-size:12.5px;border-radius:6px;cursor:pointer;white-space:nowrap;color:var(--text-primary,#162050)}
.qs-fopt:hover{background:var(--bg-page,#F5F7FA)}
.qs-fopt.on{color:var(--core-blue,#3185FC);font-weight:600}
.qs-reset{color:var(--core-blue,#3185FC);cursor:pointer;font-size:12.5px;margin-left:2px}
.qs-reset:hover{text-decoration:underline}
.qs-badge{display:inline-block;min-width:64px;text-align:center;font-size:11px;font-weight:600;border-radius:5px;padding:1px 6px}
.qs-AA{background:#E3F6EC;color:#1E8E4E}
.qs-AV{background:#FCF3DD;color:#9a6b00}
.qs-BA{background:#FBE4E2;color:#C0392B}
.qs-NA{background:#EEF1F6;color:#5A6B8C}
.qs-delta{font-size:11px;font-weight:700;margin-left:6px}
.qs-delta.dn{color:#C0392B}
.qs-delta.up{color:#1E8E4E}
.qs-tbl tr.qs-changed{background:rgba(49,133,252,.05)}
/* ===== Mobile / small-screen friendliness =====
   The metric-toggle is a single-row inline-flex segmented control; with ~10-12
   metrics it grows past a phone's width and forces the page to shrink-to-fit
   (content pinned narrow with grey to the side). On small screens turn it into
   wrapping pills so the page never overflows horizontally. (Breakpoint 900px so
   iPad-portrait widths — 768/810/834 — wrap too; the toggle alone is ~840px.) */
@media (max-width:900px){
  .wrap{padding:14px 12px 48px}
  header.mc h1{font-size:19px}
  .tabs .mc-updated{font-size:11px;white-space:normal}
  /* let the sticky tab-right cluster (Last Updated + chrome icons) shrink/wrap so it
     can't push the page a few px past a narrow phone viewport (390px overflow fix). */
  .tabs .tab-right{flex-wrap:wrap;justify-content:flex-end;min-width:0;row-gap:2px}
  .metric-toggle{display:flex;flex-wrap:wrap;gap:6px;border:none;border-radius:0;overflow:visible}
  .metric-toggle .mbtn{border:1px solid var(--border,#E4E8EE);border-radius:7px}
  .metric-toggle .mbtn:last-child{border-right:1px solid var(--border,#E4E8EE)}
  .clk-views,.view-toggle,.chips.clicks{flex-wrap:wrap}
  /* keep wide snapshot/summary tables readable: scroll them instead of crushing.
     Apply to every mcsec EXCEPT the hist-… snapshot sections, whose cards carry an
     absolutely-positioned date-dropdown panel that overflow-x:auto would clip. (The
     All Campaigns / Excl. Branded / Performance Summary sections now carry
     allcamp-/excl-/perf- ids for the filter toggle, so we exclude by hist- prefix, not
     by "has any id" — otherwise their wide tables overflow the page on mobile.) */
  .alert-day{overflow-x:auto}
  section.mcsec:not([id^="hist-"])>.card{overflow-x:auto}
  .alert-day table,section.mcsec:not([id^="hist-"])>.card table{min-width:480px}
  /* popovers can't exceed the screen */
  .dd-panel{max-width:calc(100vw - 28px)}
  .tip .tipbox{left:auto;right:0}
}"""

TABS = [("allsources", "All Sources"), ("google", "Google Ads"), ("microsoft", "Microsoft Ads"), ("meta", "Meta Ads")]

# KPI-card → Salesforce report links, per tab × grain (extracted from David's dashboards
# via backfill/sf_dashlinks.js on 2026-06-12; "Conversions" reports = Accounts).
# NOTE: David's explicit Google Day ARR link (00OQU000007ErTR2A0) is actually "Demos
# Scheduled by Week (Competitors)" — using "ARR by Day (Google Ads)" instead (flagged).
SF_BASE = "https://doorloop.lightning.force.com/lightning/r/Report/{}/view"
LINKS = {
    "google": {
        "day":   {"demos": "00OQU000000u9v32AA", "opps": "00OQU000000uA9Z2AU", "accts": "00OQU000000uABB2A2", "arr": "00OQU000006lNLt2AM"},
        "week":  {"demos": "00OQU000000NeET2A0", "opps": "00OQU000000NchO2AS", "accts": "00OQU000000NeHh2AK", "arr": "00OQU000000NeJJ2A0"},
        "month": {"demos": "00OQU000000NezH2AS", "opps": "00OQU000000NfvJ2AS", "accts": "00OQU000000NfYk2AK", "arr": "00OQU000000NfyX2AS"},
    },
    "microsoft": {
        "day":   {"demos": "00OQU000000u9YT2AY", "opps": "00OQU000000u9ev2AA", "accts": "00OQU000000u9gX2AQ", "arr": "00OQU000000u9i92AA"},
        "week":  {"demos": "00OQU000000NeO92AK", "opps": "00OQU000000NdyM2AS", "accts": "00OQU000000NeRN2A0", "arr": "00OQU000000NeSz2AK"},
        "month": {"demos": "00OQU000000Ng4z2AC", "opps": "00OQU000000Ng6b2AC", "accts": "00OQU000000Ng8D2AS", "arr": "00OQU000000Ng9p2AC"},
    },
    "meta": {
        "day":   {"demos": "00OQU000000uCBN2A2", "opps": "00OQU000000uCHp2AM", "accts": "00OQU000000uCL32AM", "arr": "00OQU000000uAMU2A2"},
        "week":  {"demos": "00OQU000000Zzbh2AC", "opps": "00OQU000000NeKw2AK", "accts": "00OQU000000Nexd2AC", "arr": "00OQU000000NezF2AS"},
        "month": {"demos": "00OQU000000Nepb2AC", "opps": "00OQU000000NgMj2AK", "accts": "00OQU000000NgOL2A0", "arr": "00OQU000000NgPx2AK"},
    },
    "allsources": {
        "day":   {"mqls": "00OQU000000u8yz2AA", "demos": "00OQU000000u90b2AA", "opps": "00OQU000000u95R2AQ", "accounts": "00OQU000000u9Bt2AI", "arr": "00OQU000000u8Vz2AI"},
        "week":  {"mqls": "00OQU000000Nbzl2AC", "demos": "00OQU000000JmAb2AK", "opps": "00OQU000000JmCD2A0", "accounts": "00OQU000000JmFR2A0", "arr": "00OQU000000K7bd2AC"},
        "month": {"mqls": "00OQU000000Nfk12AC", "demos": "00OQU000000Nfld2AC", "opps": "00OQU000000NfnF2AS", "accounts": "00OQU000000Nfor2AC", "arr": "00OQU000000NfqT2AS"},
    },
}
def links_blob():
    return {tab: {g: {m: SF_BASE.format(rid) for m, rid in mm.items()} for g, mm in gg.items()} for tab, gg in LINKS.items()}

def sec(html, sid=None):
    if not html:
        return ""
    return f'<section class="mcsec"{f" id=\"{sid}\"" if sid else ""}>{html}</section>'

# ── Rolling daily-snapshot history ──────────────────────────────────────────
# These section fragments are SNAPSHOTS of one specific day (anomaly alerts,
# the day's Top-20 CPCs, the day's bad-search-terms audit) — unlike the charts
# and funnel tables, which the client re-aggregates for any selected range from
# the 2-year fact tables. We keep a rolling HIST_RETENTION-day window of them
# embedded in the page so the date picker can show any recent single day; the
# store persists across daily rebuilds via read-back from the previous
# Paid-Analytics.html (no separate file). Map: tab → {container-suffix: frag-key}.
HIST_RETENTION = 14
HIST_MAP = {
    "allsources": {"alerts": "alerts"},
    "google":     {"alerts": "daily_alerts", "cpc": "top20_cpc", "bad": "bad_terms"},
    "microsoft":  {"alerts": "daily_alerts", "cpc": "top20_cpc", "bad": "bad_terms"},
    "meta":       {"alerts": "daily_alerts"},
}

def hist_sec(tab, suffix, html):
    """Like sec(), but gives the section a stable id so engine.js can swap its
    inner HTML to a different day's snapshot when the date picker changes."""
    return f'<section class="mcsec" id="hist-{tab}-{suffix}">{html}</section>'

def history_entry(frags):
    """Assemble today's snapshot fragments keyed exactly like HIST_MAP."""
    e = {}
    for tab, secs in HIST_MAP.items():
        tf = frags.get(tab, {})
        e[tab] = {suffix: tf.get(fk, {}).get("html", "") for suffix, fk in secs.items()}
    return e

def read_prev_history(out_path):
    """Pull the embedded daily-history JSON out of the previous Paid-Analytics.html so the
    rolling window survives a rebuild. Missing/unparseable → start fresh ({})."""
    if not os.path.exists(out_path):
        return {}
    try:
        with open(out_path) as f:
            txt = f.read()
        m = re.search(r'<script type="application/json" id="daily-history">(.*?)</script>', txt, re.S)
        return json.loads(m.group(1)) if m else {}
    except Exception:
        return {}

def prune_history(hist, keep=HIST_RETENTION):
    for d in sorted(hist.keys())[:-keep]:
        del hist[d]
    return hist

def _embed_json(blob):
    """Serialize for a <script type=application/json> tag. Escaping </ → <\\/
    keeps any '</script' inside fragment HTML from closing the tag early;
    json.loads decodes \\/ back to / transparently on read-back."""
    return json.dumps(blob, separators=(",", ":")).replace("</", "<\\/")

def run_rate_card(hist):
    """Month-end SPEND run-rate projection for the top of the All-Sources tab.

    Same formula as the manual analysis: split each channel's daily spend into a
    WEEKDAY rate and a WEEKEND rate, blend 70% last-7-days + 30% full-month-to-date,
    then apply those rates to the remaining days of the current month. Anchored to
    the latest date in the data, so it self-updates on every daily rebuild. Sums ALL
    spend rows per channel — matches the report's All-Channels 'all' view (engine.js
    spendOk is always true for kind=='all'). Spend rows are [date_idx, camp, grp,
    cost_cents, clicks, imps]; cost is cents → /100 for dollars."""
    import calendar
    W_RECENT = 0.70
    chans = [("google", "Google Ads"), ("microsoft", "Microsoft Ads"), ("meta", "Meta Ads")]
    dates = hist["google"]["dates"]
    anchor = datetime.date.fromisoformat(dates[-1])          # latest data day = "MTD through"
    y, m = anchor.year, anchor.month
    mstart = datetime.date(y, m, 1)
    mend = datetime.date(y, m, calendar.monthrange(y, m)[1])
    last7_lo = anchor - datetime.timedelta(days=6)
    is_we = lambda d: d.weekday() >= 5                        # Sat/Sun
    rem = [mstart + datetime.timedelta(days=k) for k in range((mend - mstart).days + 1)
           if (mstart + datetime.timedelta(days=k)) > anchor]
    rem_wd = sum(1 for d in rem if not is_we(d))
    rem_we = sum(1 for d in rem if is_we(d))

    def project(P):
        mtd = {}                                             # date -> spend $, current month up to anchor
        for r in P["spend"]:
            d = datetime.date.fromisoformat(dates[r[0]])
            if mstart <= d <= anchor:
                mtd[d] = mtd.get(d, 0.0) + r[3] / 100.0
        mtd_total = sum(mtd.values())
        overall = (mtd_total / len(mtd)) if mtd else 0.0
        def avg(days):
            days = [d for d in days if d in mtd]
            return (sum(mtd[d] for d in days) / len(days)) if days else None
        def blend(recent_days, all_days, other_all):
            a7, aa = avg(recent_days), avg(all_days)
            if a7 is None and aa is None:                    # no data this daytype → fall back
                return avg(other_all) if avg(other_all) is not None else overall
            if a7 is None: return aa
            if aa is None: return a7
            return W_RECENT * a7 + (1 - W_RECENT) * aa
        wd_all = [d for d in mtd if not is_we(d)]
        we_all = [d for d in mtd if is_we(d)]
        wd_rate = blend([d for d in wd_all if d >= last7_lo], wd_all, we_all)
        we_rate = blend([d for d in we_all if d >= last7_lo], we_all, wd_all)
        rest = wd_rate * rem_wd + we_rate * rem_we
        return mtd_total, rest, mtd_total + rest, wd_rate, we_rate

    money = lambda v: "$" + format(int(round(v)), ",")
    rows, vals, tot_mtd, tot_rest, tot_full = [], {}, 0.0, 0.0, 0.0
    for key, label in chans:
        mt, rs, fl, wr, wer = project(hist[key])
        rows.append((label, mt, rs, fl, wr, wer))
        vals[key] = {"mtd": round(mt), "rest": round(rs), "full": round(fl), "wd": round(wr), "we": round(wer)}
        tot_mtd += mt; tot_rest += rs; tot_full += fl
    vals["combined"] = {"mtd": round(tot_mtd), "rest": round(tot_rest), "full": round(tot_full)}

    elapsed, total_days = (anchor - mstart).days + 1, (mend - mstart).days + 1
    sub = (f"{anchor.strftime('%B %Y')} · projected from {mstart.strftime('%b %-d')}–"
           f"{anchor.strftime('%-d')} ({elapsed}/{total_days} days) · "
           f"{rem_wd} weekday + {rem_we} weekend days left")
    trs = ""
    for label, mt, rs, fl, wr, wer in rows:
        trs += (f'<tr><td>{label}</td><td>{money(mt)}</td><td>{money(rs)}</td>'
                f'<td><b>{money(fl)}</b></td><td class="rr-rate">{money(wr)} / {money(wer)}</td></tr>')
    trs += (f'<tr class="tot"><td>All channels</td><td>{money(tot_mtd)}</td><td>{money(tot_rest)}</td>'
            f'<td>{money(tot_full)}</td><td class="rr-rate">—</td></tr>')
    inner = (
        f'<h2>Month-End Spend Run Rate'
        f'<span class="dt" style="font-weight:400;color:var(--text-muted,#5A6B8C);font-size:12px;margin-left:8px">{sub}</span></h2>'
        '<table class="rr-tbl"><thead><tr>'
        '<th>Channel</th><th>MTD actual</th><th>Projected rest</th>'
        '<th>Full-month projection</th><th>Wkday / wkend $/day</th>'
        f'</tr></thead><tbody>{trs}</tbody></table>'
        '<p class="rr-note">Each channel split into weekday vs weekend daily rates, blended '
        '<b>70% last-7-days + 30% full month-to-date</b>, applied to the remaining days. '
        'Platform-reported spend; assumes current pacing holds (no budget or campaign changes).</p>')
    html = f'<section class="mcsec" id="runrate-allsources"><div class="card">{inner}</div></section>'
    return html, vals

def compose_tab(key, frags):
    """rangehost wraps ALL main tab content so position:sticky on .rangebar works
    end-to-end when scrolling (sticky needs a parent taller than the viewport).
    Banner (red alert) stays outside — it should scroll away with the page.
    Snapshot sections (alerts / CPCs / bad terms) get hist_sec() ids so engine.js
    can swap them to the day chosen in the date picker."""
    pill = frags.get("pill_html", "")
    banner = frags.get("rec_alert_html", "")  # google/microsoft only; scrolls away deliberately
    conv_banner = frags.get("conv_banner", "")  # MS offline-conversion outage (All Sources + Microsoft)
    inner = []
    if key == "allsources":
        rr = frags.get("runrate_html", "")
        if rr: inner.append(rr)   # month-end spend run rate — sits above Daily Alerts
        inner.append(hist_sec("allsources", "alerts", frags.get("alerts", {}).get("html", "")))
        inner.append('<section class="mcsec" id="changelog-allsources"></section>')  # engine.js fills it (above the charts)
        inner.append('<div class="card accordion" id="chartsec-allsources"><h2>Performance Charts<span class="dt"></span></h2><div class="acc-body" id="charts-allsources"></div></div>')
        # Funnel Scorecard / Conversion Rates / Metrics-over-time / Per-Campaign tables
        # are CLIENT-rendered from history for the selected range (engine.js).
        inner.append('<div id="as-tables"></div>')
    else:
        if pill: inner.append(f'<div class="demo-note">{pill}</div>')
        inner.append(hist_sec(key, "alerts", frags.get("daily_alerts", {}).get("html", "")))
        if key in ("google", "microsoft"):
            inner.append(sec(frags.get("all_campaigns", {}).get("html", ""), f"allcamp-{key}"))
            inner.append(sec(frags.get("excl_branded", {}).get("html", ""), f"excl-{key}"))
            inner.append(f'<section class="mcsec" id="changelog-{key}"></section>')  # engine.js fills it (above Daily Metrics)
            inner.append(f'<div class="card accordion" id="chartsec-{key}"><h2>Daily Metrics<span class="dt"></span></h2><div class="acc-body" id="charts-{key}"></div></div>')
            inner.append(hist_sec(key, "cpc", frags.get("top20_cpc", {}).get("html", "")))
            inner.append(hist_sec(key, "bad", frags.get("bad_terms", {}).get("html", "")))
            inner.append(f'<section class="mcsec" id="quality-{key}"></section>')  # engine.js fills the QS browse table
        else:  # meta
            inner.append(sec(frags.get("performance_summary", {}).get("html", ""), "perf-meta"))
            inner.append('<section class="mcsec" id="changelog-meta"></section>')  # engine.js fills it (above Daily Metrics)
            inner.append('<div class="card accordion" id="chartsec-meta"><h2>Daily Metrics<span class="dt"></span></h2><div class="acc-body" id="charts-meta"></div></div>')
            inner.append('<div id="meta-boards"></div>')
    inner_html = "\n".join(p for p in inner if p)
    rangehost = f'<div class="rangehost" id="kpi-{key}">{inner_html}</div>'
    return "\n".join(p for p in [conv_banner, banner, rangehost] if p)

def render(anchor):
    hist = {k: trim_flags(load_history(k), k) for k in ("google", "microsoft", "meta")}
    hist["allsources"] = load_history("allsources")

    # anchor-day fragments from the sibling artifacts (graceful per-section failures)
    frag_fns = {"google": adapters.google_sections, "microsoft": adapters.microsoft_sections,
                "meta": adapters.meta_sections, "allsources": adapters.allsources_sections}
    frags, errors = {}, []
    for k, fn in frag_fns.items():
        try:
            frags[k] = fn(anchor)
            errors += [f"{k}: {e}" for e in frags[k].get("errors", [])]
        except Exception as e:
            frags[k] = {}
            errors.append(f"{k}: ALL sections failed: {e}")

    # month-end spend run rate (All-Sources card + per-tab "Monthly Run Rate" KPI widgets)
    runrate_vals = {}
    try:
        rr_html, runrate_vals = run_rate_card(hist)
        frags.setdefault("allsources", {})["runrate_html"] = rr_html
    except Exception as e:
        errors.append(f"allsources: run-rate card failed: {e}")

    # keyword Quality-Score stores (quality_store.py). Embed the whole store for the
    # browse table; append the anchor day's active-keyword changes into Daily Alerts.
    quality = {}
    for plat in ("google", "microsoft"):
        store = load_quality(plat)
        quality[plat] = store or {}
        if store:
            da = frags.get(plat, {}).get("daily_alerts")
            qhtml = quality_alert_html(store, anchor)
            if da and da.get("html") and qhtml:
                # insert before the card's final </div> so it rides inside the alerts card
                head, _, tail = da["html"].rpartition("</div>")
                da["html"] = head + qhtml + "</div>" + tail
        else:
            errors.append(f"{plat}: no quality store — QS section/alerts skipped")

    # Microsoft offline-conversion (Demo_Scheduled) health → urgent banner on All Sources + Microsoft
    conv_banner = conv_health_banner(load_conv_health())
    if conv_banner:
        frags.setdefault("allsources", {})["conv_banner"] = conv_banner
        frags.setdefault("microsoft", {})["conv_banner"] = conv_banner

    data_blob = {"anchor": anchor, "links": links_blob()}
    data_blob.update(hist)
    data_blob["runrate"] = runrate_vals   # {google/microsoft/meta/combined: {mtd,rest,full,...}}

    # changelog store (deduped/classified by changelog_store.py); optional — empty if absent
    try:
        with open(os.path.join(HERE, "history", "changelog.json")) as f:
            changelog = json.load(f)
    except Exception:
        changelog = {"entries": []}

    # rolling 14-day snapshot store: read the prior window out of the existing
    # Paid-Analytics.html, fold in today, prune. (read-back keeps it to ONE file)
    out = os.path.join(HERE, "outputs", "Paid-Analytics.html")
    daily_history = read_prev_history(out)
    daily_history[anchor] = history_entry(frags)
    prune_history(daily_history)

    with open(os.path.join(HERE, "engine.js")) as f:
        engine = f.read()

    # top-of-report run-health banner (ledger fail/stale + build's own section errors)
    run_banner = health_banner(anchor, errors)

    tab_btns = "".join(f'<button class="tabbtn" data-tab="{k}">{label}</button>' for k, label in TABS)
    panes = "".join(f'<div class="tabpane" id="tab-{k}">{compose_tab(k, frags.get(k, {}))}</div>' for k, _ in TABS)

    # "Last Updated" = the actual refresh (build) time in Eastern, with time of day.
    # %Z is correct & self-adjusting: EDT in summer, EST in winter (NOT hardcoded).
    pretty = datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%A, %B %-d at %-I:%M %p %Z")
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="googlebot" content="noindex,nofollow">
<title>Paid Analytics</title>
<script src="{CHARTJS_URL}" integrity="{CHARTJS_SRI}" crossorigin="anonymous"></script>
<style>{theme.CSS}\n{adapters_assets.CSS}\n{MC_CSS}</style>
</head><body><div class="wrap">
<header class="mc"><h1>Paid Analytics</h1></header>
{run_banner}
<nav class="tabs">{tab_btns}<div class="tab-right">
  <span class="mc-updated">Last Updated: {pretty}</span>
  <div class="mc-btnrow">
    <button class="hbtn icon" id="mc-theme" type="button" aria-label="Dark/Light Mode" title="Dark/Light Mode"><span id="mc-theme-icon">&#9790;</span></button>
    <button class="hbtn icon" id="mc-expand" type="button" aria-label="Expand/Collapse All Sections" title="Expand/Collapse All Sections (press c)"><span id="mc-expand-icon">&#8863;</span></button>
    <div class="menu-wrap">
      <button class="hbtn icon" id="mc-menu-btn" type="button" aria-label="Table of Contents" title="Table of Contents (press b)">&#9776;</button>
      <nav class="menu-panel" id="mc-menu-panel"></nav>
    </div>
  </div>
</div></nav>
{panes}
<script type="application/json" id="mc-data">{_embed_json(data_blob)}</script>
<script type="application/json" id="daily-history">{_embed_json(daily_history)}</script>
<script type="application/json" id="mc-changelog">{_embed_json(changelog)}</script>
<script type="application/json" id="mc-quality">{_embed_json(quality)}</script>
<script>{adapters_assets.JS}</script>
<script>{engine}</script>
</div></body></html>"""

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(html)

    # sanity line
    g = hist["google"]
    last30 = set(range(len(g["dates"]) - 30, len(g["dates"])))
    sp30 = sum(r[3] for r in g["spend"] if r[0] in last30) / 100
    dm30 = sum(r[2] for r in g["demos"] if r[0] in last30)
    print(f"✅ wrote {out} ({os.path.getsize(out)//1024} KB)")
    print(f"   google last-30d: spend ${sp30:,.0f}, demos {dm30} | tabs: {', '.join(k for k,_ in TABS)}")
    hk = sorted(daily_history.keys())
    print(f"   daily-history: {len(hk)} day(s) [{hk[0] if hk else '—'} → {hk[-1] if hk else '—'}], retain {HIST_RETENTION}")
    cl = changelog.get("entries", [])
    print(f"   changelog: {len(cl)} entries [{changelog.get('window_start','—')} → {changelog.get('window_end','—')}]")
    if errors:
        print("   ⚠ section errors:")
        for e in errors: print("     -", e)
    return out

if __name__ == "__main__":
    render(sys.argv[1] if len(sys.argv) > 1 else yesterday_ny())
