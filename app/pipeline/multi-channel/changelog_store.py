#!/usr/bin/env python3
"""changelog_store.py — build history/changelog.json from the 3 raw change logs.

Usage:  python3 changelog_store.py [END_DATE]      (default: yesterday America/New_York)

Reads (raw, pulled by the daily routine / the one-time backfill):
    backfill/changelog_raw/posthog.json   list of PostHog annotation objects
    backfill/changelog_raw/notion.json    list of Notion Change-Log rows
    backfill/changelog_raw/slack_canvas.md the Website Changelog canvas markdown
Writes:
    history/changelog.json   deduped-across-sources, channel-classified, 12-month window

Design (locked):
  * One entry per dated change-note; cross-source duplicates merged (union-find within
    the same effective date — see dedupe()). The same site publish logged in PostHog +
    Slack + Notion collapses to ONE entry that keeps all three source links.
  * Channels model (drives per-tab routing in engine.js):
       website                -> shows on ALL tabs (Google, Microsoft, Meta, All Sources)
       google/microsoft/meta  -> that channel's tab only
       other (ChatGPT Ads, …) -> All Sources only
    PostHog + Slack default to "website" (they only ever log website/demo-form/tracking/
    consent changes). Notion is routed by its `Platform` multi-select.
  * Notion: keep Status in {Implimented, Implimenting}; effective date = Date Submitted
    (the DB dropped its Date Implimented property 2026-07-13; old raw rows still fall back to it).
  * Idempotent: a full re-pull + rebuild reproduces the store, so the daily routine just
    re-pulls the 3 sources and reruns this — backdated edits are picked up automatically.
"""
import json, os, re, sys, datetime, hashlib
from collections import defaultdict
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "backfill", "changelog_raw")
OUT = os.path.join(HERE, "history", "changelog.json")
PH_BASE = "https://us.posthog.com/project/11653/data-management/annotations/"
SLACK_URL = "https://doorloop.slack.com/docs/TPFV4E4P5/F093KQ88L8N"
WINDOW_DAYS = 365
NOTION_STATUS_KEEP = {"Implimented", "Implimenting"}
DETAIL_CAP = 1400

# Notion Platform multi-select option -> our channel bucket
PLATFORM_MAP = {
    "Google Ads": "google", "Bing Ads": "microsoft", "META Ads": "meta",
    "ChatGPT Ads": "other",
    "Webflow Website": "website", "Webflow Demo Form": "website", "Demo Form": "website",
    "calendly": "website", "Google Tag Manager": "website", "Hubspot": "website",
    "Salesforce": "website", "N8N": "website", "Outreach": "website", "outre": "website",
    "All Paid Search & Social Ads": "website",
}
SRC_PRIO = {"posthog": 0, "slack": 1, "notion": 2}

# publish-type in EITHER word order: "Published the site" OR "Webflow Website Published"
PUBLISH_RE = re.compile(
    r'(?:publish(?:ed)?\b.{0,40}\b(?:site|website|webflow|demo[\s-]?form|page)'
    r'|(?:site|website|webflow|demo[\s-]?form)\b.{0,30}\bpublish)', re.I)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}
STOP = set(("the a an and or of to for in on at is are was were be been being with from this that "
            "we our it its as by we're will would can could should have has had not no new "
            "site page pages publish published update updated change changed added add").split())


def yesterday_ny():
    return (datetime.datetime.now(ZoneInfo("America/New_York")).date() - datetime.timedelta(days=1)).isoformat()


def clean(text):
    """light markdown strip: images out, [t](u)->t, drop **/__, collapse spaces, keep newlines."""
    if not text:
        return ""
    text = text.replace("\\n", "\n")
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)        # images
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)    # links -> link text
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def first_line(text, n=120):
    line = clean(text).split("\n")[0].strip()
    line = re.sub(r'^\s*(?:\d+\.|[-*•])\s*', '', line)      # drop leading list marker
    if len(line) > n:
        line = line[:n - 1].rstrip() + "…"
    return line


