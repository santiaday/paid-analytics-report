"""
theme.py — Shared visual theme extracted from the Google Ads daily artifact TEMPLATE.
Includes all CSS classes used across Google, Microsoft, and Meta daily reports so that
a new all-channels page can replicate the look 1:1 without touching any upstream file.

CSS braces are single (plain CSS), NOT doubled — the TEMPLATE strings use {{ }} because
they are Python .format() strings; here we store the resolved stylesheet.
"""

# ---------------------------------------------------------------------------
# CSS — verbatim Google daily TEMPLATE stylesheet, {{ }} un-doubled to { }
#       Meta-only additions are appended at the end (annotated).
# ---------------------------------------------------------------------------

CSS = """\
:root {
  color-scheme: light;
  --core-blue: #3185FC;
  --navy: #162050;
  --neon: #DFFE02;
  --pink: #FF4998;
  --gray-100: #F5F7FA;
  --gray-200: #E4E8EE;
  --gray-300: #C9D0DA;
  --gray-500: #6B7280;
  --gray-700: #374151;
  --warn: #B45309;
  --warn-bg: #FEF3C7;
  --err: #B91C1C;
  --err-bg: #FEE2E2;
  --ok: #047857;
  --ok-bg: #D1FAE5;
  --shadow: 0 1px 3px rgba(22,32,80,.08), 0 1px 2px rgba(22,32,80,.05);
  --bg-page: #F5F7FA;
  --bg-card: #FFFFFF;
  --text-primary: #162050;
  --text-secondary: #374151;
  --text-muted: #6B7280;
  --border: #E4E8EE;
  --table-hover: #F5F7FA;
}

[data-theme="dark"] {
  color-scheme: dark;
  --bg-page: #0F1117;
  --bg-card: #1A1D2E;
  --text-primary: #E8EAF0;
  --text-secondary: #B0B7C3;
  --text-muted: #8A93A6;
  --border: #2D3148;
  --gray-200: #2D3148;
  --table-hover: #222540;
  --shadow: 0 1px 3px rgba(0,0,0,.4);
  --warn-bg: #3a2e0d;
  --err-bg: #3a1414;
  --ok-bg: #0f2e22;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg-page);
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.45;
}

.wrap { max-width: 1180px; margin: 0 auto; padding: 22px 22px 60px; }

header { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; }

h1 { font-size: 20px; margin: 0; font-weight: 700; letter-spacing: -.01em; }

.pill {
  display: inline-block;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 99px;
  background: var(--gray-200);
  color: var(--text-secondary);
  margin-left: 6px;
  vertical-align: middle;
}

.sub { color: var(--text-muted); font-size: 12.5px; margin: 4px 0 20px; }

#themeBtn {
  margin-left: auto;
  cursor: pointer;
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text-primary);
  border-radius: 8px;
  padding: 7px 12px;
  font-size: 13px;
  font-weight: 600;
}

.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: var(--shadow);
  padding: 16px 18px;
  margin-bottom: 20px;
}

.card h2 { font-size: 15px; margin: 0 0 12px; font-weight: 700; }
.card h2 .dt { color: var(--text-muted); font-weight: 500; font-size: 13px; }
.card h3 {
  font-size: 13.5px;
  margin: 18px 0 10px;
  font-weight: 700;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.card h3 .dt { color: var(--text-muted); font-weight: 500; font-size: 12px; }

/* Accordion cards — clicking h2 toggles .collapsed */
.card.accordion > h2 { cursor: pointer; user-select: none; }
.card.accordion > h2::after { content: "▾"; float: right; color: var(--text-muted); font-size: 13px; }
.card.accordion.collapsed > h2 { margin-bottom: 0; }
.card.accordion.collapsed > h2::after { content: "▸"; }
.card.accordion.collapsed > *:not(h2) { display: none; }

.kind-tag {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 5px;
  background: var(--gray-200);
  color: var(--text-secondary);
  letter-spacing: .02em;
  margin-right: 2px;
}

.ok-line { color: var(--ok); font-weight: 600; font-size: 13px; margin: 4px 0 10px; }

.alert-sub { color: var(--text-muted); font-size: 11.5px; font-weight: 400; margin-top: 2px; }

tr.alert-more { display: none; }
tr.alert-more.show { display: table-row; }

.alert-toggle {
  cursor: pointer;
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text-secondary);
  border-radius: 8px;
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 600;
  margin-top: 10px;
}

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td {
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
th {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .04em;
  color: var(--text-muted);
  font-weight: 600;
}
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr:hover td { background: var(--table-hover); }

.metric-name { font-weight: 600; }
.target { color: var(--text-muted); }
.status { text-align: center; font-size: 15px; }

.delta { font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }
.delta.good { color: var(--ok, #047857); }
.delta.bad  { color: var(--err, #B91C1C); }
.delta.flat { color: var(--text-muted); font-weight: 500; }

.flag-red td { background: rgba(185,28,28,.06); }

.verdict-bad {
  display: inline-block;
  background: var(--err-bg);
  color: var(--err);
  font-weight: 700;
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 6px;
}

.warn-banner {
  background: var(--warn-bg);
  color: var(--warn);
  border: 1px solid var(--warn);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 12.5px;
  font-weight: 600;
  margin: 0 0 12px;
}

.alert-top {
  background: var(--err-bg);
  color: var(--err);
  border: 2px solid var(--err);
  border-radius: 10px;
  padding: 12px 16px;
  font-size: 13.5px;
  font-weight: 600;
  margin: 0 0 18px;
  box-shadow: var(--shadow);
}

tr.row-alert td { background: var(--err-bg); }
tr.row-alert .metric-name { color: var(--err); }

.src-note {
  display: inline-block;
  background: var(--gray-200);
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 99px;
  margin-left: 6px;
  vertical-align: middle;
}

.src-tag {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 5px;
  margin-left: 4px;
  vertical-align: middle;
  letter-spacing: .02em;
}

.src-rule   { background: var(--gray-200); color: var(--text-secondary); }
.src-both   { background: var(--ok-bg); color: var(--ok); }
.src-notion { background: var(--warn-bg); color: var(--warn); }

tr.gap-row td { background: var(--warn-bg); }

.term   { font-weight: 600; }
.reason { color: var(--text-secondary); font-size: 12.5px; }
.muted  { color: var(--text-muted); }

.foot { color: var(--text-muted); font-size: 12px; margin-top: 6px; }
.foot p { margin: 3px 0; }

/* ── Entity/campaign chips ── */
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 10px; }
.chips.clicks { max-height: 78px; overflow-y: auto; padding: 2px; }

.chip {
  cursor: pointer;
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text-secondary);
  border-radius: 99px;
  padding: 3px 9px;
  font-size: 11px;
  font-weight: 600;
  user-select: none;
  white-space: nowrap;
}
.chip.on { color: #fff; }
/* When .chip.on: JS sets background and border-color to the entity's own hue via inline style. */

/* ── Chart grid (Daily Metrics) ── */
.clk-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }

/* CRITICAL: min-width:0 prevents CSS-grid items from ballooning beyond their column.
   Without it, a Chart.js canvas can overflow horizontally and scroll the whole page.
   Keep on every chart container — .clk-card AND .canvas-wrap. */
.clk-card {
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 12px 8px;
  background: var(--bg-card);
  min-width: 0;
}

.canvas-wrap { min-width: 0; }

.clk-title { font-weight: 700; font-size: 13px; margin-bottom: 8px; }

/* ── Metric toggle buttons ── */
.metric-toggle {
  display: inline-flex;
  gap: 0;
  margin: 0 0 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}

.mbtn {
  cursor: pointer;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  color: var(--text-secondary);
  background: var(--bg-card);
  user-select: none;
  border-right: 1px solid var(--border);
}
.mbtn:last-child { border-right: none; }
.mbtn.on { background: var(--core-blue); color: #fff; }

/* Per-metric active colors (data-m attribute drives CLICKS_JS color logic too) */
.mbtn[data-m="demos"].on     { background: #3185FC; color: #fff; }
.mbtn[data-m="clicks"].on    { background: #162050; color: #fff; }
.mbtn[data-m="both"].on      { background: linear-gradient(90deg, #162050 0 50%, #3185FC 50% 100%); color: #fff; }
.mbtn[data-m="imps"].on      { background: #64748B; color: #fff; }
.mbtn[data-m="spend"].on     { background: #EA580C; color: #fff; }
.mbtn[data-m="cpc"].on       { background: #0D9488; color: #fff; }
.mbtn[data-m="cpd"].on       { background: #7C3AED; color: #fff; }
.mbtn[data-m="rankis"].on    { background: #B45309; color: #fff; }
.mbtn[data-m="imprshare"].on { background: #15803D; color: #fff; }
.mbtn[data-m="ctd"].on       { background: #DB2777; color: #fff; }
.mbtn[data-m="opps"].on      { background: #0EA5E9; color: #fff; }
.mbtn[data-m="accts"].on     { background: #9333EA; color: #fff; }
.mbtn[data-m="arr"].on       { background: #059669; color: #fff; }
/* Meta-only: CPM = cost per 1,000 impressions */
.mbtn[data-m="cpm"].on       { background: #0284C7; color: #fff; }

/* ── Pipeline unattributed note ── */
.clk-pipenote {
  font-size: 11.5px;
  color: var(--warn);
  background: var(--warn-bg);
  border: 1px solid #FCD9A8;
  border-radius: 8px;
  padding: 7px 11px;
  margin-bottom: 4px;
}

/* ── Info-icon tooltips (ⓘ hover/focus popovers) ── */
/* Google style: italic "i" with border ring, turns core-blue on hover */
.tip {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 1px solid var(--gray-300);
  color: var(--gray-500);
  font: 700 10px Georgia, serif;
  font-style: italic;
  cursor: help;
  margin-left: 7px;
  vertical-align: middle;
  user-select: none;
}
.tip:hover, .tip:focus {
  background: var(--core-blue);
  border-color: var(--core-blue);
  color: #fff;
  outline: none;
}

.tip .tipbox {
  display: none;
  position: absolute;
  z-index: 60;
  top: 150%;
  left: 0;
  width: 380px;
  max-width: 80vw;
  background: var(--bg-card);
  color: var(--text-secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow);
  padding: 11px 13px;
  font: 400 12px/1.5 inherit;
  text-align: left;
  cursor: auto;
  white-space: normal;
}
.tip.tip-r .tipbox { left: auto; right: 0; }
.tip:hover .tipbox, .tip:focus .tipbox { display: block; }
.tip .tipbox p { margin: 0 0 7px; }
.tip .tipbox p:last-child { margin: 0; }
.tip .tipbox b { color: var(--text-primary); }

/* ── Canvas sizing ── */
.canvas-wrap { position: relative; height: 210px; }

/* ── Responsive breakpoints ── */
@media (max-width: 760px) {
  .clk-grid { grid-template-columns: 1fr; }
}

/* ── View toggle (Current view / Metric grid) — daily only ── */
.clk-views {
  display: inline-flex;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  margin: 0 0 14px;
}
/* .clk-views uses .vbtn children */
.clk-views .vbtn {
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  padding: 6px 16px;
  color: var(--text-secondary);
  background: var(--bg-card);
  border-right: 1px solid var(--border);
  user-select: none;
}
.clk-views .vbtn:last-child { border-right: none; }
.clk-views .vbtn.on { background: var(--core-blue); color: #fff; }

/* Metric Grid mode: 3-column tiles */
.clk-grid.grid-metrics { grid-template-columns: repeat(3, 1fr); }
.clk-grid.grid-metrics .clk-tile .canvas-wrap.clk-tilewrap { height: 132px; }
.clk-sub { font-size: 11px; color: var(--text-secondary); margin: -4px 0 8px; }

@media (max-width: 1100px) {
  .clk-grid.grid-metrics { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 760px) {
  .clk-grid.grid-metrics { grid-template-columns: 1fr; }
}

/* ── Meta-only additions ─────────────────────────────────────────────────────
   These classes exist in Meta build.py's TEMPLATE but NOT in Google's.
   Include them here so the all-channels report can support both.
   ─────────────────────────────────────────────────────────────────────────── */

/* Performance Summary view toggle (All / Prospecting / Retargeting) — Meta only.
   Uses a standalone .view-toggle wrapper (not .clk-views), with .vbtn children.
   .sumview panels are shown/hidden by toggling .on; JS listens on data-v attr. */
.view-toggle {
  display: inline-flex;
  gap: 0;
  margin: 0 0 12px;
  border: 1px solid var(--border);
  border-radius: 9px;
  overflow: hidden;
}
/* Note: .vbtn is also used inside .clk-views above; its base style is defined there.
   The .view-toggle context is the same styling — no conflict. */
.vbtn {
  cursor: pointer;
  font-size: 12.5px;
  font-weight: 600;
  padding: 6px 16px;
  color: var(--text-secondary);
  background: var(--bg-card);
  user-select: none;
  border-right: 1px solid var(--border);
}
.vbtn:last-child { border-right: none; }
.vbtn.on { background: var(--core-blue); color: #fff; }

.sumview { display: none; }
.sumview.on { display: block; }
"""


