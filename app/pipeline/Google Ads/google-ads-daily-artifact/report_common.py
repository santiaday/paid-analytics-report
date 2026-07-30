#!/usr/bin/env python3
"""
Shared renderer for the Google Ads WEEKLY and MONTHLY reports.

Both build_weekly.py and build_monthly.py build a small `cfg` dict (period
labels, data directory, the 12 time-bucket boundaries + a date→bucket mapper)
and call render(cfg). All the locked rules — demo conversion actions, the
$2,100 CPD target, the literal-"branded" exclusion, helpers — live in build.py
and are imported here. This keeps ONE source of truth (see CLAUDE.md / spec).

No network calls. Reads the JSON saved under data/<cfg['data_dir']>/ and writes
outputs/<cfg['out_name']>.

Chart.js is the only external dependency (loaded from a CDN with SRI). All chart
JavaScript reads from an embedded JSON blob, so the JS itself is static and
period-agnostic — only the data and labels change.
"""
import json, os
import build
from build import (g, micros, money, money2, conv, intf, pct, esc, short_name, tip,
                   is_branded, agg_spend, agg_demos, sum_group,
                   status_cpd, status_demos, CPD_TARGET,
                   clicks_daily_data, clicks_section_html, CLICKS_JS, CLICKS_DAYS)

ROOT = build.ROOT

# Chart.js 4.5.0 UMD — SRI computed from the actual served bytes (see CLAUDE.md
# "Weekly report"). crossorigin=anonymous is required for SRI to be enforced.
CHARTJS_SRC = "https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.min.js"
CHARTJS_SRI = "sha384-XcdcwHqIPULERb2yDEM4R0XaQKU3YnDsrTmjACBZyfdVVqjh6xQ4/DCMd7XLcA6Y"

# Brand palette for chart series.
C_BLUE, C_PINK, C_NAVY, C_TEAL, C_AMBER, C_VIOLET, C_GREEN = (
    "#3185FC", "#FF4998", "#162050", "#0EA5A4", "#F59E0B", "#8B5CF6", "#10B981")
PALETTE = [C_BLUE, C_PINK, C_TEAL, C_AMBER, C_VIOLET, C_GREEN, C_NAVY, "#EF4444"]


# ── small numeric helpers ─────────────────────────────────────────────────────
def avg_cpc(cost, clicks):
    return (cost / clicks) if clicks else None


def delta_badge(cur, prior, lower_is_better=False):
    """A small ▲/▼ % WoW (or MoM) badge. Color = good/bad, not direction:
    for most metrics up is good; for CPD and Rank-Lost IS down is good."""
    if cur is None or prior is None or prior == 0:
        return '<span class="delta flat">—</span>'
    change = (cur - prior) / prior * 100.0
    if abs(change) < 0.5:
        return '<span class="delta flat">■ 0%</span>'
    up = cur > prior
    good = (cur < prior) if lower_is_better else (cur > prior)
    arrow = "▲" if up else "▼"
    cls = "good" if good else "bad"
    return f'<span class="delta {cls}">{arrow} {abs(change):.0f}%</span>'


# ── per-campaign / per-ad-group aggregation ───────────────────────────────────
def metric_rows(rows, key_fn):
    """Aggregate campaign/ad_group metric rows by key. Impr-share & rank-lost are
    cost-weighted (a no-op when there's one row per key, as for these queries)."""
    out = {}
    for r in rows:
        k = key_fn(r)
        cost = micros(g(r, "metrics.cost_micros"))
        o = out.setdefault(k, {"cost": 0.0, "clicks": 0, "imps": 0, "isw": 0.0, "rlw": 0.0})
        o["cost"] += cost
        o["clicks"] += int(g(r, "metrics.clicks") or 0)
        o["imps"] += int(g(r, "metrics.impressions") or 0)
        o["isw"] += float(g(r, "metrics.search_impression_share") or 0) * cost
        o["rlw"] += float(g(r, "metrics.search_rank_lost_impression_share") or 0) * cost
    for o in out.values():
        o["isr"] = (o["isw"] / o["cost"]) if o["cost"] > 0 else None
        o["rl"] = (o["rlw"] / o["cost"]) if o["cost"] > 0 else None
        o["cpc"] = avg_cpc(o["cost"], o["clicks"])
    return out


def demos_by_campaign(rows):
    out = {}
    for r in rows:
        n = g(r, "campaign.name") or ""
        v = g(r, "metrics.all_conversions") or g(r, "metrics.conversions") or 0
        out[n] = out.get(n, 0.0) + float(v)
    return out


def demos_by_adgroup(rows):
    out = {}
    for r in rows:
        k = (g(r, "campaign.name") or "", g(r, "ad_group.name") or "")
        v = g(r, "metrics.all_conversions") or g(r, "metrics.conversions") or 0
        out[k] = out.get(k, 0.0) + float(v)
    return out


# ── Brain Day-0 cohort demo override (weekly/monthly only) ─────────────────────
# If the period's routine wrote data/<dir>/cohort_demos.json (Brain MCP, Paid
# Media Optimizer, Cohort basis), the report swaps Google-Ads platform demos for
# Brain Day-0 cohort demos (same-day-as-click, apples-to-apples, never matures).
# The daily report uses build.py's own template and never has this file, so it is
# unaffected. Monthly has no cohort file unless its routine creates one.
def load_cohort_demos(d):
    """Return the cohort_demos.json dict for data dir `d`, or None if absent.
    build.load() returns [] when the file is missing (falsy) and the dict when
    present (truthy)."""
    obj = build.load(d, "cohort_demos.json")
    return obj or None


