from app import assets, ui


def test_mlb_logo_resolves_full_and_abbrev():
    yanks = assets.team_logo_url("MLB", "New York Yankees")
    assert yanks == "https://www.mlbstatic.com/team-logos/147.svg"
    assert assets.team_logo_url("MLB", "NYY") == yanks
    # Athletics / Oakland variants share an id
    assert assets.team_logo_url("MLB", "OAK") == assets.team_logo_url("MLB", "Athletics")


def test_wnba_logo_resolves():
    url = assets.team_logo_url("WNBA", "Las Vegas Aces")
    assert url == "https://a.espncdn.com/i/teamlogos/wnba/500/lv.png"
    assert assets.team_logo_url("WNBA", "LV") == url


def test_unknown_team_logo_is_none():
    assert assets.team_logo_url("MLB", "Nope City") is None
    assert assets.team_logo_url("NHL", "Anything") is None  # no NHL map yet
    assert assets.team_logo_url("MLB", "") is None


def test_headshot_url():
    assert assets.mlb_headshot_url(592450).endswith("/people/592450/spots/120")
    assert assets.mlb_headshot_url(None) is None


def test_monogram_deterministic():
    i1, c1 = assets.monogram("Boston Red Sox")
    i2, c2 = assets.monogram("Boston Red Sox")
    assert (i1, c1) == (i2, c2)
    assert i1 == "BS"  # first + last word initial
    assert c1.startswith("#")
    assert assets.monogram("Aces")[0] == "AC"
    assert assets.monogram("")[0] == "?"


def test_team_badge_html_has_fallback():
    # known team -> img with onerror fallback embedding the monogram
    html = assets.team_badge_html("MLB", "New York Yankees", 40)
    assert "<img" in html and "mlbstatic" in html and "onerror" in html
    # unknown team -> pure monogram div, no img
    fb = assets.team_badge_html("NHL", "Mystery Team", 40)
    assert "<img" not in fb and "border-radius:50%" in fb


def test_game_card_html_renders_key_facts():
    g = {
        "away_team": "New York Yankees", "home_team": "Boston Red Sox",
        "game_time": "2026-06-13T23:10:00Z", "away_exp_runs": 4.4,
        "home_exp_runs": 4.9, "away_win_prob": 0.45, "home_win_prob": 0.55,
        "total_line": 8.5, "proj_total": 9.3,
        "home_ml": -120, "away_ml": 110,
        "home_ml_ev": 0.06, "away_ml_ev": -0.03, "over_ev": 0.02, "over_odds": -110,
    }
    html = ui.game_card_html("MLB", g)
    assert "New York Yankees" in html and "Boston Red Sox" in html
    assert "7:10 PM" in html        # ET time
    assert "55%" in html and "45%" in html   # win probs
    assert "Red Sox ML" in html and "EV" in html  # best edge surfaced
