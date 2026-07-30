"""Transform the Brain's paid-media-daily-keyword rows into the sibling clicks_30d.json shape.

Port of src/spend-to-clicks.ts. Google + Bing are both covered at ad-group grain:
  Brain keyword row: {DT, CAMPAIGN, AD_GROUP, SOURCE:'google-ads'|'bing', SPEND($), CLICKS, IMPRESSIONS}
  clicks_30d row:    {"segments.date","campaign.name","ad_group.name",
                      "metrics.cost_micros","metrics.clicks","metrics.impressions"}
update_history does cost = round(cost_micros/1e4) cents; SPEND is dollars → cost_micros = SPEND*1e6.
Meta is NOT in the Brain spend metrics — its clicks_30d comes from the Meta Graph pull separately.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Brain SOURCE → the platform key update_history uses (its SIB dir).
SOURCE_TO_PLATFORM: Dict[str, str] = {"google-ads": "google", "bing": "microsoft"}

# Sibling clicks_30d.json parent path relative to the pipeline's parent (SK), matching update_history's SIB.
SIB_SUBDIR: Dict[str, str] = {
    "google": "Google Ads/google-ads-daily-artifact",
    "microsoft": "Microsoft Ads/microsoft-ads-daily-artifact",
}


def _num(v: Any) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    return n if n == n and n not in (float("inf"), float("-inf")) else 0.0


def keyword_rows_to_clicks_30d(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Aggregate keyword-grain rows to ad-group grain, split by platform, in clicks_30d shape."""
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        plat = SOURCE_TO_PLATFORM.get(str(r.get("SOURCE")))
        if not plat:
            continue  # ignore any non-google/bing source
        key = f"{plat} {r.get('DT')} {r.get('CAMPAIGN')} {r.get('AD_GROUP')}"
        t = agg.get(key)
        if t is None:
            t = {"dt": r.get("DT"), "camp": r.get("CAMPAIGN"), "ag": r.get("AD_GROUP"),
                 "plat": plat, "spend": 0.0, "clicks": 0.0, "imps": 0.0}
            agg[key] = t
        t["spend"] += _num(r.get("SPEND"))
        t["clicks"] += _num(r.get("CLICKS"))
        t["imps"] += _num(r.get("IMPRESSIONS"))

    out: Dict[str, List[Dict[str, Any]]] = {"google": [], "microsoft": []}
    for t in agg.values():
        out[t["plat"]].append({
            "segments.date": t["dt"],
            "campaign.name": t["camp"],
            "ad_group.name": t["ag"],
            "metrics.cost_micros": round(t["spend"] * 1e6),
            "metrics.clicks": round(t["clicks"]),
            "metrics.impressions": round(t["imps"]),
        })
    return out
