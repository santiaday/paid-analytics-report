#!/usr/bin/env python3
"""
Meta Ads WEEKLY report builder.

Computes the Monday–Sunday week windows + the 12-week chart buckets, then hands
a `cfg` to report_common.render(). All metric logic and the locked rules live in
build.py / report_common.py — this file is just the weekly time math + config.

Usage:
    python3 build_weekly.py [WEEK_END]
    WEEK_END = the Sunday that ends the reported week (YYYY-MM-DD).
    Default = the most recent Sunday strictly before today (America/New_York).

Weeks are Monday–Sunday to match DoorLoop's Brain dashboard (NOT Sunday–Saturday),
so the Meta cohort demos line up with the same Mon–Sun weeks the Brain UI shows.

Reads data/weekly-<WEEK_END>/*.json (fetched by the weekly routine — see
weekly-routine-prompt.md), writes outputs/weekly-<WEEK_END>.html. No network.
"""
import sys
from datetime import date, datetime, timedelta
import report_common

N_BUCKETS = 12


def week_start(d):
    """Monday that starts d's week (Brain weeks are Mon–Sun). weekday(): Mon=0..Sun=6."""
    return d - timedelta(days=d.weekday())


def last_completed_sunday(today):
    """Most recent Sunday strictly before today (end of the last complete Mon–Sun week)."""
    off = (today.weekday() - 6) % 7   # Sun=6
    off = off if off > 0 else 7       # strictly before today
    return today - timedelta(days=off)


def build_cfg(week_end):
    we = datetime.strptime(week_end, "%Y-%m-%d").date()
    ws = we - timedelta(days=6)
    series_start = ws - timedelta(days=7 * (N_BUCKETS - 1))
    buckets = [series_start + timedelta(days=7 * i) for i in range(N_BUCKETS)]

    def idx_of(ds):
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
        i = (week_start(dt) - series_start).days // 7
        return i if 0 <= i < N_BUCKETS else None

    pe = we - timedelta(days=7)         # prior week end
    ps = ws - timedelta(days=7)         # prior week start
    same_year = ws.year == we.year
    period_phrase = (f"Week of {ws.strftime('%b %-d')}–"
                     f"{we.strftime('%-d, %Y') if same_year else we.strftime('%b %-d, %Y')}")
    compare_str = f"Compared to prior week: {ps.strftime('%b %-d')}–{pe.strftime('%-d, %Y')}"
    return {
        "period": "Weekly", "unit": "Week",
        "data_dir": f"weekly-{week_end}",
        "out_name": f"weekly-{week_end}.html",
        "headline": f"Meta Ads Weekly Report for {period_phrase}",
        "compare_str": compare_str,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "this_label": "This Week", "prior_label": "Prior Week",
        "bucket_labels": [f"{b.month}/{b.day}" for b in buckets],
        "bucket_index": idx_of,
        "clicks_anchor": week_end,   # "Clicks per Day" trend ends on WEEK_END (last 30 days)
    }


if __name__ == "__main__":
    week_end = sys.argv[1] if len(sys.argv) > 1 else \
        last_completed_sunday(date.today()).strftime("%Y-%m-%d")
    report_common.render(build_cfg(week_end))
