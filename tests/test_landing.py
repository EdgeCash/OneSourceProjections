from app import landing


def _slates():
    return {
        "MLB": {
            "games": [{
                "away_team": "New York Yankees", "home_team": "Boston Red Sox",
                "home_win_prob": 0.58, "away_win_prob": 0.42,
                "home_ml": -120, "home_ml_ev": 0.06,
                "away_ml": 110, "away_ml_ev": -0.05,
                "total_line": 8.5, "over_odds": -105,
                "model_over_prob": 0.55, "over_ev": 0.03,
            }],
            "props": [{
                "player": "Gerrit Cole", "team": "New York Yankees",
                "opponent": "Boston Red Sox", "market": "pitcher_strikeouts",
                "line": 6.5, "odds": -110, "model_over_prob": 0.61,
                "ev": 0.16, "kelly": 0.04,
            }],
        },
        "WNBA": {
            "games": [],
            "props": [{
                "player": "A'ja Wilson", "team": "LV", "opponent": "IND",
                "market": "Points", "line": 22.5,
                "over_odds": -115, "under_odds": -105,
                "model_over_prob": 0.60, "ev_over": 0.14, "ev_under": -0.20,
                "kelly": 0.04,
            }],
        },
    }


_PERF = {"overall": {
    "graded_games": 164, "model_brier": 0.2416, "bets": 127,
    "roi_pct": 5.06, "clv_beat_rate": 0.5229, "clv_bets": 109}}


def test_headline_stats_formats_perf():
    tiles = landing.headline_stats(_PERF)
    assert [t["label"] for t in tiles] == [
        "Games forward-tested", "Model Brier", "Beat the close",
        "Forward-test ROI"]
    vals = {t["label"]: t["value"] for t in tiles}
    assert vals["Games forward-tested"] == "164"
    assert vals["Model Brier"] == "0.242"
    assert vals["Beat the close"] == "52%"
    assert vals["Forward-test ROI"] == "+5.1%"


def test_headline_stats_handles_missing():
    tiles = landing.headline_stats(None)
    assert all(t["value"] == "—" for t in tiles)
    # empty overall shouldn't crash either
    assert landing.headline_stats({"overall": {}})[0]["value"] == "—"


def test_teaser_counts_summarizes_without_leaking():
    c = landing.teaser_counts(_slates(), min_edge=0.02)
    # Yankees ML, Over, Cole Ks, A'ja pts all clear 0.02
    assert c["total"] >= 3
    assert c["games"] >= 1 and c["props"] >= 2
    assert c["sports"] == ["MLB", "WNBA"]
    # best edge is Cole at 0.16 -> "10%+" band, never the raw number
    assert c["best_band"] == "10%+"


def test_teaser_counts_empty_slate():
    c = landing.teaser_counts({}, min_edge=0.02)
    assert c == {"total": 0, "games": 0, "props": 0, "sports": [],
                 "best_band": None}


def test_redacted_rows_strip_identifiers():
    rows = landing.redacted_rows(_slates(), min_edge=0.02, n=4)
    assert rows and len(rows) <= 4
    for r in rows:
        # only sport + market type survive; no team/player/line/ev fields
        assert set(r.keys()) == {"sport", "kind", "market"}
        blob = " ".join(str(v) for v in r.values())
        for leak in ("Yankees", "Cole", "Wilson", "Red Sox", "0.16", "6.5"):
            assert leak not in blob
