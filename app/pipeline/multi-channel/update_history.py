#!/usr/bin/env python3
"""update_history.py — daily append/refresh of the history fact tables.

Usage:
  python3 update_history.py <ANCHOR> [--brain-cohort F] [--brain-funnel F] [--kw-funnel F]
      [--meta-agdemos F] [--meta-demos-sf F] [--campfunnel F] [--ag-pipeline F] [--ag-rankis-google F]

  --meta-demos-sf F    : Salesforce Meta demos (node backfill/sf_meta_demos.js <ANCHOR-29d> <ANCHOR> F)
                         — FIRST-TOUCH demos by UTM_Campaign__c (the META demo basis since 2026-06-16,
                         replaces the Brain ma-funnel) + the Retargeting first/last/all-touch series.

  --ag-pipeline F      : one Salesforce file (node backfill/sf_agpipeline.js <ANCHOR-29d> <ANCHOR> F)
                         covering google/bing/meta — per-ad-group booked-on-day Opps/Accts/ARR
                         (agpipeline [d,c,g,opps,accts,arr$]); SOURCE filtered per channel.
  --ag-rankis-google F : Google ad-group impr-share / rank-lost IS (python3 backfill/google_agrankis.py
                         <ANCHOR-29d> <ANCHOR> F) → agrankis [d,c,g,rankLost,imprShare]. GOOGLE ONLY —
                         Bing has no ad-group impression share; Meta has no impression share.

What it does (run AFTER the sibling artifacts' morning routines, BEFORE build.py):
  1. Extends every history file's date axis through ANCHOR.
  2. Replaces the TRAILING window of each fact type from fresh sources
     (spend/clicks restate for a few days — invalid-click credits — so we
     re-write the whole trailing window instead of appending one day):
       - google/microsoft spend (30d):  sibling data/<ANCHOR>/clicks_30d.json
       - google/microsoft rank-IS (30d): sibling data/<ANCHOR>/rankis_30d.json
       - google/microsoft demos+pipeline (30d): the Brain raw pulls passed via
         --brain-cohort / --brain-funnel (one pull covers BOTH platforms —
         filter SOURCE client-side). If omitted/missing, the trailing demos/
         pipeline keep their previous values (stale-tail warning printed).
       - meta spend (30d): meta sibling clicks_30d.json (ad-set grain)
       - meta demos (30d): Salesforce FIRST-TOUCH via --meta-demos-sf (sf_meta_demos.js);
         also refreshes the Retargeting first/last/all-touch `retarget` series
       - meta pipeline (30d): meta sibling pipeline_30d.json
       - allsources (140d): all-channels sibling series.json
  3. Never touches anything older than each source's window — history is frozen.
"""
import json, os, sys, datetime, collections

HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.dirname(HERE)
SIB = {
    "google": os.path.join(SK, "Google Ads", "google-ads-daily-artifact"),
    "microsoft": os.path.join(SK, "Microsoft Ads", "microsoft-ads-daily-artifact"),
    "meta": os.path.join(SK, "meta-ads", "meta-ads-daily-artifact"),
    "allsources": os.path.join(SK, "all-channels-artifact"),
}

def jload(path):
    with open(path) as f:
        return json.load(f)

def brain_rows(path):
    obj = jload(path)
    r = obj.get("result", obj)
    if isinstance(r, str):
        r = json.loads(r)
    return r["rows"] if isinstance(r, dict) else r

def norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

def hist_path(k):
    return os.path.join(HERE, "history", k + ".json")

def extend_dates(h, anchor):
    last = h["dates"][-1]
    d = datetime.date.fromisoformat(last)
    a = datetime.date.fromisoformat(anchor)
    while d < a:
        d += datetime.timedelta(days=1)
        h["dates"].append(d.isoformat())
    return {dt: i for i, dt in enumerate(h["dates"])}

