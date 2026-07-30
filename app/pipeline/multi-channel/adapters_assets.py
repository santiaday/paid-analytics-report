#!/usr/bin/env python3
"""adapters_assets.py — the CSS + JS the adapters.py fragments depend on.

CSS = the union of the rules these anchor-day fragments use, extracted from the
four siblings' TEMPLATEs. They share lineage, so most rules are identical;
where they differ, Google's version wins (per the unified-report spec), and
all-channels-only / meta-only rules are appended:

  • Google/Microsoft base: :root + dark-theme vars, .pill, .card (+accordion),
    summary tables (.metric-name/.target/.status/.num, tr.row-alert, .flag-red),
    .delta good/bad/flat, alerts table (.kind-tag/.ok-line/.alert-sub,
    tr.alert-more + .alert-toggle expander), bad terms (.verdict-bad, .src-tag
    + .src-rule/.src-both/.src-notion, tr.gap-row, .term/.reason), the rec
    section (reuses .src-tag/.gap-row + .card h3), .warn-banner, .alert-top
    (red top banner), .muted/.foot, and the .tip/.tipbox info tooltips.
  • All-channels extras: .tablewrap, .cellmeta (prev/YoY sub-numbers),
    .delta.softgood/.softbad (sub-materiality deltas), a.rlink (dotted SF
    report links). Its `.metric-name{white-space:nowrap}` is deliberately NOT
    carried (it would reflow the Google/MS summary tables); everything else is
    identical to Google's.
  • Meta extras: .view-toggle/.vbtn/.sumview (Performance Summary view toggle).

NOT included (page-level concerns the unified report owns): body/.wrap/header/
h1/#themeBtn and all chart CSS (.clk-*, .chip(s), .metric-toggle, .mbtn) — the
anchor-day fragments contain no charts.

JS = the small behaviors the fragments embed or rely on:
  • accordion: clicking a `.card.accordion > h2` toggles `.collapsed`
    (Daily Alerts, Scorecard, Rates, per-campaign cards).
  • meta Performance Summary view toggle: clicks on `#viewToggle .vbtn`
    switch the matching `.sumview` on (ids sumview0..N).
  • The "+N more" expanders need NO registration — they ship as inline
    onclick attributes inside the fragments. NOTE: the Google/Microsoft alerts
    expander toggles every `tr.alert-more` in the DOCUMENT (verbatim sibling
    markup), so on a combined page keep each platform's fragments inside its
    own tab pane; the all-channels campaign expander is already scoped to its
    own card (`this.parentElement`). Both expander buttons reuse the id
    `alertMoreBtn` on Google+Microsoft — duplicate ids are harmless here (the
    handlers use `this`), but don't target that id from new code.

Both exported as plain strings.
"""

