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
