"""MLB StatsAPI (statsapi.mlb.com) — free, no key required. Source of truth
for the daily slate: games, teams, probable pitchers, and lineups."""

from __future__ import annotations

import requests

from ..cache import cached_json

BASE = "https://statsapi.mlb.com/api/v1"
_TTL_SCHEDULE = 15 * 60
_TTL_STATIC = 24 * 60 * 60


def _get(path: str, params: dict | None = None) -> dict:
    resp = requests.get(f"{BASE}/{path}", params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def schedule(date: str) -> list[dict]:
    """Games for a date (YYYY-MM-DD) with probable pitchers attached."""
    data = cached_json(
        f"statsapi:schedule:{date}",
        _TTL_SCHEDULE,
        lambda: _get(
            "schedule",
            {"sportId": 1, "date": date, "hydrate": "probablePitcher,team,linescore"},
        ),
    )
    games = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            games.append(
                {
                    "game_pk": g["gamePk"],
                    "game_time": g.get("gameDate"),
                    "status": g.get("status", {}).get("detailedState"),
                    "home_team": home["team"]["name"],
                    "home_team_id": home["team"]["id"],
                    "away_team": away["team"]["name"],
                    "away_team_id": away["team"]["id"],
                    "home_pitcher": home.get("probablePitcher", {}).get("fullName"),
                    "home_pitcher_id": home.get("probablePitcher", {}).get("id"),
                    "away_pitcher": away.get("probablePitcher", {}).get("fullName"),
                    "away_pitcher_id": away.get("probablePitcher", {}).get("id"),
                }
            )
    return games


def final_scores(date: str) -> list[dict]:
    """Completed games with final scores for a date (for grading)."""
    data = cached_json(
        f"statsapi:finals:{date}",
        _TTL_SCHEDULE,
        lambda: _get("schedule", {"sportId": 1, "date": date, "hydrate": "linescore"}),
    )
    out = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            status = g.get("status", {})
            if status.get("codedGameState") != "F":
                continue
            home, away = g["teams"]["home"], g["teams"]["away"]
            out.append({
                "game_pk": g["gamePk"],
                "home_team": home["team"]["name"],
                "away_team": away["team"]["name"],
                "home_score": home.get("score"),
                "away_score": away.get("score"),
                "status": "final",
            })
    return out


_TTL_LIVE = 45  # seconds


def scoreboard(date: str) -> list[dict]:
    """All games on a date with live status, scores, and inning (for the
    scoreboard). Mirrors the ESPN scoreboard shape so the UI is uniform."""
    data = cached_json(
        f"statsapi:scoreboard:{date}", _TTL_LIVE,
        lambda: _get("schedule", {"sportId": 1, "date": date,
                                  "hydrate": "linescore,team"}))
    out = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            status = g.get("status", {})
            state = {"P": "pre", "I": "in", "F": "post"}.get(
                status.get("abstractGameCode") or "", "pre")
            ls = g.get("linescore", {})
            if state == "in":
                half = ls.get("inningHalf", "")
                detail = f"{half[:3]} {ls.get('currentInningOrdinal', '')}".strip()
            elif state == "post":
                detail = "Final"
            else:
                detail = status.get("detailedState", "Scheduled")
            home, away = g["teams"]["home"], g["teams"]["away"]

            def side(t):
                tm = t.get("team", {})
                return {"team": tm.get("name"), "abbrev": tm.get("abbreviation"),
                        "logo": f"https://www.mlbstatic.com/team-logos/{tm.get('id')}.svg",
                        "score": t.get("score"), "record":
                        f"{(t.get('leagueRecord') or {}).get('wins', '')}-"
                        f"{(t.get('leagueRecord') or {}).get('losses', '')}".strip("-")}

            out.append({"sport": "MLB", "game_id": g["gamePk"],
                        "game_time": g.get("gameDate"), "state": state,
                        "detail": detail, "home": side(home), "away": side(away)})
    return out


def box_score(game_pk: int) -> dict:
    """Batting + pitching stat tables for a game, as {teams: [{team, columns,
    rows}]} matching the ESPN box_score shape."""
    try:
        data = cached_json(f"statsapi:box:{game_pk}", _TTL_LIVE,
                           lambda: _get(f"game/{game_pk}/boxscore"))
    except Exception:
        return {}
    bat_cols = ["Player", "AB", "R", "H", "RBI", "BB", "K", "AVG"]
    pit_cols = ["Pitcher", "IP", "H", "R", "ER", "BB", "K", "ERA"]
    teams = []
    for side in ("away", "home"):
        t = data.get("teams", {}).get(side, {})
        label = t.get("team", {}).get("abbreviation") or t.get("team", {}).get("name")
        bat, pit = [], []
        for p in t.get("players", {}).values():
            nm = p.get("person", {}).get("fullName", "")
            s = p.get("stats", {})
            b, pi = s.get("batting", {}), s.get("pitching", {})
            if b.get("atBats") is not None and (b.get("atBats") or b.get("plateAppearances")):
                bat.append([nm, b.get("atBats"), b.get("runs"), b.get("hits"),
                            b.get("rbi"), b.get("baseOnBalls"), b.get("strikeOuts"),
                            (p.get("seasonStats", {}).get("batting", {}) or {}).get("avg")])
            if pi.get("inningsPitched") is not None:
                pit.append([nm, pi.get("inningsPitched"), pi.get("hits"), pi.get("runs"),
                            pi.get("earnedRuns"), pi.get("baseOnBalls"), pi.get("strikeOuts"),
                            (p.get("seasonStats", {}).get("pitching", {}) or {}).get("era")])
        teams.append({"team": f"{label} — Batting", "columns": bat_cols, "rows": bat})
        teams.append({"team": f"{label} — Pitching", "columns": pit_cols, "rows": pit})
    return {"teams": teams}


def box_player_logs(game_pk: int) -> list[dict]:
    """Per-player batting + pitching lines from a finished game's boxscore,
    shaped for the player-log store (name, date, opponent, stat fields)."""
    data = cached_json(
        f"statsapi:box:{game_pk}",
        _TTL_STATIC,
        lambda: _get(f"game/{game_pk}/boxscore"),
    )
    teams = data.get("teams", {})
    names = {side: teams.get(side, {}).get("team", {}).get("abbreviation")
             or teams.get(side, {}).get("team", {}).get("name")
             for side in ("home", "away")}
    rows = []
    for side, opp in (("home", "away"), ("away", "home")):
        for p in teams.get(side, {}).get("players", {}).values():
            stats = p.get("stats", {})
            bat, pit = stats.get("batting", {}), stats.get("pitching", {})
            if not bat and not pit:
                continue
            is_pitcher = bool(pit)
            row = {
                "game_pk": game_pk,
                "name": p.get("person", {}).get("fullName"),
                "player_id": p.get("person", {}).get("id"),
                "opponent": names.get(opp),
                "team": names.get(side),
                "position": "P" if is_pitcher
                else p.get("position", {}).get("abbreviation"),
                "started": bool(pit.get("gamesStarted")) if is_pitcher else None,
            }
            src = pit if is_pitcher else bat
            for k in ("hits", "totalBases", "homeRuns", "strikeOuts",
                      "battersFaced", "baseOnBalls", "hitByPitch",
                      "atBats", "plateAppearances"):
                if k in src:
                    row[k] = src[k]
            ip = pit.get("inningsPitched")
            if ip is not None:
                # statsapi reports "5.2" meaning 5 and 2/3 innings
                try:
                    whole, frac = str(ip).split(".") if "." in str(ip) else (str(ip), "0")
                    row["inningsPitched"] = int(whole) + int(frac) / 3.0
                except ValueError:
                    pass
            rows.append(row)
    return rows


def team_recent_results(team_id: int, end_date: str, n_games: int = 30) -> list[dict]:
    """Final scores of the team's last n games on or before end_date."""
    data = cached_json(
        f"statsapi:results:{team_id}:{end_date}:{n_games}",
        _TTL_SCHEDULE,
        lambda: _get(
            "schedule",
            {
                "sportId": 1,
                "teamId": team_id,
                "endDate": end_date,
                "startDate": _shift_date(end_date, -75),
                "gameType": "R",
            },
        ),
    )
    results = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            if g.get("status", {}).get("codedGameState") != "F":
                continue
            for side in ("home", "away"):
                t = g["teams"][side]
                if t["team"]["id"] == team_id:
                    opp = g["teams"]["away" if side == "home" else "home"]
                    results.append(
                        {
                            "date": day["date"],
                            "runs_scored": t.get("score"),
                            "runs_allowed": opp.get("score"),
                        }
                    )
    return results[-n_games:]


def batting_order(game_pk: int) -> dict[str, list[dict]]:
    """Confirmed lineups from the live feed; empty lists if not yet posted."""
    data = cached_json(
        f"statsapi:boxscore:{game_pk}",
        _TTL_SCHEDULE,
        lambda: _get(f"game/{game_pk}/boxscore"),
    )
    out: dict[str, list[dict]] = {"home": [], "away": []}
    for side in ("home", "away"):
        team = data.get("teams", {}).get(side, {})
        players = team.get("players", {})
        order = []
        for p in players.values():
            slot = p.get("battingOrder")
            if slot and int(slot) % 100 == 0:  # starters have 100, 200, ... 900
                order.append(
                    {
                        "player_id": p["person"]["id"],
                        "name": p["person"]["fullName"],
                        "slot": int(slot) // 100,
                    }
                )
        out[side] = sorted(order, key=lambda r: r["slot"])
    return out


def player_season_stats(player_id: int, season: int, group: str) -> dict:
    """Season stat line for a player. group is 'hitting' or 'pitching'."""
    data = cached_json(
        f"statsapi:player:{player_id}:{season}:{group}",
        _TTL_STATIC,
        lambda: _get(
            f"people/{player_id}/stats",
            {"stats": "season", "season": season, "group": group},
        ),
    )
    for block in data.get("stats", []):
        for split in block.get("splits", []):
            return split.get("stat", {})
    return {}


def team_season_hitting(team_id: int, season: int) -> dict:
    data = cached_json(
        f"statsapi:teamhit:{team_id}:{season}",
        _TTL_STATIC,
        lambda: _get(
            f"teams/{team_id}/stats",
            {"stats": "season", "season": season, "group": "hitting"},
        ),
    )
    for block in data.get("stats", []):
        for split in block.get("splits", []):
            return split.get("stat", {})
    return {}


def _shift_date(date: str, days: int) -> str:
    from datetime import date as d, timedelta

    y, m, dd = map(int, date.split("-"))
    return (d(y, m, dd) + timedelta(days=days)).isoformat()
