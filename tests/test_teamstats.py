from onesource import teams, teamstats as ts


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
    assert set(sp["pts"]) == {"season", "home", "away", "l10", "l5"}
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

    from onesource.sports import default_slate_date

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