def cohort_buckets(cohort, n_buckets):
    """Build (per, allb, exclb) demo-trend buckets from a cohort_demos.json
    'series' map: {report-campaign-name -> [n weekly Day-0 demo sums]}. allb sums
    every campaign; exclb sums the non-branded ones (mirrors bucket_demos)."""
    series = cohort.get("series", {})
    per, allb, exclb = {}, [0.0] * n_buckets, [0.0] * n_buckets
    for name, arr in series.items():
        vals = [(arr[i] if i < len(arr) else 0.0) for i in range(n_buckets)]
        per[name] = vals
        for i in range(n_buckets):
            allb[i] += vals[i]
            if not is_branded(name):
                exclb[i] += vals[i]
    return per, allb, exclb


# ── chart data bucketing ──────────────────────────────────────────────────────
def bucket_demos(rows, idx_of, n_buckets, date_field="segments.date"):
    per, allb, exclb = {}, [0.0] * n_buckets, [0.0] * n_buckets
    for r in rows:
        i = idx_of(g(r, date_field) or "")
        if i is None:
            continue
        n = g(r, "campaign.name") or ""
        v = float(g(r, "metrics.all_conversions") or g(r, "metrics.conversions") or 0)
        per.setdefault(n, [0.0] * n_buckets)[i] += v
        allb[i] += v
        if not is_branded(n):
            exclb[i] += v
    return per, allb, exclb


def bucket_metrics(rows, idx_of, n_buckets, date_field="segments.date"):
    spend = [0.0] * n_buckets; clicks = [0.0] * n_buckets; imps = [0.0] * n_buckets
    isw = [0.0] * n_buckets; rlw = [0.0] * n_buckets; cw = [0.0] * n_buckets
    for r in rows:
        i = idx_of(g(r, date_field) or "")
        if i is None:
            continue
        cost = micros(g(r, "metrics.cost_micros"))
        spend[i] += cost
        clicks[i] += int(g(r, "metrics.clicks") or 0)
        imps[i] += int(g(r, "metrics.impressions") or 0)
        isw[i] += float(g(r, "metrics.search_impression_share") or 0) * cost
        rlw[i] += float(g(r, "metrics.search_rank_lost_impression_share") or 0) * cost
        cw[i] += cost
    def wavg(w):
        return [round(w[i] / cw[i] * 100, 1) if cw[i] > 0 else None for i in range(n_buckets)]
    return {
        "spend": [round(x) for x in spend],
        "clicks": [round(x) for x in clicks],
        "impressions": [round(x) for x in imps],
        "impr_share": wavg(isw),
        "rank_lost": wavg(rlw),
    }


