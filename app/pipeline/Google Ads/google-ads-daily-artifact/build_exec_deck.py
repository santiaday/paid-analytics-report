#!/usr/bin/env python3
"""
Executive SLIDE DECK builder for the monthly Google Ads review.

Turns the same monthly data the HTML report uses (data/monthly-<MONTH>/*.json)
PLUS the Notion audit narrative (data/monthly-<MONTH>/audit_highlights.json)
into a self-contained, brand-styled HTML presentation for the exec team.

Design follows the doorloop-html-slide-creator skill: Poppins, DoorLoop palette,
real white/navy logo, hamburger TOC, speaker-notes panel (press N), progressive
section flow, Chart.js trend charts (same Core-Blue bars as the report).

Reuses build.py / report_common.py aggregation so there is ONE source of truth
for the locked rules (demo actions, $2,100 CPD target, literal-"branded", the
stale-ad_group.name → not used here). No network calls.

Works for BOTH the monthly and weekly reports — it reuses build_monthly /
build_weekly to get the exact period config (data dir, 12 time-bucket boundaries,
labels, period wording), so the date math has ONE source of truth.

The narrative slides (headline, 4-numbers, must-act, pipeline funnel, next-period
actions, closing) come from audit_highlights.json — a small file the routine
writes from that period's Notion audit. If audit_highlights.json is absent the
deck still builds in DATA-ONLY mode (title, computed campaign table, charts, and
per-campaign deep dives) so a missing Notion audit never blocks the deck.

CLI:
    python3 build_exec_deck.py monthly <YYYY-MM>      -> outputs/monthly-exec-<YYYY-MM>.html
    python3 build_exec_deck.py weekly  <WEEK_END>     -> outputs/weekly-exec-<WEEK_END>.html
    python3 build_exec_deck.py <YYYY-MM>              (monthly, back-compat)
    python3 build_exec_deck.py                        (monthly, last completed month)
"""
import json, os, sys, datetime
import build
import report_common as rc
import build_weekly, build_monthly
from build import (g, micros, money, money2, conv, intf, pct, esc,
                   short_name, is_branded, CPD_TARGET)

ROOT = build.ROOT
LOGO_WHITE = "https://cdn.prod.website-files.com/5f073e32d304276cc8b4ff30/69d3ea8e32f87c703096f6cd_doorloop-white-logo-transparent.svg"
LOGO_NAVY  = "https://cdn.prod.website-files.com/5f073e32d304276cc8b4ff30/69d3ea8d95b0b381d2acdc36_doorloop-blue-logo-transparent.svg"


def period_labels(period, key, aud):
    """Deck wording for the period. aud (if present) can override the period /
    prior label so the human-written audit controls phrasing."""
    if period == "monthly":
        ms = datetime.datetime.strptime(key + "-01", "%Y-%m-%d").date()
        pm = build_monthly.add_months(ms, -1)
        pl = {"ml": ms.strftime("%B %Y"), "prior": pm.strftime("%B %Y"),
              "review": "Monthly", "unit_word": "months", "unit_sing": "month"}
    else:
        we = datetime.datetime.strptime(key, "%Y-%m-%d").date()
        ws = we - datetime.timedelta(days=6)
        ps = ws - datetime.timedelta(days=7)
        pe = we - datetime.timedelta(days=7)
        same = ws.year == we.year
        ml = (f"Week of {ws.strftime('%b %-d')}–"
              f"{we.strftime('%-d, %Y') if same else we.strftime('%b %-d, %Y')}")
        pl = {"ml": ml, "prior": f"Week of {ps.strftime('%b %-d')}–{pe.strftime('%-d, %Y')}",
              "review": "Weekly", "unit_word": "weeks", "unit_sing": "week"}
    if aud:
        pl["ml"] = aud.get("period_label", aud.get("month_label", pl["ml"]))
        pl["prior"] = aud.get("prior_label", pl["prior"])
    return pl


def compute(cfg):
    d = cfg["data_dir"]
    idx_of = cfg["bucket_index"]
    labels = cfg["bucket_labels"]
    n = len(labels)
    df = cfg.get("series_date_field", "segments.date")
    camp_cur = build.load(d, "camp_cur.json")
    camp_prior = build.load(d, "camp_prior.json")
    cdem_cur = rc.demos_by_campaign(build.load(d, "camp_demos_cur.json"))
    cdem_prior = rc.demos_by_campaign(build.load(d, "camp_demos_prior.json"))
    ag_cur = build.load(d, "ag_cur.json")
    ag_prior = build.load(d, "ag_prior.json")
    adem_cur = rc.demos_by_adgroup(build.load(d, "ag_demos_cur.json"))
    adem_prior = rc.demos_by_adgroup(build.load(d, "ag_demos_prior.json"))
    series_metrics = build.load(d, "series_metrics.json")
    series_demos = build.load(d, "series_demos.json")

    # Brain Day-0 cohort demo override (present only when the routine wrote it).
    cohort = rc.load_cohort_demos(d)
    if cohort:
        cdem_cur = dict(cohort.get("campaign_cur", {}))
        cdem_prior = dict(cohort.get("campaign_prior", {}))

    cm_cur = rc.metric_rows(camp_cur, lambda r: g(r, "campaign.name") or "")
    cm_prior = rc.metric_rows(camp_prior, lambda r: g(r, "campaign.name") or "")
    agm_cur = rc.metric_rows(ag_cur, lambda r: (g(r, "campaign.name") or "", g(r, "ad_group.name") or ""))
    agm_prior = rc.metric_rows(ag_prior, lambda r: (g(r, "campaign.name") or "", g(r, "ad_group.name") or ""))

    per, allb, exclb = rc.bucket_demos(series_demos, idx_of, n, df)
    if cohort:
        per, allb, exclb = rc.cohort_buckets(cohort, n)
    metrics = rc.bucket_metrics(series_metrics, idx_of, n, df)

    # active campaigns by spend desc
    active = sorted([(n, o) for n, o in cm_cur.items() if o["imps"] > 0],
                    key=lambda x: x[1]["cost"], reverse=True)
    campaigns = []
    for name, cur in active:
        prior = cm_prior.get(name) or {}
        dem = cdem_cur.get(name, 0.0)
        pdem = cdem_prior.get(name, 0.0)
        cpd = (cur["cost"] / dem) if dem > 0 else None
        pcpd = (prior.get("cost", 0) / pdem) if pdem > 0 else None
        kids = sorted([((cn, an), o) for (cn, an), o in agm_cur.items()
                       if cn == name and o["imps"] > 0],
                      key=lambda x: x[1]["cost"], reverse=True)
        ad_groups = []
        for (ck, o) in kids:
            adm = adem_cur.get(ck, 0.0)
            ad_groups.append({
                "name": ck[1], "cost": o["cost"], "cpc": o["cpc"], "imps": o["imps"],
                "isr": o["isr"], "rl": o["rl"],
                # cohort cube has no ad-group split → blank ad-group demos/CPD
                "demos": (None if cohort else adm),
                "cpd": (None if cohort else ((o["cost"] / adm) if adm > 0 else None)),
            })
        campaigns.append({
            "name": name, "short": short_name(name), "branded": is_branded(name),
            "cost": cur["cost"], "clicks": cur["clicks"], "cpc": cur["cpc"],
            "imps": cur["imps"], "isr": cur["isr"], "rl": cur["rl"],
            "demos": dem, "cpd": cpd,
            "cpd_badge": rc.delta_badge(cpd, pcpd, lower_is_better=True),
            "spend_badge": rc.delta_badge(cur["cost"], prior.get("cost")),
            "demo_badge": rc.delta_badge(dem, pdem),
            "series": [round(x, 2) for x in per.get(name, [0.0] * n)],
            "ad_groups": ad_groups,
        })

    return {
        "campaigns": campaigns, "labels": labels,
        "demo_all": [round(x, 2) for x in allb],
        "demo_excl": [round(x, 2) for x in exclb],
        "metrics": metrics,
        "cohort": bool(cohort),
        # demo data source: brain | salesforce | platform (chain Brain → SF → platform)
        "demo_source": (cohort.get("data_source", "brain") if cohort else "platform"),
        "cohort_unattributed": (cohort.get("unattributed_series", [])[-1] if cohort else 0),
    }