def tokens(s):
    return set(w for w in re.findall(r'[a-z0-9]+', s.lower()) if len(w) > 2 and w not in STOP)


def jaccard(a, b):
    return len(a & b) / len(a | b) if (a and b) else 0.0


def mk_entry(date, title, detail, channels, status, src, url, ref, publish):
    if detail and detail.strip() == title.strip():
        detail = ""
    return {"date": date, "title": title, "detail": detail, "channels": channels,
            "status": status, "sources": [{"src": src, "url": url, "ref": str(ref)}],
            "_publish": bool(publish), "_src": src}


def classify_platform(plats):
    chans = set(PLATFORM_MAP.get(p, "website") for p in plats)
    if not chans:
        return ["website"]
    if "website" in chans:                 # website affects every channel -> dominates
        return ["website"]
    return sorted(chans)


# ---- parsers -------------------------------------------------------------
def parse_posthog(raw, win_start):
    out = []
    for a in raw:
        if a.get("deleted"):
            continue
        date = (a.get("date_marker") or "")[:10]
        if not re.match(r'\d{4}-\d\d-\d\d$', date) or date < win_start:
            continue
        content = (a.get("content") or "").strip()
        if not content:
            continue
        cid = str(a.get("id"))
        out.append(mk_entry(date, first_line(content), clean(content), ["website"],
                            "implemented", "posthog", PH_BASE + cid, cid,
                            PUBLISH_RE.search(content)))
    return out


def parse_notion(raw, win_start):
    out = []
    for r in raw:
        if r.get("Status") not in NOTION_STATUS_KEEP:
            continue
        # Date Implimented was removed from the Change Log DB (2026-07-13) — Date Submitted is
        # the only date column left; keep the old key as a harmless fallback for any raw row that
        # still carries it until the next weekly full-replace re-pull.
        date = (r.get("date:Date Submitted:start") or r.get("date:Date Implimented:start") or "")[:10]
        if not re.match(r'\d{4}-\d\d-\d\d$', date) or date < win_start:
            continue
        title = clean(r.get("Title") or "").strip() or "(untitled change)"
        desc = (r.get("Description (Reason + Anticipated Impact)") or "").strip()
        if desc.lower().rstrip(".") in ("", "open to see more"):
            desc = ""
        try:
            plats = json.loads(r.get("Platform") or "[]")
        except Exception:
            plats = []
        url = r.get("url") or ""
        ref = url.rsplit("/", 1)[-1] if url else title
        status = "implementing" if r.get("Status") == "Implimenting" else "implemented"
        out.append(mk_entry(date, title, clean(desc), classify_platform(plats), status,
                            "notion", url, ref, PUBLISH_RE.search(title + " " + desc)))
    return out


def parse_slack(md, win_start):
    out, cur_h, cur_b, secs = [], None, [], []
    for ln in md.split("\n"):
        m = re.match(r'^#\s+(.+?)\s*$', ln)
        if m:
            if cur_h is not None:
                secs.append((cur_h, cur_b))
            cur_h, cur_b = m.group(1), []
        elif cur_h is not None:
            cur_b.append(ln)
    if cur_h is not None:
        secs.append((cur_h, cur_b))
    for h, body in secs:
        dm = re.match(r'([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})', h)
        if not dm or dm.group(1) not in MONTHS:
            continue                                          # "Next site publish:" etc.
        date = f"{dm.group(3)}-{MONTHS[dm.group(1)]:02d}-{int(dm.group(2)):02d}"
        if date < win_start:
            continue
        btext = "\n".join(body).strip()
        if not btext:
            continue
        out.append(mk_entry(date, first_line(btext), clean(btext), ["website"],
                            "implemented", "slack", SLACK_URL, date, True))
    return out