CSS = r"""
  :root{color-scheme:light;--core-blue:#3185FC;--navy:#162050;--neon:#DFFE02;--pink:#FF4998;
    --gray-100:#F5F7FA;--gray-200:#E4E8EE;--gray-300:#C9D0DA;--gray-500:#6B7280;--gray-700:#374151;
    --warn:#B45309;--warn-bg:#FEF3C7;--err:#B91C1C;--err-bg:#FEE2E2;--ok:#047857;--ok-bg:#D1FAE5;
    --shadow:0 1px 3px rgba(22,32,80,.08),0 1px 2px rgba(22,32,80,.05);
    --bg-page:#F5F7FA;--bg-card:#FFFFFF;--text-primary:#162050;--text-secondary:#374151;
    --text-muted:#6B7280;--border:#E4E8EE;--table-hover:#F5F7FA;}
  [data-theme="dark"]{color-scheme:dark;--bg-page:#0F1117;--bg-card:#1A1D2E;--text-primary:#E8EAF0;
    --text-secondary:#B0B7C3;--text-muted:#8A93A6;--border:#2D3148;--gray-200:#2D3148;
    --table-hover:#222540;--shadow:0 1px 3px rgba(0,0,0,.4);--warn-bg:#3a2e0d;--err-bg:#3a1414;--ok-bg:#0f2e22;}
  *{box-sizing:border-box}
  .pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:99px;background:var(--gray-200);
    color:var(--text-secondary);margin-left:6px;vertical-align:middle}
  .card{background:var(--bg-card);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);
    padding:16px 18px;margin-bottom:20px}
  .card h2{font-size:15px;margin:0 0 12px;font-weight:700}
  .card h2 .dt{color:var(--text-muted);font-weight:500;font-size:13px}
  .card h3{font-size:13.5px;margin:18px 0 10px;font-weight:700;padding-top:14px;border-top:1px solid var(--border)}
  .card h3 .dt{color:var(--text-muted);font-weight:500;font-size:12px}
  .kind-tag{display:inline-block;font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;
    background:var(--gray-200);color:var(--text-secondary);letter-spacing:.02em;margin-right:2px}
  .ok-line{color:var(--ok);font-weight:600;font-size:13px;margin:4px 0 10px}
  .alert-sub{color:var(--text-muted);font-size:11.5px;font-weight:400;margin-top:2px}
  tr.alert-more{display:none} tr.alert-more.show{display:table-row}
  .alert-toggle{cursor:pointer;border:1px solid var(--border);background:var(--bg-card);
    color:var(--text-secondary);border-radius:8px;padding:5px 12px;font-size:12px;font-weight:600;margin-top:10px}
  .card.accordion>h2{cursor:pointer;user-select:none}
  .card.accordion>h2::after{content:"▾";float:right;color:var(--text-muted);font-size:13px}
  .card.accordion.collapsed>h2{margin-bottom:0}
  .card.accordion.collapsed>h2::after{content:"▸"}
  .card.accordion.collapsed>*:not(h2){display:none}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}
  th{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);font-weight:600}
  td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
  tr:hover td{background:var(--table-hover)}
  .metric-name{font-weight:600} .target{color:var(--text-muted)} .status{text-align:center;font-size:15px}
  .delta{font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}
  .delta.good{color:var(--ok,#047857)} .delta.bad{color:var(--err,#B91C1C)} .delta.flat{color:var(--text-muted);font-weight:500}
  .flag-red td{background:rgba(185,28,28,.06)}
  .verdict-bad{display:inline-block;background:var(--err-bg);color:var(--err);font-weight:700;font-size:11px;
    padding:2px 7px;border-radius:6px}
  .warn-banner{background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn);border-radius:8px;
    padding:8px 12px;font-size:12.5px;font-weight:600;margin:0 0 12px}
  .alert-top{background:var(--err-bg);color:var(--err);border:2px solid var(--err);border-radius:10px;
    padding:12px 16px;font-size:13.5px;font-weight:600;margin:0 0 18px;box-shadow:var(--shadow)}
  tr.row-alert td{background:var(--err-bg)} tr.row-alert .metric-name{color:var(--err)}
  .src-note{display:inline-block;background:var(--gray-200);color:var(--text-secondary);font-size:11px;
    font-weight:600;padding:2px 8px;border-radius:99px;margin-left:6px;vertical-align:middle}
  .src-tag{display:inline-block;font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;
    margin-left:4px;vertical-align:middle;letter-spacing:.02em}
  .src-rule{background:var(--gray-200);color:var(--text-secondary)}
  .src-both{background:var(--ok-bg);color:var(--ok)}
  .src-notion{background:var(--warn-bg);color:var(--warn)}
  tr.gap-row td{background:var(--warn-bg)}
  .term{font-weight:600} .reason{color:var(--text-secondary);font-size:12.5px} .muted{color:var(--text-muted)}
  .foot{color:var(--text-muted);font-size:12px;margin-top:6px} .foot p{margin:3px 0}
  /* Info-icon tooltips (Google's version preferred across all four) */
  .tip{position:relative;display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;
    border-radius:50%;border:1px solid var(--gray-300);color:var(--gray-500);font:700 10px Georgia,serif;
    font-style:italic;cursor:help;margin-left:7px;vertical-align:middle;user-select:none}
  .tip:hover,.tip:focus{background:var(--core-blue);border-color:var(--core-blue);color:#fff;outline:none}
  .tip .tipbox{display:none;position:absolute;z-index:60;top:150%;left:0;width:380px;max-width:80vw;
    background:var(--bg-card);color:var(--text-secondary);border:1px solid var(--border);border-radius:10px;
    box-shadow:var(--shadow);padding:11px 13px;font:400 12px/1.5 inherit;text-align:left;cursor:auto;white-space:normal}
  .tip.tip-r .tipbox{left:auto;right:0}
  .tip:hover .tipbox,.tip:focus .tipbox{display:block}
  .tip .tipbox p{margin:0 0 7px} .tip .tipbox p:last-child{margin:0}
  .tip .tipbox b{color:var(--text-primary)}
  /* ── all-channels extras (Funnel Scorecard / Conversion Rates / drill-down) ── */
  .tablewrap{overflow-x:auto}
  .cellmeta{color:var(--text-muted);font-size:11px;margin-top:1px;white-space:nowrap}
  .delta.softgood{color:var(--ok);opacity:.45;font-weight:600}
  .delta.softbad{color:var(--warn);opacity:.8;font-weight:600}
  a.rlink{color:inherit;text-decoration-line:underline;text-decoration-style:dotted;
    text-decoration-color:var(--text-muted);text-underline-offset:2px}
  a.rlink:hover{color:var(--core-blue);text-decoration-color:var(--core-blue)}
  /* ── meta extras (Performance Summary view toggle: All / per-campaign) ── */
  .view-toggle{display:inline-flex;gap:0;margin:0 0 12px;border:1px solid var(--border);border-radius:9px;overflow:hidden}
  .view-toggle .vbtn{cursor:pointer;font-size:12.5px;font-weight:600;padding:6px 16px;color:var(--text-secondary);
    background:var(--bg-card);user-select:none;border-right:1px solid var(--border)}
  .view-toggle .vbtn:last-child{border-right:none}
  .view-toggle .vbtn.on{background:var(--core-blue);color:#fff}
  .sumview{display:none} .sumview.on{display:block}
"""

JS = r"""
  // accordion cards: click the h2 to collapse/expand (Daily Alerts, Scorecard,
  // Conversion Rates, per-campaign drill-down cards)
  document.querySelectorAll(".card.accordion>h2").forEach(function(h){
    h.addEventListener("click",function(){ h.parentElement.classList.toggle("collapsed"); });
  });
  // meta Performance Summary view toggle (All Campaigns / per-campaign tables)
  var vt=document.getElementById("viewToggle");
  if(vt){vt.addEventListener("click",function(e){
    var v=e.target&&e.target.dataset?e.target.dataset.v:null; if(v==null) return;
    vt.querySelectorAll(".vbtn").forEach(function(b){ b.classList.toggle("on",b.dataset.v===v); });
    document.querySelectorAll(".sumview").forEach(function(d){ d.classList.toggle("on",d.id==="sumview"+v); });
  });}
  // NOTE: the "+N more" expanders (alerts tables, campaign drill-down) are
  // self-contained inline onclick attributes inside the fragments — no wiring here.
"""