# ── HTML helpers ──────────────────────────────────────────────────────────────
def status_class(s):
    return "s-good" if s == "good" else ("s-bad" if s == "bad" else "s-mid")


def kpi_card(label, value, sub=""):
    return (f'<div class="kpi"><div class="kpi-v">{value}</div>'
            f'<div class="kpi-l">{esc(label)}</div>'
            f'{f"<div class=kpi-s>{sub}</div>" if sub else ""}</div>')


def notes(txt):
    return f'<div class="notes">{esc(txt)}</div>'


def slide(bg, inner, note, toc_title, extra_cls=""):
    return (f'<section class="slide bg-{bg} {extra_cls}" data-toc="{esc(toc_title)}">'
            f'{inner}{notes(note)}</section>')


def ag_table(camp, cap=8):
    ags = camp["ad_groups"]
    shown = ags[:cap]
    extra = len(ags) - len(shown)
    head = ('<tr><th>Ad Group</th><th class="num">Spend</th><th class="num">Avg CPC</th>'
            '<th class="num">Impr. Share</th><th class="num">Rank-Lost</th>'
            '<th class="num">Demos</th><th class="num">Cost/Demo</th></tr>')
    rows = ""
    for a in shown:
        cpd_cls = ""
        if a["cpd"] is not None:
            cpd_cls = "over" if a["cpd"] > CPD_TARGET else "under"
        rows += (f'<tr><td class="agn">{esc(a["name"])}</td>'
                 f'<td class="num">{money(a["cost"])}</td>'
                 f'<td class="num">{money2(a["cpc"])}</td>'
                 f'<td class="num">{pct(a["isr"])}</td>'
                 f'<td class="num">{pct(a["rl"])}</td>'
                 f'<td class="num">{conv(a["demos"])}</td>'
                 f'<td class="num {cpd_cls}">{money(a["cpd"])}</td></tr>')
    foot = (f'<div class="tnote">Showing top {len(shown)} of {len(ags)} ad groups by spend '
            f'· {extra} more in the full HTML report.</div>' if extra > 0 else "")
    return f'<table class="agt"><thead>{head}</thead><tbody>{rows}</tbody></table>{foot}'


def render_lines(s, neon=True):
    """'A|B|C' -> 'A<br>B<br>C' with the LAST line neon-underlined."""
    parts = [esc(p) for p in s.split("|")]
    if parts and neon:
        parts[-1] = f'<span class="ul-neon">{parts[-1]}</span>'
    return "<br>".join(parts)