# ---- dedupe (union-find within the same effective date) ------------------
def merge_cluster(cl, date):
    # TITLE: prefer the descriptive PostHog summary, then Slack, then Notion.
    # DETAIL: the longest (most complete) text across the cluster.
    by_src = {}
    for e in cl:
        by_src.setdefault(e["_src"], e)
    title = (by_src.get("posthog") or by_src.get("slack") or by_src.get("notion") or cl[0])["title"]
    detail = max((e["detail"] for e in cl), key=len, default="")
    primary = cl[0]
    srcs, seen = [], set()
    for e in cl:
        for s in e["sources"]:
            k = (s["src"], s["ref"])
            if k not in seen:
                seen.add(k)
                srcs.append(s)
    srcs.sort(key=lambda s: SRC_PRIO.get(s["src"], 9))
    status = "implemented" if any(e["status"] == "implemented" for e in cl) else "implementing"
    if len(detail) > DETAIL_CAP:
        detail = detail[:DETAIL_CAP - 1] + "…"
    h = hashlib.md5((date + "|" + title).encode()).hexdigest()[:8]
    return {"id": f"{date}-{h}", "date": date, "title": title, "detail": detail,
            "channels": primary["channels"], "status": status, "sources": srcs}


def dedupe(entries):
    bydate = defaultdict(list)
    for e in entries:
        bydate[e["date"]].append(e)
    merged = []
    for date, grp in bydate.items():
        n = len(grp)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        tok = [tokens(g["title"] + " " + g["detail"]) for g in grp]
        for i in range(n):
            for j in range(i + 1, n):
                a, b = grp[i], grp[j]
                if set(a["channels"]) != set(b["channels"]):     # only merge same-scope
                    continue
                base = jaccard(tok[i], tok[j])
                if a["_publish"] and b["_publish"]:
                    base += 0.4
                thresh = 0.30 if a["_src"] != b["_src"] else 0.50   # cross-source: looser
                if base >= thresh:
                    parent[find(i)] = find(j)
        clusters = defaultdict(list)
        for i in range(n):
            clusters[find(i)].append(grp[i])
        for cl in clusters.values():
            merged.append(merge_cluster(cl, date))
    merged.sort(key=lambda e: e["title"])
    merged.sort(key=lambda e: e["date"], reverse=True)
    return merged


def main():
    end = sys.argv[1] if len(sys.argv) > 1 else yesterday_ny()
    win_start = (datetime.date.fromisoformat(end) - datetime.timedelta(days=WINDOW_DAYS)).isoformat()

    def load_json(name):
        try:
            return json.load(open(os.path.join(RAW, name)))
        except Exception as e:
            print(f"   ⚠ {name} missing/unreadable ({e}) — treating as empty"); return []

    def load_text(name):
        try:
            return open(os.path.join(RAW, name)).read()
        except Exception as e:
            print(f"   ⚠ {name} missing/unreadable ({e}) — treating as empty"); return ""

    ph, no, md = load_json("posthog.json"), load_json("notion.json"), load_text("slack_canvas.md")

    e_ph, e_no, e_sl = parse_posthog(ph, win_start), parse_notion(no, win_start), parse_slack(md, win_start)
    raw_entries = e_ph + e_no + e_sl
    merged = dedupe(raw_entries)

    store = {"updated": end, "window_start": win_start, "window_end": end, "entries": merged}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(store, f, separators=(",", ":"), ensure_ascii=False)

    multi = sum(1 for e in merged if len(e["sources"]) > 1)
    chan = defaultdict(int)
    for e in merged:
        for c in e["channels"]:
            chan[c] += 1
    dates = [e["date"] for e in merged]
    print(f"✅ wrote {OUT} ({os.path.getsize(OUT)//1024} KB)")
    print(f"   raw: posthog {len(e_ph)} + notion {len(e_no)} + slack {len(e_sl)} = {len(raw_entries)}")
    print(f"   merged entries: {len(merged)}  ({len(raw_entries)-len(merged)} dups collapsed, {multi} multi-source)")
    print(f"   channels: " + ", ".join(f"{k}={v}" for k, v in sorted(chan.items())))
    print(f"   window: {win_start} → {end}  (actual {min(dates) if dates else '—'} → {max(dates) if dates else '—'})")


if __name__ == "__main__":
    main()