def bucket_clicks_data(series_metrics, demo_series, idx_of, n_buckets, labels, date_field="segments.date",
                       avg=None):
    """Build the SAME per-entity structure clicks_daily_data() returns, but bucketed
    into the report's 12 week/month buckets and keyed by campaign (no ad-group split
    in weekly/monthly). Fed to the SHARED CLICKS_JS so weekly/monthly charts get the
    daily metric-toggle chips: Demos · Clicks · Impressions · Spend · CPC · CPD ·
    Rank-Lost IS · Impr Share. demo_series = {campaign_name: [n_buckets demos]} (Brain
    Day-0 cohort when present, else bucketed platform demos). `avg` = {n,label} enables
    the 〜 moving-average chip (weekly passes 4-week, monthly 3-month; None → no chip)."""
    camps = {}
    for r in series_metrics:
        i = idx_of(g(r, date_field) or "")
        if i is None:
            continue
        n = g(r, "campaign.name") or ""
        c = camps.setdefault(n, {"clicks": [0] * n_buckets, "cost": [0.0] * n_buckets,
                                 "imps": [0] * n_buckets, "isw": [0.0] * n_buckets,
                                 "rlw": [0.0] * n_buckets, "cw": [0.0] * n_buckets})
        cost = micros(g(r, "metrics.cost_micros"))
        c["clicks"][i] += int(g(r, "metrics.clicks") or 0)
        c["cost"][i] += cost
        c["imps"][i] += int(g(r, "metrics.impressions") or 0)
        c["isw"][i] += float(g(r, "metrics.search_impression_share") or 0) * cost
        c["rlw"][i] += float(g(r, "metrics.search_rank_lost_impression_share") or 0) * cost
        c["cw"][i] += cost

    demo_series = demo_series or {}
    def demos_of(n):
        a = demo_series.get(n) or []
        return [round(a[i], 2) if i < len(a) else 0.0 for i in range(n_buckets)]
    def is_series(c):  return [round(c["isw"][i] / c["cw"][i], 4) if c["cw"][i] > 0 else None for i in range(n_buckets)]
    def rl_series(c):  return [round(c["rlw"][i] / c["cw"][i], 4) if c["cw"][i] > 0 else None for i in range(n_buckets)]
    def ic(a):  return [int(round(v)) for v in a]
    def rc(a):  return [round(v, 2) for v in a]

    allnames = list(camps.keys())
    nonbrand = [n for n in allnames if not is_branded(n)]
    has_demos = any(v > 0 for n in camps for v in demos_of(n))
    has_cost = any(v > 0 for n in camps for v in camps[n]["cost"])
    has_imps = any(v > 0 for n in camps for v in camps[n]["imps"])
    has_rankis = any(camps[n]["cw"][i] > 0 for n in camps for i in range(n_buckets))

    def sum_key(names, key):
        return [sum(camps[n][key][i] for n in names) for i in range(n_buckets)]
    def sum_demos(names):
        return [sum(demos_of(n)[i] for n in names) for i in range(n_buckets)]
    def wavg(names, num_key):   # cost-weighted IS-style fraction across `names`, per bucket
        out = [None] * n_buckets
        for i in range(n_buckets):
            num = sum(camps[n][num_key][i] for n in names)
            den = sum(camps[n]["cw"][i] for n in names)
            out[i] = round(num / den, 4) if den > 0 else None
        return out

    corder = sorted(camps, key=lambda n: sum(camps[n]["cost"]), reverse=True)
    N = max(len(corder), 1)

    all_ds = [{"label": "All campaigns", "color": "#3185FC", "total": True,
               "clicks": ic(sum_key(allnames, "clicks")), "demos": rc(sum_demos(allnames)),
               "cost": rc(sum_key(allnames, "cost")), "imps": ic(sum_key(allnames, "imps")),
               "rankis": wavg(allnames, "rlw"), "imprshare": wavg(allnames, "isw")}]
    if nonbrand:
        all_ds.append({"label": "Ex-Branded", "color": "#FF4998", "emph": True,
                       "clicks": ic(sum_key(nonbrand, "clicks")), "demos": rc(sum_demos(nonbrand)),
                       "cost": rc(sum_key(nonbrand, "cost")), "imps": ic(sum_key(nonbrand, "imps")),
                       "rankis": wavg(nonbrand, "rlw"), "imprshare": wavg(nonbrand, "isw")})
    for ci, n in enumerate(corder):
        hue = round(ci * 360 / N); c = camps[n]
        all_ds.append({"label": short_name(n), "color": f"hsl({hue},62%,52%)",
                       "clicks": ic(c["clicks"]), "demos": demos_of(n), "cost": rc(c["cost"]),
                       "imps": ic(c["imps"]), "rankis": rl_series(c), "imprshare": is_series(c)})
    out = [{"id": "clk-all", "name": "All Campaigns", "datasets": all_ds, "span": True}]

    for ci, n in enumerate(corder):
        c = camps[n]
        out.append({"id": f"clk-{ci}", "name": short_name(n), "datasets": [
            {"label": short_name(n), "color": "#3185FC", "total": True,
             "clicks": ic(c["clicks"]), "demos": demos_of(n), "cost": rc(c["cost"]),
             "imps": ic(c["imps"]), "rankis": rl_series(c), "imprshare": is_series(c)}]})

    res = {"labels": labels, "has_demos": has_demos, "has_cost": has_cost,
           "has_rankis": has_rankis, "has_imps": has_imps, "has_imprshare": has_rankis,
           "has_ctd": has_demos,   # Click→Demo conversion-rate chip (demos ÷ clicks, %) — needs demos
           "style": {"clicks": "#162050", "demos": "#3185FC", "cpc": "#0D9488", "cpd": "#7C3AED",
                     "rankis": "#B45309", "imps": "#64748B", "imprshare": "#15803D", "spend": "#EA580C",
                     "ctd": "#DB2777"},
           "campaigns": out}
    if avg:
        res["avg"] = avg   # 〜 moving-average chip ({n,label}); absent → no chip
    return res


# ── HTML fragments ────────────────────────────────────────────────────────────
def summary_table(rows, this_label, prior_label):
    # 4th element `d` is the prebuilt Difference cell HTML (this − prior), NOT escaped.
    trs = "".join(
        f'<tr><td class="metric-name">{esc(m)}</td>'
        f'<td class="num">{c}</td><td class="num">{p}</td>'
        f'<td class="num">{d}</td><td class="status">{st}</td></tr>'
        for (m, c, p, d, st) in rows)
    return (f'<table><thead><tr><th style="width:34%">Metric</th>'
            f'<th class="num">{esc(this_label)}&dagger;</th>'
            f'<th class="num">{esc(prior_label)}&dagger;</th>'
            f'<th class="num">Difference</th><th class="status">Status</th></tr></thead>'
            f'<tbody>{trs}</tbody></table>')


def diff_html(cur, prior, kind="money", lower_is_better=False, neutral=False):
    """Signed difference (cur − prior) as a colored span for the summary Difference
    column. kind: 'money' | 'int' | 'conv'. Color: green=good/red=bad unless neutral."""
    if cur is None or prior is None:
        return '<span class="delta flat">—</span>'
    dv = cur - prior
    mag = abs(dv)
    sign = "+" if dv >= 0 else "−"   # real minus sign
    if kind == "money":
        body = sign + "$" + format(int(round(mag)), ",d"); zero = round(mag) == 0
    elif kind == "int":
        body = sign + format(int(round(mag)), ",d"); zero = round(mag) == 0
    else:  # conv (demos — may be fractional)
        body = sign + (str(int(round(mag))) if abs(mag - round(mag)) < 0.05 else format(mag, ".1f"))
        zero = mag < 0.05
    if zero:
        return '<span class="delta flat">0</span>'
    cls = "flat" if neutral else ("good" if ((dv < 0) if lower_is_better else (dv > 0)) else "bad")
    return f'<span class="delta {cls}">{body}</span>'


def _cell(value_html, prior_html, badge_html):
    """A per-campaign metric cell: the current value, then a small line with the
    PRIOR-period actual ('prev N') and the WoW/MoM delta badge."""
    pv = (f'<span class="prev">prev {prior_html}</span>'
          if prior_html not in (None, "", "—") else "")
    return (f'<td class="num"><div class="cellv">{value_html}</div>'
            f'<div class="cellmeta">{pv}{badge_html}</div></td>')