def replace_window(rows, didx_keep_before, keep_keys=None, keyfn=None):
    """Drop fact rows with date index >= cutoff (they get re-written).
    keep_keys/keyfn: sibling daily pulls filter to ENABLED campaigns/ad groups, so a
    paused entity vanishes from the fresh pull entirely — keep its in-window rows
    (frozen) instead of wiping them. keyfn(row) -> key; rows whose key is NOT in
    keep_keys survive the cut."""
    if keep_keys is None:
        return [r for r in rows if r[0] < didx_keep_before]
    return [r for r in rows if r[0] < didx_keep_before or keyfn(r) not in keep_keys]

# ---- per-ad-group Day-0 demos (agdemos [d,c,g,n]) tail helpers ----
def sid(x):
    try: return str(int(x))
    except (TypeError, ValueError): return None

def load_gmap(path):   # Google Ads MCP ad_group dump: {"result":[{ad_group.id,ad_group.name,campaign.id}]}
    return {sid(r["ad_group.id"]): (r["ad_group.name"], sid(r["campaign.id"])) for r in jload(path)["result"]}

def load_msmap(path):  # ms_adgroup_map.json: {adgroup_id:[name,campaign_id]}
    return {sid(k): (v[0], sid(v[1])) for k, v in jload(path).items()}

def merge_agdemos_brain(h, didx, id2c, g2i, rows, agid2name):
    """rows = Brain keyword-funnel rows for ONE source; map AD_GROUP_ID->history group, sum DAY0."""
    if not rows:
        return None
    dts = sorted({r["DT"] for r in rows})
    cut = didx.get(dts[0], len(h["dates"]))
    h["agdemos"] = replace_window(h.get("agdemos", []), cut)
    agg = collections.Counter()
    for r in rows:
        n = int(r.get("DEMOS_SCHEDULED_AT_DAY0") or 0)
        d = didx.get(r.get("DT"))
        if not n or d is None:
            continue
        agid = sid(r.get("AD_GROUP_ID"))
        nm = agid2name.get(agid) if agid else None
        if not nm:
            continue
        g = g2i.get((nm[0], id2c.get(sid(r.get("CAMPAIGN_ID")))))
        if g is None:
            continue
        agg[(d, h["groups"][g][1], g)] += n   # c = the group's own campIdx
    h["agdemos"] += [[d, c, g, n] for (d, c, g), n in sorted(agg.items())]
    return sum(agg.values())

def merge_agpipeline_named(h, didx, id2c, g2i, rows, agid2name):
    """google/microsoft: ad-group booked-on-day pipeline (sf_agpipeline.js rows for ONE source),
    AD_GROUP_ID->history group via id->name map. agpipeline [d,c,g,opps,accts,arr$]."""
    if not rows:
        return None
    dts = sorted({r["DT"] for r in rows})
    cut = didx.get(dts[0], len(h["dates"]))
    h["agpipeline"] = replace_window(h.get("agpipeline", []), cut)
    agg = collections.defaultdict(lambda: [0, 0, 0.0])
    for r in rows:
        d = didx.get(r.get("DT"))
        if d is None:
            continue
        agid = sid(r.get("ADGROUP_ID"))
        nm = agid2name.get(agid) if agid else None
        if not nm:
            continue
        g = g2i.get((nm[0], id2c.get(sid(r.get("CAMPAIGN_ID")))))
        if g is None:
            continue
        v = agg[(d, h["groups"][g][1], g)]
        v[0] += int(r.get("OPPS") or 0); v[1] += int(r.get("ACCTS") or 0); v[2] += float(r.get("ARR") or 0)
    h["agpipeline"] += [[d, c, g, v[0], v[1], round(v[2])] for (d, c, g), v in sorted(agg.items())]
    return sum(v[0] for v in agg.values())

