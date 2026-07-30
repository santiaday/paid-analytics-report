#!/usr/bin/env python3
"""
Google Ads MONTHLY report builder.

Same tables + charts as the weekly report (report_common.render), but the time
windows are whole calendar months and the chart series spans the last 12 months,
bucketed by calendar month. All metric logic / locked rules live in build.py /
report_common.py.

Usage:
    python3 build_monthly.py [YYYY-MM]
    YYYY-MM = the reported month. Default = the last completed calendar month
    (America/New_York).

Reads data/monthly-<YYYY-MM>/*.json (fetched by the monthly routine — see
monthly-routine-prompt.md), writes outputs/monthly-<YYYY-MM>.html. No network.

Note: the series_* queries are fetched with segments.month (12 rows per
campaign), so report_common buckets on 'segments.month' for the monthly report.
"""
import sys
from datetime import date, datetime, timedelta
import report_common

N_BUCKETS = 12


def add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def last_completed_month(today):
    return add_months(today.replace(day=1), -1)   # first day of previous month


def build_cfg(month):                              # month = "YYYY-MM"
    ms = datetime.strptime(month + "-01", "%Y-%m-%d").date()   # month start
    me = add_months(ms, 1) - timedelta(days=1)                 # month end
    series_start = add_months(ms, -(N_BUCKETS - 1))
    buckets = [add_months(series_start, i) for i in range(N_BUCKETS)]
    ss_key = series_start.year * 12 + series_start.month

    def idx_of(ds):
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
        i = (dt.year * 12 + dt.month) - ss_key
        return i if 0 <= i < N_BUCKETS else None

    labels = []
    for i, b in enumerate(buckets):
        lbl = b.strftime("%b")
        if b.month == 1 or i == 0:                  # disambiguate year boundaries
            lbl += " ’" + b.strftime("%y")
        labels.append(lbl)

    pm = add_months(ms, -1)                         # prior month start
    return {
        "period": "Monthly", "unit": "Month",
        "data_dir": f"monthly-{month}",
        "out_name": f"monthly-{month}.html",
        "headline": f"Microsoft Ads Monthly Report for Month of {ms.strftime('%B %Y')}",
        "compare_str": f"Compared to prior month: {pm.strftime('%B %Y')}",
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "this_label": "This Month", "prior_label": "Prior Month",
        "bucket_labels": labels,
        "bucket_index": idx_of,
        # series rows are stored under segments.date as YYYY-MM-01 (Monthly aggregation
        # normalized in fetch_data.py), so the default series_date_field ("segments.date")
        # is correct — idx_of buckets them by calendar month.
    }


if __name__ == "__main__":
    month = sys.argv[1] if len(sys.argv) > 1 else \
        last_completed_month(date.today()).strftime("%Y-%m")
    report_common.render(build_cfg(month))