def metric_cells(cur, demos_cur, prior, demos_prior, demos_na=False):
    """Return the 8 metric <td> cells (Spend → Cost/Demo) for one campaign or
    ad group, each with its WoW/MoM delta badge. `cur`/`prior` are metric dicts;
    demos are floats (0 if none). When `demos_na` is True (cohort mode at the
    ad-group level — the Brain cohort cube has no ad-group split), the Demos and
    Cost/Demo cells render '—' instead of a misleading 0."""
    cpd = (cur["cost"] / demos_cur) if demos_cur > 0 else None
    pcpd = (prior["cost"] / demos_prior) if (prior and demos_prior > 0) else None
    P = prior or {}
    cells = [
        _cell(money(cur["cost"]),  money(P.get("cost")),  delta_badge(cur["cost"], P.get("cost"))),
        _cell(intf(cur["clicks"]), intf(P.get("clicks")), delta_badge(cur["clicks"], P.get("clicks"))),
        _cell(money2(cur["cpc"]),  money2(P.get("cpc")),  delta_badge(cur["cpc"], P.get("cpc"), lower_is_better=True)),
        _cell(intf(cur["imps"]),   intf(P.get("imps")),   delta_badge(cur["imps"], P.get("imps"))),
        _cell(pct(cur["isr"]),     pct(P.get("isr")),     delta_badge(cur["isr"], P.get("isr"))),
        _cell(pct(cur["rl"]),      pct(P.get("rl")),      delta_badge(cur["rl"], P.get("rl"), lower_is_better=True)),
    ]
    if demos_na:
        cells += ['<td class="num"><div class="cellv">—</div></td>',
                  '<td class="num"><div class="cellv">—</div></td>']
    else:
        pdem = demos_prior if prior else None
        cells += [
            _cell(conv(demos_cur), (conv(pdem) if pdem is not None else "—"), delta_badge(demos_cur, pdem)),
            _cell(money(cpd),      money(pcpd),                                delta_badge(cpd, pcpd, lower_is_better=True)),
        ]
    return "".join(cells)


def per_campaign_table(camp_cur, camp_prior, cdem_cur, cdem_prior,
                       ag_cur, ag_prior, adem_cur, adem_prior, cohort_mode=False):
    cm_cur = metric_rows(camp_cur, lambda r: g(r, "campaign.name") or "")
    cm_prior = metric_rows(camp_prior, lambda r: g(r, "campaign.name") or "")
    ag_cur_m = metric_rows(ag_cur, lambda r: (g(r, "campaign.name") or "", g(r, "ad_group.name") or ""))
    ag_prior_m = metric_rows(ag_prior, lambda r: (g(r, "campaign.name") or "", g(r, "ad_group.name") or ""))

    # active = impressions > 0 in the reported period
    active = [(n, o) for n, o in cm_cur.items() if o["imps"] > 0]
    active.sort(key=lambda x: x[1]["cost"], reverse=True)

    body = ""
    for ci, (name, cur) in enumerate(active):
        prior = cm_prior.get(name)
        cells = metric_cells(cur, cdem_cur.get(name, 0.0), prior, cdem_prior.get(name, 0.0))
        # child ad groups for this campaign, active, by spend desc
        children = [((cn, an), o) for (cn, an), o in ag_cur_m.items() if cn == name and o["imps"] > 0]
        children.sort(key=lambda x: x[1]["cost"], reverse=True)
        has_kids = bool(children)
        toggle = (f'<button class="toggle" data-camp="c{ci}" aria-label="expand">+</button>'
                  if has_kids else '<span class="toggle-spacer"></span>')
        body += (f'<tr class="camp-row">'
                 f'<td class="camp-name">{toggle}{esc(short_name(name))}</td>{cells}</tr>')
        for (ck, cur_ag) in children:
            cn, an = ck
            cells_ag = metric_cells(cur_ag, adem_cur.get(ck, 0.0),
                                    ag_prior_m.get(ck), adem_prior.get(ck, 0.0),
                                    demos_na=cohort_mode)
            body += (f'<tr class="ag-row c{ci}">'
                     f'<td class="ag-name">↳ {esc(an)}</td>{cells_ag}</tr>')

    head = ('<tr><th>Campaign / Ad Group</th><th class="num">Spend</th>'
            '<th class="num">Clicks</th><th class="num">Avg CPC</th>'
            '<th class="num">Impr.</th><th class="num">Impr. Share</th>'
            '<th class="num">Rank-Lost IS</th><th class="num">Demos</th>'
            '<th class="num">Cost/Demo</th></tr>')
    return f'<table class="perc"><thead>{head}</thead><tbody>{body}</tbody></table>', len(active)