def build_deck(period, key):
    """period in {'monthly','weekly'}; key = 'YYYY-MM' or 'WEEK_END' (Saturday)."""
    cfg = (build_monthly.build_cfg(key) if period == "monthly"
           else build_weekly.build_cfg(key))
    data = compute(cfg)
    dd = cfg["data_dir"]
    ap = os.path.join(ROOT, "data", dd, "audit_highlights.json")
    aud = json.load(open(ap)) if os.path.exists(ap) else None
    P = period_labels(period, key, aud)
    ml, prior = P["ml"], P["prior"]
    uw, us, review = P["unit_word"], P["unit_sing"], P["review"]
    n_unit = len(cfg["bucket_labels"])
    gen = datetime.datetime.now().strftime("%b %-d, %Y")
    NA = "(narrative slides need the Notion audit — running in data-only mode)"

    # demo basis — source chain Brain → Salesforce → platform (always labeled on the title slide)
    cohort = data.get("cohort")
    demo_source = data.get("demo_source", "brain" if cohort else "platform")
    demos_word = "Day-0 cohort demos" if cohort else "demos"
    if demo_source == "salesforce":
        demo_basis_note = " · demos: Salesforce Day-0 cohort (Brain unavailable)"
        demo_def = ("Demos = <b>Salesforce Day-0 cohort</b> (demo scheduled the same day the lead was created — "
                    "CDF1; the Brain was unreachable this run). Attributed by Lead.campaign_ID__c = Google Ads "
                    "campaign ID; runs ~10&ndash;25% above the Brain Day-0 number (attribution basis, not growth). "
                    "Ad-group rows show &ldquo;&mdash;&rdquo; (no ad-group split).")
    elif demo_source == "brain":
        demo_basis_note = " · demos: Brain Day-0 cohort"
        demo_def = ("Demos = <b>Brain Day-0 cohort</b> (scheduled the same day as the click; never matures, "
                    "apples-to-apples). Attributed by Google Ads campaign ID. Ad-group rows show &ldquo;&mdash;&rdquo; "
                    "(the cohort cube has no ad-group split).")
    else:
        demo_basis_note = " · demos: Google Ads platform conversions (Brain + Salesforce unavailable)"
        demo_def = "Demos = Demo Scheduled Salesforce Conversion (platform conversions — last-resort basis)."

    slides = []

    # 1 — Title (Navy) — always
    slides.append(slide("navy",
        f'<img class="logo" src="{LOGO_WHITE}" alt="DoorLoop">'
        f'<div class="title-wrap">'
        f'<div class="eyebrow">{review} Performance Review</div>'
        f'<h1 class="hero">Google Ads<br>{review} <span class="ul">Review.</span></h1>'
        f'<div class="byline">{esc(ml)} · search campaigns{demo_basis_note} · generated {esc(gen)}</div>'
        f'</div><span class="notes-hint">Press N for speaker notes</span>',
        f"Welcome. This is the {ml} Google Ads performance review for the executive team. "
        f"We cover the headline efficiency story, what needs action, a campaign-by-campaign deep dive, and the "
        f"downstream pipeline impact. Spend about 30 seconds here, then advance. Everything traces to the Google "
        f"Ads API and the Notion {period} audit.",
        "Title"))

    # 2 — Headline big text (Core Blue) — narrative
    if aud and aud.get("headline_big"):
        slides.append(slide("blue",
            f'<div class="bigwrap"><div class="eyebrow light">The headline</div>'
            f'<h2 class="big">{render_lines(aud["headline_big"])}</h2>'
            + (f'<div class="big-sub">{esc(aud["headline_sub"])}</div>' if aud.get("headline_sub") else "")
            + '</div>',
            "This is the single takeaway leadership should remember. " + aud.get("headline_sub", "")
            + " Pause here for emphasis before moving into the numbers.",
            "Headline"))

    # 3 — Picture in N numbers (White) — narrative
    if aud and aud.get("four_numbers"):
        cards = "".join(
            f'<div class="num-card {"good" if x.get("good") else "bad"}">'
            f'<div class="nc-v">{esc(x["value"])}</div>'
            f'<div class="nc-l">{esc(x["metric"])}</div>'
            f'<div class="nc-c">{esc(x["change"])} · target {esc(x["target"])}</div></div>'
            for x in aud["four_numbers"])
        slides.append(slide("white",
            f'<img class="logo" src="{LOGO_NAVY}" alt="DoorLoop">'
            f'<h2 class="title">{esc(ml)} in <span class="ul">numbers.</span></h2>'
            f'<div class="num-grid">{cards}</div>',
            f"The {us} in {len(aud['four_numbers'])} numbers. Walk left to right, about 10 seconds each. "
            "Green-bordered cards are on or beating target; red borders are the open problems.",
            "Numbers"))

    # 4 — Must act (White) — narrative, chunked 3 per slide
    def act_list(items, light=False):
        cls = " light" if light else ""
        return "".join(
            f'<li><span class="act-t">{esc(a["title"])}</span>'
            f'<span class="act-d{cls}">{esc(a["detail"])}</span></li>' for a in items)
    if aud and aud.get("must_act"):
        chunks = [aud["must_act"][i:i + 3] for i in range(0, len(aud["must_act"]), 3)]
        for ci, chunk in enumerate(chunks):
            sub = "P0 fixes." if ci == 0 else "next up."
            slides.append(slide("white",
                f'<img class="logo" src="{LOGO_NAVY}" alt="DoorLoop">'
                f'<h2 class="title">Must act — <span class="ul">{sub}</span></h2>'
                f'<ul class="acts">{act_list(chunk)}</ul>',
                "The action core of the meeting. Each item has an owner and a date. Walk through them one at a "
                "time as the bullets reveal; spend about 60 seconds total on this slide.",
                f"Must Act {ci + 1}"))

    # 5 — Section divider: Performance (Navy) — always
    slides.append(slide("navy",
        '<div class="sec"><div class="sec-ey">Section 01</div>'
        '<h2 class="sec-t">Campaign<br><span class="ul">performance.</span></h2></div>',
        "Section break. We move from the summary into the numbers: all-up totals, a campaign-by-campaign view, "
        "then trend charts. Move quickly through this divider.",
        "§ Performance"))

    # 6 — Campaigns at a glance (White) — audit-driven if present, else computed
    if aud and aud.get("campaign_status"):
        crows = "".join(
            f'<tr><td class="cn">{esc(c["name"])}</td>'
            f'<td class="num">{esc(c["spend"])}</td><td class="num">{esc(c["demos"])}</td>'
            f'<td class="num">{esc(c["cpd"])}</td><td class="num">{esc(c.get("prior_cpd", c.get("apr_cpd","—")))}</td>'
            f'<td class="num {status_class(c["status"])}">{esc(c["mom"])}</td>'
            f'<td class="{status_class(c["status"])}">{esc(c["status_label"])}</td></tr>'
            for c in aud["campaign_status"])
        head = (f'<tr><th>Campaign</th><th class="num">Spend</th><th class="num">Demos</th>'
                f'<th class="num">CPD</th><th class="num">Prior CPD</th><th class="num">{"MoM" if period=="monthly" else "WoW"}</th>'
                f'<th>Status</th></tr>')
    else:
        crows = ""
        for c in data["campaigns"]:
            scl = "s-bad" if (c["cpd"] and c["cpd"] > CPD_TARGET and not c["branded"]) else "s-good"
            lbl = ("Excluded" if c["branded"] else
                   ("Over ceiling" if (c["cpd"] and c["cpd"] > CPD_TARGET) else "Under ceiling"))
            crows += (f'<tr><td class="cn">{esc(c["short"])}</td>'
                      f'<td class="num">{money(c["cost"])}</td><td class="num">{conv(c["demos"])}</td>'
                      f'<td class="num">{money(c["cpd"])}</td><td class="num">{c["cpd_badge"]}</td>'
                      f'<td class="{scl}">{lbl}</td></tr>')
        head = ('<tr><th>Campaign</th><th class="num">Spend</th><th class="num">Demos</th>'
                '<th class="num">CPD</th><th class="num">Δ</th><th>Status</th></tr>')
    slides.append(slide("white",
        f'<img class="logo" src="{LOGO_NAVY}" alt="DoorLoop">'
        f'<h2 class="title">Campaigns at a <span class="ul">glance.</span></h2>'
        f'<table class="big-tbl"><thead>{head}</thead><tbody>{crows}</tbody></table>'
        f'<div class="tnote">{demo_def} CPD ceiling ${CPD_TARGET:,}. '
        f'Branded is excluded from the blended non-branded ceiling.</div>',
        "The whole account on one slide, sorted by spend. Green means cost per demo is healthy or improving; "
        "red flags the campaigns over the ceiling. This is the reference table for the deep dives that follow. "
        "Spend about 90 seconds here.",
        "Campaign Table"))

    # 7 — Demo trend charts (White) — always (data-driven)
    slides.append(slide("white",
        f'<img class="logo" src="{LOGO_NAVY}" alt="DoorLoop">'
        f'<h2 class="title">Demand trend — <span class="ul">12 {uw}.</span></h2>'
        f'<div class="chart-row">'
        f'<div class="ch-card"><div class="ch-t">All Campaigns — {demos_word} / {us}</div>'
        f'<div class="cw"><canvas id="c-all"></canvas></div></div>'
        f'<div class="ch-card"><div class="ch-t">Excl. Branded — {demos_word} / {us}</div>'
        f'<div class="cw"><canvas id="c-excl"></canvas></div></div></div>',
        f"Twelve-{us} demo trend. The left chart is all campaigns; the right is non-branded, which feeds "
        f"new-logo pipeline. Point at the trough and the most recent {us} as you talk through the trajectory.",
        "Demo Trend"))

    # 8 — Spend & efficiency combined (White) — always
    slides.append(slide("white",
        f'<img class="logo" src="{LOGO_NAVY}" alt="DoorLoop">'
        f'<h2 class="title">Spend &amp; <span class="ul">efficiency.</span></h2>'
        f'<div class="chips" id="metricChips"></div>'
        f'<div class="cw tall"><canvas id="c-metric"></canvas></div>',
        f"Spend against the efficiency signals — impression share and rank-lost share — over 12 {uw}. Toggle the "
        "chips to layer in clicks, impressions, or the share metrics. Higher impression share with falling "
        "rank-lost means we are capturing more of the auction. Demo the chips live to show interactivity.",
        "Spend & Efficiency"))

    # 9 — Section divider: Deep dives (Navy) — always
    slides.append(slide("navy",
        '<div class="sec"><div class="sec-ey">Section 02</div>'
        '<h2 class="sec-t">Campaign<br><span class="ul">deep dives.</span></h2>'
        '<div class="sec-sub">One slide per campaign · ad groups + trend</div></div>',
        f"Section break into the campaign deep dives. Each slide is one campaign: KPIs, its ad groups as a "
        f"table, and its own 12-{us} demo trend, in spend order. Move quickly through this divider.",
        "§ Deep Dives"))

    # 10..N — one slide per campaign (data-driven; enriched by audit status if present)
    status_by_name = {c["name"]: c for c in (aud["campaign_status"] if aud and aud.get("campaign_status") else [])}
    for i, c in enumerate(data["campaigns"]):
        st = status_by_name.get(c["name"])
        if st:
            badge = f'<span class="pill {status_class(st["status"])}">{esc(st["status_label"])}</span>'
        else:
            over = c["cpd"] and c["cpd"] > CPD_TARGET and not c["branded"]
            badge = (f'<span class="pill {"s-bad" if over else "s-good"}">'
                     f'{"Over ceiling" if over else ("Excluded" if c["branded"] else "Under ceiling")}</span>')
        kpis = (
            f'<div class="kpi-row">'
            f'{kpi_card("Spend", money(c["cost"]), c["spend_badge"])}'
            f'{kpi_card("Demos", conv(c["demos"]), c["demo_badge"])}'
            f'{kpi_card("Cost / Demo", money(c["cpd"]), c["cpd_badge"])}'
            f'{kpi_card("Avg CPC", money2(c["cpc"]))}'
            f'{kpi_card("Impr. Share", pct(c["isr"]))}'
            f'</div>')
        inner = (
            f'<img class="logo" src="{LOGO_NAVY}" alt="DoorLoop">'
            f'<div class="camp-head"><h2 class="title sm">{esc(c["short"])}</h2>{badge}</div>'
            f'{kpis}'
            f'<div class="camp-body">'
            f'<div class="camp-left">{ag_table(c)}</div>'
            f'<div class="camp-right"><div class="ch-t">{demos_word.capitalize()} / {us} — 12 {uw}</div>'
            f'<div class="cw"><canvas id="c-camp-{i}"></canvas></div></div>'
            f'</div>')
        note = (f"{c['short']}: {money(c['cost'])} spend, {conv(c['demos'])} demos, cost per demo {money(c['cpd'])}. "
                + (f"Status: {st['status_label']}, CPD moved {st['mom']}. " if st else "")
                + "The table is ad groups by spend; the chart is this campaign's demo trend. Call out the "
                f"highest-spend ad group and whether its cost per demo is above or below the ${CPD_TARGET:,} "
                "ceiling (red cells are over). Spend about 45 seconds.")
        slides.append(slide("white", inner, note, c["short"], "camp"))

    # N+1/N+2 — Pipeline section + Brain funnel (narrative, optional)
    if aud and aud.get("brain_funnel"):
        slides.append(slide("navy",
            '<div class="sec"><div class="sec-ey">Section 03</div>'
            '<h2 class="sec-t">Pipeline<br><span class="ul">&amp; revenue.</span></h2></div>',
            "Section break into downstream impact — what the demos turned into further down the funnel: MQLs, "
            "opportunities, accounts won, and new ARR. Move quickly through the divider.",
            "§ Pipeline"))
        brows = "".join(
            f'<tr><td class="cn">{esc(b["metric"])}</td>'
            f'<td class="num">{esc(b.get("cur", b.get("may","")))}</td>'
            f'<td class="num">{esc(b.get("prior", b.get("apr","")))}</td>'
            f'<td class="num {"s-good" if b.get("good") else "s-bad"}">{esc(b["mom"])}</td></tr>'
            for b in aud["brain_funnel"])
        delta_col = "MoM" if period == "monthly" else "WoW"
        slides.append(slide("white",
            f'<img class="logo" src="{LOGO_NAVY}" alt="DoorLoop">'
            f'<h2 class="title">{esc(aud.get("brain_title", "Pipeline impact — NonBrand"))}'
            f' <span class="ul">&nbsp;</span></h2>'
            f'<table class="big-tbl"><thead><tr><th>Metric</th><th class="num">{esc(ml)}</th>'
            f'<th class="num">{esc(prior)}</th><th class="num">{delta_col}</th></tr></thead>'
            f'<tbody>{brows}</tbody></table>'
            + (f'<div class="tnote">{esc(aud["brain_note"])}</div>' if aud.get("brain_note") else ""),
            "This is the proof the spend is working — leading demo gains converting into pipeline and ARR. "
            "Read down the green column. This is the slide that justifies the budget.",
            "Pipeline"))

    # N+3 — Next-period actions (Navy) — narrative, optional
    if aud and aud.get("next_actions"):
        label = aud.get("next_actions_label", "Next priorities")
        # underline the last word of the label
        words = label.split()
        head_html = (esc(" ".join(words[:-1])) + ' <span class="ul-neon">' + esc(words[-1]) + '.</span>'
                     if len(words) > 1 else f'<span class="ul-neon">{esc(label)}.</span>')
        jacts = act_list(aud["next_actions"], light=True)
        slides.append(slide("navy",
            f'<img class="logo" src="{LOGO_WHITE}" alt="DoorLoop">'
            f'<h2 class="title light">{head_html}</h2>'
            f'<ul class="acts light">{jacts}</ul>',
            "The forward game plan, in priority order, each with a date and an owner. Close the meeting on this "
            "— it's the commitment slide.",
            "Next Plan"))

    # N+4 — Closing (Core Blue) — always
    closing = aud.get("closing_big") if aud else None
    closing = closing or "Efficiency in focus.|Pipeline tracked.|Clear next steps."
    slides.append(slide("blue",
        f'<img class="logo" src="{LOGO_WHITE}" alt="DoorLoop">'
        f'<div class="bigwrap"><div class="eyebrow light">In one line</div>'
        f'<h2 class="big">{render_lines(closing)}</h2></div>',
        "Close on the through-line and the specific next steps. Thank the room and open for questions.",
        "Close"))

    chart_json = json.dumps({
        "labels": data["labels"],
        "all": data["demo_all"],
        "excl": data["demo_excl"],
        "metrics": data["metrics"],
        "camps": [{"id": f"c-camp-{i}", "series": c["series"]}
                  for i, c in enumerate(data["campaigns"])],
    }, separators=(",", ":"))

    html = (PAGE_HEAD.replace("Google Ads Monthly Review", f"Google Ads {review} Review")
            + "".join(slides)
            + PAGE_FOOT.replace("__CHART_JSON__", chart_json)
                       .replace("__CHARTJS_SRC__", rc.CHARTJS_SRC)
                       .replace("__CHARTJS_SRI__", rc.CHARTJS_SRI))

    out = os.path.join(ROOT, "outputs", f"{period}-exec-{key}.html")
    with open(out, "w") as f:
        f.write(html)

    print("Wrote", out)
    print(f"  Period: {period} {key} | audit: {'yes' if aud else 'NO — '+NA}")
    print(f"  Slides: {len(slides)} | campaigns: {len(data['campaigns'])}")
    print(f"  Campaign deep-dive ad-group counts: "
          + ", ".join(f"{c['short']}={len(c['ad_groups'])}" for c in data["campaigns"]))
    return out


