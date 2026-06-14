"""Parser checks for the sportsoddshistory NFL ingest (results + closing
lines), using real lines from the pasted dump including the tricky cases:
home vs away favorite, neutral site, OT, ties, PK, and seeded playoff rows."""

from onesource import nfl_history as nh

SAMPLE = "\n".join([
    "2016 Regular Season - Week 1",
    # away favorite (Carolina -3 @ Denver), favorite lost outright + OT-less
    "Thu\tSep 8, 2016\t8:40\t\tCarolina Panthers\tL 20-21\tL -3\t@\tDenver Broncos\tO 40.5",
    # home favorite (@ Baltimore -3 over Buffalo)
    "Sun\tSep 11, 2016\t1:04\t@\tBaltimore Ravens\tW 13-7\tW -3\t\tBuffalo Bills\tU 44.5",
    # OT game, away favorite
    "Sun\tSep 11, 2016\t1:05\t@\tKansas City Chiefs\tW 33-27 (OT)\tL -6.5\t\tSan Diego Chargers\tO 45.5",
    "2016 Regular Season - Week 4",
    # neutral site (London) with trailing note
    "Sun\tOct 2, 2016\t9:35\tN\tIndianapolis Colts\tL 27-30\tL -1\t\tJacksonville Jaguars\tO 48\tat London",
    "2016 Regular Season - Week 3",
    # pick'em
    "Thu\tSep 22, 2016\t8:26\t@\tNew England Patriots\tW 27-0\tW PK\t\tHouston Texans\tU 38.5",
    "2016 Playoffs",
    # seeded playoff row with round label
    "AFC Wild Card\tSat\tJan 7, 2017\t4:35\t@\tHouston Texans (4)\tW 27-14\tW -4\t\tOakland Raiders (5)\tO 38",
    # neutral-site Super Bowl, OT
    "Super Bowl LI\tSun\tFeb 5, 2017\t6:30\tN\tNew England Patriots (1)\tW 34-28 (OT)\tW -3\t\tAtlanta Falcons (2)\tO 57",
])


def _by_teams(games, home, away):
    return next(g for g in games if g["home_team"] == home and g["away_team"] == away)


def test_parses_all_games():
    games = nh.parse_games(SAMPLE)
    assert len(games) == 7


def test_away_favorite_home_away_and_spread():
    g = _by_teams(nh.parse_games(SAMPLE), "Denver Broncos", "Carolina Panthers")
    assert g["home_score"] == 21 and g["away_score"] == 20
    assert g["ml_winner"] == "Denver Broncos"
    assert g["spread_home"] == 3.0          # Carolina -3 away -> home +3
    assert g["over_under"] == 40.5
    assert g["season"] == 2016 and g["week"] == 1 and not g["neutral_site"]


def test_home_favorite_spread_sign():
    g = _by_teams(nh.parse_games(SAMPLE), "Baltimore Ravens", "Buffalo Bills")
    assert g["home_score"] == 13 and g["away_score"] == 7
    assert g["spread_home"] == -3.0          # Baltimore home favorite
    assert g["over_under"] == 44.5


def test_ot_flag_and_era_team_name():
    g = _by_teams(nh.parse_games(SAMPLE), "Kansas City Chiefs", "San Diego Chargers")
    assert g["is_ot"] and g["home_score"] == 33 and g["away_score"] == 27


def test_neutral_site():
    g = _by_teams(nh.parse_games(SAMPLE), "Jacksonville Jaguars", "Indianapolis Colts")
    assert g["neutral_site"] and g["week"] == 4
    assert g["home_score"] == 30 and g["away_score"] == 27


def test_pickem_spread_zero():
    g = _by_teams(nh.parse_games(SAMPLE), "New England Patriots", "Houston Texans")
    assert g["spread_home"] == 0.0


def test_playoff_round_and_seed_stripped():
    games = nh.parse_games(SAMPLE)
    wc = _by_teams(games, "Houston Texans", "Oakland Raiders")
    assert wc["playoff_round"] == "AFC Wild Card" and wc["season_type"] == 3
    sb = _by_teams(games, "Atlanta Falcons", "New England Patriots")
    assert sb["neutral_site"] and sb["is_ot"] and sb["home_score"] == 28


def test_closing_line_rows_schema():
    g = _by_teams(nh.parse_games(SAMPLE), "Baltimore Ravens", "Buffalo Bills")
    rows = nh.closing_line_rows(g)
    markets = {(r["market"], r["side"]): r for r in rows}
    assert set(markets) == {("spread", "home"), ("spread", "away"),
                            ("total", "over"), ("total", "under")}
    assert markets[("spread", "home")]["line"] == -3.0
    assert markets[("spread", "away")]["line"] == 3.0
    assert markets[("total", "over")]["line"] == 44.5
    assert all(r["american_odds"] == -110 and r["sport"] == "NFL" for r in rows)
    assert all(r["event_id"] == g["game_id"] for r in rows)


