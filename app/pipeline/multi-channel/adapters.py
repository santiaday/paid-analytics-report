#!/usr/bin/env python3
"""adapters.py — anchor-day (report-day) HTML section fragments for the unified
multi-tab report, produced by REUSING the four sibling daily generators' own
functions (never reimplementing their logic).

Siblings (each imported via importlib from its build.py; every call runs with
cwd chdir'd into that sibling's folder so any relative path resolves there):

  google     →  ../Google Ads/google-ads-daily-artifact/build.py
  microsoft  →  ../Microsoft Ads/microsoft-ads-daily-artifact/build.py
  meta       →  ../meta-ads/meta-ads-daily-artifact/build.py
  allsources →  ../all-channels-artifact/build.py

Public API (anchor = 'YYYY-MM-DD', the report day):

  google_sections(anchor)     -> {section_key: {"title", "html"}, "pill_html",
                                  "rec_alert_html", "errors": [...]}
  microsoft_sections(anchor)  -> {..., "pill_html", "errors"}
  meta_sections(anchor)       -> {..., "pill_html", "errors"}
  allsources_sections(anchor) -> {..., "errors"}

Every html value is a FRAGMENT (a .card / table block) identical to the markup
the sibling's own daily page renders for that section — ready to drop into a
page body that carries adapters_assets.CSS + adapters_assets.JS. No <html>/
<head> wrappers. Each section is built inside its own try/except: a failure
yields a small red banner and a traceback entry in "errors" — one broken
section never kills the page.

The per-section orchestration below mirrors each sibling's render() (the
minimal slice of it needed for these sections). Inline template blocks
(summary tables, Top-20 CPC rows, the demo-source pill variants, the meta
view-toggle, the all-channels tooltips) are copied VERBATIM from the
siblings' render()/TEMPLATE so output matches the daily pages byte-for-byte.
"""
import importlib.util
import json
import os
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(HERE)          # "Skills - Claude Code"

PATHS = {
    "google":     os.path.join(SKILLS, "Google Ads", "google-ads-daily-artifact"),
    "microsoft":  os.path.join(SKILLS, "Microsoft Ads", "microsoft-ads-daily-artifact"),
    "meta":       os.path.join(SKILLS, "meta-ads", "meta-ads-daily-artifact"),
    "allsources": os.path.join(SKILLS, "all-channels-artifact"),
}

_MODS = {}

def _mod(key):
    """Import the sibling's build.py under a unique module name (cached)."""
    if key not in _MODS:
        path = os.path.join(PATHS[key], "build.py")
        name = f"mc_adapter_{key}_build"
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        with _cd(PATHS[key]):
            spec.loader.exec_module(mod)
        _MODS[key] = mod
    return _MODS[key]

@contextmanager
def _cd(path):
    """chdir into a sibling's folder for the duration of its calls (their
    loaders use ROOT-anchored paths, but this guarantees any relative path
    inside their code also resolves to the right folder)."""
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)

ERR_HTML = '<div class="card"><p>⚠ section failed: {err}</p></div>'

def _put(out, key, title, fn):
    """Run one section builder; on failure store a red banner + the traceback."""
    try:
        out[key] = {"title": title, "html": fn()}
    except Exception as e:
        out["errors"].append(f"{key}: {e}\n{traceback.format_exc()}")
        out[key] = {"title": title, "html": ERR_HTML.format(err=_esc_basic(str(e)))}