# ── main render ───────────────────────────────────────────────────────────────
def render(cfg):
    d = cfg["data_dir"]
    n_buckets = len(cfg["bucket_labels"])
    idx_of = cfg["bucket_index"]
    period = cfg["period"]              # "Weekly" / "Monthly"
    unit = cfg["unit"]                  # "Week" / "Month"

    camp_cur = build.load(d, "camp_cur.json")
    camp_prior = build.load(d, "camp_prior.json")
    cdem_cur = demos_by_campaign(build.load(d, "camp_demos_cur.json"))
    cdem_prior = demos_by_campaign(build.load(d, "camp_demos_prior.json"))
    ag_cur = build.load(d, "ag_cur.json")
    ag_prior = build.load(d, "ag_prior.json")
    adem_cur = demos_by_adgroup(build.load(d, "ag_demos_cur.json"))
    adem_prior = demos_by_adgroup(build.load(d, "ag_demos_prior.json"))
    series_metrics = build.load(d, "series_metrics.json")
    series_demos = build.load(d, "series_demos.json")

    # ── Brain Day-0 cohort demo override (weekly/monthly only) ────────────────
    # If the routine wrote cohort_demos.json, demos + CPD + the demo trend use
    # Brain Day-0 cohort numbers (same-day-as-click, apples-to-apples, never
    # matures) instead of maturation-biased Google-Ads platform conversions.
    cohort = load_cohort_demos(d)
    if cohort:
        cdem_cur = dict(cohort.get("campaign_cur", {}))
        cdem_prior = dict(cohort.get("campaign_prior", {}))
    demo_label = "Demos (Day-0 cohort)" if cohort else "Demos Scheduled"

    # ── summary tables (This vs Prior) ────────────────────────────────────────
    sc = agg_spend(camp_cur); sp = agg_spend(camp_prior)
    def summary(excl):
        s, dm, cpd = sum_group(sc, cdem_cur, excl)
        ps, pdm, pcpd = sum_group(sp, cdem_prior, excl)
        return [
            ("Spend", money(s), money(ps), diff_html(s, ps, "money", neutral=True), "✅"),
            (demo_label, conv(dm), conv(pdm), diff_html(dm, pdm, "conv"), status_demos(dm, pdm)),
            ("Cost Per Demo", money(cpd), money(pcpd),
             diff_html(cpd, pcpd, "money", lower_is_better=True), status_cpd(cpd)),
        ]
    all_tbl = summary_table(summary(False), cfg["this_label"], cfg["prior_label"])
    excl_tbl = summary_table(summary(True), cfg["this_label"], cfg["prior_label"])

    # ── per-campaign expandable table ─────────────────────────────────────────
    perc_tbl, n_active = per_campaign_table(
        camp_cur, camp_prior, cdem_cur, cdem_prior,
        ag_cur, ag_prior, adem_cur, adem_prior, cohort_mode=bool(cohort))

    # ── chart: ONE per-campaign metric-toggle trend (SAME chips as the daily report)
    # Replaces the old demo-bars / combined-metrics / metric-detail / 30-day-clicks
    # sections with a single section whose charts each toggle Demos · Clicks ·
    # Impressions · Spend · CPC · CPD · Rank-Lost IS · Impr Share over the buckets.
    df = cfg.get("series_date_field", "segments.date")
    per, allb, exclb = bucket_demos(series_demos, idx_of, n_buckets, df)
    if cohort:
        per, allb, exclb = cohort_buckets(cohort, n_buckets)
    metrics = bucket_metrics(series_metrics, idx_of, n_buckets, df)   # kept for the sanity line

    avg = {"n": 4, "label": "4-week avg"} if unit == "Week" else {"n": 3, "label": "3-month avg"}
    perf = bucket_clicks_data(series_metrics, per, idx_of, n_buckets, cfg["bucket_labels"], df, avg=avg)
    clicks_json = json.dumps(perf, separators=(",", ":"))
    clicks_js = CLICKS_JS
    clicks_section = (
        '<div class="card accordion">'
        f'<h2>Performance by {unit} <span class="dt">— last {n_buckets} {unit.lower()}s · '
        'toggle the metric · overlay campaigns</span>' + tip(
        '<p>First chart: <b>All Campaigns</b> total &mdash; toggle '
        'the <b>Ex-Branded</b> chip or any campaign chip to overlay it. Then one chart per campaign. Each chart '
        'toggles <b>Demos</b> &middot; <b>Clicks</b> &middot; <b>Impressions</b> &middot; <b>Spend</b> &middot; '
        '<b>CPC</b> &middot; <b>CPD</b> &middot; <b>Rank-Lost IS</b> &middot; <b>Impr Share</b>. '
        f'The <b>&#12316; {avg["label"]}</b> chip overlays a dashed trend line '
        '(ratio metrics use rolling totals, so low-volume buckets don\'t distort it).</p>') +
        '<span class="chev">▼</span></h2>'
        '<div class="acc-body">'
        '<div class="clk-grid" id="clkGrid"></div></div></div>')

    # totals for the sanity line
    all_s, all_d, all_cpd = sum_group(sc, cdem_cur, False)
    ex_s, ex_d, ex_cpd = sum_group(sc, cdem_cur, True)

    # ── demo-basis pill + footnote — source chain Brain → Salesforce → platform ──
    # cohort_demos.json carries data_source ("brain" | "salesforce"; absent = brain for
    # back-compat); no file = platform (last resort). A pill at the top of the page
    # ALWAYS names the demo data source.
    ul = unit.lower()
    basis_src = (cohort.get("data_source", "brain") if cohort else "platform")
    if cohort:
        unattr = cohort.get("unattributed_series") or []
        unattr_last = unattr[-1] if unattr else 0
        extra = (f"~{conv(unattr_last)} more Day-0 demos this {ul} matched paid Google spend but not "
                 f"these {n_active} campaigns. " if unattr_last else "")
    if basis_src == "salesforce":
        basis_note = '<span class="pill pill-sf">Demos: Salesforce Day-0 cohort (Brain unavailable)</span>'
        demo_footnote = (
            '<p>&dagger; <b>Demos = Salesforce Day-0 cohort</b> — the Brain was unreachable this run, so demos '
            'come straight from Salesforce (read-only): leads whose demo-scheduled date equals their lead-created '
            'date (David&rsquo;s CDF1 formula, <code>Original_Demo_Scheduled_At__c</code> basis), attached to each '
            'campaign by <code>Lead.campaign_ID__c</code> = the Google Ads <b>campaign ID</b>. Same Day-0 idea as '
            'the Brain cohort but UTM first-click attribution, so it typically runs <b>~10&ndash;25% above</b> the '
            'Brain number — a documented basis difference, not growth. <b>Ad-group rows show '
            '&ldquo;&mdash;&rdquo; for Demos / Cost-Per-Demo</b> &mdash; the cohort has no ad-group split. '
            f'{extra}<b>Prior</b> = the {ul} immediately before. CPD target &le; ${CPD_TARGET:,}.</p>'
        )
    elif basis_src == "brain":
        basis_note = '<span class="pill pill-cohort">Demos: Brain Day-0 cohort</span>'
        demo_footnote = (
            '<p>&dagger; <b>Demos = Brain Day-0 cohort</b> — demos scheduled the <i>same day</i> as the '
            'ad click, pulled from DoorLoop&rsquo;s Brain (Paid Media Optimizer &middot; Cohort basis &middot; '
            '<code>gtm-paid-media-daily-cohort</code>). This is apples-to-apples '
            f'{ul}-over-{ul} and <b>never matures</b> &mdash; unlike Google Ads platform conversions, which '
            f'back-date to the click and keep growing for ~90 days (so recent {ul}s look artificially low). '
            'Demos attach to each campaign by Google Ads <b>campaign ID</b> (the spend-matched campaign), so '
            'the count is consistent with spend. Cohort Day-0 runs ~25-30% below the all-demos-by-booked-date '
            'number in Salesforce (which includes demos booked days after the click). <b>Ad-group rows show '
            '&ldquo;&mdash;&rdquo; for Demos / Cost-Per-Demo</b> &mdash; the cohort cube has no ad-group split. '
            f'{extra}<b>Prior</b> = the {ul} immediately before. CPD target &le; ${CPD_TARGET:,}.</p>'
        )
    else:
        basis_note = ('<span class="pill pill-platform">Demos: Google Ads platform conversions '
                      '(Brain + Salesforce unavailable)</span>')
        demo_footnote = (
            '<p>&dagger; <b>Demos</b> = Google Ads conversions for <i>Demo Scheduled Salesforce '
            'Conversion</i> + <i>2. Demo Scheduled</i>, counted by conversion date. <b>Prior</b> = the '
            f'{ul} immediately before. These are Google-Ads-reported demos and will differ from '
            'the Brain Day-0 cohort numbers in the full dashboard. <i>(Last-resort basis: both the Brain '
            f'Day-0 cohort and the Salesforce fallback were unavailable this run.)</i> '
            f'CPD target &le; ${CPD_TARGET:,}.</p>'
        )

    # Δ-badge explainer → tooltip on the Per-Campaign title; demo footnote → tooltip on the basis pill
    perc_tip = tip(f'<p>Δ badge compares this {unit.lower()} vs the prior {unit.lower()}. Green = good, '
                   'red = bad (for Cost/Demo, Avg CPC and Rank-Lost IS, <i>down</i> is good). '
                   'Sorted by spend. Ad groups indented.</p>')
    basis_note = basis_note + tip(demo_footnote)

    html = PAGE.format(
        period=period, unit=unit, unit_lower=unit.lower(),
        headline=esc(cfg["headline"]), compare_str=esc(cfg["compare_str"]),
        generated=esc(cfg["generated"]),
        this_label=esc(cfg["this_label"]), prior_label=esc(cfg["prior_label"]),
        all_table=all_tbl, excl_table=excl_tbl, perc_table=perc_tbl, perc_tip=perc_tip,
        n_active=n_active, n_buckets=n_buckets,
        cpd_target=f"{CPD_TARGET:,}",
        chartjs_src=CHARTJS_SRC, chartjs_sri=CHARTJS_SRI,
        clicks_section=clicks_section, clicks_json=clicks_json, clicks_js=clicks_js,
        line_labels_js=build.LINE_LABELS_JS,
        basis_note=basis_note, demo_footnote=demo_footnote,
    )

    out_dir = os.path.join(ROOT, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, cfg["out_name"])
    with open(out_path, "w") as f:
        f.write(html)

    # ── sanity line ───────────────────────────────────────────────────────────
    last = metrics["spend"][-1]
    print("Wrote", out_path)
    print(f"  This {unit} — All: spend={money(all_s)} demos={conv(all_d)} cpd={money(all_cpd)}")
    print(f"  This {unit} — Excl: spend={money(ex_s)} demos={conv(ex_d)} cpd={money(ex_cpd)}")
    print(f"  Active campaigns: {n_active} | ad groups (cur, imps>0): "
          f"{sum(1 for r in ag_cur if (g(r,'metrics.impressions') or 0) > 0)}")
    print(f"  12-bucket demos (All): {[round(x,1) for x in allb]}")
    print(f"  Last bucket spend (All): {money(last)}")
    return out_path


