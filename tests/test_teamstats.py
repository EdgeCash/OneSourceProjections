from project547 import teams, teamstats as ts


def test_canon_resolves_variants():
    assert teams.canon("MLB", "Boston Red Sox") == "BOS"
    assert teams.canon("MLB", "BOS") == "BOS"
    assert teams.canon("MLB", "Red Sox") == "BOS"
    assert teams.canon("MLB", "OAK") == teams.canon("MLB", "Athletics")
    assert teams.canon("WNBA", "Las Vegas Aces") == "LV"
    assert teams.canon("WNBA", "LV") == "LV"
    assert teams.canon("WNBA", "Aces") == "LV"
    # unknown -> normalized fallback, never crashes
    assert teams.canon("MLB", "Mystery") == "mystery"


def test_team_games_build_wnba():
    df = ts.team_games("WNBA", (2025, 2026))
    assert len(df) > 200
    for col in ("team", "date", "pts", "fg2_pct", "opp_pts", "reb"):
        assert col in df.columns
    # team column is canonicalized (abbrevs)
    assert "LV" in set(df["team"])
    # shooting percentages are fractions
    valid = df["fg2_pct"].dropna()
    assert (valid.between(0, 1)).mean() > 0.95


def test_team_games_build_mlb():
    df = ts.team_games("MLB", (2025, 2026))
    assert len(df) > 1000
    for col in ("team", "runs", "opp_runs", "hits", "pk", "nrfi"):
        assert col in df.columns
    assert "BOS" in set(df["team"])


def test_splits_and_ranks():
    df = ts.team_games("WNBA", (2025, 2026))
    sp = ts.splits("WNBA", df, "LV", "2026-06-04")
    assert "pts" in sp
    assert set(sp["pts"]) == {"season", "home", "away", "l5", "l10", "l15",
                              "l20", "l30"}
    assert sp["pts"]["l5"] and sp["pts"]["l5"] > 50  # a real PPG
    ranks = ts.league_ranks("WNBA", df, "2026-06-04", "l5")
    assert "pts" in ranks
    assert min(ranks["pts"].values()) == 1
    assert max(ranks["pts"].values()) <= 14


def test_matchup_structure_wnba():
    m = ts.matchup("WNBA", "Las Vegas Aces", "Indiana Fever", "2026-06-04")
    assert m["n_teams"] >= 12
    rows = m["away_off_vs_home_def"]
    assert any(r["stat"] == "PPG" for r in rows)
    ppg = next(r for r in rows if r["stat"] == "PPG")
    # offense vs defense paired correctly: both PPG-scale numbers
    assert ppg["off_l5"] > 50 and ppg["def_l5"] > 50
    assert ppg["off_rank"] is not None and 0 <= ppg["adv"] <= 3


def test_matchup_structure_mlb():
    m = ts.matchup("MLB", "Boston Red Sox", "New York Yankees", "2026-06-02")
    assert m["n_teams"] >= 25
    rows = m["home_off_vs_away_def"]
    runs = next(r for r in rows if r["stat"] == "Runs/G")
    assert runs["off_l5"] is not None and runs["def_l5"] is not None
    assert m["trends"]  # NRFI%/F5/etc present for MLB


def test_advantage_scaling():
    assert ts._advantage(2, 28) == 3    # huge edge
    assert ts._advantage(10, 12) == 0   # negligible
    assert ts._advantage(None, 5) == 0  # missing -> no advantage


