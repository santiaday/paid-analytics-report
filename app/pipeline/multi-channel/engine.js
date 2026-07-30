/* engine.js — client-side engine for the multi-channel unified report (v2).
   Mirrors the original daily artifacts' DOM/class conventions (theme.py NOTES):
   .metric-toggle/.mbtn[data-m], .chips.clicks/.chip, .chip.avgbtn, .clk-views/.vbtn,
   .clk-grid(.grid-metrics)/.clk-card/.canvas-wrap. All aggregation happens here. */
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("mc-data").textContent);
  var HIST = (function () {
    var el = document.getElementById("daily-history");
    try { return el ? JSON.parse(el.textContent) : {}; } catch (e) { return {}; }
  })();

  /* Registry of every Chart.js instance, so the Dark/Light toggle can re-skin them
     (axis ticks/gridlines inherit Chart.defaults.color, which we flip on theme change). */
  window.__mcCharts = window.__mcCharts || [];
  function regChart(c) { window.__mcCharts.push(c); return c; }

  /* ---------- formatting ---------- */
  function fmtMoney(v, dp) {
    if (v == null || !isFinite(v)) return "—";
    return "$" + v.toLocaleString("en-US", { minimumFractionDigits: dp || 0, maximumFractionDigits: dp || 0 });
  }
  function fmtInt(v) { return v == null || !isFinite(v) ? "—" : Math.round(v).toLocaleString("en-US"); }
  function fmtPct(v, dp) { return v == null || !isFinite(v) ? "—" : (v * 100).toFixed(dp == null ? 1 : dp) + "%"; }
  var DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function dUTC(iso) { return new Date(iso + "T12:00:00Z"); }
  function shortDate(iso) { var d = dUTC(iso); return MON[d.getUTCMonth()] + " " + d.getUTCDate(); }
  function longDate(iso) { var d = dUTC(iso); return DOW[d.getUTCDay()] + ", " + MON[d.getUTCMonth()] + " " + d.getUTCDate() + " " + d.getUTCFullYear(); }
  function bkTitle(bk, j) {
    if (bk.grain === "day") return longDate(bk.keys[j]);
    if (bk.grain === "week") return "Week of " + shortDate(bk.keys[j]) + " " + bk.keys[j].slice(0, 4);
    return MON[+bk.keys[j].slice(5, 7) - 1] + " " + bk.keys[j].slice(0, 4);
  }

  var METRICS = {
    demos:    { label: "Demos",         color: "#3185FC", fmt: fmtInt, kind: "count", grain: "campaign", agok: true },
    clicks:   { label: "Clicks",        color: "#162050", fmt: fmtInt, kind: "count" },
    imps:     { label: "Impressions",   color: "#64748B", fmt: fmtInt, kind: "count" },
    spend:    { label: "Spend",         color: "#EA580C", fmt: function (v) { return fmtMoney(v); }, kind: "count" },
    cpc:      { label: "CPC",           color: "#0D9488", fmt: function (v) { return fmtMoney(v, 2); }, kind: "ratio", num: "spend", den: "clicks" },
    cpm:      { label: "CPM",           color: "#0284C7", fmt: function (v) { return fmtMoney(v, 2); }, kind: "ratio", num: "spend1000", den: "imps" },
    cpd:      { label: "CPD",           color: "#7C3AED", fmt: function (v) { return fmtMoney(v); }, kind: "ratio", num: "spend", den: "demos", grain: "campaign", agok: true },
    ctd:      { label: "Click→Demo",    color: "#DB2777", fmt: function (v) { return fmtPct(v, 2); }, kind: "ratio", num: "demos", den: "clicks", grain: "campaign", agok: true },
    opps:     { label: "Opportunities", color: "#0EA5E9", fmt: fmtInt, kind: "count", grain: "campaign", agok: true },
    accts:    { label: "Accounts",      color: "#9333EA", fmt: fmtInt, kind: "count", grain: "campaign", agok: true },
    arr:      { label: "New ARR",       color: "#059669", fmt: function (v) { return fmtMoney(v); }, kind: "count", grain: "campaign", agok: true },
    rankis:   { label: "Rank-Lost IS",  color: "#B45309", fmt: function (v) { return fmtPct(v, 1); }, kind: "wavg", grain: "campaign" },
    imprshare:{ label: "Impr Share",    color: "#15803D", fmt: function (v) { return fmtPct(v, 1); }, kind: "wavg", grain: "campaign" }
  };
  var HUES = ["#E4572E", "#17BEBB", "#FFC914", "#76B041", "#A26769", "#5B5F97", "#FF6B9D", "#2E86AB", "#C05299", "#6A994E", "#BC4B51", "#427AA1", "#8E7DBE", "#D08C60", "#3D5A80", "#9C6644"];

  /* ---------- date ranges + buckets ---------- */
  var PRESETS = [["yesterday", "Yesterday"], ["l7", "Last 7 Days"], ["l30", "Last 30 Days"], ["l60", "Last 60 Days"], ["l90", "Last 90 Days"], ["tmonth", "This Month"], ["lmonth", "Last Month"], ["tyear", "This Year"], ["lyear", "Last Year"], ["all", "All Time"], ["custom", "Custom…"]];
  function rangeFor(preset, dates, c1, c2) {
    var n = dates.length, last = n - 1;
    function idxOf(iso, fb) { var i = dates.indexOf(iso); return i >= 0 ? i : fb; }
    function clamp(i) { return Math.max(0, Math.min(last, i)); }
    var a = dates[last], y = a.slice(0, 4), m = a.slice(5, 7);
    switch (preset) {
      case "yesterday": return [last, last];
      case "l7": return [clamp(last - 6), last];
      case "l30": return [clamp(last - 29), last];
      case "l60": return [clamp(last - 59), last];
      case "l90": return [clamp(last - 89), last];
      case "tmonth": return [idxOf(y + "-" + m + "-01", 0), last];
      case "lmonth": {
        var pm = m === "01" ? [String(+y - 1), "12"] : [y, String(+m - 1).padStart(2, "0")];
        return [idxOf(pm[0] + "-" + pm[1] + "-01", 0), clamp(idxOf(y + "-" + m + "-01", last) - 1)];
      }
      case "tyear": return [idxOf(y + "-01-01", 0), last];
      case "lyear": return [idxOf((+y - 1) + "-01-01", 0), clamp(idxOf(y + "-01-01", last) - 1)];
      case "all": return [0, last];
      case "custom": {
        var s = c1 ? idxOf(c1, 0) : 0, e = c2 ? idxOf(c2, last) : last;
        if (s > e) { var t = s; s = e; e = t; }
        return [s, e];
      }
    }
    return [clamp(last - 29), last];
  }
  function priorRange(preset, r, dates) {
    var len = r[1] - r[0] + 1;
    if (preset === "tyear" || preset === "lyear") {
      var s = dates[r[0]], i0 = dates.indexOf((+s.slice(0, 4) - 1) + s.slice(4));
      if (i0 < 0) return null;
      return [i0, Math.min(i0 + len - 1, Math.max(i0, r[0] - 1))];
    }
    if (r[0] - len < 0) return null;
    return [r[0] - len, r[0] - 1];
  }
  function buckets(dates, r, grain) {
    var keys = [], labels = [], map = new Array(dates.length).fill(-1), cur = null;
    for (var i = r[0]; i <= r[1]; i++) {
      var iso = dates[i], k;
      if (grain === "day") k = iso;
      else if (grain === "week") { var d = dUTC(iso), off = (d.getUTCDay() + 6) % 7; d.setUTCDate(d.getUTCDate() - off); k = d.toISOString().slice(0, 10); }
      else k = iso.slice(0, 7);
      if (k !== cur) { cur = k; keys.push(k); labels.push(grain === "day" ? shortDate(iso) : grain === "week" ? "wk " + shortDate(k) : MON[+k.slice(5, 7) - 1] + " '" + k.slice(2, 4)); }
      map[i] = keys.length - 1;
    }
    return { keys: keys, labels: labels, map: map, grain: grain };
  }
  function maWin(grain) { return grain === "day" ? 7 : grain === "week" ? 4 : 3; }
  function maLabel(grain) { return grain === "day" ? "7-day avg" : grain === "week" ? "4-week avg" : "3-month avg"; }

  /* ---------- platform aggregation ---------- */
  function brandTest(P) {
    var w = P.flags && P.flags.branded;
    if (!w) return function () { return false; };
    return function (name) { return (name || "").toLowerCase().indexOf(w) >= 0; };
  }
  function makeAgg(P, bk, ent) {
    /* ent.kind ∈ {all, xb, camp, grp, sel}. "sel" = the top-bar Campaign/Ad-group
       filter: ent.camps (Set|null = all campaigns) ∩ ent.grps (Set|null = no
       ad-group filter). When an ad-group filter is on, demos come from agdemos and
       opps/accts/arr from agpipeline (both ad-group-grain fact arrays) — NEVER a
       campaign rollup. rankis/impr-share use agrankis via aggRankIS (Google only). */
    var nb = bk.keys.length, isBr = brandTest(P);
    function Z() { return new Array(nb).fill(0); }
    var o = { spend: Z(), clicks: Z(), imps: Z(), demos: Z(), opps: Z(), accts: Z(), arr: Z() };
    var camps = ent.camps, grps = ent.grps;   // only set for kind === "sel"
    function spendOk(c, g) {
      if (ent.kind === "all") return true;
      if (ent.kind === "xb") return c >= 0 && !isBr(P.campaigns[c][1]);
      if (ent.kind === "camp") return c === ent.c;
      if (ent.kind === "grp") return c === ent.c && g === ent.g;
      return (!camps || camps.has(c)) && (!grps || grps.has(g));   // sel
    }
    function campOk(c) {   // campaign-grain gate (demos / pipeline)
      if (ent.kind === "all") return true;
      if (ent.kind === "xb") return c >= 0 && !isBr(P.campaigns[c][1]);
      if (ent.kind === "sel") return !camps || camps.has(c);
      return c === ent.c;
    }
    var i, r, b;
    for (i = 0; i < P.spend.length; i++) {
      r = P.spend[i]; b = bk.map[r[0]];
      if (b < 0 || !spendOk(r[1], r[2])) continue;
      o.spend[b] += r[3] / 100; o.clicks[b] += r[4]; o.imps[b] += r[5];
    }
    var useAg = ent.kind === "grp" || (ent.kind === "sel" && grps);
    if (!useAg) {
      for (i = 0; i < P.demos.length; i++) {
        r = P.demos[i]; b = bk.map[r[0]];
        if (b < 0) continue;
        if (ent.kind !== "all" && (r[1] < 0 || !campOk(r[1]))) continue;
        o.demos[b] += r[2];
      }
      for (i = 0; i < P.pipeline.length; i++) {
        r = P.pipeline[i]; b = bk.map[r[0]];
        if (b < 0) continue;
        if (ent.kind !== "all" && (r[1] < 0 || !campOk(r[1]))) continue;
        o.opps[b] += r[2]; o.accts[b] += r[3]; o.arr[b] += r[4];
      }
      /* Retargeting attribution toggle (Meta): swap the standalone Retargeting line's demos to
         the last-touch / all-touch series from P.retarget [d, ft, lt, at]. Only the single
         Retargeting campaign line (kind:"camp", c === retIdx) — the Selection-total / All lines
         and every other campaign stay FIRST-touch (the headline). CPD / Click→Demo derive from
         o.demos so they follow the toggle. (2026-06-16) */
      if (ent.retB && ent.retB !== "ft" && ent.kind === "camp" && P.retarget && ent.c === retIdxOf(P)) {
        o.demos = Z();
        var rcol = ent.retB === "lt" ? 2 : 3;
        for (i = 0; i < P.retarget.length; i++) { r = P.retarget[i]; b = bk.map[r[0]]; if (b >= 0) o.demos[b] += r[rcol]; }
      }
    } else {
      /* ad-group demos: Day-0 cohort (Brain kw-funnel for google/ms, SF for meta), agdemos [d,c,g,n] */
      var ad = P.agdemos || [];
      for (i = 0; i < ad.length; i++) {
        r = ad[i]; b = bk.map[r[0]];
        if (b < 0) continue;
        if (ent.kind === "grp") { if (r[1] === ent.c && r[2] === ent.g) o.demos[b] += r[3]; }
        else if ((!camps || camps.has(r[1])) && grps.has(r[2])) o.demos[b] += r[3];
      }
      /* ad-group opps/accts/arr: booked-on-day, Salesforce (all channels), agpipeline [d,c,g,opps,accts,arr] */
      var ap = P.agpipeline || [];
      for (i = 0; i < ap.length; i++) {
        r = ap[i]; b = bk.map[r[0]];
        if (b < 0) continue;
        if (ent.kind === "grp") { if (r[1] !== ent.c || r[2] !== ent.g) continue; }
        else if (!((!camps || camps.has(r[1])) && grps.has(r[2]))) continue;
        o.opps[b] += r[3]; o.accts[b] += r[4]; o.arr[b] += r[5];
      }
    }
    return o;
  }
  function retIdxOf(P) {   // Meta Retargeting_Funnel_Steps campaign idx (for the attribution toggle)
    if (P._retIdx === undefined) {
      P._retIdx = -1;
      for (var i = 0; i < P.campaigns.length; i++) { if (P.campaigns[i][1] === "Retargeting_Funnel_Steps") { P._retIdx = i; break; } }
    }
    return P._retIdx;
  }
  function costIndex(P) {
    if (P._costIdx) return P._costIdx;
    var m = {};
    for (var i = 0; i < P.spend.length; i++) {
      var r = P.spend[i], k = r[0] + "_" + r[1];
      m[k] = (m[k] || 0) + r[3] / 100;
    }
    P._costIdx = m; return m;
  }
  function agCostIndex(P) {   // (date_c_g) -> cost, to cost-weight ad-group rank/impr-share
    if (P._agCostIdx) return P._agCostIdx;
    var m = {};
    for (var i = 0; i < P.spend.length; i++) {
      var r = P.spend[i], k = r[0] + "_" + r[1] + "_" + r[2];
      m[k] = (m[k] || 0) + r[3] / 100;
    }
    P._agCostIdx = m; return m;
  }
  function aggRankIS(P, bk, ent) {
    var nb = bk.keys.length, isBr = brandTest(P);
    var rk = new Array(nb).fill(0), is = new Array(nb).fill(0), w = new Array(nb).fill(0);
    var useAg = ent.kind === "grp" || (ent.kind === "sel" && ent.grps);
    if (useAg) {
      /* ad-group grain: agrankis [d,c,g,rankLost,imprShare], cost-weighted by ad-group spend.
         Only Google has agrankis — Microsoft/Meta have no ad-group impr-share → w stays 0 → null (blank). */
      var ar = P.agrankis || [], aci = agCostIndex(P), camps = ent.camps, grps = ent.grps;
      for (var j = 0; j < ar.length; j++) {
        var ra = ar[j], ba = bk.map[ra[0]];
        if (ba < 0) continue;
        var ca = ra[1], ga = ra[2];
        if (ent.kind === "grp") { if (ca !== ent.c || ga !== ent.g) continue; }
        else { if (camps && !camps.has(ca)) continue; if (!grps.has(ga)) continue; }
        if (ra[3] == null && ra[4] == null) continue;
        var costA = aci[ra[0] + "_" + ca + "_" + ga] || 0.01;
        rk[ba] += (ra[3] || 0) * costA; is[ba] += (ra[4] || 0) * costA; w[ba] += costA;
      }
    } else {
      var ci = costIndex(P);
      for (var i = 0; i < (P.rankis || []).length; i++) {
        var r = P.rankis[i], b = bk.map[r[0]];
        if (b < 0) continue;
        var c = r[1];
        if (ent.kind === "xb" && (c < 0 || isBr(P.campaigns[c][1]))) continue;
        if (ent.kind === "camp" && c !== ent.c) continue;
        if (ent.kind === "sel" && ent.camps && !ent.camps.has(c)) continue;
        var cost = ci[r[0] + "_" + c] || 0.01;
        rk[b] += r[2] * cost; is[b] += r[3] * cost; w[b] += cost;
      }
    }
    return { rk: rk.map(function (v, j) { return w[j] ? v / w[j] : null; }), is: is.map(function (v, j) { return w[j] ? v / w[j] : null; }) };
  }
  function selHash(ent) {
    return (ent.camps ? Array.from(ent.camps).sort(function (a, b) { return a - b; }).join(",") : "*") + "|" +
           (ent.grps ? Array.from(ent.grps).sort(function (a, b) { return a - b; }).join(",") : "*");
  }
  function entKey(ent) {
    if (ent.kind === "sel") return "sel_" + selHash(ent);
    return ent.kind + "_" + (ent.c == null ? "" : ent.c) + "_" + (ent.g == null ? "" : ent.g) +
           (ent.retB && ent.retB !== "ft" ? "_" + ent.retB : "");
  }
  function aggFor(P, bk, ent) {
    P._aggCache = P._aggCache && P._aggCache._bk === bk ? P._aggCache : { _bk: bk };
    var k = entKey(ent);
    return P._aggCache[k] || (P._aggCache[k] = makeAgg(P, bk, ent));
  }
  function seriesFor(P, bk, ent, metric) {
    var m = METRICS[metric];
    if (metric === "rankis" || metric === "imprshare") {
      /* ent passed straight through — aggRankIS reads agrankis for ad-group entities
         (Google) and the campaign rankis otherwise; blank where no source. */
      P._aggCache = P._aggCache && P._aggCache._bk === bk ? P._aggCache : { _bk: bk };
      var k2 = "ris_" + entKey(ent);
      var R = P._aggCache[k2] || (P._aggCache[k2] = aggRankIS(P, bk, ent));
      return metric === "rankis" ? R.rk : R.is;
    }
    /* campaign-grain metric with no ad-group source for this entity → blank (never a rollup) */
    if ((ent.kind === "grp" || (ent.kind === "sel" && ent.grps)) && m.grain === "campaign" && !m.agok) return null;
    var A = aggFor(P, bk, ent);
    if (m.kind === "ratio") {
      var num = m.num === "spend1000" ? A.spend.map(function (v) { return v * 1000; }) : A[m.num];
      return num.map(function (v, j) { return A[m.den][j] > 0 ? v / A[m.den][j] : null; });
    }
    return A[metric].slice();
  }
  /* moving average — locked methodology: ratio metrics = rolling-sum ÷ rolling-sum;
     counts & share metrics = gap-tolerant trailing mean */
  function trailMean(arr, win) {
    return arr.map(function (_, j) {
      var s = 0, n = 0;
      for (var k = Math.max(0, j - win + 1); k <= j; k++) if (arr[k] != null) { s += arr[k]; n++; }
      return n ? s / n : null;
    });
  }
  function rollRatio(num, den, win, mult) {
    return num.map(function (_, j) {
      var s = 0, t = 0;
      for (var k = Math.max(0, j - win + 1); k <= j; k++) { s += num[k] || 0; t += den[k] || 0; }
      return t > 0 ? s * (mult || 1) / t : null;
    });
  }
  function maOf(P, bk, ent, metric, arr) {
    var m = METRICS[metric], win = maWin(bk.grain);
    if (m.kind === "ratio" && ent && !(ent.kind === "grp" && m.grain === "campaign" && !m.agok)) {
      var A = aggFor(P, bk, ent);
      return rollRatio(m.num === "spend1000" ? A.spend : A[m.num], A[m.den], win, m.num === "spend1000" ? 1000 : 1);
    }
    return trailMean(arr, win);
  }

  /* ---------- chart plumbing ---------- */
  function lineLabels(fmt) {
    return {
      id: "lineLabels",
      afterDatasetsDraw: function (chart) {
        if (chart.config.type !== "line") return;
        var ctx = chart.ctx, vis = [];
        chart.data.datasets.forEach(function (ds, i) { if (!ds._noLabel && chart.isDatasetVisible(i)) vis.push(i); });
        ctx.save(); ctx.font = "600 10px -apple-system,Segoe UI,Roboto,sans-serif";
        vis.forEach(function (i) {
          var meta = chart.getDatasetMeta(i), data = chart.data.datasets[i].data, lastJ = -1;
          for (var j = data.length - 1; j >= 0; j--) if (data[j] != null) { lastJ = j; break; }
          if (lastJ < 0) return;
          var el = meta.data[lastJ];
          if (el) { ctx.fillStyle = chart.data.datasets[i].borderColor; ctx.textAlign = "left"; ctx.fillText(fmt(data[lastJ]), el.x + 5, el.y + 3); }
          if (vis.length === 1) {
            var pk = -1, tr = -1;
            data.forEach(function (v, j2) {
              if (v == null) return;
              if (pk < 0 || v > data[pk]) pk = j2;
              if (tr < 0 || v < data[tr]) tr = j2;
            });
            [pk, tr].forEach(function (j2, which) {
              if (j2 < 0 || j2 === lastJ || (which === 1 && j2 === pk)) return;
              var e2 = meta.data[j2]; if (!e2) return;
              ctx.textAlign = "center"; ctx.fillStyle = chart.data.datasets[i].borderColor;
              var above = which === 0 || e2.y > chart.chartArea.bottom - 14;
              ctx.fillText(fmt(data[j2]), e2.x, above ? e2.y - 6 : e2.y + 13);
            });
          }
        });
        ctx.restore();
      }
    };
  }
  function chartOpts(fmt, bk, small) {
    return {
      responsive: true, maintainAspectRatio: false, animation: false,
      layout: small ? { padding: { right: 8, top: 8 } } : { padding: { right: 56, top: 16, bottom: 4 } },
      interaction: { mode: "nearest", axis: "x", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: function (items) { return items.length ? bkTitle(bk, items[0].dataIndex) : ""; },
            label: function (it) { return (it.dataset.label ? it.dataset.label + ": " : "") + fmt(it.parsed.y); }
          }
        }
      },
      scales: {
        x: { ticks: { maxTicksLimit: small ? 7 : 14, font: { size: small ? 9 : 10 } }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { font: { size: small ? 9 : 10 }, maxTicksLimit: small ? 5 : 8, callback: function (v) { return fmt(v); } } }
      }
    };
  }
  function lineDS(label, data, color, main, noLabel) {
    return { label: label, data: data, borderColor: color, backgroundColor: color,
      borderWidth: main ? 2.2 : 1.6, pointRadius: 0, tension: .25, fill: false,
      borderDash: noLabel ? [6, 4] : undefined, _noLabel: !!noLabel };
  }

  /* chip helpers (theme conventions: .chip + class "on" + inline colors) */
  function entityChip(label, color, on, cb) {
    var c = document.createElement("span");
    c.className = "chip" + (on ? " on" : "");
    c.textContent = label;
    c.style.borderColor = color;
    function paint(o) { c.classList.toggle("on", o); c.style.background = o ? color : ""; c.style.color = o ? "#fff" : ""; }
    paint(on);
    c.onclick = function () { var o = !c.classList.contains("on"); paint(o); cb(o); };
    return c;
  }

  /* ---------- chart card ---------- */
  function chartCard(opts) {
    /* opts: {host,P,title,mainEnt,mainLabel,overlays,state,span} */
    var card = document.createElement("div"); card.className = "clk-card";
    if (opts.span) card.style.gridColumn = "1/-1";
    var h = document.createElement("div"); h.className = "clk-title"; h.textContent = opts.title;
    card.appendChild(h);
    var chips = document.createElement("div"); chips.className = "chips clicks"; card.appendChild(chips);
    if (opts.note) {
      var nt = document.createElement("div"); nt.textContent = opts.note;
      nt.style.cssText = "font-size:11px;color:#64748B;margin:-2px 0 6px"; card.appendChild(nt);
    }
    var wrap = document.createElement("div"); wrap.className = "canvas-wrap clk";
    wrap.style.height = opts.span ? "300px" : "220px"; wrap.style.minWidth = "0";
    var cv = document.createElement("canvas"); wrap.appendChild(cv); card.appendChild(wrap);
    opts.host.appendChild(card);
    /* chip on/off state is supplied by the caller (opts.chip) so it SURVIVES a chart
       rebuild on metric / moving-avg / grain change — only a filter/range change (which
       changes the overlay set) resets it. chip.main = main line on; chip.on[i] = overlay i. */
    var chip = opts.chip || { main: true, on: {} };
    var chart = null, clHits = [];
    if (opts.markers) wireClCanvas(cv, clHits);   // changelog annotation dots (span chart only)
    function build() {
      var st = opts.state, P = opts.P, bk = st.bk, metric = st.metric, m = METRICS[metric];
      var ds = [];
      if (chip.main) {
        var main = seriesFor(P, bk, opts.mainEnt, metric);
        if (main) {
          ds.push(lineDS(opts.mainLabel, main, m.color, true));
          ds[ds.length - 1]._ent = opts.mainEnt;
        }
      }
      opts.overlays.forEach(function (o, i) {
        if (!chip.on[i]) return;
        var s = seriesFor(P, bk, o.ent, metric);
        if (!s) return;
        var d = lineDS(o.label, s, HUES[i % HUES.length]);
        d._ent = o.ent; ds.push(d);
      });
      if (st.ma) ds.slice().forEach(function (d0) {
        ds.push(lineDS(d0.label + " (avg)", maOf(opts.P, st.bk, d0._ent, metric, d0.data), d0.borderColor, false, true));
      });
      if (chart) chart.destroy();
      var plugins = [lineLabels(m.fmt)], opt = chartOpts(m.fmt, st.bk);
      if (opts.markers) { plugins.push(clMarkerPlugin(function () { return clDayMap(opts.markers.tab, opts.markers.mode(), st.bk); }, clHits)); opt.layout.padding.bottom = 24; }
      chart = regChart(new Chart(cv, { type: "line", data: { labels: st.bk.labels, datasets: ds }, plugins: plugins, options: opt }));
    }
    chips.appendChild(entityChip(opts.mainLabel, "#162050", chip.main, function (o) { chip.main = o; build(); }));
    opts.overlays.forEach(function (o, i) {
      chips.appendChild(entityChip(o.label, HUES[i % HUES.length], !!chip.on[i], function (v) { chip.on[i] = v; build(); }));
    });
    build();
    return card;
  }

  /* ---------- shared controls ---------- */
  /* segmented Day/Week/Month control — rebuilds state.bk + fires onChange. Used in
     the platform Daily-Metrics card (grain lives with the chart it controls, 2026-06-15). */
  function grainControl(state, dates, onChange) {
    var grains = document.createElement("span"); grains.className = "grainbtns";
    [["day", "Day"], ["week", "Week"], ["month", "Month"]].forEach(function (g) {
      var b = document.createElement("span");
      b.className = "gbtn" + (state.grain === g[0] ? " on" : ""); b.textContent = g[1];
      b.onclick = function () {
        state.grain = g[0];
        grains.querySelectorAll(".gbtn").forEach(function (x) { x.classList.toggle("on", x.textContent === g[1]); });
        state.bk = buckets(dates, state.range, state.grain);
        onChange();
      };
      grains.appendChild(b);
    });
    return grains;
  }
  function dateControls(host, dates, state, onChange, opts) {
    opts = opts || {};
    var bar = document.createElement("div"); bar.className = "rangebar";
    var row = document.createElement("div"); row.className = "rb-row";
    var sel = document.createElement("select"); sel.className = "rangesel";
    PRESETS.forEach(function (p) { var o = document.createElement("option"); o.value = p[0]; o.textContent = p[1]; if (p[0] === state.preset) o.selected = true; sel.appendChild(o); });
    var c1 = document.createElement("input"), c2 = document.createElement("input");
    c1.type = "date"; c2.type = "date"; c1.min = c2.min = dates[0]; c1.max = c2.max = dates[dates.length - 1];
    c1.value = dates[Math.max(0, dates.length - 30)]; c2.value = dates[dates.length - 1];
    var cw = document.createElement("span"); cw.className = "customdates"; cw.style.display = "none";
    cw.appendChild(c1); cw.appendChild(document.createTextNode(" → ")); cw.appendChild(c2);
    var lbl = document.createElement("div"); lbl.className = "rangelbl";
    function recompute() {
      state.range = rangeFor(state.preset, dates, c1.value, c2.value);
      state.bk = buckets(dates, state.range, state.grain);
      lbl.textContent = dates[state.range[0]] + " → " + dates[state.range[1]] + " (" + (state.range[1] - state.range[0] + 1) + " days)";
      onChange();
    }
    function apply() { state.preset = sel.value; cw.style.display = sel.value === "custom" ? "" : "none"; recompute(); }
    sel.onchange = apply; c1.onchange = apply; c2.onchange = apply;
    row.appendChild(sel); row.appendChild(cw);
    if (!opts.noGrain) row.appendChild(grainControl(state, dates, recompute));
    bar.appendChild(row); bar.appendChild(lbl);
    // Prepend so the sticky bar stays at the top even when rangehost wraps all tab content
    host.insertBefore(bar, host.firstChild);
    state.range = rangeFor(state.preset, dates, c1.value, c2.value);
    state.bk = buckets(dates, state.range, state.grain);
    lbl.textContent = dates[state.range[0]] + " → " + dates[state.range[1]] + " (" + (state.range[1] - state.range[0] + 1) + " days)";
    return { row: row, recompute: recompute };
  }
  /* multi-select dropdown (search + All/Clear). selSet is the live selection (empty = All).
     setItems prunes selections that fell out of the current option list (range/cascade). */
  function msControl(label, selSet, onch) {
    var box = document.createElement("div"); box.className = "ms";
    var btn = document.createElement("span"); btn.className = "gbtn msbtn";
    var panel = document.createElement("div"); panel.className = "mspanel"; panel.style.display = "none";
    var search = document.createElement("input"); search.placeholder = "search…"; search.className = "mssearch";
    var acts = document.createElement("div"); acts.className = "msacts";
    acts.innerHTML = '<a data-a="all">Select all</a> · <a data-a="clear">Clear</a>';
    var list = document.createElement("div"); list.className = "mslist";
    var items = [];
    function sync() { btn.textContent = label + ": " + (selSet.size ? selSet.size + " of " + items.length : "All") + " ▾"; }
    function render() {
      var q = search.value.toLowerCase();
      list.innerHTML = "";
      items.forEach(function (it) {
        if (q && it.label.toLowerCase().indexOf(q) < 0) return;
        var l = document.createElement("label");
        var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = selSet.has(it.v);
        cb.onchange = function () { cb.checked ? selSet.add(it.v) : selSet.delete(it.v); sync(); onch(); };
        l.appendChild(cb); l.appendChild(document.createTextNode(" " + it.label));
        if (it.sub) { var s = document.createElement("span"); s.className = "mssub"; s.textContent = it.sub; l.appendChild(s); }
        list.appendChild(l);
      });
    }
    search.oninput = render;
    acts.onclick = function (e) {
      var a = e.target && e.target.dataset.a; if (!a) return;
      if (a === "clear") selSet.clear(); else items.forEach(function (it) { selSet.add(it.v); });
      render(); sync(); onch();
    };
    btn.onclick = function (e) {
      e.stopPropagation();
      var open = panel.style.display !== "none";
      document.querySelectorAll(".mspanel").forEach(function (p) { p.style.display = "none"; });
      panel.style.display = open ? "none" : "";
      if (!open) { search.value = ""; render(); search.focus(); }
    };
    document.addEventListener("click", function (e) { if (!box.contains(e.target)) panel.style.display = "none"; });
    panel.appendChild(search); panel.appendChild(acts); panel.appendChild(list);
    box.appendChild(btn); box.appendChild(panel);
    sync();
    return { el: box, sync: sync, setItems: function (its) {
      items = its;
      Array.from(selSet).forEach(function (v) { if (!its.some(function (it) { return it.v === v; })) selSet.delete(v); });
      sync(); render();
    } };
  }

  function deltaHtml(cur, prev, opts) {
    /* number + percent + arrow; opts: {fmt, lowerGood, neutral, pts} */
    if (prev == null || !isFinite(prev) || (prev === 0 && cur === 0)) return '<div class="kdelta flat">—</div>';
    var d = cur - prev, fmt = opts.fmt || fmtInt;
    var pct = prev !== 0 ? d / Math.abs(prev) * 100 : null;
    var arrow = d > 0 ? "▲" : d < 0 ? "▼" : "■";
    var cls = "flat";
    if (!opts.neutral && d !== 0) cls = (opts.lowerGood ? d < 0 : d > 0) ? "good" : "bad";
    if (opts.pts) {  // percentage-point metric (e.g. Impression Share) — pts already conveys magnitude
      var ptsTxt = (d >= 0 ? "+" : "−") + Math.abs(d * 100).toFixed(1) + " pts";
      return '<div class="kdelta ' + cls + '">' + arrow + " " + ptsTxt + ' <span class="vsprior">vs prior</span></div>';
    }
    var num = (d >= 0 ? "+" : "−") + fmt(Math.abs(d)).replace(/^\$?/, opts.fmt === undefined ? "" : "$").replace("$$", "$");
    if (opts.fmt) num = (d >= 0 ? "+" : "−") + opts.fmt(Math.abs(d));
    else num = (d >= 0 ? "+" : "−") + fmtInt(Math.abs(d));
    var pctTxt = opts.pts ? Math.abs(d * 100).toFixed(1) + " pts" : pct == null ? "" : "(" + (pct >= 0 ? "+" : "−") + Math.abs(pct).toFixed(0) + "%)";
    return '<div class="kdelta ' + cls + '">' + arrow + " " + num + " " + pctTxt + ' <span class="vsprior">vs prior</span></div>';
  }
  function badge(cur, prev, opts) {
    if (prev == null || !isFinite(prev)) return "";
    var d = cur - prev;
    var cls = "flat";
    if (d !== 0) cls = (opts && opts.lowerGood ? d < 0 : d > 0) ? "good" : "bad";
    if (opts && opts.neutral) cls = "flat";
    var pct = prev !== 0 ? d / Math.abs(prev) * 100 : null;
    var txt = opts && opts.pts ? (d >= 0 ? "+" : "−") + Math.abs(d * 100).toFixed(1) + "pts"
      : pct == null ? (d >= 0 ? "+" : "−") + fmtInt(Math.abs(d)) : (pct >= 0 ? "+" : "−") + Math.abs(pct).toFixed(0) + "%";
    return ' <span class="delta ' + cls + '">' + txt + "</span>";
  }

  function sumRange(P, r, ent) {
    var bk1 = { keys: ["x"], labels: ["x"], map: P.dates.map(function (_, i) { return i >= r[0] && i <= r[1] ? 0 : -1; }), grain: "day" };
    var A = makeAgg(P, bk1, ent || { kind: "all" });
    return { spend: A.spend[0], clicks: A.clicks[0], imps: A.imps[0], demos: A.demos[0], opps: A.opps[0], accts: A.accts[0], arr: A.arr[0] };
  }
  function rangeRankIS(P, r, ent) {
    /* cost-weighted search-impression-share over the range for an entity, via aggRankIS —
       so it's ad-group-aware: reads agrankis (Google) under an ad-group filter, the campaign
       rankis otherwise. Returns null when there's no source (e.g. Meta, or an ad-group filter
       on Microsoft where Bing has no ad-group impr-share). */
    if (!P.rankis || !P.rankis.length) return null;
    var bk1 = { keys: ["x"], labels: ["x"], map: P.dates.map(function (_, i) { return i >= r[0] && i <= r[1] ? 0 : -1; }), grain: "day" };
    return aggRankIS(P, bk1, ent || { kind: "all" }).is[0];
  }
  function kpiLink(tabKey, grain, metricKey) {
    var L = DATA.links && DATA.links[tabKey];
    return (L && L[grain] && metricKey && L[grain][metricKey]) || null;
  }
  function kpiCards(host, getItems) {
    /* items: [label, value, deltaHtml, linkUrl?] — value becomes a link to the live
       SF report (grain-aware: day/week/month) when a url is provided */
    var div = document.createElement("div"); div.className = "kpis";
    // Insert right after the rangebar (dateControls always runs first and prepends it)
    var rb = host.querySelector(".rangebar");
    if (rb && rb.nextSibling) { host.insertBefore(div, rb.nextSibling); }
    else { host.appendChild(div); }
    return { update: function () {
      div.innerHTML = getItems().map(function (it) {
        var val = it[3] ? '<a class="kpilink" href="' + it[3] + '" target="_blank" rel="noopener" title="Open the live Salesforce report">' + it[1] + " ↗</a>" : it[1];
        // it[4] = optional tooltip text → ⓘ info popover next to the label (basis note, etc.)
        var lab = it[0] + (it[4] ? ' <span class="tip kpitip" tabindex="0" role="note" aria-label="About ' + it[0] + '"><span class="tipbox">' + it[4] + '</span>i</span>' : '');
        return '<div class="kpi"><div class="klabel">' + lab + '</div><div class="kval">' + val + "</div>" + (it[2] || '<div class="kdelta flat">&nbsp;</div>') + "</div>";
      }).join("");
    } };
  }

  function accCard(host, title, collapsed) {
    var card = document.createElement("div"); card.className = "card accordion" + (collapsed ? " collapsed" : "");
    var h2 = document.createElement("h2"); h2.textContent = title;
    h2.addEventListener("click", function () { card.classList.toggle("collapsed"); });
    var body = document.createElement("div"); body.className = "acc-body";
    card.appendChild(h2); card.appendChild(body); host.appendChild(card);
    return body;
  }

  /* ================= platform tab ================= */
  function topCamps(P, r, n) {
    var sp = {};
    for (var i = 0; i < P.spend.length; i++) {
      var row = P.spend[i];
      if (row[0] < r[0] || row[0] > r[1]) continue;
      sp[row[1]] = (sp[row[1]] || 0) + row[3];
    }
    return Object.keys(sp).map(Number).sort(function (a, b) { return sp[b] - sp[a]; }).slice(0, n || 12);
  }
  /* ad groups ACTIVE in the selected range: impressions > 1 (catches ad groups paused
     today but enabled during the window), sorted by impressions desc. Caller slices + notes. */
  function topGroups(P, c, r) {
    var im = {};
    for (var i = 0; i < P.spend.length; i++) {
      var row = P.spend[i];
      if (row[0] < r[0] || row[0] > r[1] || row[1] !== c) continue;
      im[row[2]] = (im[row[2]] || 0) + row[5];
    }
    return Object.keys(im).map(Number).filter(function (g) { return im[g] > 1; })
      .sort(function (a, b) { return im[b] - im[a]; });
  }
  function initPlatformTab(key) {
    var P = DATA[key];
    var hostK = document.getElementById("kpi-" + key);
    var hostC = document.getElementById("charts-" + key);
    if (!P || !hostC) return null;
    var state = { preset: "l30", grain: "day", metric: "demos", ma: false, view: "cur",
                  campSel: new Set(), grpSel: new Set(), retB: "ft",
                  chip: { main: true, on: {} }, _sig: null, _after: [] };
    function selEnt() {
      // UNFILTERED view → kind "all" so the channel total INCLUDES unattributed conversions
      // (records whose UTM campaign doesn't map to a tracked campaign). "sel" with null camps
      // silently drops camp_idx<0; "all" keeps them — matching the All-campaigns chart line AND
      // the Salesforce channel totals. Only switch to "sel" once a Campaign/Ad-group filter is on. (2026-06-17)
      if (!state.campSel.size && !state.grpSel.size) return { kind: "all" };
      return { kind: "sel", camps: state.campSel.size ? state.campSel : null, grps: state.grpSel.size ? state.grpSel : null };
    }
    function campsOrNull() { return state.campSel.size ? state.campSel : null; }

    var dc = dateControls(hostK, P.dates, state, refresh, { noGrain: true });
    /* Campaign + Ad-group filters live in the top bar (right of the range), drive the
       whole tab. Ad-group list cascades from the campaign selection + impressions in range. */
    var msCamp = msControl("Campaign", state.campSel, refresh);
    var msSet = msControl("Ad group", state.grpSel, refresh);
    var reset = document.createElement("span"); reset.className = "gbtn"; reset.textContent = "Reset filters";
    reset.onclick = function () { state.campSel.clear(); state.grpSel.clear(); msCamp.sync(); msSet.sync(); refresh(); };
    dc.row.appendChild(msCamp.el); dc.row.appendChild(msSet.el); dc.row.appendChild(reset);

    var clSec = initChangelogSection(key, function () { return [P.dates[state.range[0]], P.dates[state.range[1]]]; }, function () { refresh(); });
    var kp = kpiCards(hostK, function () {
      var e = selEnt(), grpA = state.grpSel.size > 0;
      var t = sumRange(P, state.range, e), pr = priorRange(state.preset, state.range, P.dates);
      var p = pr ? sumRange(P, pr, e) : null;
      var cpd = t.demos > 0 ? t.spend / t.demos : null;
      var pcpd = p && p.demos > 0 ? p.spend / p.demos : null;
      var roas = t.spend > 0 ? t.arr / t.spend : null;   // ad-group-aware: t.arr is agpipeline under a filter
      var $ = function (v) { return fmtMoney(v); };
      var L = function (mk) { return grpA ? null : kpiLink(key, state.grain, mk); };  // SF report links aren't filtered
      var items = [
        ["Spend", fmtMoney(t.spend), p && deltaHtml(t.spend, p.spend, { fmt: $, neutral: true })],
        ["Impressions", fmtInt(t.imps), p && deltaHtml(t.imps, p.imps, {})]
      ];
      /* "Monthly Run Rate" — the channel's full-month spend projection, copied verbatim from the
         All-Sources run-rate card (DATA.runrate, computed once in build.py). Range-independent: it's
         always the current month's projection, not the selected range. (2026-06-17) */
      var rr = DATA.runrate && DATA.runrate[key];
      if (rr) items.splice(1, 0, ["Monthly Run Rate", fmtMoney(rr.full),
        '<div class="kdelta flat"><span class="vsprior">full-month spend projection</span></div>']);
      if (P.rankis && P.rankis.length) {   // Google/Microsoft only (Meta has none)
        // ad-group-aware: rangeRankIS reads agrankis under an ad-group filter (Google), else the
        // campaign rankis — Microsoft/Meta have no ad-group agrankis → null → "—" under that filter.
        var is = rangeRankIS(P, state.range, e), pis = pr ? rangeRankIS(P, pr, e) : null;
        items.push(["Impr Share", is == null ? "—" : fmtPct(is, 1),
          (is != null && pis != null) ? deltaHtml(is, pis, { pts: true }) : null]);
      }
      items.push(["Clicks", fmtInt(t.clicks), p && deltaHtml(t.clicks, p.clicks, {})]);
      if (key === "meta") {   // CPC + CPM (Meta tab only); lower is better, so down = good
        var m2 = function (v) { return fmtMoney(v, 2); };
        var cpcv = t.clicks > 0 ? t.spend / t.clicks : null;
        var cpmv = t.imps > 0 ? t.spend * 1000 / t.imps : null;
        var pcpc = p && p.clicks > 0 ? p.spend / p.clicks : null;
        var pcpm = p && p.imps > 0 ? p.spend * 1000 / p.imps : null;
        items.push(
          ["CPC", cpcv == null ? "—" : m2(cpcv), (cpcv != null && pcpc != null) ? deltaHtml(cpcv, pcpc, { fmt: m2, lowerGood: true }) : null],
          ["CPM", cpmv == null ? "—" : m2(cpmv), (cpmv != null && pcpm != null) ? deltaHtml(cpmv, pcpm, { fmt: m2, lowerGood: true }) : null]
        );
      }
      // Per-tab basis tooltips on the funnel KPIs. Meta demos = SF first-touch event-view;
      // Google/Microsoft demos = Brain Day-0 cohort. Opps/Accounts/ARR are booked-on-day (all tabs).
      var COHORT = "Based on the Brain Day-0 cohort (first website visit at click-date)";
      var ARRTIP = "SUM of Stripe Subscription ARR, booked on the Stripe first-invoice date without coupons, AI or add-ons";
      var TIP = {
        demos: { meta: "Based on Original Demo Scheduled At date.", google: COHORT, microsoft: COHORT },
        opps:  { meta: "Based on the Opp Created Date.", google: "Based on the Opportunity Created Date", microsoft: "Based on the Opportunity Created Date" },
        accts: { meta: "Based on the Stripe Subscription First Invoice At Date.", google: "Based on the Stripe Subscription First Invoice At date", microsoft: "Based on the Stripe Subscription First Invoice At date" },
        arr:   { google: ARRTIP, microsoft: ARRTIP }
      };
      items.push(
        ["Demos", fmtInt(t.demos), p && deltaHtml(t.demos, p.demos, {}), L("demos"), TIP.demos[key] || null],
        ["Cost / Demo", cpd == null ? "—" : fmtMoney(cpd), pcpd != null && cpd != null ? deltaHtml(cpd, pcpd, { fmt: $, lowerGood: true }) : null],
        ["Opps", fmtInt(t.opps), p && deltaHtml(t.opps, p.opps, {}), L("opps"), TIP.opps[key] || null],
        ["Accounts", fmtInt(t.accts), p && deltaHtml(t.accts, p.accts, {}), L("accts"), TIP.accts[key] || null],
        ["New ARR", fmtMoney(t.arr), p && deltaHtml(t.arr, p.arr, { fmt: $ }), L("arr"), TIP.arr[key] || null],
        ["ROAS", roas == null ? "—" : roas.toFixed(2) + "x", null]
      );
      return items;
    });

    // Attribution-basis banner, just above the KPI widgets. Meta = SF first-touch event-view;
    // Google/Microsoft demos = Brain Day-0 cohort (Opps/Accounts/ARR are booked-on-day from SF).
    var BASIS_BANNER = {
      meta: "All data is first-touch event-view from Salesforce (not cohort view).",
      google: "Demos = Brain Day-0 cohort (first website visit at click-date). Opps, Accounts & ARR = booked-on-day from Salesforce.",
      microsoft: "Demos = Brain Day-0 cohort (first website visit at click-date). Opps, Accounts & ARR = booked-on-day from Salesforce."
    };
    if (BASIS_BANNER[key]) {
      var kEl = hostK.querySelector(".kpis");
      if (kEl) {
        var bn = document.createElement("div"); bn.className = "basisbanner";
        bn.textContent = BASIS_BANNER[key];
        hostK.insertBefore(bn, kEl);
      }
    }

    var avail = ["demos", "clicks", "imps", "spend", "cpc"];
    if (key === "meta") avail.push("cpm");
    avail = avail.concat(["cpd", "ctd", "opps", "accts", "arr"]);
    if (P.rankis && P.rankis.length) avail = avail.concat(["rankis", "imprshare"]);

    /* consolidated control row inside the card: grain · view · moving-avg (grain moved
       here from the top bar 2026-06-15). Metric chips sit on their own row below. */
    var ctrls = document.createElement("div"); ctrls.className = "clk-ctrls";
    ctrls.appendChild(grainControl(state, P.dates, refresh));
    var views = document.createElement("div"); views.className = "clk-views";
    views.innerHTML = '<span class="vbtn on" data-v="cur">Current view</span><span class="vbtn" data-v="grid">Metric grid</span>';
    views.onclick = function (e) {
      var v = e.target && e.target.dataset.v; if (!v) return;
      state.view = v;
      views.querySelectorAll(".vbtn").forEach(function (b) { b.classList.toggle("on", b.dataset.v === v); });
      refresh();
    };
    ctrls.appendChild(views);
    var avg = document.createElement("span"); avg.className = "chip avgbtn"; avg.textContent = "〜 " + maLabel(state.grain);
    avg.onclick = function () {
      state.ma = !state.ma; avg.classList.toggle("on", state.ma);
      avg.style.background = state.ma ? "#64748B" : ""; avg.style.borderColor = state.ma ? "#64748B" : ""; avg.style.color = state.ma ? "#fff" : "";
      refresh();
    };
    ctrls.appendChild(avg);
    hostC.appendChild(ctrls);

    /* metric toggle (theme: .metric-toggle + span.mbtn[data-m]) */
    var mt = document.createElement("div"); mt.className = "metric-toggle";
    avail.forEach(function (k) {
      var b = document.createElement("span");
      b.className = "mbtn" + (state.metric === k ? " on" : "");
      b.dataset.m = k; b.textContent = METRICS[k].label;
      b.onclick = function () {
        state.metric = k;
        mt.querySelectorAll(".mbtn").forEach(function (x) { x.classList.toggle("on", x.dataset.m === k); });
        refresh();
      };
      mt.appendChild(b);
    });
    hostC.appendChild(mt);
    /* Retargeting attribution toggle (Meta only): switches the Retargeting line's demo basis
       among Salesforce First-touch (default/headline) · Last-touch · All-touch. Affects ONLY the
       Retargeting line (and its CPD / Click→Demo); every other line + the KPIs stay first-touch. */
    if (key === "meta" && retIdxOf(P) >= 0 && (P.retarget || []).length) {
      var rb = document.createElement("div"); rb.className = "metric-toggle"; rb.style.marginTop = "4px";
      rb.appendChild(Object.assign(document.createElement("span"),
        { textContent: "Retargeting demos:", style: "font-size:12px;color:#64748B;align-self:center;margin-right:4px" }));
      [["ft", "First-touch"], ["lt", "Last-touch"], ["at", "All-touch"]].forEach(function (o) {
        var b = document.createElement("span"); b.className = "mbtn" + (state.retB === o[0] ? " on" : "");
        b.dataset.rb = o[0]; b.textContent = o[1];
        b.onclick = function () {
          state.retB = o[0];
          rb.querySelectorAll(".mbtn").forEach(function (x) { x.classList.toggle("on", x.dataset.rb === o[0]); });
          refresh();
        };
        rb.appendChild(b);
      });
      hostC.appendChild(rb);
    }
    var note = document.createElement("div"); note.className = "clk-pipenote"; note.style.display = "none"; hostC.appendChild(note);
    var grid = document.createElement("div"); grid.className = "clk-grid"; hostC.appendChild(grid);

    /* hide the whole-account day-snapshot tables (All Campaigns / Excl. Branded / Meta
       Performance Summary) while a filter is active — they can't represent a subset. */
    var filtNote = document.createElement("section"); filtNote.className = "mcsec"; filtNote.style.display = "none";
    filtNote.innerHTML = '<div class="card"><p class="muted" style="margin:0">Filtered view — the whole-account day-snapshot table' +
      (key === "meta" ? " (Performance Summary)" : "s (All Campaigns / Excl. Branded)") +
      ' below are hidden because they are single-day, whole-account tables that can\'t represent a campaign / ad-group subset. The filtered, range-aware totals are in the KPIs and chart above; clear the filter to see them.</p></div>';
    var firstAnchor = document.getElementById("allcamp-" + key) || document.getElementById("perf-" + key);
    if (firstAnchor && firstAnchor.parentNode) firstAnchor.parentNode.insertBefore(filtNote, firstAnchor);
    function setAnchorVis(filtered) {
      ["allcamp-", "excl-", "perf-"].forEach(function (pfx) {
        var el = document.getElementById(pfx + key); if (el) el.style.display = filtered ? "none" : "";
      });
      filtNote.style.display = filtered ? "" : "none";
    }
    function refreshFilterOptions() {
      var r = state.range, campSp = {};
      for (var i = 0; i < P.spend.length; i++) { var row = P.spend[i]; if (row[0] < r[0] || row[0] > r[1]) continue; campSp[row[1]] = (campSp[row[1]] || 0) + row[3]; }
      msCamp.setItems(Object.keys(campSp).map(Number).sort(function (a, b) { return campSp[b] - campSp[a]; })
        .map(function (c) { return { v: c, label: P.campaigns[c] ? P.campaigns[c][1] : "?" }; }));
      var eff = campsOrNull(), grpImp = {};
      for (var j = 0; j < P.spend.length; j++) { var rw = P.spend[j]; if (rw[0] < r[0] || rw[0] > r[1]) continue; if (eff && !eff.has(rw[1])) continue; grpImp[rw[2]] = (grpImp[rw[2]] || 0) + rw[5]; }
      msSet.setItems(Object.keys(grpImp).map(Number).filter(function (g) { return grpImp[g] > 1; }).sort(function (a, b) { return grpImp[b] - grpImp[a]; })
        .map(function (g) { var gr = P.groups[g], ci = gr[gr.length - 1]; return { v: g, label: gr.length === 3 ? gr[1] : gr[0], sub: P.campaigns[ci] ? P.campaigns[ci][1] : "" }; }));
    }

    function basisNote() {
      var bIdx = P.dates.indexOf(P.demo_boundary || "");
      var spans = bIdx > 0 && state.range[0] < bIdx;
      var bits = [];
      if (key === "meta" && ["demos", "cpd", "ctd"].indexOf(state.metric) >= 0)
        bits.push("Meta demos = Salesforce first-touch (first-click UTM campaign). The Retargeting line has a First / Last / All-touch toggle (first-touch is the headline; last-touch and all-touch are also from Salesforce — all-touch is an assist count that slightly under-counts very long journeys).");
      if (spans && ["demos", "cpd", "ctd"].indexOf(state.metric) >= 0)
        bits.push("⚠ Demo basis changes on " + P.demo_boundary + ": before = Salesforce Day-0 (the Brain retains only 12 months), after = Brain Day-0 — expect a step at the boundary" + (key === "meta" ? " (SF undercounts Meta vs Brain)" : "") + ".");
      if (spans && ["opps", "accts", "arr"].indexOf(state.metric) >= 0)
        bits.push("⚠ Pipeline attribution before " + P.demo_boundary + " is Salesforce UTM name-matching; unmatched campaigns roll into the account total only.");
      if ((P.agdemos || []).length && ["demos", "cpd", "ctd"].indexOf(state.metric) >= 0)
        bits.push("Per-ad-group demos are Day-0 cohort (" + (key === "meta" ? "Salesforce" : "Brain") + " attribution); some demos carry no ad-group in the UTM, so ad-group lines won't fully sum to the campaign total.");
      if (state.grpSel.size && ["opps", "accts", "arr"].indexOf(state.metric) >= 0)
        bits.push("Per-ad-group Opps / Accounts / ARR are Salesforce booked-on-day, attributed via the converting lead's ad group; some pipeline carries no ad-group, so ad-group lines won't fully sum to the campaign total.");
      if (state.grpSel.size && ["rankis", "imprshare"].indexOf(state.metric) >= 0 && !(P.agrankis || []).length)
        bits.push("Impression share / rank-lost IS has no ad-group breakdown on " + (key === "meta" ? "Meta" : "Microsoft") + " — it is blank while an ad-group filter is active (Google has ad-group impression share; Microsoft/Meta do not).");
      note.style.display = bits.length ? "" : "none";
      note.textContent = bits.join(" ");
    }
    function refresh() {
      avg.textContent = "〜 " + maLabel(state.grain);
      refreshFilterOptions();
      kp.update(); basisNote();
      if (clSec) clSec.refresh();
      updateHistSections(key, state, P.dates);
      state._after.forEach(function (fn) { fn(); });   // dependent sections (e.g. Meta leaderboards) follow the top-of-tab filters
      P._aggCache = null;
      var filtered = state.campSel.size > 0 || state.grpSel.size > 0, grpA = state.grpSel.size > 0;
      setAnchorVis(filtered);
      grid.innerHTML = "";
      grid.classList.toggle("grid-metrics", state.view === "grid");
      if (state.view === "grid") return renderGrid(selEnt());
      /* reset the chart's line-chip selections ONLY when the overlay set changes
         (filter or range). Metric / moving-avg / grain switches keep the same overlays,
         so the user's toggled lines persist across them (2026-06-15). */
      var sig = selHash(selEnt()) + "|" + state.range.join("-");
      if (sig !== state._sig) { state.chip = { main: true, on: {} }; state._sig = sig; }
      var overlays = [], mainEnt, mainLabel, title, moreNote = null;
      if (!filtered) {
        mainEnt = { kind: "all" }; mainLabel = "All campaigns"; title = "All Campaigns";
        if (P.flags && P.flags.branded) overlays.push({ ent: { kind: "xb" }, label: "Ex-Branded" });
        topCamps(P, state.range, 12).forEach(function (c) { overlays.push({ ent: { kind: "camp", c: c }, label: P.campaigns[c][1] }); });
      } else {
        mainEnt = selEnt(); mainLabel = "Selection total"; title = "Filtered selection";
        if (grpA) {
          var gim = {};
          for (var i = 0; i < P.spend.length; i++) { var rw = P.spend[i]; if (rw[0] < state.range[0] || rw[0] > state.range[1]) continue; if (state.grpSel.has(rw[2])) gim[rw[2]] = (gim[rw[2]] || 0) + rw[5]; }
          var gs = Array.from(state.grpSel).sort(function (a, b) { return (gim[b] || 0) - (gim[a] || 0); });
          gs.slice(0, 14).forEach(function (g) { var gr = P.groups[g], ci = gr[gr.length - 1]; overlays.push({ ent: { kind: "grp", c: ci, g: g }, label: gr.length === 3 ? gr[1] : gr[0] }); });
          if (gs.length > 14) moreNote = "Showing the top 14 of " + gs.length + " selected ad groups (by impressions).";
        } else {
          Array.from(state.campSel).forEach(function (c) { overlays.push({ ent: { kind: "camp", c: c }, label: P.campaigns[c] ? P.campaigns[c][1] : "?" }); });
        }
      }
      /* Retargeting attribution toggle (Meta): tag the Retargeting campaign overlay so makeAgg
         swaps its demos to last/all-touch, and label the line with the active basis. */
      if (key === "meta" && state.retB !== "ft") {
        var ri = retIdxOf(P), rlbl = state.retB === "lt" ? "last-touch" : "all-touch";
        overlays.forEach(function (o) { if (o.ent.kind === "camp" && o.ent.c === ri) { o.ent.retB = state.retB; o.label += " (" + rlbl + ")"; } });
      }
      chartCard({ host: grid, P: P, title: title, mainEnt: mainEnt, mainLabel: mainLabel, overlays: overlays, state: state, span: true, note: moreNote,
        chip: state.chip,
        markers: { tab: key, mode: function () { return clSec ? clSec.mode() : "all"; } } });
    }
    function renderGrid(ent) {
      avail.forEach(function (mk) {
        var s = seriesFor(P, state.bk, ent, mk);
        if (!s || !s.some(function (v) { return v != null; })) return;   // skip metrics with no data for this selection
        var card = document.createElement("div"); card.className = "clk-card";
        var t = document.createElement("div"); t.className = "clk-title"; t.textContent = METRICS[mk].label;
        card.appendChild(t);
        var wrap = document.createElement("div"); wrap.className = "canvas-wrap clk";
        wrap.style.height = "150px"; wrap.style.minWidth = "0";
        var cv = document.createElement("canvas"); wrap.appendChild(cv); card.appendChild(wrap);
        grid.appendChild(card);
        var ds = [lineDS("", s, "#3185FC", false)];
        if (state.ma) ds.push(lineDS("avg", maOf(P, state.bk, ent, mk, s), "#3185FC", false, true));
        regChart(new Chart(cv, { type: "line", data: { labels: state.bk.labels, datasets: ds }, options: chartOpts(METRICS[mk].fmt, state.bk, true) }));
      });
    }
    refresh();
    return { refresh: refresh, state: state, P: P };
  }

  /* ================= All Sources tab ================= */
  var AS_METRICS = {
    mqls:      { label: "MQLs",         arr: "mqls",       fmt: fmtInt, color: "#162050" },
    demos:     { label: "Demos Sched",  arr: "demos",      fmt: fmtInt, color: "#3185FC" },
    demos_day0:{ label: "Day-0 Cohort", arr: "demos_day0", fmt: fmtInt, color: "#7C3AED" },
    completed: { label: "Demos Compl",  arr: "completed",  fmt: fmtInt, color: "#0D9488" },
    opps:      { label: "Opps",         arr: "opps",       fmt: fmtInt, color: "#0EA5E9" },
    accounts:  { label: "Accounts",     arr: "accounts",   fmt: fmtInt, color: "#9333EA" },
    arr:       { label: "New ARR",      arr: "arr",        fmt: function (v) { return fmtMoney(v); }, color: "#059669" },
    r_mql_demo: { label: "MQL→Demo",    num: "demos",     den: "mqls",      fmt: function (v) { return fmtPct(v); }, color: "#B45309" },
    r_demo_comp:{ label: "Sched→Compl", num: "completed", den: "demos",     fmt: function (v) { return fmtPct(v); }, color: "#DB2777" },
    r_comp_opp: { label: "Compl→Opp",   num: "opps",      den: "completed", fmt: function (v) { return fmtPct(v); }, color: "#15803D" },
    r_opp_acct: { label: "Opp→Acct",    num: "accounts",  den: "opps",      fmt: function (v) { return fmtPct(v); }, color: "#64748B" }
  };
  var AS_ORDER = ["all", "google", "google_xb", "google_brand", "bing", "bing_xb", "bing_brand", "meta", "blog", "direct", "reviews"];
  function asSliceArr(A, slice, arrName) {
    if (slice === "google_brand" || slice === "bing_brand") {
      var base = slice === "google_brand" ? "google" : "bing", xb = base + "_xb";
      return A.slices[base][arrName].map(function (v, i) { return v - A.slices[xb][arrName][i]; });
    }
    return A.slices[slice][arrName];
  }
  function asLabel(A, slice) {
    if (slice === "google_brand") return "Google Ads Brand";
    if (slice === "bing_brand") return "Bing Ads Brand";
    return A.slices[slice].label;
  }
  function asBucket(A, slice, mk, bk, r) {
    var M = AS_METRICS[mk], nb = bk.keys.length;
    var out = new Array(nb).fill(0), den = M.den ? new Array(nb).fill(0) : null;
    for (var i = r[0]; i <= r[1]; i++) {
      var b = bk.map[i]; if (b < 0) continue;
      if (M.arr) out[b] += asSliceArr(A, slice, M.arr)[i];
      else { out[b] += asSliceArr(A, slice, M.num)[i]; den[b] += asSliceArr(A, slice, M.den)[i]; }
    }
    if (M.den) return { vals: out.map(function (v, j) { return den[j] > 0 ? v / den[j] : null; }), num: out, den: den };
    return { vals: out };
  }
  function asSum(A, slice, mk, r) {
    var M = AS_METRICS[mk], s = 0, d = 0;
    for (var i = r[0]; i <= r[1]; i++) {
      if (M.arr) s += asSliceArr(A, slice, M.arr)[i];
      else { s += asSliceArr(A, slice, M.num)[i]; d += asSliceArr(A, slice, M.den)[i]; }
    }
    return M.den ? (d > 0 ? s / d : null) : s;
  }
  function initAllSources() {
    var A = DATA.allsources;
    var hostK = document.getElementById("kpi-allsources");
    var hostC = document.getElementById("charts-allsources");
    var hostT = document.getElementById("as-tables");
    if (!A || !hostC) return null;
    var state = { preset: "l30", grain: "day", metric: "demos", ma: false };
    dateControls(hostK, A.dates, state, refresh);
    var clSec = initChangelogSection("allsources", function () { return [A.dates[state.range[0]], A.dates[state.range[1]]]; }, function () { refresh(); });
    // Per-KPI source/basis tooltips (mirrors the channel tabs' Round 18 pattern). These
    // explain WHY a channel's "Demos Sched" here (Salesforce, Original Demo Scheduled date)
    // can differ from the same channel's Demos on the Google/Microsoft tabs (Brain Day-0
    // cohort) — the "Day-0 Cohort" KPI here is that same cohort basis. (David, 2026-06-19.)
    var AS_TIP = {
      mqls: "MQLs (Is_MQL) counted on the lead-created date — Salesforce.",
      demos: "Original demos scheduled, counted on the Original Demo Scheduled date — Salesforce. This is the headline “demos scheduled” figure.",
      demos_day0: "Brain Day-0 cohort (first website visit at click-date) — the SAME basis the Google & Microsoft tabs use for Demos, which is why a channel's Demos there can differ from “Demos Sched” here.",
      completed: "Demos with status Completed — Salesforce.",
      opps: "Opportunities counted on the Opportunity Created date — Salesforce.",
      accounts: "New accounts counted on the Stripe Subscription First Invoice At date — Salesforce.",
      arr: "New ARR (SUM of Stripe Subscription ARR) booked on the Stripe first-invoice date, without coupons, AI or add-ons."
    };
    var kp = kpiCards(hostK, function () {
      var pr = priorRange(state.preset, state.range, A.dates);
      var arr = ["mqls", "demos", "demos_day0", "completed", "opps", "accounts", "arr"].map(function (k) {
        var M = AS_METRICS[k];
        var cur = asSum(A, "all", k, state.range);
        var prev = pr ? asSum(A, "all", k, pr) : null;
        return [M.label, M.fmt(cur), prev != null ? deltaHtml(cur, prev, { fmt: k === "arr" ? function (v) { return fmtMoney(v); } : undefined }) : null,
                kpiLink("allsources", state.grain, k), AS_TIP[k] || null];
      });
      /* "Monthly Run Rate" — combined all-channel full-month spend projection (same value the
         run-rate card below breaks out by channel; build.py DATA.runrate.combined). */
      var rr = DATA.runrate && DATA.runrate.combined;
      if (rr) arr.unshift(["Monthly Run Rate", fmtMoney(rr.full),
        '<div class="kdelta flat"><span class="vsprior">full-month spend projection</span></div>']);
      return arr;
    });

    // Attribution-basis banner, just above the KPI widgets (mirrors the channel tabs).
    // Tells the reader the All-Sources funnel is Salesforce booked-on-day, and that
    // "Day-0 Cohort" is the Brain basis the Google/Microsoft tabs use — so their Demos
    // can differ from "Demos Sched" here. (David, 2026-06-19.)
    (function () {
      var kEl = hostK.querySelector(".kpis");
      if (!kEl) return;
      var bn = document.createElement("div"); bn.className = "basisbanner";
      bn.textContent = "Funnel data is from Salesforce, booked on each event's own date — " +
        "“Demos Sched” = Original Demo Scheduled date; MQLs, Opps, Accounts & ARR likewise. " +
        "“Day-0 Cohort” is the Brain first-website-visit cohort (the basis the Google & Microsoft " +
        "tabs use for Demos), so a channel's Demos there can differ slightly from “Demos Sched” here.";
      hostK.insertBefore(bn, kEl);
    })();

    /* metric toggle + avg chip */
    var mt = document.createElement("div"); mt.className = "metric-toggle";
    Object.keys(AS_METRICS).forEach(function (k) {
      var b = document.createElement("span");
      b.className = "mbtn" + (k === state.metric ? " on" : ""); b.dataset.m = k;
      b.textContent = AS_METRICS[k].label;
      b.style.setProperty("--asc", AS_METRICS[k].color);
      b.onclick = function () {
        state.metric = k;
        mt.querySelectorAll(".mbtn").forEach(function (x) { x.classList.toggle("on", x.dataset.m === k); });
        refresh();
      };
      mt.appendChild(b);
    });
    var avg = document.createElement("span"); avg.className = "chip avgbtn"; avg.textContent = "〜 " + maLabel(state.grain);
    avg.onclick = function () {
      state.ma = !state.ma; avg.classList.toggle("on", state.ma);
      avg.style.background = state.ma ? "#64748B" : ""; avg.style.borderColor = state.ma ? "#64748B" : ""; avg.style.color = state.ma ? "#fff" : "";
      refresh();
    };
    mt.appendChild(avg);
    hostC.appendChild(mt);
    var grid = document.createElement("div"); grid.className = "clk-grid"; hostC.appendChild(grid);

    /* client-side range tables */
    var scoreBody = accCard(hostT, "Funnel Scorecard", true);
    var ratesBody = accCard(hostT, "Conversion Rates", true);
    var matrixBody = accCard(hostT, "Metrics Over Time", true);
    var drillBody = accCard(hostT, "Per-Campaign Drill-Down", true);
    var matrixSlice = "all";

    /* channel visibility for the Funnel Scorecard and Conversion Rates tables */
    var scoreColsOn = {};
    AS_ORDER.forEach(function (k) { scoreColsOn[k] = true; });

    var chipOn = {};
    function asMA(slice, mk, B) {
      var M = AS_METRICS[mk], win = maWin(state.bk.grain);
      if (M.den) return rollRatio(B.num, B.den, win);
      return trailMean(B.vals, win);
    }
    function refresh() {
      avg.textContent = "〜 " + maLabel(state.grain);
      kp.update();
      if (clSec) clSec.refresh();
      updateHistSections("allsources", state, A.dates);
      grid.innerHTML = "";
      var M = AS_METRICS[state.metric];
      /* span chart */
      var card = document.createElement("div"); card.className = "clk-card"; card.style.gridColumn = "1/-1";
      card.innerHTML = '<div class="clk-title">All Channels — ' + M.label + "</div>";
      var chips = document.createElement("div"); chips.className = "chips clicks"; card.appendChild(chips);
      var wrap = document.createElement("div"); wrap.className = "canvas-wrap clk"; wrap.style.height = "300px"; wrap.style.minWidth = "0";
      var cv = document.createElement("canvas"); wrap.appendChild(cv); card.appendChild(wrap);
      grid.appendChild(card);
      var clHits = []; wireClCanvas(cv, clHits);   // changelog annotation dots on the All-Channels chart
      var chart = null;
      if (chipOn.all === undefined) chipOn.all = true;
      function build() {
        var ds = [];
        AS_ORDER.forEach(function (k, i) {
          if (!chipOn[k]) return;
          var B = asBucket(A, k, state.metric, state.bk, state.range);
          var col = k === "all" ? M.color : HUES[i % HUES.length];
          var d = lineDS(asLabel(A, k), B.vals, col, k === "all");
          ds.push(d);
          if (state.ma) ds.push(lineDS(d.label + " (avg)", asMA(k, state.metric, B), col, false, true));
        });
        if (chart) chart.destroy();
        var plugins = [lineLabels(M.fmt)], opt = chartOpts(M.fmt, state.bk);
        plugins.push(clMarkerPlugin(function () { return clDayMap("allsources", clSec ? clSec.mode() : "all", state.bk); }, clHits)); opt.layout.padding.bottom = 24;
        chart = regChart(new Chart(cv, { type: "line", data: { labels: state.bk.labels, datasets: ds }, plugins: plugins, options: opt }));
      }
      AS_ORDER.forEach(function (k, i) {
        var col = k === "all" ? "#162050" : HUES[i % HUES.length];
        chips.appendChild(entityChip(asLabel(A, k), col, !!chipOn[k], function (o) { chipOn[k] = o; build(); }));
      });
      build();
      /* per-channel small charts */
      AS_ORDER.forEach(function (k) {
        if (k === "all") return;
        var c2 = document.createElement("div"); c2.className = "clk-card";
        c2.innerHTML = '<div class="clk-title">' + asLabel(A, k) + "</div>";
        var w2 = document.createElement("div"); w2.className = "canvas-wrap clk"; w2.style.height = "170px"; w2.style.minWidth = "0";
        var cv2 = document.createElement("canvas"); w2.appendChild(cv2); c2.appendChild(w2);
        grid.appendChild(c2);
        var B = asBucket(A, k, state.metric, state.bk, state.range);
        var ds = [lineDS("", B.vals, M.color, false)];
        if (state.ma) ds.push(lineDS("avg", asMA(k, state.metric, B), M.color, false, true));
        regChart(new Chart(cv2, { type: "line", data: { labels: state.bk.labels, datasets: ds }, options: chartOpts(M.fmt, state.bk, true) }));
      });
      renderScore(); renderRates(); renderMatrix(); renderDrill();
    }

    function scoreChipRow() {
      return '<div class="chips clicks" style="margin:0 0 8px">' +
        AS_ORDER.map(function (k) {
          var on = scoreColsOn[k];
          return '<span class="chip score-chip" data-col="' + k + '"' +
            (on ? ' style="background:#3185FC;border-color:#3185FC;color:#fff"' : '') + '>' +
            asLabel(A, k) + '</span>';
        }).join('') + '</div>';
    }
    function attachScoreChips(el) {
      el.querySelectorAll('.score-chip').forEach(function (btn) {
        btn.onclick = function () {
          var key = btn.dataset.col;
          var onCount = AS_ORDER.filter(function (k) { return scoreColsOn[k]; }).length;
          if (scoreColsOn[key] && onCount <= 1) return;
          scoreColsOn[key] = !scoreColsOn[key];
          renderScore(); renderRates();
        };
      });
    }
    function renderScore() {
      var pr = priorRange(state.preset, state.range, A.dates);
      var cols = AS_ORDER.filter(function (k) { return scoreColsOn[k]; });
      var h = scoreChipRow() + '<div class="tablewrap"><table><thead><tr><th>Metric</th>' + cols.map(function (k) { return "<th class='num'>" + asLabel(A, k) + "</th>"; }).join("") + "</tr></thead><tbody>";
      ["mqls", "demos", "demos_day0", "completed", "opps", "accounts", "arr"].forEach(function (mk) {
        var M = AS_METRICS[mk];
        h += "<tr><td class='metric-name'>" + M.label + "</td>" + cols.map(function (k) {
          var cur = asSum(A, k, mk, state.range), prev = pr ? asSum(A, k, mk, pr) : null;
          return "<td class='num'>" + M.fmt(cur) + badge(cur, prev, {}) + "</td>";
        }).join("") + "</tr>";
      });
      scoreBody.innerHTML = h + "</tbody></table></div><div class='foot'>Selected range vs the preceding window of equal length. Salesforce basis (read-only).</div>";
      attachScoreChips(scoreBody);
    }
    function renderRates() {
      var pr = priorRange(state.preset, state.range, A.dates);
      var cols = AS_ORDER.filter(function (k) { return scoreColsOn[k]; });
      var h = scoreChipRow() + '<div class="tablewrap"><table><thead><tr><th>Rate</th>' + cols.map(function (k) { return "<th class='num'>" + asLabel(A, k) + "</th>"; }).join("") + "</tr></thead><tbody>";
      ["r_mql_demo", "r_demo_comp", "r_comp_opp", "r_opp_acct"].forEach(function (mk) {
        var M = AS_METRICS[mk];
        h += "<tr><td class='metric-name'>" + M.label + "</td>" + cols.map(function (k) {
          var cur = asSum(A, k, mk, state.range), prev = pr ? asSum(A, k, mk, pr) : null;
          return "<td class='num'>" + M.fmt(cur) + (cur != null && prev != null ? badge(cur, prev, { pts: true }) : "") + "</td>";
        }).join("") + "</tr>";
      });
      ratesBody.innerHTML = h + "</tbody></table></div>";
      attachScoreChips(ratesBody);
    }
    function renderMatrix() {
      /* metrics over time — last ≤20 buckets of the selected grain, frozen first col+header */
      var bk = state.bk, n = bk.keys.length, from = Math.max(0, n - 20);
      var chipsRow = '<div class="chips clicks matrixchips">' + AS_ORDER.map(function (k) {
        return '<span class="chip' + (matrixSlice === k ? " on" : "") + '" data-s="' + k + '" style="border-color:#3185FC' + (matrixSlice === k ? ";background:#3185FC;color:#fff" : "") + '">' + asLabel(A, k) + "</span>";
      }).join("") + "</div>";
      var hdr = "<tr><th>Metric</th>";
      for (var j = from; j < n; j++) hdr += "<th class='num'>" + bk.labels[j] + "</th>";
      hdr += "</tr>";
      var body = "";
      Object.keys(AS_METRICS).forEach(function (mk) {
        var M = AS_METRICS[mk];
        var B = asBucket(A, matrixSlice, mk, bk, state.range);
        body += "<tr><td>" + M.label + "</td>";
        for (var j = from; j < n; j++) body += "<td class='num'>" + M.fmt(B.vals[j]) + "</td>";
        body += "</tr>";
      });
      matrixBody.innerHTML = chipsRow + '<div class="matrixwrap"><table class="matrix"><thead>' + hdr + "</thead><tbody>" + body + "</tbody></table></div>" +
        "<div class='foot'>" + (n > 20 ? "Showing the most recent 20 of " + n + " " + bk.grain + " buckets — narrow the range or coarsen the grain for older columns." : "") + "</div>";
      matrixBody.querySelectorAll(".matrixchips .chip").forEach(function (c) {
        c.onclick = function () { matrixSlice = c.dataset.s; renderMatrix(); };
      });
    }
    function renderDrill() {
      var CF = A.campfunnel;
      if (!CF) { drillBody.innerHTML = "<p class='muted'>No campaign-funnel facts.</p>"; return; }
      var pr = priorRange(state.preset, state.range, A.dates);
      var html = "";
      ["google-ads", "bing", "meta"].forEach(function (srcName, sIdx) {
        var cur = {}, prev = {};
        CF.rows.forEach(function (r) {
          if (r[1] !== sIdx) return;
          var tgt = r[0] >= state.range[0] && r[0] <= state.range[1] ? cur : (pr && r[0] >= pr[0] && r[0] <= pr[1] ? prev : null);
          if (!tgt) return;
          var v = tgt[r[2]] || (tgt[r[2]] = [0, 0, 0, 0, 0, 0]);
          for (var x = 0; x < 6; x++) v[x] += r[3 + x];
        });
        var keys = Object.keys(cur).map(Number).sort(function (a, b) { return (cur[b][1] - cur[a][1]) || (cur[b][0] - cur[a][0]); });
        var label = { "google-ads": "Google Ads", "bing": "Bing Ads", "meta": "Meta" }[srcName];
        html += "<h3>" + label + " <span class='muted'>(" + keys.length + " campaigns)</span></h3>";
        html += '<div class="tablewrap"><table><thead><tr><th>Campaign (UTM)</th><th class="num">MQLs</th><th class="num">Demos</th><th class="num">Compl</th><th class="num">Opps</th><th class="num">Accts</th><th class="num">New ARR</th></tr></thead><tbody>';
        var shown = 0;
        keys.forEach(function (c, i) {
          var v = cur[c], pv = prev[c] || [null, null, null, null, null, null];
          var hide = i >= 12 ? " class='drill-more'" : "";
          if (i >= 12) shown++;
          html += "<tr" + hide + "><td>" + decodeURIComponent(String(CF.campaigns[c]).replace(/\+/g, " ")) + "</td>";
          [0, 1, 2, 3, 4].forEach(function (x) {
            html += "<td class='num'>" + fmtInt(v[x]) + badge(v[x], pv[x], {}) + "</td>";
          });
          html += "<td class='num'>" + fmtMoney(v[5]) + badge(v[5], pv[5], {}) + "</td></tr>";
        });
        html += "</tbody></table></div>";
        if (shown) html += "<div><span class='chip drill-toggle'>+" + shown + " more</span></div>";
      });
      drillBody.innerHTML = html + "<div class='foot'>Salesforce UTM-campaign grain, selected range vs prior window. Demos = Original Demo Scheduled date; Opps = created date; Accounts/ARR = Stripe first invoice.</div>";
      drillBody.querySelectorAll(".drill-toggle").forEach(function (b) {
        b.onclick = function () {
          var tbl = b.parentElement.previousElementSibling;
          tbl.querySelectorAll("tr.drill-more").forEach(function (tr) { tr.classList.toggle("show"); });
          b.textContent = b.textContent[0] === "+" ? "− less" : b.textContent.replace("− less", "+ more");
        };
      });
    }
    refresh();
    return { refresh: refresh };
  }

  /* ================= Meta filters + leaderboards ================= */
  function initMetaBoards(pt) {
    var P = DATA.meta;
    var host = document.getElementById("meta-boards");
    if (!P || !host || !pt) return;
    /* No own date/campaign/ad-set controls anymore — the leaderboards are driven by the
       MAIN top-of-tab filters: the shared state's range picker + Campaign/Ad group (=ad set)
       dropdowns. We read pt.state directly and re-render via its _after hook (2026-06-17). */
    var state = pt.state;
    var card = document.createElement("div"); card.className = "card";
    card.innerHTML = "<h2>Campaign &amp; Ad-Set Leaderboards</h2>";
    host.appendChild(card);
    var note = document.createElement("div"); note.className = "clk-pipenote"; card.appendChild(note);
    var tblC = document.createElement("div"); card.appendChild(tblC);
    var tblA = document.createElement("div"); card.appendChild(tblA);

    function inRange(d) { return d >= state.range[0] && d <= state.range[1]; }
    function refresh() {
      var selCamps = state.campSel, selSets = state.grpSel;   // the top-of-tab filters
      function campOk(c) { return selCamps.size === 0 || selCamps.has(c); }
      function setOk(g) { return selSets.size === 0 || selSets.has(g); }
      var byC = {}, byG = {};
      function blank() { return { spend: 0, clicks: 0, imps: 0, demos: null, opps: null, accts: null, arr: null }; }
      P.spend.forEach(function (r) {
        if (!inRange(r[0]) || !campOk(r[1]) || !setOk(r[2])) return;
        var c = byC[r[1]] || (byC[r[1]] = blank()); c.spend += r[3] / 100; c.clicks += r[4]; c.imps += r[5];
        var g = byG[r[2]] || (byG[r[2]] = blank()); g.spend += r[3] / 100; g.clicks += r[4]; g.imps += r[5];
      });
      var setFilterOn = selSets.size > 0;
      var other = null;    // funnel credited to VALID campaigns with no spend in range (KPI-reconciling row)
      var unattr = null;   // funnel with NO mapped campaign (camp_idx<0) — also in the KPI/SF totals
      if (!setFilterOn) {
        P.demos.forEach(function (r) {
          if (!inRange(r[0]) || r[1] < 0 || !campOk(r[1]) || !byC[r[1]]) return;
          byC[r[1]].demos = (byC[r[1]].demos || 0) + r[2];
        });
        P.pipeline.forEach(function (r) {
          if (!inRange(r[0]) || r[1] < 0 || !campOk(r[1]) || !byC[r[1]]) return;
          var c = byC[r[1]];
          c.opps = (c.opps || 0) + r[2]; c.accts = (c.accts || 0) + r[3]; c.arr = (c.arr || 0) + r[4];
        });
        // Propagate campaign-grain funnel to each ad-set row for display
        // (multiple ad-sets in the same campaign share the same parent total)
        Object.keys(byG).map(Number).forEach(function (g) {
          var ci = P.groups[g] && P.groups[g][2];
          if (ci != null && byC[ci]) {
            byG[g].demos = byC[ci].demos;
            byG[g].opps = byC[ci].opps;
            byG[g].accts = byC[ci].accts;
            byG[g].arr = byC[ci].arr;
          }
        });
        // "Other (no-spend campaigns)": demos/opps/accts/ARR credited (SF first-click UTM) to
        // valid Meta campaigns that did NOT spend in this range, so the Campaign-Leaderboard total
        // reconciles with the tab's KPIs. camp_idx<0 (fully unattributed) is excluded here — exactly
        // as the KPI excludes it — so Σ(spending campaigns) + Other == KPI demos. (2026-06-17)
        var o = { spend: 0, clicks: 0, imps: 0, demos: 0, opps: 0, accts: 0, arr: 0 };
        P.demos.forEach(function (r) { if (inRange(r[0]) && r[1] >= 0 && campOk(r[1]) && !byC[r[1]]) o.demos += r[2]; });
        P.pipeline.forEach(function (r) { if (inRange(r[0]) && r[1] >= 0 && campOk(r[1]) && !byC[r[1]]) { o.opps += r[2]; o.accts += r[3]; o.arr += r[4]; } });
        if (o.demos || o.opps || o.accts || o.arr) other = o;
        // "Unattributed (no campaign)": camp_idx<0 — fb/ig conversions with no mapped campaign id.
        // Only in the fully-unfiltered view; these are in the tab KPIs (kind "all") + Salesforce, so
        // surfacing them here makes the Campaign Leaderboard total reconcile with both. (2026-06-17)
        if (!selCamps.size) {
          var u = { spend: 0, clicks: 0, imps: 0, demos: 0, opps: 0, accts: 0, arr: 0 };
          P.demos.forEach(function (r) { if (inRange(r[0]) && r[1] < 0) u.demos += r[2]; });
          P.pipeline.forEach(function (r) { if (inRange(r[0]) && r[1] < 0) { u.opps += r[2]; u.accts += r[3]; u.arr += r[4]; } });
          if (u.demos || u.opps || u.accts || u.arr) unattr = u;
        }
      }
      note.textContent = setFilterOn
        ? "Ad-set filter active: Demos / Opps / Accounts / ARR are campaign-grain (no ad-set attribution) and are hidden while an ad-set filter is on."
        : "Demos = Salesforce first-touch (first-click UTM campaign). Opps/Accounts/ARR = booked-on-day from Salesforce, campaign-grain. Ad-set rows show parent campaign totals — not per-ad-set attribution. “Other (no-spend campaigns)” = funnel credited to Meta campaigns with no spend in this range, so the Campaign total reconciles with the tab’s KPIs.";
      var campRows = Object.keys(byC).map(Number).map(function (c) {
        return { name: P.campaigns[c] ? P.campaigns[c][1] : "?", d: byC[c] };
      });
      if (other) campRows.push({ name: "Other (no-spend campaigns)", d: other, other: true });
      if (unattr) campRows.push({ name: "Untracked / legacy campaigns", d: unattr, unattr: true });
      renderBoard(tblC, "Campaign Leaderboard", campRows, !setFilterOn);
      var adsetRows = Object.keys(byG).map(Number).map(function (g) {
        return { name: P.groups[g] ? P.groups[g][1] : "?", d: byG[g] };
      });
      // Same catch-all buckets as the Campaign board so the Ad-Set board reflects the full channel
      // funnel too. These have no ad set (no-spend campaign / no campaign at all) — the Ad-Set Total
      // stays "—" (parent-campaign-grain funnel is non-summable), but the demos/opps/ARR now show.
      if (other) adsetRows.push({ name: "Other (no-spend campaigns)", d: other, other: true });
      if (unattr) adsetRows.push({ name: "Untracked / legacy campaigns", d: unattr, unattr: true });
      renderBoard(tblA, "Ad-Set Leaderboard", adsetRows, !setFilterOn, true);
    }
    function renderBoard(hostEl, title, rows, withFunnel, campGrainFunnel) {
      rows.sort(function (a, b) { return b.d.spend - a.d.spend; });
      var tot = { spend: 0, clicks: 0, imps: 0, demos: 0, opps: 0, accts: 0, arr: 0 };
      rows.forEach(function (r) {
        tot.spend += r.d.spend; tot.clicks += r.d.clicks; tot.imps += r.d.imps;
        // Skip funnel sum for campaign-grain ad-set rows — summing would double-count
        if (!campGrainFunnel) {
          tot.demos += r.d.demos || 0; tot.opps += r.d.opps || 0; tot.accts += r.d.accts || 0; tot.arr += r.d.arr || 0;
        }
      });
      function cells(d, funnelOk, totRow) {
        var ctr = d.imps > 0 ? d.clicks / d.imps : null;
        var cpc = d.clicks > 0 ? d.spend / d.clicks : null;
        var cpm = d.imps > 0 ? d.spend * 1000 / d.imps : null;
        var cpd = funnelOk && !totRow && d.demos > 0 && d.spend > 0 ? d.spend / d.demos : null;
        var roas = funnelOk && !totRow && d.spend > 0 && d.arr != null ? d.arr / d.spend : null;
        function fc(v, f) { return "<td class='num'>" + (v == null ? "—" : f(v)) + "</td>"; }
        return fc(d.spend, fmtMoney) + fc(d.clicks, fmtInt) + fc(d.imps, fmtInt) +
          fc(ctr, function (v) { return fmtPct(v, 2); }) + fc(cpc, function (v) { return fmtMoney(v, 2); }) + fc(cpm, function (v) { return fmtMoney(v, 2); }) +
          (funnelOk
            ? (campGrainFunnel && totRow
                ? "<td class='num'>—</td>".repeat(6)
                : fc(d.demos, fmtInt) + fc(cpd, function (v) { return fmtMoney(v); }) + fc(d.opps, fmtInt) + fc(d.accts, fmtInt) + fc(d.arr, function (v) { return fmtMoney(v); }) + fc(roas, function (v) { return v.toFixed(2) + "x"; }))
            : "<td class='num'>—</td>".repeat(6));
      }
      var h = "<h3>" + title + "</h3>";
      if (campGrainFunnel && withFunnel) {
        h += '<div class="clk-pipenote" style="font-size:12px;margin:0 0 8px">Funnel columns = parent campaign total (no per-ad-set attribution). Total row shows — to avoid double-counting.</div>';
      }
      h += '<div class="tablewrap"><table><thead><tr><th>' +
        (title.indexOf("Ad-Set") === 0 ? "Ad set" : "Campaign") +
        "</th><th class='num'>Spend</th><th class='num'>Clicks</th><th class='num'>Impr</th><th class='num'>CTR</th><th class='num'>CPC</th><th class='num'>CPM</th><th class='num'>Demos</th><th class='num'>$/Demo</th><th class='num'>Opps</th><th class='num'>Accts</th><th class='num'>New ARR</th><th class='num'>ROAS</th></tr></thead><tbody>";
      var OTHER_TIP = '<p><b>Other (no-spend campaigns)</b> rolls up Demos / Opps / Accounts / New ARR credited — via Salesforce first-click UTM campaign — to Meta campaigns that had <b>no spend</b> in the selected date range. These are usually paused or retired campaigns (e.g. older <i>[JL]</i> ones) that still receive conversion credit.</p><p>The row shows <b>$0 spend</b>, so the cost metrics (CPC / CPM / $/Demo / ROAS) display “—”. It appears in both leaderboards so they reflect the full channel total shown in this tab’s KPI cards.</p>';
      var UNATTR_TIP = '<p><b>Untracked / legacy campaigns</b> = fb / ig conversions that ARE attributed in Salesforce to a UTM campaign — just not one in this report’s Meta campaign list. Retired/legacy campaigns (the [JL] family), one-off tags (ad_comment), or naming variants (JF_USA-Branded_Expansion), plus any whose UTM campaign can’t be matched to a current Meta campaign.</p><p>They are real channel conversions — counted in this tab’s KPI cards and in the Salesforce reports — grouped here because the report can’t tie the campaign name to a live Meta campaign row. These campaigns aren’t spending now, so cost metrics show “—”.</p>';
      function tipSpan(label, txt) {
        return ' <span class="tip" tabindex="0" role="note" aria-label="About ' + label + '" style="margin-left:5px"><span class="tipbox" style="top:auto;bottom:150%">' + txt + '</span>i</span>';
      }
      rows.forEach(function (r) {
        var nm = r.name + (r.other ? tipSpan("Other (no-spend campaigns)", OTHER_TIP)
                          : r.unattr ? tipSpan("Untracked / legacy campaigns", UNATTR_TIP) : "");
        h += "<tr><td>" + nm + "</td>" + cells(r.d, withFunnel, false) + "</tr>";
      });
      h += "<tr class='tot'><td>Total (" + rows.length + ")</td>" + cells(tot, withFunnel, true) + "</tr></tbody></table></div>";
      hostEl.innerHTML = h;
    }
    state._after.push(refresh);   // re-render whenever the main Meta-tab filters change
    refresh();                    // initial paint
  }

  /* ---------- daily snapshot history (date-picker driven) ----------
     SNAPSHOT sections (Daily Alerts, Top-20-CPC, bad-terms) are one-day snapshots,
     not range-aggregatable like the charts. As of 2026-06-15 ALL of them have their
     OWN multi-select day dropdown (initHistPicker) that is independent of the main
     date picker — so this map is empty and updateHistSections is a no-op kept only
     as a defensive fallback. */
  var HIST_SECTIONS = { allsources: [], google: [], microsoft: [], meta: [] };
  function wireAccordions(root) {
    root.querySelectorAll(".card.accordion>h2").forEach(function (h) {
      h.addEventListener("click", function () { h.parentElement.classList.toggle("collapsed"); });
    });
  }
  function histEmpty(iso, suffix) {
    var L = { alerts: "Daily Alerts", cpc: "Top 20 Search Term CPCs", bad: "Bad Search Terms" };
    return '<div class="card"><h2>' + (L[suffix] || "Section") + ' <span class="dt">— ' + (iso || "") +
      '</span></h2><p class="muted">No stored snapshot for this day — only the last 14 days are kept. ' +
      'Pick a day within the last 14 days, or widen the range to see yesterday’s.</p></div>';
  }
  function updateHistSections(tabKey, state, dates) {
    var secs = HIST_SECTIONS[tabKey]; if (!secs) return;
    var iso = (state.range[0] === state.range[1]) ? dates[state.range[0]] : DATA.anchor;
    if (state._histIso === iso) return;   // date unchanged → keep DOM (and its expand state)
    state._histIso = iso;
    var day = HIST[iso] && HIST[iso][tabKey];
    secs.forEach(function (suffix) {
      var el = document.getElementById("hist-" + tabKey + "-" + suffix);
      if (!el) return;
      var html = day && day[suffix];
      el.innerHTML = html ? html : histEmpty(iso, suffix);
      wireAccordions(el);
    });
  }

  /* ---------- Snapshot sections: own multi-select day dropdown (per-tab, persisted) ----------
     Generic engine for the three one-day snapshot sections — Daily Alerts, Top 20
     Search Term CPCs, Bad Search Terms. Each gets its OWN multi-select day dropdown
     (independent of the main date picker) that stacks any subset of the rolling 14
     stored days newest-first, inside a collapsible accordion card (expanded by
     default). Daily Alerts additionally gets a "reviewed" checkbox on every alert /
     recommendation row (remembered in localStorage, per tab). Read-only — marks are local. */
  var DOW_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  function lsGet(k, def) { try { var v = JSON.parse(localStorage.getItem(k)); return v == null ? def : v; } catch (e) { return def; } }
  function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function histDays(tabKey, suffix) {
    return Object.keys(HIST).filter(function (iso) {
      return HIST[iso] && HIST[iso][tabKey] && (suffix in HIST[iso][tabKey]) && HIST[iso][tabKey][suffix];
    }).sort().reverse();
  }
  var SNAP_TIP =
    '<p>Pick which day(s) to show — selected days stack newest-first. This selector is <b>independent of the date range</b> at the top of the page; it only changes this section.</p>' +
    '<p>Only the rolling last 14 days are kept; older days drop off automatically.</p>';
  var ALERTS_TIP =
    '<p>Pick which day(s) of alerts to show — selected days stack newest-first. This selector is <b>independent of the date range</b> at the top of the page; it only changes this section.</p>' +
    '<p>The checkbox on each row marks it <b>reviewed</b> and is remembered in this browser (per tab). Only the rolling last 14 days are kept; older days drop off automatically.</p>';
  function initHistPicker(tabKey, suffix, opts) {
    var host = document.getElementById("hist-" + tabKey + "-" + suffix);
    if (!host || host._pickerInit) return;
    var days = histDays(tabKey, suffix);
    if (!days.length) return;            // nothing stored → leave server-rendered card
    host._pickerInit = true;
    var newest = days[0];
    var base = suffix === "alerts" ? "alert" : suffix;   // keep legacy localStorage keys for alerts
    var DKEY = "mc_" + base + "days_" + tabKey, AKEY = "mc_" + base + "ack_" + tabKey;
    var sel = (lsGet(DKEY, null) || []).filter(function (d) { return days.indexOf(d) >= 0; });
    // ALWAYS surface the newest stored day. The saved selection persists yesterday's
    // pick, but a fresh build adds a NEW newest day the old selection doesn't include —
    // so without this the latest day's alerts silently never show until you pick them by
    // hand (David, 2026-06-19). Re-add newest on every load, for all 4 tabs.
    if (sel.indexOf(newest) < 0) sel.unshift(newest);
    if (!sel.length) sel = [newest];     // (defensive) nothing stored → yesterday only
    var ack = lsGet(AKEY, {}), pruned = {}, changed = false;   // drop ticks for days out of window
    Object.keys(ack).forEach(function (id) { if (days.indexOf(id.split("|")[1]) >= 0) pruned[id] = 1; else changed = true; });
    if (changed) { ack = pruned; lsSet(AKEY, ack); }

    function dayLabel(iso) { var d = dUTC(iso); return DOW_LONG[d.getUTCDay()] + " " + MON[d.getUTCMonth()] + " " + d.getUTCDate() + (iso === newest ? " - yesterday" : ""); }

    host.innerHTML =
      '<div class="card accordion">' +
        '<h2>' + opts.title + ' <span class="dt" data-dt></span> ' +
          '<span class="tip" tabindex="0" role="note" aria-label="About ' + opts.title + '"><span class="tipbox">' +
          (opts.tip || SNAP_TIP) +
          '</span>i</span>' +
        '</h2>' +
        '<div class="alert-pick"><div class="dd" data-dd>' +
          '<button type="button" class="dd-btn" data-ddbtn></button>' +
          '<div class="dd-panel" data-ddpanel><div class="dd-top"><span class="dd-link" data-all>Select all</span><span class="dd-link" data-clear>Clear</span></div><div data-ddrows></div></div>' +
        '</div>' +
        (opts.ack ? '<label class="alert-hide"><input type="checkbox" data-hide> Hide reviewed</label>' : '') +
        '</div>' +
        '<div class="alert-body" data-body></div>' +
      '</div>';

    var card = host.firstChild;
    var dtEl = card.querySelector("[data-dt]");
    var btn = card.querySelector("[data-ddbtn]");
    var panel = card.querySelector("[data-ddpanel]");
    var rowsWrap = card.querySelector("[data-ddrows]");
    var body = card.querySelector("[data-body]");

    // "Hide reviewed" filter (alerts only): toggles .hide-done on the body so the
    // CSS rule hides ticked rows — lets you see only the alerts still to get to.
    var hideKey = "mc_" + base + "hide_" + tabKey, hideCb = card.querySelector("[data-hide]");
    if (hideCb) {
      hideCb.checked = lsGet(hideKey, false);
      body.classList.toggle("hide-done", hideCb.checked);
      hideCb.addEventListener("change", function (e) {
        body.classList.toggle("hide-done", e.target.checked);
        lsSet(hideKey, e.target.checked);
      });
    }

    function addAckColumn(tbl, ti, iso) {
      var hr = tbl.querySelector("thead tr");
      if (hr) { var th = document.createElement("th"); th.className = "ack"; hr.insertBefore(th, hr.firstChild); }
      tbl.querySelectorAll("tbody tr").forEach(function (tr) {
        var first = tr.children[0];
        if (tr.children.length <= 1 && first && first.hasAttribute("colspan")) {   // muted/placeholder/expander row — keep aligned, no checkbox
          first.colSpan = (parseInt(first.getAttribute("colspan"), 10) || 1) + 1; return;
        }
        var id;
        if (ti === 0) {   // anomaly-alerts table: key by entity + metric (legacy scheme)
          var ent = tr.children[1] ? tr.children[1].textContent.trim().replace(/\s+/g, " ").slice(0, 80) : "";
          var met = tr.children[2] ? tr.children[2].textContent.trim() : "";
          id = tabKey + "|" + iso + "|" + ent + "|" + met;
        } else {          // Google's Recommendations (or any later table): key by row text
          id = tabKey + "|" + iso + "|r" + ti + "|" + (tr.textContent || "").trim().replace(/\s+/g, " ").slice(0, 90);
        }
        var td = document.createElement("td"); td.className = "ack";
        td.innerHTML = '<input type="checkbox" class="alert-ack"' + (ack[id] ? " checked" : "") + ' aria-label="Mark reviewed">';
        if (ack[id]) tr.classList.add("alert-done");
        td.firstChild.addEventListener("change", function (e) {
          if (e.target.checked) { ack[id] = 1; } else { delete ack[id]; }
          lsSet(AKEY, ack); tr.classList.toggle("alert-done", e.target.checked);
        });
        tr.insertBefore(td, tr.firstChild);
      });
    }
    function renderBody() {
      body.innerHTML = "";
      var chosen = days.filter(function (d) { return sel.indexOf(d) >= 0; });   // newest-first
      if (!chosen.length) { body.innerHTML = '<p class="muted" style="padding:6px 2px">No days selected — pick a day above to show its snapshot.</p>'; return; }
      chosen.forEach(function (iso) {
        var frag = ((HIST[iso] && HIST[iso][tabKey] && HIST[iso][tabKey][suffix]) || "").trim();
        var tmp = document.createElement("div"); tmp.innerHTML = frag;
        var inner = tmp.querySelector(".card") || tmp;
        var h2 = inner.querySelector("h2");
        if (h2) h2.parentNode.removeChild(h2);
        if (opts.ack) inner.querySelectorAll("table").forEach(function (tbl, ti) { addAckColumn(tbl, ti, iso); });
        var block = document.createElement("div"); block.className = "alert-day";
        block.innerHTML = '<div class="alert-dayhd">' + dayLabel(iso) + '</div>';
        while (inner.firstChild) block.appendChild(inner.firstChild);
        body.appendChild(block);
      });
    }
    function syncLabel() {
      dtEl.textContent = sel.length > 1 ? ("— " + sel.length + " days selected") : "";
      btn.innerHTML = (sel.length === 1 ? dayLabel(sel[0]) : (sel.length ? sel.length + " days selected" : "Select days")) + ' <span class="dd-car">▾</span>';
    }
    function renderRows() {
      rowsWrap.innerHTML = "";
      days.forEach(function (iso) {
        var lab = document.createElement("label"); lab.className = "dd-row";
        lab.innerHTML = '<input type="checkbox"' + (sel.indexOf(iso) >= 0 ? " checked" : "") + '> ' + dayLabel(iso);
        lab.querySelector("input").addEventListener("change", function (e) {
          if (e.target.checked) { if (sel.indexOf(iso) < 0) sel.push(iso); }
          else { sel = sel.filter(function (x) { return x !== iso; }); }
          lsSet(DKEY, sel); syncLabel(); renderBody();
        });
        rowsWrap.appendChild(lab);
      });
    }
    btn.addEventListener("click", function (e) { e.stopPropagation(); panel.classList.toggle("open"); });
    card.querySelector("[data-all]").addEventListener("click", function () { sel = days.slice(); lsSet(DKEY, sel); renderRows(); syncLabel(); renderBody(); });
    card.querySelector("[data-clear]").addEventListener("click", function () { sel = []; lsSet(DKEY, sel); renderRows(); syncLabel(); renderBody(); });
    document.addEventListener("click", function (ev) { if (panel.classList.contains("open") && !card.querySelector("[data-dd]").contains(ev.target)) panel.classList.remove("open"); });
    card.querySelector("h2").addEventListener("click", function () { card.classList.toggle("collapsed"); });
    renderRows(); syncLabel(); renderBody();
  }

  /* ================= Changelog — 3-source change log (per-tab list + chart markers) =================
     Data: <script id="mc-changelog"> built by changelog_store.py (deduped across PostHog
     annotations + Slack canvas + Notion). Channels model drives per-tab routing:
       website -> every tab · google/microsoft/meta -> that tab · other (ChatGPT…) -> All Sources only.
     A collapsed-by-default accordion sits above Daily Metrics; the same entries also show as
     count-dots under the x-axis of the All-Campaigns / All-Channels chart (click for a popover). */
  var CL = (function () {
    var el = document.getElementById("mc-changelog");
    try { return el ? JSON.parse(el.textContent) : { entries: [] }; } catch (e) { return { entries: [] }; }
  })();
  var CL_SRC = { posthog: "PostHog", slack: "Slack canvas", notion: "Notion" };
  var CL_CH = { website: "Website / shared", google: "Google", microsoft: "Microsoft", meta: "Meta", other: "Other" };
  var CL_TIP =
    '<p>Major changes pulled daily from the <b>Slack Website Changelog</b>, <b>PostHog annotations</b>, and the <b>Notion Change Log</b> — de-duplicated across sources.</p>' +
    '<p>Filtered to the date range at the top of the page. Channel-specific ad changes show only on their channel’s tab; website / shared changes show on every tab.</p>';
  function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function clTabShow(e, tabKey) {
    var ch = e.channels || [];
    if (tabKey === "allsources") return ch.indexOf("website") >= 0 || ch.indexOf("other") >= 0;
    if (ch.indexOf("website") >= 0) return true;     // website/shared -> every channel tab
    return ch.indexOf(tabKey) >= 0;                  // channel-specific -> its own tab
  }
  function clFilterPass(e, tabKey, mode) {
    if (!mode || mode === "all") return true;
    if (mode === "website") return (e.channels || []).indexOf("website") >= 0;
    if (mode === "channel") return (e.channels || []).indexOf(tabKey) >= 0;
    return true;
  }
  function changelogForTab(tabKey, mode) {
    return (CL.entries || []).filter(function (e) { return clTabShow(e, tabKey) && clFilterPass(e, tabKey, mode); });
  }
  function clBucketKey(iso, grain) {
    if (grain === "week") { var d = dUTC(iso), off = (d.getUTCDay() + 6) % 7; d.setUTCDate(d.getUTCDate() - off); return d.toISOString().slice(0, 10); }
    if (grain === "month") return iso.slice(0, 7);
    return iso;
  }
  function clDayMap(tabKey, mode, bk) {   // {bucketIndex: [entries]} for buckets inside the range
    var dm = {};
    changelogForTab(tabKey, mode).forEach(function (e) {
      var bi = bk.keys.indexOf(clBucketKey(e.date, bk.grain));
      if (bi >= 0) (dm[bi] = dm[bi] || []).push(e);
    });
    return dm;
  }
  function clLinks(e) {
    return (e.sources || []).map(function (s) {
      return '<a href="' + esc(s.url) + '" target="_blank" rel="noopener">' + (CL_SRC[s.src] || s.src) + "</a>";
    }).join(" / ");
  }

  /* shared annotation popover — opens on CLICK, stays open, closes on outside click */
  var _clPop = null;
  function ensureClPop() {
    if (_clPop) return _clPop;
    _clPop = document.createElement("div"); _clPop.id = "cl-pop"; _clPop.style.display = "none";
    _clPop.addEventListener("click", function (e) { e.stopPropagation(); });   // clicks inside keep it open
    document.body.appendChild(_clPop);
    document.addEventListener("click", hideClPop);
    window.addEventListener("resize", hideClPop);
    return _clPop;
  }
  function hideClPop() { if (_clPop) _clPop.style.display = "none"; }
  function clPopRow(e) {
    var d = e.detail ? '<div class="pop-d">' + esc(e.detail) + "</div>" : "";
    var st = e.status === "implementing" ? ' <span class="cl-statustag">implementing</span>' : "";
    return '<div class="pop-row"><div class="pop-t">' + esc(e.title) + st + "</div>" + d +
      '<div class="pop-meta">' + shortDate(e.date) + " · " + clLinks(e) + " · " + (CL_CH[(e.channels || [])[0]] || "") + "</div></div>";
  }
  function showClPop(items, sx, sy) {
    var p = ensureClPop();
    p.innerHTML = '<div class="pop-hd">' + items.length + (items.length > 1 ? " changes" : " change") + "</div>" + items.map(clPopRow).join("");
    p.style.display = "block";
    var w = p.offsetWidth, h = p.offsetHeight, vw = window.innerWidth, vh = window.innerHeight;
    var left = Math.max(6, Math.min(sx - w / 2, vw - w - 6));
    var top = sy - h - 12; if (top < 6) top = Math.min(sy + 16, vh - h - 6);
    p.style.left = left + "px"; p.style.top = Math.max(6, top) + "px";
  }
  function clMarkerPlugin(getDayMap, hits) {   // draws count-dots under the x-axis labels
    return { id: "clMarkers", afterDraw: function (ch) {
      var xs = ch.scales.x; if (!xs) return;
      var ctx = ch.ctx, y = xs.bottom + 9, dm = getDayMap();
      hits.length = 0;
      Object.keys(dm).forEach(function (bi) {
        bi = +bi; var x = xs.getPixelForValue(bi), items = dm[bi], n = items.length;
        ctx.save();
        ctx.beginPath(); ctx.arc(x, y, 6, 0, 2 * Math.PI); ctx.fillStyle = "#3185FC"; ctx.fill();
        ctx.fillStyle = "#fff"; ctx.font = "700 8.5px -apple-system,Segoe UI,Roboto,sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(n > 99 ? "99" : String(n), x, y + 0.5);
        ctx.restore();
        hits.push({ x: x, y: y, items: items });
      });
      ch.canvas.__clHits = hits.map(function (h) { return { x: h.x, y: h.y, n: h.items.length }; });  // QA hook (harmless)
    } };
  }
  function wireClCanvas(cv, hits) {
    cv.addEventListener("mousemove", function (ev) {
      var r = cv.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top, hit = null;
      hits.forEach(function (hh) { if (Math.abs(mx - hh.x) < 11 && Math.abs(my - hh.y) < 11) hit = hh; });
      cv.style.cursor = hit ? "pointer" : "";
    });
    cv.addEventListener("click", function (ev) {
      var r = cv.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top, hit = null;
      hits.forEach(function (hh) { if (Math.abs(mx - hh.x) < 11 && Math.abs(my - hh.y) < 11) hit = hh; });
      if (hit) { ev.stopPropagation(); showClPop(hit.items, r.left + hit.x, r.top + hit.y); }
      else hideClPop();
    });
  }
  function clItemHtml(e) {
    var det = e.detail ? '<div class="cl-detail">' + esc(e.detail) + "</div>" : "";
    var st = e.status === "implementing" ? ' <span class="cl-statustag">implementing</span>' : "";
    return '<div class="cl-item">' +
      '<div class="cl-title" data-cltoggle><span class="arrow">▶</span>' + esc(e.title) + st + "</div>" +
      '<div class="cl-body">' + det +
        '<div class="cl-meta"><span class="cl-flbl">Source:</span>' + clLinks(e) +
        ' · <span class="cl-scopechip">' + (CL_CH[(e.channels || [])[0]] || "") + "</span></div>" +
      "</div></div>";
  }
  function initChangelogSection(tabKey, getRangeISO, onFilterChange) {
    var host = document.getElementById("changelog-" + tabKey);
    if (!host) return null;
    var mode = "all";
    host.innerHTML =
      '<div class="card accordion collapsed">' +
        '<h2>Changelog <span class="cl-count" data-clcount>0</span>' +
          '<span class="tip" tabindex="0" role="note" aria-label="About the Changelog"><span class="tipbox">' + CL_TIP + "</span>i</span>" +
        "</h2>" +
        '<div class="acc-body">' +
          '<div class="cl-filter" data-clfilter></div>' +
          '<div class="cl-scope" data-clscope></div>' +
          "<div data-cllist></div>" +
        "</div>" +
      "</div>";
    var card = host.firstChild;
    card.querySelector("h2").addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest(".tip")) return;   // info icon shouldn't toggle
      card.classList.toggle("collapsed");
    });
    var elCount = card.querySelector("[data-clcount]"), elFilter = card.querySelector("[data-clfilter]"),
        elScope = card.querySelector("[data-clscope]"), elList = card.querySelector("[data-cllist]");
    function renderFilter() {
      if (tabKey === "allsources") { elFilter.style.display = "none"; return; }   // all entries here are website/shared
      var opts = [["all", "All"], ["website", "Website / shared"], ["channel", CL_CH[tabKey] + " only"]];
      elFilter.innerHTML = '<span class="cl-flbl">Show:</span>' + opts.map(function (o) {
        return '<button type="button" class="fchip' + (mode === o[0] ? " on" : "") + '" data-f="' + o[0] + '">' + o[1] + "</button>";
      }).join("");
      elFilter.querySelectorAll(".fchip").forEach(function (b) {
        b.onclick = function () { mode = b.dataset.f; render(); if (onFilterChange) onFilterChange(); };
      });
    }
    function render() {
      renderFilter();
      var rng = getRangeISO(), lo = rng[0], hi = rng[1];
      var list = changelogForTab(tabKey, mode).filter(function (e) { return e.date >= lo && e.date <= hi; });
      elCount.textContent = list.length;
      elScope.textContent = tabKey === "allsources"
        ? "Website / shared changes that affect all paid channels (channel-specific ad changes appear only on their own tab)."
        : "Changes affecting " + CL_CH[tabKey] + ", incl. website / shared. Use the filter to narrow.";
      var html = "", last = "";
      list.forEach(function (e) {
        if (e.date !== last) { html += '<div class="cl-date">' + longDate(e.date) + "</div>"; last = e.date; }
        html += clItemHtml(e);
      });
      elList.innerHTML = html || '<p class="muted" style="padding:6px 2px">No changes in this date range.</p>';
      elList.querySelectorAll("[data-cltoggle]").forEach(function (t) {
        t.onclick = function () { t.parentNode.classList.toggle("open"); };
      });
    }
    render();
    return { refresh: render, mode: function () { return mode; } };
  }

  /* ================= Keyword Quality Scores (Google + Microsoft browse table) =================
     Data: <script id="mc-quality"> built by quality_store.py — the latest live keyword QS
     snapshot ({keywords}) + a 14-day list of per-day {changes}. QS is an output-only CURRENT
     value (no historical QS to query), so movement is detected by diffing daily snapshots; the
     anchor day's ACTIVE-keyword changes are flagged in Daily Alerts (server-rendered by build.py),
     and this collapsed section is the full per-keyword browse table with the latest day's change
     marked inline. Default collapsed; "active keywords only" filter on by default. */
  var QUAL = (function () {
    var el = document.getElementById("mc-quality");
    try { return el ? JSON.parse(el.textContent) : {}; } catch (e) { return {}; }
  })();
  var QFIELD = { qs: "Overall QS", ctr: "Expected CTR", rel: "Ad Relevance", lpx: "Landing Page Exp." };
  var QCOMP = { AA: "Above avg", AV: "Average", BA: "Below avg", "?": "—" };
  var QCLS = { AA: "qs-AA", AV: "qs-AV", BA: "qs-BA" };
  function qBadge(code) { return '<span class="qs-badge ' + (QCLS[code] || "qs-NA") + '">' + (QCOMP[code] || "—") + "</span>"; }
  function qDelta(list, field) {
    if (!list) return "";
    for (var i = 0; i < list.length; i++) {
      if (list[i].f === field) { var d = list[i].dir; return ' <span class="qs-delta ' + (d < 0 ? "dn" : "up") + '">' + (d < 0 ? "▼" : "▲") + "</span>"; }
    }
    return "";
  }
  var QS_TIP =
    '<p>Live keyword Quality Scores (1–10) + the three components — <b>Expected CTR</b>, <b>Ad Relevance</b>, <b>Landing Page Experience</b> — snapshotted daily.</p>' +
    '<p>Quality Score has no history in the ad APIs, so day-over-day movement is detected by comparing snapshots; the latest day’s changes are marked here and flagged in <b>Daily Alerts</b>. Tracking begins the day after the first snapshot.</p>';
  function initQualitySection(tabKey) {
    var host = document.getElementById("quality-" + tabKey);
    if (!host || host._qinit) return;
    var store = QUAL[tabKey];
    if (!store || !store.keywords || !Object.keys(store.keywords).length) return;
    host._qinit = true;
    var rows = Object.keys(store.keywords).map(function (k) { return { k: k, r: store.keywords[k] }; });
    rows.sort(function (a, b) { return (b.r.IMP || 0) - (a.r.IMP || 0); });
    var chg = {}, daily = store.daily || [], latest = daily.length ? daily[daily.length - 1] : null;
    if (latest && latest.date === store.anchor) latest.changes.forEach(function (c) { (chg[c.k] || (chg[c.k] = [])).push(c); });
    var nChanged = Object.keys(chg).length;
    var aKey = "mc_qs_active_" + tabKey;
    var activeOnly = lsGet(aKey, true);
    var upd = store.updated ? (function () { var d = dUTC(store.updated); return MON[d.getUTCMonth()] + " " + d.getUTCDate(); })() : "";

    // ---- columns (declarative): a sort fn + an optional per-header filter ----
    // Component columns (CTR/Rel/LPX) hold codes (AA/AV/BA/?) → sort by quality RANK, not
    // alphabetically. Filterable columns get a ▾ button in the header that opens a popover;
    // clicking the header TITLE sorts. (No separate filter row — David, 2026-06-19.)
    var QRANK = { AA: 3, AV: 2, BA: 1 };
    var COLS = [
      { label: "Keyword",           sort: function (r) { return (r.KW || "").toLowerCase(); }, text: true },
      { label: "Match Type",        sort: function (r) { return (r.MT || "").toLowerCase(); }, text: true, fkey: "mt" },
      { label: "Campaign",          sort: function (r) { return (r.C || "").toLowerCase(); }, text: true },
      { label: "Ad Group",          sort: function (r) { return (r.G || "").toLowerCase(); }, text: true },
      { label: "Avg CPC",           sort: function (r) { return r.CPC == null ? -1 : r.CPC; }, num: true },
      { label: "QS",                sort: function (r) { return r.QS == null ? -1 : r.QS; }, num: true, fkey: "qs" },
      { label: "Expected CTR",      sort: function (r) { return QRANK[r.CTR] || 0; }, fkey: "ctr" },
      { label: "Ad Relevance",      sort: function (r) { return QRANK[r.REL] || 0; }, fkey: "rel" },
      { label: "Landing Page Exp.", sort: function (r) { return QRANK[r.LPX] || 0; }, fkey: "lpx" }
    ];
    var IMPR_COL = 4;   // default sort column (Avg CPC, descending). IMP is kept in the data for the
                        // "active keywords only" filter + sort tiebreak, but no longer shown as a column.

    // distinct values present, per filterable column
    var mtSet = {}, qsSet = {};
    rows.forEach(function (o) { if (o.r.MT) mtSet[o.r.MT] = 1; if (o.r.QS != null) qsSet[o.r.QS] = 1; });
    var COMP = [["AA", "Above avg"], ["AV", "Average"], ["BA", "Below avg"], ["?", "Unknown"]];
    function fOpts(key) {
      if (key === "mt") return Object.keys(mtSet).sort().map(function (v) { return [v, v]; });
      if (key === "qs") return Object.keys(qsSet).map(Number).sort(function (a, b) { return b - a; }).map(function (v) { return [String(v), "QS " + v]; });
      return COMP;
    }
    function rowFval(r, key) {
      if (key === "mt") return r.MT || "";
      if (key === "qs") return r.QS == null ? "" : String(r.QS);
      return r[{ ctr: "CTR", rel: "REL", lpx: "LPX" }[key]] || "?";
    }
    var filt = { mt: "", qs: "", ctr: "", rel: "", lpx: "" };   // "" = All

    var headHtml = COLS.map(function (c, i) {
      var cls = c.num ? ' class="num"' : "";
      var fbtn = c.fkey ? '<button type="button" class="qs-fbtn" data-fk="' + c.fkey + '" aria-label="Filter ' + c.label + '">&#9662;</button>' : "";
      return '<th' + cls + ' data-si="' + i + '"><span class="qs-th-t">' + c.label + '<span class="qs-caret"></span></span>' + fbtn + '</th>';
    }).join("");

    host.innerHTML =
      '<div class="card accordion collapsed">' +
        '<h2>Quality Scores <span class="dt">— ' + rows.length + ' keywords' + (upd ? ", as of " + upd : "") + '</span> ' +
          '<span class="tip" tabindex="0" role="note" aria-label="About Quality Scores"><span class="tipbox">' + QS_TIP + '</span>i</span>' +
        '</h2>' +
        '<div class="qs-ctrls">' +
          '<label><input type="checkbox" data-act' + (activeOnly ? " checked" : "") + '> Active keywords only</label>' +
          '<input type="search" data-q placeholder="Filter keyword / campaign…" class="rangesel" style="min-width:180px">' +
          '<a class="qs-reset" data-reset role="button" tabindex="0">Reset filters</a>' +
          '<span data-qn></span>' +
        '</div>' +
        '<div class="tablewrap qs-wrap"><table class="qs-tbl"><thead><tr>' + headHtml +
        '</tr></thead><tbody data-body></tbody></table></div>' +
      '</div>';
    var card = host.firstChild;
    var body = card.querySelector("[data-body]");
    var actCb = card.querySelector("[data-act]");
    var qIn = card.querySelector("[data-q]");
    var nEl = card.querySelector("[data-qn]");
    card.querySelector("h2").addEventListener("click", function (e) { if (!e.target.closest(".tip")) card.classList.toggle("collapsed"); });

    var sortIdx = IMPR_COL, sortAsc = false;   // default: Impr descending
    var ths = Array.prototype.slice.call(card.querySelectorAll("thead th"));
    function updateCarets() {
      ths.forEach(function (th) {
        var si = +th.getAttribute("data-si"), c = th.querySelector(".qs-caret");
        if (si === sortIdx) { th.classList.add("sorted"); c.textContent = sortAsc ? " ▲" : " ▼"; }
        else { th.classList.remove("sorted"); c.textContent = " ↕"; }
      });
    }
    function updateFilterBtns() {
      ths.forEach(function (th) {
        var fb = th.querySelector(".qs-fbtn"); if (!fb) return;
        fb.classList.toggle("on", !!filt[fb.getAttribute("data-fk")]);
      });
    }

    // ---- header filter popover (single shared, fixed-positioned so the scroll container can't clip it) ----
    var pop = document.createElement("div"); pop.className = "qs-fpop"; document.body.appendChild(pop);
    var popKey = null;
    function closePop() { pop.classList.remove("open"); popKey = null; }
    function openPop(key, btn) {
      popKey = key;
      var opts = [["", "All"]].concat(fOpts(key));
      pop.innerHTML = opts.map(function (o) {
        var on = filt[key] === o[0];
        return '<div class="qs-fopt' + (on ? " on" : "") + '" data-v="' + esc(o[0]) + '">' + (on ? "● " : "○ ") + esc(o[1]) + "</div>";
      }).join("");
      var r = btn.getBoundingClientRect();
      pop.style.visibility = "hidden"; pop.classList.add("open");
      var w = pop.offsetWidth, h = pop.offsetHeight;
      pop.style.left = Math.max(6, Math.min(r.left, window.innerWidth - w - 6)) + "px";
      // open below the button, but flip above if it would run off the bottom of the viewport
      var top = (r.bottom + 4 + h > window.innerHeight - 6) ? (r.top - h - 4) : (r.bottom + 4);
      pop.style.top = Math.max(6, top) + "px";
      pop.style.visibility = "";
    }
    pop.addEventListener("click", function (e) {
      var opt = e.target.closest(".qs-fopt"); if (!opt || !popKey) return;
      filt[popKey] = opt.getAttribute("data-v");
      closePop(); updateFilterBtns(); render();
    });
    document.addEventListener("click", function (e) {
      if (pop.classList.contains("open") && !e.target.closest(".qs-fpop") && !e.target.closest(".qs-fbtn")) closePop();
    });

    ths.forEach(function (th) {
      var si = +th.getAttribute("data-si");
      th.querySelector(".qs-th-t").addEventListener("click", function () {
        if (si === sortIdx) sortAsc = !sortAsc;
        else { sortIdx = si; sortAsc = !!COLS[si].text; }   // text cols open A→Z, numeric/rank cols high→low
        updateCarets(); render();
      });
      var fb = th.querySelector(".qs-fbtn");
      if (fb) fb.addEventListener("click", function (e) {
        e.stopPropagation();
        var key = fb.getAttribute("data-fk");
        if (pop.classList.contains("open") && popKey === key) { closePop(); return; }
        openPop(key, fb);
      });
    });

    function render() {
      var q = (qIn.value || "").trim().toLowerCase();
      var html = "", shown = 0;
      var get = COLS[sortIdx].sort;
      var view = rows.slice().sort(function (a, b) {
        var va = get(a.r), vb = get(b.r), d;
        if (typeof va === "string") d = va < vb ? -1 : va > vb ? 1 : 0;
        else d = va - vb;
        d = sortAsc ? d : -d;
        if (d === 0) return (b.r.IMP || 0) - (a.r.IMP || 0);   // tiebreak: impressions desc
        return d;
      });
      view.forEach(function (o) {
        var r = o.r;
        if (activeOnly && !(r.IMP > 0)) return;
        if (q && (r.KW + " " + r.C + " " + r.G).toLowerCase().indexOf(q) < 0) return;
        if (filt.mt && rowFval(r, "mt") !== filt.mt) return;
        if (filt.qs && rowFval(r, "qs") !== filt.qs) return;
        if (filt.ctr && rowFval(r, "ctr") !== filt.ctr) return;
        if (filt.rel && rowFval(r, "rel") !== filt.rel) return;
        if (filt.lpx && rowFval(r, "lpx") !== filt.lpx) return;
        shown++;
        var cl = chg[o.k];
        html += '<tr' + (cl ? ' class="qs-changed"' : "") + ">" +
          "<td>" + esc(r.KW) + "</td>" +
          "<td>" + (r.MT ? '<span class="qs-mt">' + esc(r.MT) + "</span>" : "—") + "</td>" +
          "<td>" + esc(r.C) + "</td>" +
          "<td>" + esc(r.G) + "</td>" +
          '<td class="num">' + (r.CPC == null ? "—" : fmtMoney(r.CPC, 2)) + "</td>" +
          '<td class="num qs-num">' + (r.QS == null ? "—" : r.QS) + qDelta(cl, "qs") + "</td>" +
          "<td>" + qBadge(r.CTR) + qDelta(cl, "ctr") + "</td>" +
          "<td>" + qBadge(r.REL) + qDelta(cl, "rel") + "</td>" +
          "<td>" + qBadge(r.LPX) + qDelta(cl, "lpx") + "</td></tr>";
      });
      body.innerHTML = html || '<tr><td colspan="9" class="muted">No keywords match.</td></tr>';
      nEl.textContent = shown + " shown" + (nChanged ? " · " + nChanged + " changed today" : "");
    }

    function resetFilters() {
      filt = { mt: "", qs: "", ctr: "", rel: "", lpx: "" };
      qIn.value = "";
      activeOnly = true; actCb.checked = true; lsSet(aKey, true);
      closePop(); updateFilterBtns(); render();
    }
    var resetEl = card.querySelector("[data-reset]");
    resetEl.addEventListener("click", resetFilters);
    resetEl.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); resetFilters(); } });
    actCb.addEventListener("change", function (e) { activeOnly = e.target.checked; lsSet(aKey, activeOnly); render(); });
    qIn.addEventListener("input", render);
    updateCarets(); updateFilterBtns(); render();
  }

  /* ---------- tabs ---------- */
  var inited = {};
  function showTab(key) {
    document.querySelectorAll(".tabbtn").forEach(function (b) { b.classList.toggle("on", b.dataset.tab === key); });
    document.querySelectorAll(".tabpane").forEach(function (p) { p.classList.toggle("on", p.id === "tab-" + key); });
    if (!inited[key]) {
      inited[key] = true;
      if (key === "allsources") initAllSources();
      else { var pt = initPlatformTab(key); if (key === "meta") initMetaBoards(pt); }
      initHistPicker(key, "alerts", { title: "Daily Alerts", ack: true, tip: ALERTS_TIP });
      if (key === "google" || key === "microsoft") {
        initHistPicker(key, "cpc", { title: "Top 20 Search Term CPCs", ack: false, tip: SNAP_TIP });
        initHistPicker(key, "bad", { title: "Bad Search Terms", ack: false, tip: SNAP_TIP });
        initQualitySection(key);
      }
    }
    window.dispatchEvent(new Event("resize"));
  }
  document.querySelectorAll(".tabbtn").forEach(function (b) {
    b.addEventListener("click", function () { showTab(b.dataset.tab); });
  });

  /* ---------- header chrome: Dark/Light · Expand-all · Table-of-Contents ----------
     Same three icon glyphs as the shared HTML template (☾/☀, ⊟/⊞, ☰). Wired here (not
     inline onclick) because the report's logic lives inside this IIFE. */
  var MC_THEME_KEY = "mc_theme";
  function mcApplyTheme(dark) {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    var ic = document.getElementById("mc-theme-icon");
    if (ic) ic.innerHTML = dark ? "&#9728;" : "&#9790;";   /* ☀ in dark mode, ☾ in light */
    try { localStorage.setItem(MC_THEME_KEY, dark ? "dark" : "light"); } catch (e) {}
    if (window.Chart) {                                    /* re-skin chart axes/gridlines */
      Chart.defaults.color = dark ? "#c9d3f2" : "#5A6B8C";
      Chart.defaults.borderColor = dark ? "rgba(255,255,255,.12)" : "rgba(0,0,0,.08)";
      window.__mcCharts.forEach(function (c) { try { c.update("none"); } catch (e) {} });
    }
  }
  var mcThemeBtn = document.getElementById("mc-theme");
  if (mcThemeBtn) mcThemeBtn.addEventListener("click", function () {
    mcApplyTheme(document.documentElement.getAttribute("data-theme") !== "dark");
  });

  var mcAllCollapsed = false;
  var mcExpandBtn = document.getElementById("mc-expand");
  if (mcExpandBtn) mcExpandBtn.addEventListener("click", function () {
    var pane = document.querySelector(".tabpane.on") || document;
    mcAllCollapsed = !mcAllCollapsed;
    pane.querySelectorAll(".card.accordion").forEach(function (c) { c.classList.toggle("collapsed", mcAllCollapsed); });
    var ic = document.getElementById("mc-expand-icon");
    if (ic) ic.innerHTML = mcAllCollapsed ? "&#8862;" : "&#8863;";   /* ⊞ when collapsed, ⊟ when expanded */
    mcExpandBtn.setAttribute("aria-label", mcAllCollapsed ? "Expand all sections" : "Collapse all sections");
  });

  var MC_TABS = [["allsources", "All Sources"], ["google", "Google Ads"], ["microsoft", "Microsoft Ads"], ["meta", "Meta Ads"]];
  var mcMenuBtn = document.getElementById("mc-menu-btn"), mcMenuPanel = document.getElementById("mc-menu-panel");
  function mcBuildMenu() {
    if (!mcMenuPanel) return;
    mcMenuPanel.innerHTML = "";
    var activeBtn = document.querySelector(".tabbtn.on");
    var activeKey = activeBtn ? activeBtn.dataset.tab : "allsources";
    MC_TABS.forEach(function (t, i) {
      if (i) { var sep = document.createElement("div"); sep.className = "menu-sep"; mcMenuPanel.appendChild(sep); }
      var a = document.createElement("a"); a.className = "mp-tab"; a.textContent = t[1]; a.href = "#";
      a.onclick = function (e) { e.preventDefault(); showTab(t[0]); window.scrollTo({ top: 0, behavior: "smooth" }); mcMenuPanel.classList.remove("open"); };
      mcMenuPanel.appendChild(a);
      if (t[0] === activeKey) {   /* list the visible section titles of the active tab */
        var pane = document.getElementById("tab-" + t[0]);
        if (pane) pane.querySelectorAll(".card>h2").forEach(function (h) {
          var card = h.closest(".card"); if (!card || card.offsetParent === null) return;
          var title = ((h.childNodes[0] && h.childNodes[0].textContent) || h.textContent || "").trim();
          if (!title) return;
          var s = document.createElement("a"); s.className = "mp-sec"; s.textContent = title; s.href = "#";
          s.onclick = function (e) {
            e.preventDefault(); card.classList.remove("collapsed");
            card.scrollIntoView({ behavior: "smooth", block: "start" }); mcMenuPanel.classList.remove("open");
          };
          mcMenuPanel.appendChild(s);
        });
      }
    });
  }
  if (mcMenuBtn) mcMenuBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    if (!mcMenuPanel.classList.contains("open")) mcBuildMenu();
    mcMenuPanel.classList.toggle("open");
  });
  if (mcMenuPanel) mcMenuPanel.addEventListener("click", function (e) { e.stopPropagation(); });
  document.addEventListener("click", function () { if (mcMenuPanel) mcMenuPanel.classList.remove("open"); });

  /* keyboard shortcuts: d = toggle the hamburger menu (Table of Contents), c = expand/collapse
     all sections. Reuse the buttons' own click handlers so behavior stays identical. Ignored
     while typing in a field (search / date / notes) or when a modifier key is held. */
  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
    var t = e.target;
    if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
    var k = (e.key || "").toLowerCase();
    if (k === "b" && mcMenuBtn) { e.preventDefault(); mcMenuBtn.click(); }
    else if (k === "c" && mcExpandBtn) { e.preventDefault(); mcExpandBtn.click(); }
  });

  /* apply the saved theme BEFORE the first charts are built so they pick up the right defaults */
  try { mcApplyTheme(localStorage.getItem(MC_THEME_KEY) === "dark"); } catch (e) {}

  showTab("allsources");
})();