# ---------------------------------------------------------------------------
# NOTES — conventions used by CLICKS_JS and the shared template so a new
#         engine can mirror them exactly.
# ---------------------------------------------------------------------------

NOTES = """
THEME CONVENTIONS EXTRACTED FROM google-ads-daily-artifact/build.py + meta-ads-daily-artifact/build.py
======================================================================================================

(a) Layout / colors (light mode)
---------------------------------
- .wrap max-width: 1180px  (both Google and Meta — identical)
- body background:  var(--bg-page)  →  #F5F7FA  (light gray page)
- body color:       var(--text-primary) → #162050  (deep navy)
- card background:  var(--bg-card)  →  #FFFFFF
- border:           var(--border)   →  #E4E8EE
- core-blue accent: --core-blue: #3185FC
- font stack: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif
- base font-size: 14px, line-height 1.45
- dark mode is toggled by setting data-theme="dark" on <html>; CSS vars flip automatically.

(b) CLICKS_JS DOM/class conventions
-------------------------------------
Chart data blob:
  <script id="clicks-data" type="application/json">{ ... }</script>
  JS reads it with: JSON.parse(document.getElementById('clicks-data').textContent)
  Shape: { labels, has_demos, has_cost, has_rankis, has_imps, has_imprshare, has_ctd,
           has_opps, has_accts, has_arr, style, campaigns, dow?, avg?, views?,
           pipeline_note?, pipeline_source? }

Chart container:
  #clkGrid  — the <div class="clk-grid"> the JS fills.
  Each campaign gets a .clk-card div; the All-Campaigns card gets style.gridColumn='1/-1'
  (full-width span). Chart canvas lives inside a .canvas-wrap.clk div.

Metric toggle buttons (.mbtn):
  - Container: <div class="metric-toggle">
  - Each button: <span class="mbtn [on]" data-m="<mode>" title="...">Label</span>
  - Active state: class "on" is added/removed by the click handler.
  - Modes/colors: demos=#3185FC, clicks=#162050, imps=#64748B, spend=#EA580C,
    cpc=#0D9488, cpd=#7C3AED, ctd=#DB2777, rankis=#B45309, imprshare=#15803D,
    opps=#0EA5E9, accts=#9333EA, arr=#059669
    (Meta adds: cpm=#0284C7)
  - "both" mode is a gradient button (deprecated in newer builds; removed 2026-06-08).
  - Default mode: 'demos' (if has_demos), else 'clicks'.

Moving-average chip (.avgbtn):
  - A <span class="chip avgbtn"> sibling to the metric-toggle div.
  - Text: "〜 " + AVG.label  (e.g. "〜 7-day avg")
  - Active state: JS toggles class "on" AND sets inline styles:
      background='#64748B', borderColor='#64748B', color='#fff'
  - Driven by D.avg = {n, label} in the chart data blob; absent → no chip rendered.
  - MA datasets carry _noLabel:true so LINE_LABELS_JS skips them in peak/trough logic.

Entity chips (campaign/ad-group toggle):
  - Container: <div class="chips clicks">
  - Each entity: <span class="chip [on]">Label</span>
  - Active state: class "on" + inline background=entityColor + color='#fff'.
  - Inactive: background and color cleared (shows border only via entityColor inline).
  - Border color is always set to the entity's color via inline style.
  - JS tracks hidden state in a local hidden={} dict keyed by entity label.

View toggle (Current / Metric Grid) — daily only, gated on D.views:
  - Wrapper: <div class="clk-views"> inserted as a sibling BEFORE #clkGrid.
  - Buttons: <span class="vbtn [on]">Current view</span> and <span class="vbtn">Metric grid</span>
  - In grid mode: #clkGrid gains the class "grid-metrics" → 3-column tile layout.
  - In current mode: class "grid-metrics" is removed → 2-column chart layout.

(c) Meta-only CSS that differs from Google's
----------------------------------------------
1. `.view-toggle` + `.vbtn` + `.sumview` — Meta's Performance Summary view switcher
   (All / Prospecting / Retargeting). Uses data-v attribute on .vbtn; JS toggles
   .sumview.on panels. Not present in Google daily.

2. `.mbtn[data-m="cpm"].on { background: #0284C7 }` — CPM button (Meta only).
   Google daily does NOT have a CPM metric or button.

3. Meta's `.tip` circle uses a slightly different base style (gray filled background
   instead of border-only ring, `cursor:default` not `cursor:help`). Both share the
   same .tipbox popover structure. The CSS in this file uses Google's version (border
   ring, italic i, cursor:help) as it is the more common baseline; Meta's variant is
   cosmetically minimal.

4. Meta's TEMPLATE omits: .src-rule/.src-both/.src-notion (bad-term tags), .gap-row,
   .flag-red, .verdict-bad, .src-note — these are Google/negative-keyword-specific.
   Included in CSS above for completeness; unused in Meta context.

5. Meta's TEMPLATE includes `.card h3` (sub-section header) in Google's but NOT in its
   own stylesheet — both share the same pattern. CSS above has it from Google.

(d) Accordion collapse mechanism
-----------------------------------
- Any card can be an accordion: <div class="card accordion">
- Clicking the <h2> child toggles class "collapsed" on the parent .card.
- CSS hides all children except h2 when .collapsed is set.
- The ▾/▸ chevron toggles via the ::after pseudo-element on h2.
- JS wiring (verbatim from TEMPLATE):
    document.querySelectorAll(".card.accordion>h2").forEach(function(h){
      h.addEventListener("click", function(){ h.parentElement.classList.toggle("collapsed"); });
    });
- Accordions are OPEN by default; add class "collapsed" to start closed.

(e) min-width:0 critical rule
--------------------------------
Both .clk-card AND .canvas-wrap carry min-width:0. This is mandatory on CSS-grid items
containing Chart.js canvases — without it the canvas can grow beyond the column width
and overflow the page horizontally. Never remove this rule from any chart container.
"""


if __name__ == "__main__":
    # Sanity check: import and measure
    print(f"CSS length: {len(CSS)} bytes")
    print(f"NOTES length: {len(NOTES)} bytes")
    print("theme.py OK")