def test_default_slate_date_stays_on_today_during_evening():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from project547.sports import default_slate_date

    et = ZoneInfo("America/New_York")
    dates = ["2026-06-12", "2026-06-13"]
    slates = {
        "2026-06-12": {"MLB": {"games": [
            {"game_time": "2026-06-12T23:05:00Z"},  # 7:05 PM ET
            {"game_time": "2026-06-12T23:10:00Z"}]}},
        "2026-06-13": {"MLB": {"games": [
            {"game_time": "2026-06-13T23:05:00Z"}]}},
    }
    # evening of game day, games underway -> still today's slate
    assert default_slate_date(dates, slates,
                              datetime(2026, 6, 12, 19, 22, tzinfo=et)) == "2026-06-12"
    # morning of game day -> today, not tomorrow
    assert default_slate_date(dates, slates,
                              datetime(2026, 6, 12, 9, 0, tzinfo=et)) == "2026-06-12"
    # late night after today's games finished -> roll to tomorrow
    assert default_slate_date(dates, slates,
                              datetime(2026, 6, 12, 23, 30, tzinfo=et)) == "2026-06-13"
    # afternoon-only slate, evening -> games done, roll forward
    aft = {"2026-06-12": {"MLB": {"games": [{"game_time": "2026-06-12T17:05:00Z"}]}},
           "2026-06-13": {}}
    assert default_slate_date(dates, aft,
                              datetime(2026, 6, 12, 19, 22, tzinfo=et)) == "2026-06-13"
    assert default_slate_date([], {}) is None


def test_matchup_window_extras_and_toggle():
    m5 = ts.matchup("WNBA", "Las Vegas Aces", "Indiana Fever", "2026-06-02", window="l5")
    m30 = ts.matchup("WNBA", "Las Vegas Aces", "Indiana Fever", "2026-06-02", window="l30")
    assert m5 and m30
    # header extras present
    for m, lbl in ((m5, "L5"), (m30, "L30")):
        assert m["window_label"] == lbl
        assert isinstance(m["home_rest"], (int, type(None)))
        assert m["home_power_rank"] is None or m["home_power_rank"] >= 1
        assert m["home_sos_rank"] is None or m["home_sos_rank"] >= 1
    # rows carry every recency window
    r = m5["away_off_vs_home_def"][0]
    for w in ("off_season", "off_l30", "off_l20", "off_l15", "off_l10", "off_l5"):
        assert w in r
    # the window genuinely changes the rank basis for at least one stat
    ranks5 = {x["stat"]: x["off_rank"] for x in m5["away_off_vs_home_def"]}
    ranks30 = {x["stat"]: x["off_rank"] for x in m30["away_off_vs_home_def"]}
    assert ranks5 != ranks30


def test_power_sos_rest_helpers():
    df = ts.team_games("WNBA", (2025, 2026))
    pr = ts.power_ranks("WNBA", df, "2026-06-02", "l10")
    sos = ts.sos_ranks("WNBA", df, "2026-06-02", "l10")
    assert pr and min(pr.values()) == 1
    assert sos and min(sos.values()) == 1
    rest = ts.days_rest(df, "LV", "2026-06-02")
    assert rest is None or rest >= 0


def test_elo_ratings_and_power_sos_source():
    df = ts.team_games("WNBA", (2025, 2026))
    elo = ts.elo_ratings("WNBA", df, "2026-06-02")
    assert elo and len(elo) >= 10
    assert all(isinstance(v, float) for v in elo.values())
    # power uses Elo (current strength) -> window-independent
    p5 = ts.power_ranks("WNBA", df, "2026-06-02", "l5", elo)
    p30 = ts.power_ranks("WNBA", df, "2026-06-02", "l30", elo)
    assert p5 == p30 and min(p5.values()) == 1
    # sos uses opponent Elo over the window -> window-dependent
    s5 = ts.sos_ranks("WNBA", df, "2026-06-02", "l5", elo)
    s30 = ts.sos_ranks("WNBA", df, "2026-06-02", "l30", elo)
    assert s5 and s30 and s5 != s30
    # no-ratings path still works (proxy fallback)
    assert ts.power_ranks("WNBA", df, "2026-06-02", "l10")  # margin proxy
    assert ts.sos_ranks("WNBA", df, "2026-06-02", "l10")    # opp win% proxy


def test_football_matchup_ready():
    df = ts.team_games("NFL", (2024, 2025))
    assert len(df) > 500
    last = str(df["date"].max())
    m = ts.matchup("NFL", "Kansas City Chiefs", "Buffalo Bills", last, window="l5")
    assert m and {r["stat"] for r in m["away_off_vs_home_def"]} >= {
        "Points/G", "Total Yds/G", "Pass Yds/G", "Rush Yds/G"}
    assert m["home_power_rank"] and m["home_sos_rank"]