def merge_agrankis(h, didx, id2c, g2i, rows, agid2name):
    """google only: ad-group impr-share / rank-lost (google_agrankis.py rows). agrankis [d,c,g,rankLost,imprShare]."""
    if not rows:
        return None
    dts = sorted({r["DT"] for r in rows})
    cut = didx.get(dts[0], len(h["dates"]))
    h["agrankis"] = replace_window(h.get("agrankis", []), cut)
    out = {}
    for r in rows:
        d = didx.get(r.get("DT"))
        if d is None:
            continue
        nm = agid2name.get(sid(r.get("AD_GROUP_ID")))
        if not nm:
            continue
        g = g2i.get((nm[0], id2c.get(sid(r.get("CAMPAIGN_ID")))))
        if g is None:
            continue
        out[(d, h["groups"][g][1], g)] = [r.get("RANK_LOST"), r.get("IMPR_SHARE")]
    h["agrankis"] += [[d, c, g, v[0], v[1]] for (d, c, g), v in sorted(out.items())]
    return len(out)

def main():
    if len(sys.argv) < 2:
        sys.exit("usage: update_history.py <ANCHOR> [--brain-cohort F] [--brain-funnel F]")
    anchor = sys.argv[1]
    opts = dict(zip(sys.argv[2::2], sys.argv[3::2]))
    warn = []

    # ---------------- google + microsoft ----------------
    for plat, brain_src in (("google", "google-ads"), ("microsoft", "bing")):
        h = jload(hist_path(plat))
        didx = extend_dates(h, anchor)
        name2c = {norm(c[1]): i for i, c in enumerate(h["campaigns"])}
        id2c = {str(c[0]): i for i, c in enumerate(h["campaigns"]) if c[0] is not None}
        g2i = {(g[0], g[1]): i for i, g in enumerate(h["groups"])}

        def camp_by_name(name):
            k = norm(name)
            if k not in name2c:
                h["campaigns"].append([None, name]); name2c[k] = len(h["campaigns"]) - 1
            return name2c[k]
        def grp(name, c):
            if (name, c) not in g2i:
                h["groups"].append([name, c]); g2i[(name, c)] = len(h["groups"]) - 1
            return g2i[(name, c)]

        f = os.path.join(SIB[plat], "data", anchor, "clicks_30d.json")
        if os.path.exists(f):
            rows = jload(f)
            dts = sorted({r["segments.date"] for r in rows})
            cut = didx[dts[0]]
            fresh = [(didx.get(r["segments.date"]), camp_by_name(r["campaign.name"]),
                      grp(r["ad_group.name"], camp_by_name(r["campaign.name"])), r) for r in rows]
            keep = {(c, g) for d, c, g, _ in fresh}
            h["spend"] = replace_window(h["spend"], cut, keep, lambda r: (r[1], r[2]))
            for d, c, g, r in fresh:
                if d is None: continue
                h["spend"].append([d, c, g, round(r.get("metrics.cost_micros", 0) / 1e4),
                                   int(r.get("metrics.clicks", 0)), int(r.get("metrics.impressions", 0))])
        else:
            warn.append(f"{plat}: clicks_30d missing — spend tail stale")

        f = os.path.join(SIB[plat], "data", anchor, "rankis_30d.json")
        if os.path.exists(f):
            rows = jload(f)
            dts = sorted({r["segments.date"] for r in rows})
            cut = didx[dts[0]]
            keep = {camp_by_name(r["campaign.name"]) for r in rows}
            h["rankis"] = replace_window(h["rankis"], cut, keep, lambda r: r[1])
            for r in rows:
                d = didx.get(r["segments.date"])
                if d is None: continue
                h["rankis"].append([d, camp_by_name(r["campaign.name"]),
                                    round(r.get("metrics.search_rank_lost_impression_share") or 0, 4),
                                    round(r.get("metrics.search_impression_share") or 0, 4), 0])
        else:
            warn.append(f"{plat}: rankis_30d missing — rank-IS tail stale")

        if opts.get("--brain-cohort") and os.path.exists(opts["--brain-cohort"]):
            all_cohort = brain_rows(opts["--brain-cohort"])
            # GUARD (2026-06-22, parity with the daily artifacts): if the Brain cohort cube has NO
            # row for the ANCHOR at all (the 24h cache / Snowflake load lagged and the cube ends at
            # ANCHOR-1), do NOT rewrite the demos tail — replace_window would drop the trailing
            # window and refill only through ANCHOR-1, silently ZEROING the anchor day (the false-0
            # bug). A true zero day still has the anchor ROW present (some source logged it), so
            # "no row for the date anywhere" = missing data. Preserve prior history + warn; the
            # agent should re-pull with refresh=true (MCP-only). Self-heals next run.
            if all_cohort and not any(str(r.get("DT"))[:10] == anchor for r in all_cohort):
                mx = max((str(r.get("DT"))[:10] for r in all_cohort), default="(none)")
                warn.append(f"{plat}: brain-cohort cube lacks ANCHOR {anchor} (cube ends {mx}) — "
                            f"demos tail NOT rewritten (kept prior to avoid a false 0); re-pull with refresh=true")
            else:
                rows = [r for r in all_cohort if r.get("SOURCE") == brain_src]
                if rows:
                    dts = sorted({r["DT"] for r in rows})
                    cut = didx.get(dts[0], len(h["dates"]))
                    h["demos"] = replace_window(h["demos"], cut)
                    agg = collections.Counter()
                    for r in rows:
                        n = int(r.get("DEMO_SCHEDULED_AT_DAY0") or 0)
                        if not n or r["DT"] not in didx: continue
                        c = id2c.get(str(int(r["CAMPAIGN_ID"])) if r.get("CAMPAIGN_ID") is not None else "", -1)
                        agg[(didx[r["DT"]], c)] += n
                    h["demos"] += [[d, c, n] for (d, c), n in sorted(agg.items())]
        else:
            warn.append(f"{plat}: no --brain-cohort — demos tail stale")
        if opts.get("--brain-funnel") and os.path.exists(opts["--brain-funnel"]):
            rows = [r for r in brain_rows(opts["--brain-funnel"]) if r.get("SOURCE") == brain_src]
            if rows:
                dts = sorted({r["DT"] for r in rows})
                cut = didx.get(dts[0], len(h["dates"]))
                h["pipeline"] = replace_window(h["pipeline"], cut)
                agg = collections.defaultdict(lambda: [0, 0, 0.0])
                for r in rows:
                    o, a, arr = int(r.get("OPPS") or 0), int(r.get("ACCOUNTS") or 0), float(r.get("NEW_ARR") or 0)
                    if not (o or a or arr) or r["DT"] not in didx: continue
                    c = id2c.get(str(int(r["CAMPAIGN_ID"])) if r.get("CAMPAIGN_ID") is not None else "", -1)
                    v = agg[(didx[r["DT"]], c)]
                    v[0] += o; v[1] += a; v[2] += arr
                h["pipeline"] += [[d, c, v[0], v[1], round(v[2])] for (d, c), v in sorted(agg.items())]
        else:
            warn.append(f"{plat}: no --brain-funnel — pipeline tail stale")

        # per-ad-group Day-0 demos (agdemos): Brain keyword-funnel + cached id->name map
        kwf = opts.get("--kw-funnel")
        gmap = (os.path.join(HERE, "backfill", "agdemos_raw", "google_adgroup_map.json")
                if plat == "google" else os.path.join(HERE, "backfill", "ms_adgroup_map.json"))
        amap = None
        if kwf and os.path.exists(kwf) and os.path.exists(gmap):
            amap = load_gmap(gmap) if plat == "google" else load_msmap(gmap)
            krows = [r for r in brain_rows(kwf) if r.get("SOURCE") == brain_src]
            got = merge_agdemos_brain(h, didx, id2c, g2i, krows, amap)
            if got is None:
                warn.append(f"{plat}: --kw-funnel had no {brain_src} rows — agdemos tail stale")
        else:
            warn.append(f"{plat}: no --kw-funnel / id-map — agdemos tail stale")

        # per-ad-group booked-on-day pipeline (agpipeline): one Salesforce file covers all channels
        agp = opts.get("--ag-pipeline")
        if agp and os.path.exists(agp) and (amap or os.path.exists(gmap)):
            if amap is None:
                amap = load_gmap(gmap) if plat == "google" else load_msmap(gmap)
            prows = [r for r in jload(agp).get("rows", []) if r.get("SOURCE") == brain_src]
            if merge_agpipeline_named(h, didx, id2c, g2i, prows, amap) is None:
                warn.append(f"{plat}: --ag-pipeline had no {brain_src} rows — agpipeline tail stale")
        else:
            warn.append(f"{plat}: no --ag-pipeline / id-map — agpipeline tail stale")

        # per-ad-group impr-share / rank-lost IS (agrankis): GOOGLE only (Bing has no ad-group IS)
        if plat == "google":
            agr = opts.get("--ag-rankis-google")
            if agr and os.path.exists(agr) and (amap or os.path.exists(gmap)):
                if amap is None:
                    amap = load_gmap(gmap)
                if merge_agrankis(h, didx, id2c, g2i, jload(agr).get("rows", []), amap) is None:
                    warn.append("google: --ag-rankis-google empty — agrankis tail stale")
            else:
                warn.append("google: no --ag-rankis-google — agrankis tail stale")
        json.dump(h, open(hist_path(plat), "w"), separators=(",", ":"))

    # ---------------- meta ----------------
    h = jload(hist_path("meta"))
    didx = extend_dates(h, anchor)
    name2c = {norm(c[1]): i for i, c in enumerate(h["campaigns"])}
    id2c = {str(c[0]): i for i, c in enumerate(h["campaigns"]) if c[0] is not None}
    gname2i = {(g[1], g[2]): i for i, g in enumerate(h["groups"])}
    def mcamp(name):
        k = norm(name)
        if k not in name2c:
            h["campaigns"].append([None, name]); name2c[k] = len(h["campaigns"]) - 1
        return name2c[k]
    f = os.path.join(SIB["meta"], "data", anchor, "clicks_30d.json")
    if os.path.exists(f):
        rows = jload(f)
        dts = sorted({r["segments.date"] for r in rows})
        cut = didx[dts[0]]
        fresh = []
        for r in rows:
            c = mcamp(r["campaign.name"])
            gk = (r["ad_group.name"], c)
            if gk not in gname2i:
                h["groups"].append([None, r["ad_group.name"], c]); gname2i[gk] = len(h["groups"]) - 1
            fresh.append((didx.get(r["segments.date"]), c, gname2i[gk], r))
        keep = {(c, g) for d, c, g, _ in fresh}
        h["spend"] = replace_window(h["spend"], cut, keep, lambda r: (r[1], r[2]))
        for d, c, g, r in fresh:
            if d is None: continue
            h["spend"].append([d, c, g, round(r.get("metrics.cost_micros", 0) / 1e4),
                               int(r.get("metrics.clicks", 0)), int(r.get("metrics.impressions", 0))])
    else:
        warn.append("meta: clicks_30d missing — spend tail stale")
    # demos: Salesforce FIRST-TOUCH by UTM_Campaign__c name (sf_meta_demos.js daily pull) —
    # replaces the old Brain ma-funnel last-touch basis (2026-06-16). Unmapped first-touch UTM
    # campaign → unattributed (-1); NEVER create a new campaign for it (use name2c.get, not mcamp).
    # Also refresh the Retargeting first/last/all-touch series for the chart attribution chip.
    smf = opts.get("--meta-demos-sf")
    if smf and os.path.exists(smf):
        sfm = jload(smf)
        ftrows = sfm.get("ft", [])
        dts = sorted({r["DT"] for r in ftrows})
        if dts:
            cut = didx.get(dts[0], len(h["dates"]))
            h["demos"] = replace_window(h["demos"], cut)
            for r in ftrows:
                d = didx.get(r["DT"])
                if d is None: continue
                h["demos"].append([d, name2c.get(norm(r["CAMP"]), -1), int(r["N"])])
        rrows = sfm.get("retarget", [])
        rdts = sorted({r["DT"] for r in rrows})
        if rdts:
            rcut = didx.get(rdts[0], len(h["dates"]))
            h["retarget"] = replace_window(h.get("retarget", []), rcut)
            for r in rrows:
                d = didx.get(r["DT"])
                if d is None: continue
                if r.get("FT") or r.get("LT") or r.get("AT"):
                    h["retarget"].append([d, int(r["FT"]), int(r["LT"]), int(r["AT"])])
    else:
        warn.append("meta: no --meta-demos-sf — demos/retarget tail stale")
    f = os.path.join(SIB["meta"], "data", anchor, "pipeline_30d.json")
    if os.path.exists(f):
        p = jload(f)
        days = p.get("days", [])
        if days:
            cut = didx.get(days[0], len(h["dates"]))
            h["pipeline"] = replace_window(h["pipeline"], cut)
            def emit(name_idx, blob):
                for j, dt in enumerate(days):
                    if dt not in didx: continue
                    o, a, arr = blob["opps"][j], blob["accts"][j], blob["arr"][j]
                    if o or a or arr:
                        h["pipeline"].append([didx[dt], name_idx, int(o), int(a), round(float(arr))])
            for name, blob in p.get("campaigns", {}).items():
                emit(mcamp(name), blob)
            if p.get("unattributed"):
                emit(-1, p["unattributed"])
    else:
        warn.append("meta: pipeline_30d missing — pipeline tail stale")

    # meta per-ad-set Day-0 demos (agdemos): Salesforce ad-set cohort (sf_agdemos.js meta);
    # ADGROUP_ID == the ad-set id stored in groups[g][0] → direct join (no name map).
    maf = opts.get("--meta-agdemos")
    if maf and os.path.exists(maf):
        rows = jload(maf).get("rows", [])
        adset2g = {sid(g[0]): i for i, g in enumerate(h["groups"]) if g[0] is not None}
        dts = sorted({r["DT"] for r in rows})
        if dts:
            cut = didx.get(dts[0], len(h["dates"]))
            h["agdemos"] = replace_window(h.get("agdemos", []), cut)
            agg = collections.Counter()
            for r in rows:
                n = int(r.get("DEMOS") or 0)
                d = didx.get(r.get("DT"))
                if not n or d is None:
                    continue
                g = adset2g.get(sid(r.get("ADGROUP_ID"))) if r.get("ADGROUP_ID") else None
                if g is None:
                    continue
                agg[(d, h["groups"][g][2], g)] += n
            h["agdemos"] += [[d, c, g, n] for (d, c, g), n in sorted(agg.items())]
    else:
        warn.append("meta: no --meta-agdemos — agdemos tail stale")

    # meta per-ad-set booked-on-day pipeline (agpipeline): same Salesforce file; SOURCE=='meta';
    # ADGROUP_ID == the ad-set id in groups[g][0] → direct join.
    agp = opts.get("--ag-pipeline")
    if agp and os.path.exists(agp):
        prows = [r for r in jload(agp).get("rows", []) if r.get("SOURCE") == "meta"]
        adset2g = {sid(g[0]): i for i, g in enumerate(h["groups"]) if g[0] is not None}
        dts = sorted({r["DT"] for r in prows})
        if dts:
            cut = didx.get(dts[0], len(h["dates"]))
            h["agpipeline"] = replace_window(h.get("agpipeline", []), cut)
            agg = collections.defaultdict(lambda: [0, 0, 0.0])
            for r in prows:
                d = didx.get(r.get("DT"))
                if d is None:
                    continue
                g = adset2g.get(sid(r.get("ADGROUP_ID"))) if r.get("ADGROUP_ID") else None
                if g is None:
                    continue
                v = agg[(d, h["groups"][g][2], g)]
                v[0] += int(r.get("OPPS") or 0); v[1] += int(r.get("ACCTS") or 0); v[2] += float(r.get("ARR") or 0)
            h["agpipeline"] += [[d, c, g, v[0], v[1], round(v[2])] for (d, c, g), v in sorted(agg.items())]
    else:
        warn.append("meta: no --ag-pipeline — agpipeline tail stale")
    json.dump(h, open(hist_path("meta"), "w"), separators=(",", ":"))

    # ---------------- allsources ----------------
    h = jload(hist_path("allsources"))
    # per-campaign funnel tail (drill-down tables): --campfunnel <raw file> from
    # `node backfill/sf_campfunnel.js <ANCHOR-29d> <ANCHOR>` (now incl. OPPS/ACCTS/ARR)
    # resolve the campfunnel raw file: a --campfunnel path written with a DASHED window
    # (sf_campfunnel_2026-05-25_2026-06-23.json) won't exist — sf_campfunnel.js writes
    # sf_campfunnel_YYYYMMDD_YYYYMMDD.json (no dashes). If the given path is missing/omitted,
    # auto-discover: prefer the file that ENDS at ANCHOR (this run's window), else the newest
    # numerically-tagged file. The [0-9]* char-class excludes non-dated files like sf_campfunnel_2yr.json.
    cf_path = opts.get("--campfunnel")
    if not (cf_path and os.path.exists(cf_path)):
        import glob
        _raw = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backfill", "raw")
        _atag = anchor.replace("-", "")
        _cands = sorted(glob.glob(os.path.join(_raw, "sf_campfunnel_[0-9]*_%s.json" % _atag))) \
              or sorted(glob.glob(os.path.join(_raw, "sf_campfunnel_[0-9]*_[0-9]*.json")))
        if _cands:
            _auto = _cands[-1]
            warn.append("allsources: --campfunnel %s → auto-discovered %s" % (
                ("'%s' missing" % cf_path) if cf_path else "omitted", os.path.basename(_auto)))
            cf_path = _auto
    if cf_path and os.path.exists(cf_path):
        rows = jload(cf_path)
        cf = h.get("campfunnel")
        if cf and rows:
            didx0 = {dt: i for i, dt in enumerate(h["dates"])}
            cmap = {n: i for i, n in enumerate(cf["campaigns"])}
            def cf_idx(name):
                k = name if name else "(no campaign)"
                if k not in cmap:
                    cf["campaigns"].append(k); cmap[k] = len(cf["campaigns"]) - 1
                return cmap[k]
            dts = sorted({r["DT"] for r in rows})
            cut = didx0.get(dts[0], len(h["dates"]))
            cf["rows"] = [r for r in cf["rows"] if r[0] < cut]
            for r in rows:
                if r["DT"] not in didx0 or r["SOURCE"] not in cf["sources"]: continue
                cf["rows"].append([didx0[r["DT"]], cf["sources"].index(r["SOURCE"]), cf_idx(r.get("UTM_CAMPAIGN")),
                                   int(r.get("MQLS") or 0), int(r.get("DEMOS") or 0), int(r.get("COMPLETED") or 0),
                                   int(r.get("OPPS") or 0), int(r.get("ACCTS") or 0), round(float(r.get("ARR") or 0))])
            cf["rows"].sort()
    else:
        warn.append("allsources: no --campfunnel — drill-down tail stale")
    f = os.path.join(SIB["allsources"], "data", anchor, "series.json")
    if os.path.exists(f):
        s = jload(f)
        days = s["days"]
        didx = extend_dates(h, anchor)
        for key, sl in s["slices"].items():
            if key not in h["slices"]: continue
            H = h["slices"][key]
            for m in ("mqls", "demos", "demos_day0", "completed", "opps", "accounts", "arr"):
                # pad history arrays to the new date length
                while len(H[m]) < len(h["dates"]): H[m].append(0)
                for j, dt in enumerate(days):
                    if dt in didx:
                        v = sl[m][j]
                        H[m][didx[dt]] = round(v) if m == "arr" else v
    else:
        warn.append("allsources: series.json missing — tail stale")
    json.dump(h, open(hist_path("allsources"), "w"), separators=(",", ":"))

    print(f"✅ history updated through {anchor}")
    for w in warn:
        print("  ⚠", w)

if __name__ == "__main__":
    main()
