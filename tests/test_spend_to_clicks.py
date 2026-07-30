from spend_to_clicks import keyword_rows_to_clicks_30d, SOURCE_TO_PLATFORM


def test_aggregates_keyword_rows_to_ad_group_grain_per_platform():
    rows = [
        {"DT": "2026-07-12", "CAMPAIGN": "C1", "AD_GROUP": "AG1", "SOURCE": "google-ads", "SPEND": "10.5", "CLICKS": "3", "IMPRESSIONS": "100"},
        {"DT": "2026-07-12", "CAMPAIGN": "C1", "AD_GROUP": "AG1", "SOURCE": "google-ads", "SPEND": 4.5, "CLICKS": 2, "IMPRESSIONS": 50},
        {"DT": "2026-07-12", "CAMPAIGN": "C2", "AD_GROUP": "AG2", "SOURCE": "bing", "SPEND": 7, "CLICKS": 1, "IMPRESSIONS": 20},
        {"DT": "2026-07-12", "CAMPAIGN": "C3", "AD_GROUP": "AG3", "SOURCE": "facebook", "SPEND": 99, "CLICKS": 9, "IMPRESSIONS": 9},
    ]
    out = keyword_rows_to_clicks_30d(rows)

    # google: two keyword rows in the same ad-group collapse to one, spend summed → cost_micros.
    assert len(out["google"]) == 1
    g = out["google"][0]
    assert g["segments.date"] == "2026-07-12"
    assert g["campaign.name"] == "C1"
    assert g["ad_group.name"] == "AG1"
    assert g["metrics.cost_micros"] == round(15.0 * 1e6)   # 10.5 + 4.5 dollars → micros
    assert g["metrics.clicks"] == 5
    assert g["metrics.impressions"] == 150

    # bing maps to the microsoft platform bucket.
    assert len(out["microsoft"]) == 1
    assert out["microsoft"][0]["metrics.cost_micros"] == 7_000_000

    # non-google/bing sources (facebook) are ignored — Meta comes from the Graph pull.
    assert SOURCE_TO_PLATFORM.get("facebook") is None


def test_bad_numeric_values_coerce_to_zero_not_crash():
    out = keyword_rows_to_clicks_30d([
        {"DT": "d", "CAMPAIGN": "c", "AD_GROUP": "a", "SOURCE": "google-ads", "SPEND": None, "CLICKS": "x", "IMPRESSIONS": ""},
    ])
    row = out["google"][0]
    assert row["metrics.cost_micros"] == 0
    assert row["metrics.clicks"] == 0
    assert row["metrics.impressions"] == 0


def test_empty_input_yields_empty_platform_lists():
    out = keyword_rows_to_clicks_30d([])
    assert out == {"google": [], "microsoft": []}