def _esc_basic(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def _dates(anchor):
    run_date = (datetime.strptime(anchor, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][datetime.strptime(anchor, "%Y-%m-%d").weekday()]
    pretty = datetime.strptime(anchor, "%Y-%m-%d").strftime("%A %B %-d, %Y")
    return run_date, dow, pretty


# ════════════════════════════════════════════════════════════════════════════
# Shared search-platform helpers (Google + Microsoft share the same template;
# these reproduce the inline blocks of their render() verbatim)
# ════════════════════════════════════════════════════════════════════════════
def _summary_table(m, tid, rows, cur_label, prior_label):
    """Verbatim copy of the summary_table() closure in the Google/MS render()."""
    trs = ""
    for row in rows:
        met, c, p, t, st = row[:5]
        cls = row[5] if len(row) > 5 else ""   # optional row class (e.g. budget-loss alert)
        trs += (f'<tr class="{cls}"><td class="metric-name">{m.esc(met)}</td>'
                f'<td class="num">{c}</td><td class="num">{p}</td>'
                f'<td class="num">{t}</td><td class="status">{st}</td></tr>')
    return (f'<table id="{tid}"><thead><tr><th style="width:30%">Metric</th>'
            f'<th class="num">{cur_label}&dagger;</th><th class="num">{prior_label}&dagger;</th>'
            f'<th class="num">Difference</th><th class="status">Status</th></tr></thead><tbody>{trs}</tbody></table>')

def _summary_rows(m, spend_cur, demos_cur, spend_prior, demos_prior, excl):
    """Verbatim copy of the summary() closure in the Google/MS render()."""
    s, d, cpd = m.sum_group(spend_cur, demos_cur, excl)
    ps, pd, pcpd = m.sum_group(spend_prior, demos_prior, excl)
    return [
        ("Spend", m.money(s), m.money(ps), m.sdiff(s, ps, "money", neutral=True), "✅"),
        ("Demos Scheduled", m.conv(d), m.conv(pd), m.sdiff(d, pd, "conv"), m.status_demos(d, pd)),
        ("Cost Per Demo", m.money(cpd), m.money(pcpd), m.sdiff(cpd, pcpd, "money", lower_is_better=True), m.status_cpd(cpd)),
    ]

def _cpc_demos(m, anchor):
    """(kind, {term_lower: value}) — trailing-90d Demos Scheduled per search term, from the
    platform's converted90.json (already pulled daily for the bad-term classifier; no new pull).
    Google's rows carry a conversion COUNT → kind='count'; Microsoft's converted90 is presence-only
    (offline Demo_Scheduled goal, no per-term count at search-query grain) → kind='presence'."""
    rows = m.load(anchor, "converted90.json") or []
    has_count = any(("metrics.conversions" in r or "metrics.all_conversions" in r) for r in rows)
    out = {}
    for r in rows:
        t = (m.g(r, "search_term_view.search_term") or "").lower()
        if not t:
            continue
        if has_count:
            n = m.g(r, "metrics.conversions")
            if n is None:
                n = m.g(r, "metrics.all_conversions") or 0
            out[t] = out.get(t, 0) + (n or 0)
        else:
            out[t] = True
    return ("count" if has_count else "presence"), out

def _cpc_demo_foot(kind):
    if kind == "count":
        return ('† Demos = Demos Scheduled conversions ("2. Demo Scheduled" + "Demo Scheduled '
                'Salesforce Conversion") attributed to this search term over the trailing 90 days '
                '(click-date basis; very recent clicks may still be converting).')
    if kind == "presence":
        return ('† Demos = ✓ if this term drove ≥1 Demo_Scheduled (Salesforce/CRM offline import) in '
                'the trailing 90 days. Microsoft exposes no per-term demo COUNT at search-query grain, '
                'so this is presence, not a count.')
    return ""

def _cpc_rows(m, top20, demo_kind=None, demo_map=None):
    """Verbatim copy of the Top-20 CPC row loop in the Google/MS render(), plus a DEMOS column
    (trailing-90d Demos Scheduled per term — a count for Google, ✓/— presence for Microsoft)."""
    demo_map = demo_map or {}
    cpc_rows = ""
    if not top20:
        cpc_rows = '<tr><td colspan="7" class="muted">No search-term data.</td></tr>'
    for o in top20:
        red = o["cpc"] > m.CPC_RED
        v = demo_map.get((o["term"] or "").lower())
        if demo_kind == "presence":
            demo_cell = "✓" if v else "—"
        elif demo_kind == "count":
            demo_cell = m.intf(v) if v else "—"
        else:
            demo_cell = "—"
        cpc_rows += (f'<tr class="{"flag-red" if red else ""}">'
                     f'<td class="term">{"🔴 " if red else ""}{m.esc(o["term"])}</td>'
                     f'<td class="num">{m.money2(o["cpc"])}</td>'
                     f'<td class="num">{m.intf(o["clicks"])}</td>'
                     f'<td class="num">{demo_cell}</td>'
                     f'<td class="num">{m.money2(o["cost"])}</td>'
                     f'<td>{m.esc(o["camp"])}</td><td>{m.esc(o["ag"])}</td></tr>')
    return cpc_rows

def _cpc_card(m, pretty, cpc_rows, demo_foot=""):
    """Top-20 CPC card markup, verbatim from the Google/MS TEMPLATE (+ DEMOS column)."""
    foot_extra = f"<p>{demo_foot}</p>" if demo_foot else ""
    return (f'''<div class="card">
    <h2>Top 20 Search Term CPCs <span class="dt">— {pretty}</span></h2>
    <table><thead><tr><th>Search Term</th><th class="num">Avg CPC</th><th class="num">Clicks</th>
      <th class="num">Demos</th><th class="num">Cost</th><th>Campaign</th><th>Ad Group</th></tr></thead>
      <tbody>{cpc_rows}</tbody></table>
    <div class="foot"><p>🔴 = Avg CPC over ${m.CPC_RED}.</p>{foot_extra}</div>
  </div>''')

def _bad_card(pretty, bad_count, bad_foot, bad_banner, bad_rows, sheets_url=None):
    """Bad Search Terms card markup, verbatim from the Google/MS TEMPLATE.
    sheets_url (optional) → a "View Google Sheets" link at the top of the card,
    pointing at the Drive folder that houses this platform's search-terms sheets."""
    sheets_link = (f'<p class="foot" style="margin:2px 0 10px"><a href="{sheets_url}" '
                   f'target="_blank" rel="noopener">📄 View Google Sheets</a></p>') if sheets_url else ""
    return (f'''<div class="card">
    <h2>Bad Search Terms <span class="dt">— {pretty} · {bad_count} flagged</span>{bad_foot}</h2>
    {sheets_link}
    {bad_banner}
    <table><thead><tr><th class="num">#</th><th>Verdict</th><th>Term</th><th>Campaign / Ad Group</th>
      <th class="num">Spend</th><th class="num">Imps</th><th class="num">Clicks</th><th class="num">Conv</th>
      <th>Reason</th></tr></thead><tbody>{bad_rows}</tbody></table>
  </div>''')

def _search_extras(m, st_cur, st_prior, ranklost_rows, proj):
    """The 4 extra All-Campaigns rows (Top-3 CPCs, Rank-lost IS, Budget-lost,
    Projected month) — verbatim from the Google/MS render()."""
    c3 = m.top3_cpc(st_cur)
    p3 = " / ".join(m.money((float(m.g(r, "metrics.average_cpc") or 0)) / 1e6) for r in st_prior[:3]) or "—"
    rl_ranked = m.rank_lost_ranked(ranklost_rows)
    qualifying = [o for o in rl_ranked if o["cost"] >= m.RANK_MIN_SPEND]
    worst_obj = qualifying[0] if qualifying else (rl_ranked[0] if rl_ranked else None)
    worst = worst_obj["rl"] if worst_obj else None
    rl_txt = f"{m.short_name(worst_obj['name'])} {m.pct(worst)}" if worst_obj else "—"
    blost = m.budget_lost(ranklost_rows)
    if blost:
        bl_txt = ", ".join(f"{m.short_name(o['name'])} {m.pct(o['bl'])}" for o in blost)
        bl_row = ("Search Lost IS (Budget)", bl_txt, "—", "—", "🔴", "row-alert")
    else:
        bl_row = ("Search Lost IS (Budget)", "0%", "—", "—", "✅")
    return [
        ("Top 3 CPCs (search terms)", c3, p3, "—", "🟢"),
        ("Rank-lost IS (Highest)", rl_txt, "—", "—", m.status_rank(worst)),
        bl_row,
        ("Projected monthly spend", m.money(proj), "—", "—", ""),
    ]


# ════════════════════════════════════════════════════════════════════════════
# GOOGLE
# ════════════════════════════════════════════════════════════════════════════
def google_sections(anchor):
    out = {"errors": []}
    m = _mod("google")
    run_date, dow, pretty = _dates(anchor)
    cur_label, prior_label = "Yesterday", "Prior " + dow

    with _cd(PATHS["google"]):
        # ── Shared intermediates (mirrors render(); guarded as a unit) ───────
        shared_err = None
        try:
            spend_cur = m.agg_spend(m.load(anchor, "spend_cur.json"))
            spend_prior = m.agg_spend(m.load(anchor, "spend_prior.json"))
            demos_cur = m.agg_demos(m.load(anchor, "demos_cur.json"))
            demos_prior = m.agg_demos(m.load(anchor, "demos_prior.json"))
            cohort = m.load_cohort_demos(anchor)
            if cohort:
                demos_cur = dict(cohort.get("campaign_cur", {}))
                demos_prior = dict(cohort.get("campaign_prior", {}))
            ranklost_rows = m.load(anchor, "ranklost.json")
            st_cur = m.load(anchor, "searchterms.json")
            st_prior = m.load(anchor, "searchterms_prior.json")
            proj = m.projected_month(m.load(anchor, "monthspend.json"), run_date)
        except Exception as e:
            shared_err = e
            out["errors"].append(f"google shared: {e}\n{traceback.format_exc()}")

        def need_shared():
            if shared_err is not None:
                raise RuntimeError(f"shared data failed: {shared_err}")

        # ── Demo-source pill (verbatim basis_note blocks from render()) ──────
        try:
            need_shared()
            basis_src = (cohort.get("data_source", "brain") if cohort else "platform")
            if basis_src == "salesforce":
                basis_note = ('<span class="pill" style="background:#B45309;color:#fff;border:0">'
                              'Demos: Salesforce Day-0 cohort (Brain unavailable)</span>')
                demo_footnote = (
                    '<p>&dagger; <b>Demos = Salesforce Day-0 cohort</b> — the Brain was unreachable this run, so demos '
                    'come straight from Salesforce (read-only): leads whose demo-scheduled date equals their lead-created '
                    'date (David&rsquo;s CDF1 formula, <code>Original_Demo_Scheduled_At__c</code> basis), attached to each '
                    'campaign by <code>Lead.campaign_ID__c</code> = the Google Ads <b>campaign ID</b>. Same Day-0 idea as '
                    'the Brain cohort but UTM first-click attribution, so it typically runs <b>~10&ndash;25% above</b> the '
                    'Brain number — a documented basis difference, not growth. <b>Prior</b> = the same weekday exactly '
                    '7 days earlier. The chart&rsquo;s ad-group demo lines are flat &mdash; the cohort has no ad-group '
                    'split.</p>')
            elif basis_src == "brain":
                basis_note = '<span class="pill" style="background:#3185FC;color:#fff;border:0">Demos: Brain Day-0 cohort</span>'
                demo_footnote = (
                    '<p>&dagger; <b>Demos = Brain Day-0 cohort</b> — demos scheduled the <i>same day</i> as the ad click '
                    '(DoorLoop Brain &middot; Paid Media Optimizer &middot; Cohort basis &middot; '
                    '<code>gtm-paid-media-daily-cohort</code>), attached to each campaign by Google Ads <b>campaign ID</b>. '
                    'Apples-to-apples day-over-day and <b>never matures</b> (unlike Google-Ads platform conversions, which '
                    'keep growing for ~90 days). <b>Prior</b> = the same weekday exactly 7 days earlier. The chart&rsquo;s '
                    'ad-group demo lines are flat &mdash; the cohort cube has no ad-group split. Falls back to Salesforce '
                    'Day-0 cohort, then platform demos, if the Brain is unreachable.</p>')
            else:
                basis_note = ('<span class="pill" style="background:#64748B;color:#fff;border:0">'
                              'Demos: Google Ads platform conversions (Brain + Salesforce unavailable)</span>')
                demo_footnote = (
                    '<p>&dagger; <b>Demos</b> = Google Ads conversions for <i>Demo Scheduled Salesforce Conversion</i> + '
                    '<i>2. Demo Scheduled</i>, counted by conversion date. <b>Prior</b> = the same weekday exactly 7 days '
                    'earlier. <i>(Platform demos — last-resort basis: both the Brain Day-0 cohort and the Salesforce '
                    'fallback were unavailable this run.)</i></p>')
            out["pill_html"] = basis_note + m.tip(demo_footnote)
        except Exception as e:
            out["pill_html"] = ""
            out["errors"].append(f"google pill: {e}\n{traceback.format_exc()}")

        # ── Recommendations (feeds both the rec_alert banner and Daily Alerts) ─
        rec_rows, rec_count, rec_foot, rec_alert = (
            '<tr><td colspan="3" class="muted">—</td></tr>', 0, "", "")
        try:
            need_shared()
            id2name = {}
            for fn in ("spend_cur.json", "spend_prior.json"):
                for r in m.load(anchor, fn):
                    cid, nm = m.g(r, "campaign.id"), m.g(r, "campaign.name")
                    if cid and nm:
                        id2name[str(cid)] = nm
            rec_rows, rec_count, _rec_note, rec_foot, rec_alert = m.render_recommendations(anchor, id2name)
            out["rec_alert_html"] = rec_alert
        except Exception as e:
            out["rec_alert_html"] = ""
            out["errors"].append(f"google recommendations: {e}\n{traceback.format_exc()}")

        # ── All Campaigns ─────────────────────────────────────────────────────
        def s_all():
            need_shared()
            all_rows = _summary_rows(m, spend_cur, demos_cur, spend_prior, demos_prior, False)
            all_rows += _search_extras(m, st_cur, st_prior, ranklost_rows, proj)
            return f'<div class="card accordion"><h2>All Campaigns</h2>{_summary_table(m, "tblAll", all_rows, cur_label, prior_label)}</div>'
        _put(out, "all_campaigns", "All Campaigns", s_all)

        # ── Excl. Branded ─────────────────────────────────────────────────────
        def s_excl():
            need_shared()
            excl_rows = _summary_rows(m, spend_cur, demos_cur, spend_prior, demos_prior, True)
            return ('<div class="card accordion"><h2>Excl. Branded <span class="dt">— active campaigns without '
                    '“branded” in the name</span></h2>'
                    f'{_summary_table(m, "tblExcl", excl_rows, cur_label, prior_label)}</div>')
        _put(out, "excl_branded", "Excl. Branded", s_excl)

        # ── Daily Alerts (anomalies + Google's Recommendations subsection) ───
        def s_alerts():
            need_shared()
            clicks_rows = m.load(anchor, "clicks_30d.json")
            chart_data = None
            if clicks_rows:
                demos_rows = m.load(anchor, "demos_30d.json")
                rankis_rows = m.load(anchor, "rankis_30d.json")
                pipeline = m.load_pipeline(anchor)
                chart_data = m.clicks_daily_data(clicks_rows, anchor,
                                                 demos_rows=(None if cohort else (demos_rows or None)),
                                                 camp_demos=(cohort.get("daily_series") if cohort else None),
                                                 rankis_rows=rankis_rows or None, daily=True,
                                                 pipeline=pipeline)
            alerts = m.compute_alerts(chart_data, anchor) if chart_data else []
            alerts_body, alerts_count = m.render_alerts_section(alerts, chart_data is not None)
            return (f'''<div class="card accordion">
    <h2>Daily Alerts <span class="dt">— {pretty}</span></h2>
    {alerts_body}
    <h3>Google's Recommendations <span class="dt">— {rec_count} to review</span>{rec_foot}</h3>
    <table><thead><tr><th>Recommendation</th><th class="num">Campaigns</th><th>Where</th></tr></thead>
      <tbody>{rec_rows}</tbody></table>
  </div>''')
        _put(out, "daily_alerts", "Daily Alerts", s_alerts)

        # ── Top 20 Search Term CPCs ───────────────────────────────────────────
        def s_cpc():
            need_shared()
            top20 = m.search_term_cpcs(st_cur)[:20]
            dk, dm = _cpc_demos(m, anchor)
            return _cpc_card(m, pretty, _cpc_rows(m, top20, dk, dm), demo_foot=_cpc_demo_foot(dk))
        _put(out, "top20_cpc", "Top 20 Search Term CPCs", s_cpc)

        # ── Bad Search Terms ──────────────────────────────────────────────────
        def s_bad():
            need_shared()
            converted90 = set((m.g(r, "search_term_view.search_term") or "").lower()
                              for r in m.load(anchor, "converted90.json"))
            vocab = set()
            for r in m.load_vocab(anchor):
                for tk in m.tokens(m.g(r, "ad_group_criterion.keyword.text") or ""):
                    vocab.add(tk)
            bad = m.build_bad_terms(st_cur, converted90, vocab)
            bad_rows, bad_count, bad_banner, bad_foot, _src, _gaps = \
                m.render_bad_terms_section(anchor, bad, int(m.OVERLAP_RESCUE * 100))
            return _bad_card(pretty, bad_count, bad_foot, bad_banner, bad_rows,
                             sheets_url="https://drive.google.com/drive/u/1/folders/REDACTED")
        _put(out, "bad_terms", "Bad Search Terms", s_bad)

    return out


# ════════════════════════════════════════════════════════════════════════════
# MICROSOFT  (same template as Google minus the Recommendations subsection)
# ════════════════════════════════════════════════════════════════════════════
def microsoft_sections(anchor):
    out = {"errors": []}
    m = _mod("microsoft")
    run_date, dow, pretty = _dates(anchor)
    cur_label, prior_label = "Yesterday", "Prior " + dow

    with _cd(PATHS["microsoft"]):
        shared_err = None
        try:
            spend_cur = m.agg_spend(m.load(anchor, "spend_cur.json"))
            spend_prior = m.agg_spend(m.load(anchor, "spend_prior.json"))
            demos_cur = m.agg_demos(m.load(anchor, "demos_cur.json"))
            demos_prior = m.agg_demos(m.load(anchor, "demos_prior.json"))
            cohort = m.load_cohort_demos(anchor)
            if cohort:
                demos_cur = dict(cohort.get("campaign_cur", {}))
                demos_prior = dict(cohort.get("campaign_prior", {}))
            ranklost_rows = m.load(anchor, "ranklost.json")
            st_cur = m.load(anchor, "searchterms.json")
            st_prior = m.load(anchor, "searchterms_prior.json")
            proj = m.projected_month(m.load(anchor, "monthspend.json"), run_date)
        except Exception as e:
            shared_err = e
            out["errors"].append(f"microsoft shared: {e}\n{traceback.format_exc()}")

        def need_shared():
            if shared_err is not None:
                raise RuntimeError(f"shared data failed: {shared_err}")

        # ── Demo-source pill (verbatim basis_note blocks from the MS render()) ─
        try:
            need_shared()
            basis_src = (cohort.get("data_source", "brain") if cohort else "platform")
            if basis_src == "salesforce":
                basis_note = ('<span class="pill" style="background:#B45309;color:#fff;border:0">'
                              'Demos: Salesforce Day-0 cohort (Brain unavailable)</span>')
                demo_footnote = (
                    '<p>&dagger; <b>Demos = Salesforce Day-0 cohort</b> — the Brain was unreachable this run, so demos '
                    'come straight from Salesforce (read-only): leads whose demo-scheduled date equals their lead-created '
                    'date (David&rsquo;s CDF1 formula, <code>Original_Demo_Scheduled_At__c</code> basis), attached to each '
                    'campaign by <code>Lead.campaign_ID__c</code> = the Microsoft Ads <b>campaign ID</b>. Same Day-0 idea '
                    'as the Brain cohort but UTM first-click attribution, so it typically runs <b>~10&ndash;25% above</b> '
                    'the Brain number — a documented basis difference, not growth. <b>Prior</b> = the same weekday exactly '
                    '7 days earlier. Chart ad-group demo lines are flat &mdash; the cohort has no ad-group split.</p>')
            elif basis_src == "brain":
                basis_note = '<span class="pill" style="background:#3185FC;color:#fff;border:0">Demos: Brain Day-0 cohort</span>'
                demo_footnote = (
                    '<p>&dagger; <b>Demos = Brain Day-0 cohort</b> — demos scheduled the <i>same day</i> as the ad click '
                    '(DoorLoop Brain &middot; Paid Media Optimizer &middot; Cohort basis &middot; '
                    '<code>gtm-paid-media-daily-cohort</code> &middot; <code>SOURCE=bing</code>), attached to each campaign '
                    'by Microsoft Ads <b>campaign ID</b>. Apples-to-apples day-over-day, <b>never matures</b>, and recovers '
                    'demos the Microsoft Reporting API misses (the Competitors offline-import gap). <b>Prior</b> = the same '
                    'weekday exactly 7 days earlier. Chart ad-group demo lines are flat &mdash; the cohort cube has no '
                    'ad-group split. Falls back to Salesforce Day-0 cohort, then platform demos, if the Brain is '
                    'unreachable.</p>')
            else:
                basis_note = ('<span class="pill" style="background:#64748B;color:#fff;border:0">'
                              'Demos: Microsoft Ads platform conversions (Brain + Salesforce unavailable)</span>')
                demo_footnote = (
                    '<p>&dagger; <b>Demos Scheduled</b> = Microsoft Ads conversions for the <i>Demo_Scheduled</i> goal '
                    '(Salesforce/CRM import). Competitors counts both <i>Demo_Scheduled</i> and <i>Online Demo Scheduled</i> '
                    'to cover history before its goal changed on 2026-06-04. <b>Prior</b> = the same weekday exactly 7 days '
                    'earlier. <i>(Platform demos — last-resort basis: both the Brain Day-0 cohort and the Salesforce '
                    'fallback were unavailable this run.)</i></p>')
            out["pill_html"] = basis_note + m.tip(demo_footnote)
        except Exception as e:
            out["pill_html"] = ""
            out["errors"].append(f"microsoft pill: {e}\n{traceback.format_exc()}")

        def s_all():
            need_shared()
            all_rows = _summary_rows(m, spend_cur, demos_cur, spend_prior, demos_prior, False)
            all_rows += _search_extras(m, st_cur, st_prior, ranklost_rows, proj)
            return f'<div class="card accordion"><h2>All Campaigns</h2>{_summary_table(m, "tblAll", all_rows, cur_label, prior_label)}</div>'
        _put(out, "all_campaigns", "All Campaigns", s_all)

        def s_excl():
            need_shared()
            excl_rows = _summary_rows(m, spend_cur, demos_cur, spend_prior, demos_prior, True)
            return ('<div class="card accordion"><h2>Excl. Branded <span class="dt">— active campaigns without '
                    '“branded” in the name</span></h2>'
                    f'{_summary_table(m, "tblExcl", excl_rows, cur_label, prior_label)}</div>')
        _put(out, "excl_branded", "Excl. Branded", s_excl)

        def s_alerts():
            need_shared()
            clicks_rows = m.load(anchor, "clicks_30d.json")
            chart_data = None
            if clicks_rows:
                demos_rows = m.load(anchor, "demos_30d.json")
                rankis_rows = m.load(anchor, "rankis_30d.json")
                pipeline = m.load_pipeline(anchor)
                chart_data = m.clicks_daily_data(clicks_rows, anchor,
                                                 demos_rows=(None if cohort else (demos_rows or None)),
                                                 camp_demos=(cohort.get("daily_series") if cohort else None),
                                                 rankis_rows=rankis_rows or None, daily=True,
                                                 pipeline=pipeline)
            alerts = m.compute_alerts(chart_data, anchor) if chart_data else []
            alerts_body, alerts_count = m.render_alerts_section(alerts, chart_data is not None)
            return (f'''<div class="card accordion">
    <h2>Daily Alerts <span class="dt">— {pretty}</span></h2>
    {alerts_body}
  </div>''')
        _put(out, "daily_alerts", "Daily Alerts", s_alerts)

        def s_cpc():
            need_shared()
            top20 = m.search_term_cpcs(st_cur)[:20]
            dk, dm = _cpc_demos(m, anchor)
            return _cpc_card(m, pretty, _cpc_rows(m, top20, dk, dm), demo_foot=_cpc_demo_foot(dk))
        _put(out, "top20_cpc", "Top 20 Search Term CPCs", s_cpc)

        def s_bad():
            need_shared()
            converted90 = set((m.g(r, "search_term_view.search_term") or "").lower()
                              for r in m.load(anchor, "converted90.json"))
            # MS vocab is ENRICHED (thin keyword corpus): keywords + converted
            # search terms + ad-group names — verbatim from the MS render().
            vocab = set()
            for r in m.load_vocab(anchor):
                for tk in m.tokens(m.g(r, "ad_group_criterion.keyword.text") or ""):
                    vocab.add(tk)
            for term in converted90:
                for tk in m.tokens(term):
                    vocab.add(tk)
            for r in st_cur:
                for tk in m.tokens(m.g(r, "ad_group.name") or ""):
                    vocab.add(tk)
            known_bad = m.load_known_bad_brands()
            bad = m.build_bad_terms(st_cur, converted90, vocab, known_bad)
            bad_rows, bad_count, bad_banner, bad_foot, _src, _gaps = \
                m.render_bad_terms_section(anchor, bad, int(m.OVERLAP_RESCUE * 100))
            return _bad_card(pretty, bad_count, bad_foot, bad_banner, bad_rows,
                             sheets_url="https://drive.google.com/drive/u/1/folders/REDACTED")
        _put(out, "bad_terms", "Bad Search Terms", s_bad)

    return out


# ════════════════════════════════════════════════════════════════════════════
# META
# ════════════════════════════════════════════════════════════════════════════
def meta_sections(anchor):
    out = {"errors": []}
    m = _mod("meta")
    run_date, dow, pretty = _dates(anchor)
    cur_label, prior_label = "Yesterday", "Prior " + dow

    with _cd(PATHS["meta"]):
        shared_err = None
        meta_basis = "nofile"          # set to "sf" when the SF first-touch daily pull is present
        ret_ft = ret_lt = ret_at = None
        try:
            spend_cur = m.agg_spend(m.load(anchor, "spend_cur.json"))
            spend_prior = m.agg_spend(m.load(anchor, "spend_prior.json"))
            demos_cur = m.agg_demos(m.load(anchor, "demos_cur.json"))
            demos_prior = m.agg_demos(m.load(anchor, "demos_prior.json"))
            cohort = m.load_cohort_demos(anchor)
            if cohort:
                demos_cur = dict(cohort.get("campaign_cur", {}))
                demos_prior = dict(cohort.get("campaign_prior", {}))
            # multi-channel override: Meta demos are Salesforce FIRST-TOUCH (2026-06-16), NOT the
            # sibling's Brain cohort. Use the report-day SF pull (sf_meta_demos.js) so the anchor-day
            # Performance Summary matches the SF-first-touch charts/KPIs. Absent → leave sibling values
            # (rare; the range charts still use the SF first-touch history regardless).
            sf_demos_path = os.path.join(HERE, "data", anchor, "sf_meta_demos.json")
            if os.path.exists(sf_demos_path):
                sfd = json.load(open(sf_demos_path))
                prior_dt = (datetime.strptime(anchor, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
                cur, prio = {}, {}
                for r in sfd.get("ft", []):
                    if r["DT"] == anchor:
                        cur[r["CAMP"]] = cur.get(r["CAMP"], 0) + r["N"]
                    elif r["DT"] == prior_dt:
                        prio[r["CAMP"]] = prio.get(r["CAMP"], 0) + r["N"]
                demos_cur, demos_prior = cur, prio
                meta_basis = "sf"
                for r in sfd.get("retarget", []):
                    if r["DT"] == anchor:
                        ret_ft, ret_lt, ret_at = r.get("FT", 0), r.get("LT", 0), r.get("AT", 0)
            proj = m.projected_month(m.load(anchor, "monthspend.json"), run_date)
        except Exception as e:
            shared_err = e
            out["errors"].append(f"meta shared: {e}\n{traceback.format_exc()}")

        def need_shared():
            if shared_err is not None:
                raise RuntimeError(f"shared data failed: {shared_err}")

        # ── Demo-source pill — multi-channel Meta demos = Salesforce FIRST-TOUCH (2026-06-16).
        #    Explicit by design (David): the pill states the basis is SF first-touch; the chart's
        #    Retargeting line carries a First / Last / All-touch toggle (all three from Salesforce).
        try:
            need_shared()
            if meta_basis == "sf":
                basis_note = ('<span class="pill" style="background:#3185FC;color:#fff;border:0">'
                              'Demos: Salesforce first-touch (fb+ig, by campaign)</span>')
                ret_line = ""
                if ret_ft is not None:
                    ret_line = ('<b>Retargeting</b> on the report day &mdash; <b>SF first-touch:</b> %d &middot; '
                                '<b>last-touch:</b> %d &middot; <b>all-touch:</b> %d. (The chart&rsquo;s Retargeting '
                                'line has a <b>First / Last / All-touch</b> toggle; first-touch is the headline '
                                'everywhere else.) ' % (ret_ft, ret_lt, ret_at))
                demo_footnote = (
                    '<p>&dagger; <b>Demos = Salesforce FIRST-TOUCH</b> &mdash; the first-click campaign '
                    '(<code>UTM_Source__c</code> = fb/ig, campaign = <code>UTM_Campaign__c</code>), counted by '
                    '<code>Original_Demo_Scheduled_At__c</code> date. This is the headline basis for every Meta '
                    'campaign (consistent with Google / Bing / All-Sources, which are all first-click UTM). ' + ret_line +
                    'Last-touch (<code>UTM_Campaign_Last_Touch__c</code>) and all-touch '
                    '(<code>UTM_Campaign_All_Touches__c</code> &mdash; an assist/involvement count, slightly '
                    'under-counts very long paths) also come from Salesforce. First-touch <b>under-credits '
                    'Retargeting</b> (it is rarely the first click) &mdash; expected, that is what the toggle is for. '
                    '<b>Prior</b> = the same weekday exactly 7 days earlier.</p>')
            else:
                basis_note = ('<span class="pill" style="background:#64748B;color:#fff;border:0">'
                              'Demos: Salesforce first-touch (fb+ig) &mdash; report-day snapshot unavailable</span>')
                demo_footnote = (
                    '<p>&dagger; <b>Demos = Salesforce first-touch</b> (first-click <code>UTM_Campaign__c</code>, '
                    'fb/ig). The report-day SF pull was missing this run, so the single-day Performance Summary may '
                    'lag; the range charts and KPIs still use the Salesforce first-touch history.</p>')
            out["pill_html"] = basis_note + m.tip(demo_footnote)
        except Exception as e:
            out["pill_html"] = ""
            out["errors"].append(f"meta pill: {e}\n{traceback.format_exc()}")

        # ── Performance Summary (All Campaigns → per-campaign view toggle) ────
        def s_perf():
            need_shared()

            def summary_table(tid, rows):
                trs = ""
                for row in rows:
                    met, c, p, t, st = row[:5]
                    cls = row[5] if len(row) > 5 else ""
                    trs += (f'<tr class="{cls}"><td class="metric-name">{m.esc(met)}</td>'
                            f'<td class="num">{c}</td><td class="num">{p}</td>'
                            f'<td class="num">{t}</td><td class="status">{st}</td></tr>')
                return (f'<table id="{tid}"><thead><tr><th style="width:30%">Metric</th>'
                        f'<th class="num">{cur_label}&dagger;</th><th class="num">{prior_label}&dagger;</th>'
                        f'<th class="num">Difference</th><th class="status">Status</th></tr></thead><tbody>{trs}</tbody></table>')

            def summary_rows(pred, with_proj):
                s, d, cpd = m.sum_view(spend_cur, demos_cur, pred)
                ps, pd, pcpd = m.sum_view(spend_prior, demos_prior, pred)
                rows = [
                    ("Spend", m.money(s), m.money(ps), m.sdiff(s, ps, "money", neutral=True), "✅"),
                    ("Demos Scheduled", m.conv(d), m.conv(pd), m.sdiff(d, pd, "conv"), m.status_demos(d, pd)),
                    ("Cost Per Demo", m.money(cpd), m.money(pcpd), m.sdiff(cpd, pcpd, "money", lower_is_better=True), m.status_cpd(cpd)),
                ]
                if with_proj:
                    rows.append(("Projected monthly spend", m.money(proj), "—", "—", ""))
                return rows

            camp_names = [r["name"] for r in sorted(spend_cur, key=lambda r: -r["cost"])]
            views = [("All Campaigns", None)] + [(m.short_name(n) or n, n) for n in camp_names]
            view_chips, view_tables = "", ""
            for vi, (vlabel, vname) in enumerate(views):
                pred = (lambda nm: True) if vname is None else (lambda nm, _v=vname: nm == _v)
                rows = summary_rows(pred, with_proj=(vname is None))
                on = " on" if vi == 0 else ""
                view_chips += f'<span class="vbtn{on}" data-v="{vi}">{m.esc(vlabel)}</span>'
                view_tables += f'<div class="sumview{on}" id="sumview{vi}">{summary_table(f"tblView{vi}", rows)}</div>'
            return (f'''<div class="card accordion">
    <h2>Performance Summary</h2>
    <div class="view-toggle" id="viewToggle">{view_chips}</div>
    {view_tables}
  </div>''')
        _put(out, "performance_summary", "Performance Summary", s_perf)

        # ── Daily Alerts ──────────────────────────────────────────────────────
        def s_alerts():
            need_shared()
            clicks_rows = m.load(anchor, "clicks_30d.json")
            chart_data = None
            if clicks_rows:
                demos_rows = m.load(anchor, "demos_30d.json")
                pipeline = m.load_pipeline(anchor)
                chart_data = m.clicks_daily_data(clicks_rows, anchor,
                                                 demos_rows=(None if cohort else (demos_rows or None)),
                                                 camp_demos=(cohort.get("daily_series") if cohort else None),
                                                 daily=True,
                                                 pipeline=pipeline)
            alerts = m.compute_alerts(chart_data, anchor) if chart_data else []
            alerts_body, alerts_count = m.render_alerts_section(alerts, chart_data is not None)
            return (f'''<div class="card accordion">
    <h2>Daily Alerts <span class="dt">— {pretty}</span></h2>
    {alerts_body}
  </div>''')
        _put(out, "daily_alerts", "Daily Alerts", s_alerts)

    return out


# ════════════════════════════════════════════════════════════════════════════
# ALL SOURCES  (all-channels funnel — daily grain pieces of render("daily", key))
# ════════════════════════════════════════════════════════════════════════════
def allsources_sections(anchor):
    out = {"errors": []}
    try:
        m = _mod("allsources")
    except Exception:  # noqa: BLE001
        # The all-channels-artifact SERVER-SIDE renderer isn't vendored in this deployment. That's OK:
        # the interactive All-Sources scorecard / conversion rates / charts render CLIENT-SIDE from the
        # history "slices" (engine.js), which sf_allsources.py keeps current via series.json. Only the
        # anchor-day server sections (an alerts card) come from this module — skip them silently so the
        # missing sibling never raises "ALL sections failed" into the top health banner.
        return {"errors": []}

    with _cd(PATHS["allsources"]):
        shared_err = None
        try:
            S, errs = m.load_series(anchor)          # daily dirname == anchor
            if S is not None and S["days"][-1] != anchor:
                errs.append(f"window ends {S['days'][-1]}, expected {anchor}")
            if errs:
                raise RuntimeError("preflight: " + "; ".join(errs))
            days = S["days"]
            slices = S["slices"]
            day_objs = [m._d(x) for x in days]
            years = {day_objs[0].year, day_objs[-1].year}
            holi = m.us_holidays(years)
            cfg = m.ALERTS_CFG["daily"]
            A = slices["all"]
            arr_total = sum(A["arr"])
            stage_val = {k: (arr_total / sum(A[k])) if sum(A[k]) else 0.0 for k in m.COUNTS}
            ei = len(days) - 1
            cmp_i = ei - 7
            B = m.bucket(slices, [[cmp_i], [ei]])
            cur, prev, yoy = 1, 0, None
            anchor_d = day_objs[ei]
            pretty = anchor_d.strftime("%A, %B %-d, %Y")
            score_dt = f"{anchor_d.strftime('%b %-d')} vs {day_objs[cmp_i].strftime('%b %-d')}"
            prev_lbl = "prior wk"
            banners = []
            # Holiday eval days are suppressed in compute_alerts; the in-card note explains it
            # (a loose banner here gets stripped by initHistPicker anyway). The comparison-day
            # banner still applies to non-holiday eval days whose prior-week reference was a holiday.
            if day_objs[cmp_i] in holi and anchor_d not in holi:
                banners.append(f'<div class="warn-banner">⚠️ The comparison day '
                               f'({day_objs[cmp_i].strftime("%b %-d")}) was {holi[day_objs[cmp_i]]} — '
                               f'the "prior wk" deltas may overstate change.</div>')
        except Exception as e:
            shared_err = e
            out["errors"].append(f"allsources shared: {e}\n{traceback.format_exc()}")

        def need_shared():
            if shared_err is not None:
                raise RuntimeError(f"shared data failed: {shared_err}")

        # ── Alerts card (holiday banners ride along, as on the daily page) ───
        def s_alerts():
            need_shared()
            alerts = m.compute_alerts(slices, day_objs, "daily", cfg, eval_idx=ei,
                                      stage_val=stage_val, holi=holi)
            alerts_body, n_alerts = m.render_alerts_section(alerts, "daily", cfg, holiday_name=holi.get(anchor_d))
            return ("".join(banners)
                    + f'''<div class="card accordion">
    <h2>Daily Alerts <span class="dt">— {pretty}</span></h2>
    {alerts_body}
  </div>''')
        _put(out, "alerts", "Daily Alerts", s_alerts)

        # ── basis_note + tooltips (verbatim from render(); shared by the two
        #    table sections below) ───────────────────────────────────────────
        def _basis_and_brain():
            # Brain Day-0 cohort overlay (display verification only)
            brain_day0 = None
            cd_path = os.path.join(m.ROOT, "data", anchor, "cohort_demos.json")
            if os.path.exists(cd_path):
                try:
                    brain_day0 = (json.load(open(cd_path)) or {}).get("slices") or None
                except Exception:
                    brain_day0 = None
            bc_path = os.path.join(m.ROOT, "data", anchor, "brain_check.json")
            basis_note = "<p><b>Source:</b> Salesforce (direct, read-only). "
            if brain_day0:
                basis_note += ("Day-0 row also shows the <b>Brain basis</b> for paid channels — the Brain "
                               "buckets its cohort by First-Website-Visit date with click-level attribution, "
                               "so it runs lower than the Salesforce number; that gap is the documented basis "
                               "difference, not an error. ")
            if os.path.exists(bc_path):
                try:
                    bc = json.load(open(bc_path))
                except Exception:
                    bc = {"status": "error", "note": "brain_check.json unreadable"}
                st = bc.get("status")
                if st == "ok":
                    basis_note += f"Brain spot-check: ✓ totals within tolerance. {m.esc(bc.get('note', ''))}</p>"
                elif st == "mismatch":
                    basis_note += "Brain spot-check: ⚠️ mismatch (see banner).</p>"
                else:
                    basis_note += "Brain: unreachable this run.</p>"
            else:
                basis_note += "Brain not consulted on this run (daily runs are Salesforce-only by design).</p>"
            return basis_note, brain_day0

        _DEFS_HTML = (
            "<p><b>Definitions:</b> MQLs = leads with Is MQL = true by lead-created date · "
            "Demos Scheduled = by ORIGINAL demo-scheduled date (never the mutable reschedule date) · "
            "Demos Day-0 Cohort = demos scheduled the SAME day the lead was created (CDF1 cohort; "
            "matches the live cohort reports exactly) · Demos Completed = demo status &ldquo;Completed&rdquo; "
            "by completed-at date (≈9% lack the timestamp and fall back to their original scheduled date) · "
            "Opportunities = Opportunity records by created date (channel = the Account's UTM source) · "
            "New Accounts/ARR = first Stripe invoice date, Stripe Subscription ARR. Test accounts excluded "
            "everywhere. Channel attribution = first-click UTM (campaign drill-downs too).</p>"
        )
        _RATES_HTML = (
            "<p>Rates are same-period ratios (e.g. demos scheduled in the window ÷ MQLs in the window) — "
            "late-stage metrics lag early ones, so very recent windows run low on Opp→Account. "
            "Numerator-first definitions: each rate = next-stage count ÷ this-stage count.</p>"
        )

        # ── Funnel Scorecard ──────────────────────────────────────────────────
        def s_scorecard():
            need_shared()
            basis_note, brain_day0 = _basis_and_brain()
            scorecard_tip = m.tip(_DEFS_HTML + basis_note)
            scorecard = m.scorecard_html(B, cur, prev, yoy, prev_lbl, brain=brain_day0, grain="daily")
            return (f'''<div class="card accordion">
    <h2>Funnel Scorecard <span class="dt">— {score_dt}</span>{scorecard_tip}</h2>
    {scorecard}
  </div>''')
        _put(out, "scorecard", "Funnel Scorecard", s_scorecard)

        # ── Conversion Rates ──────────────────────────────────────────────────
        def s_rates():
            need_shared()
            rates_tip = m.tip(_RATES_HTML)
            rates = m.rates_html(B, cur, prev, yoy, prev_lbl)
            return (f'''<div class="card accordion">
    <h2>Conversion Rates <span class="dt">— {score_dt}</span>{rates_tip}</h2>
    {rates}
  </div>''')
        _put(out, "rates", "Conversion Rates", s_rates)

        # ── Per-Campaign Drill-Down (one accordion card per paid channel) ─────
        def s_campaigns():
            need_shared()
            html = m.campaigns_html(S.get("campaigns"), score_dt, prev_lbl)
            if not html:
                return '<div class="card"><p class="muted">No campaign drill-down data in series.json.</p></div>'
            return html
        _put(out, "campaigns", "Per-Campaign Drill-Down", s_campaigns)

    return out


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else None
    if not a:
        print("usage: python3 adapters.py <ANCHOR YYYY-MM-DD>")
        sys.exit(1)
    for name, fn in (("google", google_sections), ("microsoft", microsoft_sections),
                     ("meta", meta_sections), ("allsources", allsources_sections)):
        r = fn(a)
        print(f"== {name}")
        for k, v in r.items():
            if isinstance(v, dict):
                print(f"   {k:22s} {len(v['html']):7,d} chars")
            elif k == "errors":
                for e in v:
                    print("   ERROR:", e.splitlines()[0])
            else:
                print(f"   {k:22s} {len(v):7,d} chars (special)")