# ── static CSS/markup head ──────────────────────────────────────────────────────
PAGE_HEAD = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex, nofollow"><meta name="googlebot" content="noindex, nofollow">
<title>Google Ads Monthly Review</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{--navy:#162050;--blue:#3185FC;--neon:#DFFE02;--pink:#FF4998;--lblue:#DBEAFF;
    --cloud:#F0EFEB;--white:#FFFFFF;--good:#0E9F6E;--bad:#E11D48;
    --ease:cubic-bezier(.16,1,.3,1);--font:'Poppins',-apple-system,BlinkMacSystemFont,sans-serif;}
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%}
  body{font-family:var(--font);background:#000;color:var(--navy);overflow:hidden}
  .slide{position:fixed;inset:0;height:100vh;height:100dvh;width:100vw;overflow:hidden;
    display:flex;flex-direction:column;justify-content:center;
    padding:clamp(36px,5vw,90px);opacity:0;visibility:hidden;
    transition:opacity .5s var(--ease);z-index:1}
  .slide.active{opacity:1;visibility:visible;z-index:2}
  .bg-navy{background:var(--navy);color:var(--white)}
  .bg-blue{background:var(--blue);color:var(--white)}
  .bg-white{background:var(--white);color:var(--navy)}
  .logo{position:absolute;top:clamp(24px,3vw,46px);left:clamp(32px,4vw,80px);
    height:clamp(22px,2.4vw,30px);width:auto}
  .eyebrow{font-size:clamp(12px,1.3vw,16px);font-weight:600;letter-spacing:.16em;
    text-transform:uppercase;color:var(--blue);margin-bottom:clamp(14px,1.8vw,22px)}
  .eyebrow.light{color:var(--neon)}
  /* hero / big text */
  .title-wrap{max-width:1100px}
  .hero{font-size:clamp(44px,7.4vw,108px);font-weight:800;line-height:1.02;letter-spacing:-.02em}
  .byline{margin-top:clamp(18px,2.4vw,30px);font-size:clamp(13px,1.4vw,18px);
    font-weight:500;color:rgba(255,255,255,.75)}
  .bigwrap{max-width:1200px}
  .big{font-size:clamp(40px,6.6vw,98px);font-weight:800;line-height:1.04;letter-spacing:-.02em}
  .big-sub{margin-top:clamp(16px,2vw,26px);font-size:clamp(16px,1.9vw,26px);
    font-weight:500;color:rgba(255,255,255,.82)}
  /* titles */
  .title{font-size:clamp(30px,4.4vw,62px);font-weight:800;letter-spacing:-.015em;
    line-height:1.04;margin-bottom:clamp(20px,2.6vw,38px)}
  .title.sm{font-size:clamp(26px,3.4vw,46px);margin-bottom:0}
  .title.light{color:var(--white)}
  .ul{position:relative;white-space:nowrap}
  .ul::after{content:'';position:absolute;left:0;bottom:.04em;width:100%;
    height:clamp(7px,.85vw,13px);background:var(--neon);z-index:-1;
    transform:scaleX(0);transform-origin:left;transition:transform .8s var(--ease) .35s}
  .slide.active .ul::after{transform:scaleX(1)}
  .ul-neon{color:var(--neon)}
  /* 4 numbers */
  .num-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:clamp(14px,1.6vw,24px)}
  .num-card{border-radius:18px;padding:clamp(20px,2.4vw,34px);background:var(--cloud);
    border:2px solid transparent;opacity:0;transform:translateY(26px);
    transition:opacity .6s var(--ease),transform .6s var(--ease)}
  .slide.active .num-card{opacity:1;transform:none}
  .num-card.good{border-color:var(--good)} .num-card.bad{border-color:var(--bad)}
  .num-card:nth-child(1){transition-delay:.1s}.num-card:nth-child(2){transition-delay:.2s}
  .num-card:nth-child(3){transition-delay:.3s}.num-card:nth-child(4){transition-delay:.4s}
  .nc-v{font-size:clamp(30px,3.6vw,54px);font-weight:800;letter-spacing:-.02em}
  .nc-l{font-size:clamp(13px,1.4vw,18px);font-weight:600;margin-top:6px}
  .nc-c{font-size:clamp(11px,1.15vw,14px);font-weight:500;color:#5b647a;margin-top:10px}
  /* action lists */
  ul.acts{list-style:none;max-width:1180px;display:flex;flex-direction:column;gap:clamp(12px,1.5vw,20px)}
  ul.acts li{display:flex;flex-direction:column;gap:3px;padding-left:clamp(20px,2vw,30px);
    position:relative;opacity:0;transform:translateX(-22px);
    transition:opacity .5s var(--ease),transform .5s var(--ease)}
  .slide.active ul.acts li.reveal{opacity:1;transform:none}
  ul.acts li::before{content:'';position:absolute;left:0;top:.5em;width:clamp(9px,1vw,13px);
    height:clamp(9px,1vw,13px);border-radius:3px;background:var(--blue)}
  ul.acts.light li::before{background:var(--neon)}
  .act-t{font-size:clamp(17px,1.9vw,26px);font-weight:700;letter-spacing:-.01em}
  .act-d{font-size:clamp(12.5px,1.35vw,17px);font-weight:400;color:#4a5268;line-height:1.45}
  .act-d.light{color:rgba(255,255,255,.78)}
  ul.acts.light .act-t{color:var(--white)}
  /* section dividers */
  .sec{max-width:1100px}
  .sec-ey{font-size:clamp(12px,1.3vw,16px);font-weight:600;letter-spacing:.16em;
    text-transform:uppercase;color:var(--neon);margin-bottom:clamp(16px,2vw,26px)}
  .sec-t{font-size:clamp(40px,6.4vw,96px);font-weight:800;line-height:1.02;letter-spacing:-.02em}
  .sec-sub{margin-top:clamp(16px,2vw,24px);font-size:clamp(13px,1.5vw,20px);
    font-weight:500;color:rgba(255,255,255,.72)}
  /* tables */
  table{width:100%;border-collapse:collapse}
  .big-tbl{font-size:clamp(12px,1.4vw,18px)}
  .big-tbl th,.big-tbl td{text-align:left;padding:clamp(8px,1.05vw,15px) clamp(8px,1vw,16px);
    border-bottom:1px solid #E4E8EE}
  .big-tbl th{font-size:clamp(10px,1.05vw,13px);text-transform:uppercase;letter-spacing:.05em;
    color:#6B7280;font-weight:600}
  .big-tbl .cn,.big-tbl .agn{font-weight:600}
  td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
  .s-good{color:var(--good);font-weight:700}.s-bad{color:var(--bad);font-weight:700}.s-mid{color:#6B7280}
  .tnote{margin-top:clamp(10px,1.4vw,18px);font-size:clamp(10px,1.05vw,13px);color:#6B7280;font-weight:400}
  /* charts */
  .chart-row{display:grid;grid-template-columns:1fr 1fr;gap:clamp(18px,2vw,30px)}
  .ch-card{background:var(--cloud);border-radius:16px;padding:clamp(14px,1.6vw,22px)}
  .ch-t{font-size:clamp(12px,1.3vw,16px);font-weight:700;margin-bottom:clamp(8px,1vw,14px)}
  .cw{position:relative;height:clamp(220px,30vh,360px)}
  .cw.tall{height:clamp(300px,46vh,540px)}
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:clamp(10px,1.3vw,16px)}
  .chip{cursor:pointer;border:1.5px solid #C9D0DA;background:#fff;color:#4a5268;border-radius:99px;
    padding:6px 14px;font-size:clamp(11px,1.2vw,14px);font-weight:600;font-family:var(--font);user-select:none}
  .chip.on{background:var(--blue);color:#fff;border-color:var(--blue)}
  /* campaign deep-dive layout */
  .slide.camp{padding-top:clamp(26px,3.6vw,64px);padding-bottom:clamp(18px,2.6vw,46px);justify-content:flex-start}
  .camp-head{display:flex;align-items:center;gap:clamp(12px,1.5vw,20px);margin-bottom:clamp(10px,1.4vw,20px);margin-top:clamp(30px,3.4vw,52px)}
  .pill{font-size:clamp(11px,1.2vw,15px);font-weight:700;padding:5px 14px;border-radius:99px}
  .pill.s-good{background:rgba(14,159,110,.12);color:var(--good)}
  .pill.s-bad{background:rgba(225,29,72,.1);color:var(--bad)}
  .kpi-row{display:grid;grid-template-columns:repeat(5,1fr);gap:clamp(10px,1.2vw,18px);
    margin-bottom:clamp(12px,1.5vw,22px)}
  .kpi{background:var(--cloud);border-radius:14px;padding:clamp(9px,1.3vw,18px)}
  .kpi-v{font-size:clamp(20px,2.4vw,34px);font-weight:800;letter-spacing:-.02em}
  .kpi-l{font-size:clamp(11px,1.15vw,14px);font-weight:600;color:#4a5268;margin-top:4px}
  .kpi-s{margin-top:6px}
  .camp-body{display:grid;grid-template-columns:1.45fr 1fr;gap:clamp(18px,2.2vw,32px);align-items:start}
  .agt{font-size:clamp(10.5px,1.15vw,15px)}
  .agt th,.agt td{padding:clamp(4px,.6vw,9px) clamp(6px,.8vw,12px);border-bottom:1px solid #E4E8EE;text-align:left}
  .agt th{font-size:clamp(9px,.95vw,12px);text-transform:uppercase;letter-spacing:.04em;color:#6B7280;font-weight:600}
  .agt .over{color:var(--bad);font-weight:700}.agt .under{color:var(--good);font-weight:600}
  .camp-right .cw{height:clamp(200px,32vh,380px)}
  /* delta badges (reused from report) */
  .delta{display:inline-block;font-size:clamp(10px,1.05vw,13px);font-weight:700;font-variant-numeric:tabular-nums}
  .delta.good{color:var(--good)}.delta.bad{color:var(--bad)}.delta.flat{color:#6B7280;font-weight:500}
  /* hamburger + TOC */
  .hamburger{position:fixed;top:clamp(18px,2.2vw,30px);right:clamp(18px,2.2vw,30px);z-index:50;
    width:44px;height:44px;border:none;background:transparent;cursor:pointer;
    display:flex;flex-direction:column;justify-content:center;gap:5px;mix-blend-mode:difference}
  .hamburger span{display:block;height:2.5px;width:26px;background:#fff;border-radius:2px}
  .toc-backdrop{position:fixed;inset:0;background:rgba(10,12,24,.55);opacity:0;visibility:hidden;
    transition:opacity .35s var(--ease);z-index:60}
  .toc-backdrop.open{opacity:1;visibility:visible}
  .toc-panel{position:fixed;top:0;right:0;height:100%;width:min(380px,86vw);background:var(--navy);
    color:var(--cloud);transform:translateX(100%);transition:transform .4s var(--ease);z-index:70;
    padding:clamp(24px,3vw,40px);overflow-y:auto}
  .toc-panel.open{transform:none}
  .toc-panel h3{font-size:14px;text-transform:uppercase;letter-spacing:.12em;color:var(--neon);margin-bottom:18px}
  .toc-grid{display:flex;flex-direction:column;gap:8px}
  .toc-item{display:flex;gap:12px;align-items:baseline;cursor:pointer;padding:9px 12px;border-radius:9px;
    font-size:14px;font-weight:500;color:rgba(255,255,255,.82);transition:background .2s}
  .toc-item:hover{background:rgba(255,255,255,.08)}
  .toc-item.active{background:rgba(223,254,2,.14);color:#fff}
  .toc-num{font-size:11px;font-weight:700;color:var(--neon);min-width:30px}
  /* progress + notes */
  .progress{position:fixed;bottom:0;left:0;height:3px;background:var(--neon);z-index:40;transition:width .4s var(--ease)}
  .notes{display:none;position:fixed;bottom:0;left:0;right:0;background:rgba(10,10,20,.93);
    color:rgba(255,255,255,.92);font-size:clamp(13px,1.4vw,17px);font-family:var(--font);line-height:1.6;
    padding:clamp(14px,2vw,26px) clamp(18px,3vw,46px);z-index:80;border-top:2px solid var(--neon)}
  body.notes-visible .slide.active .notes{display:block}
  .notes-hint{position:absolute;bottom:clamp(10px,1.4vw,18px);left:clamp(32px,4vw,80px);
    font-size:clamp(9px,1vw,11px);color:rgba(255,255,255,.4);pointer-events:none;user-select:none}
  .slide:not(.s-first) .notes-hint{display:none}
  @media (prefers-reduced-motion:reduce){*{transition:none!important}}
  @media (max-width:820px){.num-grid{grid-template-columns:repeat(2,1fr)}
    .kpi-row{grid-template-columns:repeat(3,1fr)}.camp-body{grid-template-columns:1fr}
    .chart-row{grid-template-columns:1fr}}
</style></head><body>
<button class="hamburger" id="hamburger" aria-label="Open slide index"><span></span><span></span><span></span></button>
<div class="toc-backdrop" id="tocBackdrop"></div>
<aside class="toc-panel" id="tocPanel"><h3>Slides</h3><div class="toc-grid" id="tocGrid"></div></aside>
<div class="progress" id="progress"></div>
"""

PAGE_FOOT = r"""
<script id="chart-data" type="application/json">__CHART_JSON__</script>
<script src="__CHARTJS_SRC__" integrity="__CHARTJS_SRI__" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
(function(){
  var slides=Array.from(document.querySelectorAll('.slide'));
  slides[0].classList.add('s-first');
  var chartsBuilt={};
  var D=JSON.parse(document.getElementById('chart-data').textContent);

  var Deck={
    cur:0,step:0,
    show:function(i){
      if(i<0||i>=slides.length)return;
      slides[this.cur].classList.remove('active');
      this.cur=i;this.step=0;
      slides[i].classList.add('active');
      this.resetBullets(i);
      document.getElementById('progress').style.width=((i+1)/slides.length*100)+'%';
      this.updTOC();
      buildChartsFor(i);
    },
    bullets:function(i){return Array.from(slides[i].querySelectorAll('ul.acts li'));},
    resetBullets:function(i){this.bullets(i).forEach(function(b){b.classList.remove('reveal');});},
    next:function(){
      var b=this.bullets(this.cur);
      if(this.step<b.length){b[this.step].classList.add('reveal');this.step++;return;}
      this.show(this.cur+1);
    },
    prev:function(){
      var b=this.bullets(this.cur);
      if(this.step>0){this.step--;b[this.step].classList.remove('reveal');return;}
      this.show(this.cur-1);
    },
    updTOC:function(){
      Array.from(document.querySelectorAll('.toc-item')).forEach(function(it,idx){
        it.classList.toggle('active',idx===Deck.cur);});
    }
  };

  // build TOC
  var grid=document.getElementById('tocGrid');
  slides.forEach(function(s,i){
    var it=document.createElement('div');it.className='toc-item';
    it.innerHTML='<span class="toc-num">'+String(i+1).padStart(2,'0')+'</span><span>'+
      (s.getAttribute('data-toc')||('Slide '+(i+1)))+'</span>';
    it.addEventListener('click',function(){Deck.show(i);closeTOC();});
    grid.appendChild(it);
  });

  // ── charts ──────────────────────────────────────────────
  function whenChart(cb){ if(typeof Chart!=='undefined'){cb();} else {setTimeout(function(){whenChart(cb);},120);} }
  var BLUE='#3185FC',grid_c='rgba(120,130,150,.16)';
  function barCfg(series,labels){
    return {type:'bar',data:{labels:labels,datasets:[{data:series,backgroundColor:BLUE,borderRadius:5,maxBarThickness:30}]},
      options:{responsive:true,maintainAspectRatio:false,layout:{padding:{top:18}},
      plugins:{legend:{display:false},tooltip:{callbacks:{label:function(t){return ' '+t.parsed.y+' demos';}}}},
      scales:{y:{beginAtZero:true,grid:{color:grid_c},ticks:{precision:0}},
              x:{grid:{display:false},ticks:{font:{size:10},maxRotation:0,minRotation:0,autoSkip:false}}}},
      plugins:[valueOnBar]};
  }
  var valueOnBar={id:'vob',afterDatasetsDraw:function(ch){
    var ctx=ch.ctx,ds=ch.data.datasets[0],meta=ch.getDatasetMeta(0);
    ctx.save();ctx.font='700 10px Poppins,sans-serif';ctx.fillStyle='#162050';
    ctx.textAlign='center';ctx.textBaseline='bottom';
    meta.data.forEach(function(bar,i){var v=ds.data[i];if(v==null)return;ctx.fillText((Math.round(v*10)/10),bar.x,bar.y-3);});
    ctx.restore();}};

  function buildChartsFor(i){
    whenChart(function(){
      var s=slides[i];
      if(chartsBuilt[i])return;
      // demo trend slide
      if(s.querySelector('#c-all')){
        new Chart(s.querySelector('#c-all'),barCfg(D.all,D.labels));
        new Chart(s.querySelector('#c-excl'),barCfg(D.excl,D.labels));
        chartsBuilt[i]=1;
      }
      // metric combined slide
      if(s.querySelector('#c-metric')){ buildMetric(s); chartsBuilt[i]=1; }
      // campaign slide
      var cc=s.querySelector('canvas[id^="c-camp-"]');
      if(cc){
        var rec=D.camps.find(function(x){return x.id===cc.id;});
        if(rec){ new Chart(cc,barCfg(rec.series,D.labels)); }
        chartsBuilt[i]=1;
      }
    });
  }
  function buildMetric(s){
    var M=D.metrics;
    var defs=[
      {k:'spend',l:'Spend',c:'#3185FC',ax:'y',f:function(v){return '$'+Math.round(v).toLocaleString();}},
      {k:'impressions',l:'Impressions',c:'#0EA5A4',ax:'y',f:function(v){return Number(v).toLocaleString();}},
      {k:'clicks',l:'Clicks',c:'#F59E0B',ax:'y',f:function(v){return Number(v).toLocaleString();}},
      {k:'impr_share',l:'Impr. Share',c:'#8B5CF6',ax:'y1',f:function(v){return v+'%';}},
      {k:'rank_lost',l:'Rank-Lost IS',c:'#FF4998',ax:'y1',f:function(v){return v+'%';}}
    ];
    var ON={spend:true};
    var ds=defs.map(function(d){return {label:d.l,data:M[d.k],borderColor:d.c,backgroundColor:d.c,
      yAxisID:d.ax,tension:.3,borderWidth:2.5,pointRadius:2,spanGaps:true,hidden:!ON[d.k]};});
    var ch=new Chart(s.querySelector('#c-metric'),{type:'line',data:{labels:D.labels,datasets:ds},
      options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
        plugins:{legend:{display:false},tooltip:{callbacks:{label:function(t){var d=defs[t.datasetIndex];return ' '+d.l+': '+d.f(t.parsed.y);}}}},
        scales:{y:{position:'left',beginAtZero:true,grid:{color:grid_c},ticks:{callback:function(v){return v>=1000?(v/1000)+'k':v;}}},
          y1:{position:'right',beginAtZero:true,max:100,grid:{display:false},ticks:{callback:function(v){return v+'%';}}},
          x:{grid:{display:false}}}}});
    var box=s.querySelector('#metricChips');
    defs.forEach(function(d,idx){
      var b=document.createElement('span');b.className='chip'+(ON[d.k]?' on':'');b.textContent=d.l;b.style.borderColor=d.c;
      b.addEventListener('click',function(e){e.stopPropagation();var x=ch.data.datasets[idx];x.hidden=!x.hidden;
        b.classList.toggle('on',!x.hidden);ch.update();});
      box.appendChild(b);
    });
  }

  // ── nav ─────────────────────────────────────────────────
  function closeTOC(){document.getElementById('tocPanel').classList.remove('open');
    document.getElementById('tocBackdrop').classList.remove('open');}
  function openTOC(){document.getElementById('tocPanel').classList.add('open');
    document.getElementById('tocBackdrop').classList.add('open');}
  document.getElementById('hamburger').addEventListener('click',function(e){e.stopPropagation();
    var p=document.getElementById('tocPanel');p.classList.contains('open')?closeTOC():openTOC();});
  document.getElementById('tocBackdrop').addEventListener('click',closeTOC);

  document.addEventListener('click',function(e){
    if(e.target.closest('.toc-panel,.hamburger,.toc-backdrop,.chip'))return;
    Deck.next();
  });
  document.addEventListener('keydown',function(e){
    if(e.key==='n'||e.key==='N'){document.body.classList.toggle('notes-visible');return;}
    if(e.key==='ArrowRight'||e.key===' '||e.key==='PageDown'){e.preventDefault();Deck.next();}
    else if(e.key==='ArrowLeft'||e.key==='PageUp'){e.preventDefault();Deck.prev();}
    else if(e.key==='Escape'){closeTOC();}
    else if(e.key==='Home'){Deck.show(0);}
    else if(e.key==='End'){Deck.show(slides.length-1);}
  });
  var tsx=0;
  document.addEventListener('touchstart',function(e){tsx=e.changedTouches[0].clientX;},{passive:true});
  document.addEventListener('touchend',function(e){
    var dx=e.changedTouches[0].clientX-tsx;
    if(Math.abs(dx)>50){dx<0?Deck.next():Deck.prev();}
  },{passive:true});

  Deck.show(0);
})();
</script>
</body></html>"""


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("monthly", "weekly"):
        period = args[0]
        key = args[1] if len(args) > 1 else None
    elif args and len(args[0]) == 10 and args[0].count("-") == 2:
        period, key = "weekly", args[0]          # YYYY-MM-DD
    elif args:
        period, key = "monthly", args[0]          # YYYY-MM (back-compat)
    else:
        period, key = "monthly", None
    if key is None:
        if period == "monthly":
            t = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
            key = t.strftime("%Y-%m")
        else:
            key = build_weekly.last_completed_sunday(datetime.date.today()).strftime("%Y-%m-%d")
    build_deck(period, key)