NFLVERSE_ROWS = [
    # away favorite: CAR -3 @ DEN  -> home DEN underdog, home spread +3
    {"game_id": "2016_01_CAR_DEN", "season": 2016, "week": 1, "game_type": "REG",
     "gameday": "2016-09-08", "away_team": "CAR", "home_team": "DEN",
     "away_score": 20.0, "home_score": 21.0, "spread_line": -3.0, "total_line": 40.5,
     "away_moneyline": -150.0, "home_moneyline": 136.0, "home_spread_odds": -111.0,
     "away_spread_odds": 101.0, "over_odds": -100.0, "under_odds": -110.0,
     "location": "Home", "overtime": 0.0},
    # home favorite: ATL -2.5 vs TB
    {"game_id": "2016_01_TB_ATL", "season": 2016, "week": 1, "game_type": "REG",
     "gameday": "2016-09-11", "away_team": "TB", "home_team": "ATL",
     "away_score": 31.0, "home_score": 24.0, "spread_line": 2.5, "total_line": 46.5,
     "away_moneyline": 120.0, "home_moneyline": -133.0, "home_spread_odds": -108.0,
     "away_spread_odds": -102.0, "over_odds": -105.0, "under_odds": -105.0,
     "location": "Home", "overtime": 0.0},
    # relocated franchise (OAK -> current name) + neutral-site playoff
    {"game_id": "2016_22_NE_ATL", "season": 2016, "week": 22, "game_type": "SB",
     "gameday": "2017-02-05", "away_team": "NE", "home_team": "ATL",
     "away_score": 34.0, "home_score": 28.0, "spread_line": -3.0, "total_line": 57.0,
     "away_moneyline": -155.0, "home_moneyline": 135.0, "home_spread_odds": -110.0,
     "away_spread_odds": -110.0, "over_odds": -110.0, "under_odds": -110.0,
     "location": "Neutral", "overtime": 1.0},
    {"game_id": "2016_05_X_OAK", "season": 2016, "week": 5, "game_type": "REG",
     "gameday": "2016-10-09", "away_team": "SD", "home_team": "OAK",
     "away_score": 31.0, "home_score": 34.0, "spread_line": 3.0, "total_line": 50.5,
     "away_moneyline": 130.0, "home_moneyline": -150.0, "home_spread_odds": -110.0,
     "away_spread_odds": -110.0, "over_odds": -110.0, "under_odds": -110.0,
     "location": "Home", "overtime": 0.0},
]


def test_nflverse_spread_sign_and_names():
    games = {g["game_id"]: g for g in nh.games_from_nflverse(NFLVERSE_ROWS)}
    den = games["2016_01_CAR_DEN"]
    assert den["home_team"] == "Denver Broncos" and den["away_team"] == "Carolina Panthers"
    assert den["spread_home"] == 3.0          # away CAR favored -3 -> home +3
    assert den["home_ml"] == 136.0 and den["away_ml"] == -150.0
    atl = games["2016_01_TB_ATL"]
    assert atl["spread_home"] == -2.5         # home ATL favored
    # relocated franchises map to current names
    assert games["2016_05_X_OAK"]["home_team"] == "Las Vegas Raiders"
    assert games["2016_05_X_OAK"]["away_team"] == "Los Angeles Chargers"


def test_nflverse_playoff_and_ot_and_neutral():
    sb = next(g for g in nh.games_from_nflverse(NFLVERSE_ROWS)
              if g["game_id"] == "2016_22_NE_ATL")
    assert sb["season_type"] == 3 and sb["playoff_round"] == "SB"
    assert sb["is_ot"] and sb["neutral_site"]
    assert sb["ml_winner"] == "New England Patriots"


def test_nflverse_closing_rows_include_moneyline_and_real_odds():
    den = next(g for g in nh.games_from_nflverse(NFLVERSE_ROWS)
               if g["game_id"] == "2016_01_CAR_DEN")
    rows = {(r["market"], r["side"]): r for r in nh.closing_line_rows(den)}
    assert set(rows) == {("spread", "home"), ("spread", "away"),
                         ("total", "over"), ("total", "under"),
                         ("moneyline", "home"), ("moneyline", "away")}
    assert rows[("spread", "home")]["line"] == 3.0
    assert rows[("spread", "home")]["american_odds"] == -111      # real odds, not -110
    assert rows[("moneyline", "home")]["american_odds"] == 136
    assert rows[("moneyline", "home")]["line"] is None


def test_write_history_roundtrip(tmp_path):
    games = nh.parse_games(SAMPLE)
    counts = nh.write_history(games, tmp_path)
    assert counts[2016]["games"] >= 5 and counts[2016]["lines"] >= 20
    # games store is a gz JSON array readable by history.backfill_games
    import gzip, json
    with gzip.open(tmp_path / "backfill" / "nfl" / "2016" / "games.json.gz", "rt") as f:
        arr = json.load(f)
    assert isinstance(arr, list) and arr and "home_team" in arr[0]
    # closing lines is gz JSONL
    with gzip.open(tmp_path / "closing_lines" / "nfl" / "2016.jsonl.gz", "rt") as f:
        first = json.loads(f.readline())
    assert first["market"] in ("spread", "total") and first["sport_key"] == "NFL"