# ── page template (CSS + JS are static; data comes from the JSON blob) ────────
PAGE = r"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="googlebot" content="noindex, nofollow">
<title>{headline}</title>
<style>
  :root{{color-scheme:light;--core-blue:#3185FC;--navy:#162050;--neon:#DFFE02;--pink:#FF4998;
    --gray-100:#F5F7FA;--gray-200:#E4E8EE;--gray-300:#C9D0DA;--gray-500:#6B7280;--gray-700:#374151;
    --warn:#B45309;--warn-bg:#FEF3C7;--err:#B91C1C;--err-bg:#FEE2E2;--ok:#047857;--ok-bg:#D1FAE5;
    --shadow:0 1px 3px rgba(22,32,80,.08),0 1px 2px rgba(22,32,80,.05);
    --bg-page:#F5F7FA;--bg-card:#FFFFFF;--text-primary:#162050;--text-secondary:#374151;
    --text-muted:#6B7280;--border:#E4E8EE;--table-hover:#F5F7FA;--ag-bg:#FAFBFD;}}
  [data-theme="dark"]{{color-scheme:dark;--bg-page:#0F1117;--bg-card:#1A1D2E;--text-primary:#E8EAF0;
    --text-secondary:#B0B7C3;--text-muted:#8A93A6;--border:#2D3148;--gray-200:#2D3148;
    --table-hover:#222540;--ag-bg:#15182a;--shadow:0 1px 3px rgba(0,0,0,.4);
    --warn-bg:#3a2e0d;--err-bg:#3a1414;--ok-bg:#0f2e22;}}
  *{{box-sizing:border-box}}
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:var(--bg-page);color:var(--text-primary);font-size:14px;line-height:1.45}}
  .wrap{{max-width:1280px;margin:0 auto;padding:22px 22px 60px}}
  header{{display:flex;flex-wrap:wrap;align-items:center;gap:14px}}
  h1{{font-size:20px;margin:0;font-weight:700;letter-spacing:-.01em}}
  .pill{{display:inline-block;font-size:11px;padding:2px 8px;border-radius:99px;background:var(--gray-200);
    color:var(--text-secondary);margin-left:6px;vertical-align:middle}}
  .pill-cohort{{background:var(--core-blue);color:#fff}}
  .pill-sf{{background:#B45309;color:#fff}}
  .pill-platform{{background:#64748B;color:#fff}}
  .tip{{position:relative;display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;
    border-radius:50%;border:1px solid var(--border);color:var(--text-muted);font:italic 700 10px Georgia,serif;
    cursor:help;margin-left:7px;vertical-align:middle;user-select:none}}
  .tip:hover,.tip:focus{{background:var(--core-blue);border-color:var(--core-blue);color:#fff;outline:none}}
  .tip .tipbox{{display:none;position:absolute;z-index:60;top:150%;left:0;width:380px;max-width:80vw;
    background:var(--bg-card);color:var(--text-secondary);border:1px solid var(--border);border-radius:10px;
    box-shadow:var(--shadow);padding:11px 13px;font:400 12px/1.5 inherit;text-align:left;cursor:auto;white-space:normal}}
  .tip.tip-r .tipbox{{left:auto;right:0}}
  .tip:hover .tipbox,.tip:focus .tipbox{{display:block}}
  .tip .tipbox p{{margin:0 0 7px}} .tip .tipbox p:last-child{{margin:0}}
  .tip .tipbox b{{color:var(--text-primary)}}
  .sub{{color:var(--text-muted);font-size:12.5px;margin:4px 0 20px}}
  #themeBtn{{margin-left:auto;cursor:pointer;border:1px solid var(--border);background:var(--bg-card);
    color:var(--text-primary);border-radius:8px;padding:7px 12px;font-size:13px;font-weight:600}}
  .card{{background:var(--bg-card);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);
    padding:16px 18px;margin-bottom:20px}}
  .card h2{{font-size:15px;margin:0 0 12px;font-weight:700}}
  .card h2 .dt{{color:var(--text-muted);font-weight:500;font-size:13px}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
  @media(max-width:820px){{.grid2{{grid-template-columns:1fr}}}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}}
  th{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);font-weight:600}}
  td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
  tr:hover td{{background:var(--table-hover)}}
  .metric-name{{font-weight:600}} .target{{color:var(--text-muted)}} .status{{text-align:center;font-size:15px}}
  /* per-campaign table */
  table.perc td{{padding:7px 10px}}
  .camp-row .camp-name{{font-weight:700}}
  .camp-row:hover td{{background:var(--table-hover)}}
  tr.ag-row{{display:none}}
  tr.ag-row.show{{display:table-row}}
  .ag-row td{{background:var(--ag-bg);font-size:12.5px;color:var(--text-secondary)}}
  .ag-row .ag-name{{padding-left:26px}}
  .cellv{{font-weight:600}}
  .delta{{display:block;font-size:10.5px;font-weight:700;margin-top:1px;font-variant-numeric:tabular-nums}}
  .delta.good{{color:var(--ok)}} .delta.bad{{color:var(--err)}} .delta.flat{{color:var(--text-muted);font-weight:500}}
  .toggle{{cursor:pointer;border:1px solid var(--border);background:var(--bg-card);color:var(--text-primary);
    border-radius:6px;width:20px;height:20px;line-height:1;padding:0;margin-right:8px;font-weight:700;font-size:13px}}
  .toggle-spacer{{display:inline-block;width:20px;margin-right:8px}}
  /* charts */
  .chart-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}
  @media(max-width:900px){{.chart-grid{{grid-template-columns:repeat(2,1fr)}}}}
  @media(max-width:600px){{.chart-grid{{grid-template-columns:1fr}}}}
  /* collapsible full-width sections */
  .card.accordion>h2{{cursor:pointer;display:flex;align-items:center;gap:8px;user-select:none}}
  .card.accordion>h2 .chev{{margin-left:auto;transition:transform .15s ease;font-size:11px;color:var(--text-muted)}}
  .card.accordion.collapsed>h2{{margin-bottom:0}}
  .card.accordion.collapsed>h2 .chev{{transform:rotate(-90deg)}}
  .card.accordion.collapsed>.acc-body{{display:none}}
  .chart-card{{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;padding:12px 12px 8px}}
  .chart-title{{font-size:12.5px;font-weight:700;margin-bottom:6px}}
  .canvas-wrap{{position:relative;height:180px}}
  .canvas-wrap.tall{{height:320px}}
  .chips{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}}
  .chip{{cursor:pointer;border:1px solid var(--border);background:var(--bg-card);color:var(--text-secondary);
    border-radius:99px;padding:5px 12px;font-size:12px;font-weight:600;user-select:none}}
  .chip.on{{background:var(--core-blue);color:#fff;border-color:var(--core-blue)}}
  .chips.clicks{{max-height:78px;overflow-y:auto;padding:2px;margin:2px 0 10px}}
  .chips.clicks .chip{{white-space:nowrap;padding:3px 9px;font-size:11px}}
  .clk-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}}
  @media(max-width:760px){{.clk-grid{{grid-template-columns:1fr}}}}
  .clk-card{{border:1px solid var(--border);border-radius:12px;padding:12px 12px 8px;background:var(--bg-card);min-width:0}}
  .canvas-wrap{{min-width:0}}
  .clk-title{{font-weight:700;font-size:13px;margin-bottom:8px}}
  .canvas-wrap.clk{{height:210px}}
  /* per-chart metric toggle (Demos/Clicks/Impressions/Spend/CPC/CPD/Rank-Lost IS/Impr Share) */
  .metric-toggle{{display:inline-flex;flex-wrap:wrap;gap:0;margin:0 0 8px;border:1px solid var(--border);border-radius:8px;overflow:hidden}}
  .mbtn{{cursor:pointer;font-size:11px;font-weight:600;padding:3px 10px;color:var(--text-secondary);
    background:var(--bg-card);user-select:none;border-right:1px solid var(--border)}}
  .mbtn:last-child{{border-right:none}}
  .mbtn.on{{background:var(--core-blue);color:#fff}}
  .mbtn[data-m="demos"].on{{background:#3185FC;color:#fff}}
  .mbtn[data-m="clicks"].on{{background:#162050;color:#fff}}
  .mbtn[data-m="imps"].on{{background:#64748B;color:#fff}}
  .mbtn[data-m="spend"].on{{background:#EA580C;color:#fff}}
  .mbtn[data-m="cpc"].on{{background:#0D9488;color:#fff}}
  .mbtn[data-m="cpd"].on{{background:#7C3AED;color:#fff}}
  .mbtn[data-m="rankis"].on{{background:#B45309;color:#fff}}
  .mbtn[data-m="imprshare"].on{{background:#15803D;color:#fff}}
  .prev{{color:var(--text-muted);font-size:10px;margin-right:5px;font-weight:600}}
  .cellmeta{{display:block;margin-top:1px;line-height:1.35}}
  .foot{{color:var(--text-muted);font-size:12px;margin-top:6px}} .foot p{{margin:3px 0}}
</style></head>
<body><div class="wrap">
  <header>
    <h1>{headline} <span class="pill">Google Ads only</span>{basis_note}</h1>
    <button id="themeBtn">◐ Theme</button>
  </header>
  <div class="sub">{compare_str} · generated {generated}</div>

  <div class="grid2">
    <div class="card"><h2>All Campaigns</h2>{all_table}</div>
    <div class="card"><h2>Excl. Branded <span class="dt">— without “branded” in the name</span></h2>{excl_table}</div>
  </div>

  <div class="card accordion">
    <h2>Per-Campaign Performance <span class="dt">— {n_active} active campaigns · click <b>+</b> to expand ad groups · value + {unit}-over-{unit} Δ</span>{perc_tip}<span class="chev">▼</span></h2>
    <div class="acc-body">
      {perc_table}
    </div>
  </div>

  {clicks_section}
</div>

<script id="clicks-data" type="application/json">{clicks_json}</script>
<script src="{chartjs_src}" integrity="{chartjs_sri}" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>{line_labels_js}</script>
<script>
(function(){{
  var btn=document.getElementById("themeBtn");
  btn.addEventListener("click",function(){{
    var h=document.documentElement; h.dataset.theme=h.dataset.theme==="dark"?"light":"dark";
  }});
  // expand / collapse ad-group child rows
  document.querySelectorAll(".toggle").forEach(function(t){{
    t.addEventListener("click",function(e){{
      e.stopPropagation();
      var cls=t.getAttribute("data-camp");
      var opening=!t.classList.contains("open");
      t.classList.toggle("open",opening);
      document.querySelectorAll("tr.ag-row."+cls).forEach(function(r){{r.classList.toggle("show",opening);}});
      t.textContent=opening?"–":"+";
    }});
  }});
  // collapse / expand full-width accordion sections
  document.querySelectorAll(".card.accordion>h2").forEach(function(h){{
    h.addEventListener("click",function(){{ h.parentElement.classList.toggle("collapsed"); }});
  }});
}})();
</script>
<script>{clicks_js}</script>
</body></html>"""
