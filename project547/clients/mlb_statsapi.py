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


def final_linescores(date: str) -> list[dict]:
    """Completed games for a date with first-inning and first-5 runs derived
    from the linescore innings array — the fields the NRFI/F5 models and the
    daily backfill assembly need. Mirrors :func:`final_scores` but keeps the
    per-inning detail. Production-only (the sandbox proxy blocks statsapi)."""
    data = cached_json(
        f"statsapi:finals_ls:{date}",
        _TTL_SCHEDULE,
        lambda: _get("schedule", {"sportId": 1, "date": date, "hydrate": "linescore"}),
    )
    out = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            if g.get("status", {}).get("codedGameState") != "F":
                continue
            innings = (g.get("linescore", {}) or {}).get("innings", []) or []
            if not innings:
                continue

            def _runs(half, upto=None):
                tot = 0
                for inn in innings:
                    if upto is not None and inn.get("num", 0) > upto:
                        break
                    tot += (inn.get(half, {}) or {}).get("runs", 0) or 0
                return tot

            home, away = g["teams"]["home"], g["teams"]["away"]
            f1_away = (innings[0].get("away", {}) or {}).get("runs", 0) or 0
            f1_home = (innings[0].get("home", {}) or {}).get("runs", 0) or 0
            out.append({
                "game_pk": g["gamePk"], "date": date,
                "home_team": home["team"]["name"],
                "away_team": away["team"]["name"],
                "home_score": home.get("score"), "away_score": away.get("score"),
                "f1_away": f1_away, "f1_home": f1_home,
                "f5_away": _runs("away", upto=5), "f5_home": _runs("home", upto=5),
                "nrfi": (f1_away == 0) and (f1_home == 0),
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
                      "atBats", "plateAppearances", "earnedRuns", "runs"):
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


def hitter_platoon_splits_live(player_id: int, season: int) -> dict:
    """Current-season vL/vR platoon lines for a hitter, live from the statSplits
    endpoint (``sitCodes=vl,vr``). Returns ``{"vl": {avg, slg, plateAppearances},
    "vr": {...}}`` (only the codes with data), or ``{}``. This is the freshest
    platoon source — the committed backfill splits lag by design — so the platoon
    module layers it ahead of the committed table. Cached for the schedule TTL
    (splits move slowly within a day). Empty on any error."""
    if not player_id:
        return {}
    try:
        data = cached_json(
            f"statsapi:statsplits:{player_id}:{season}:hitting", _TTL_SCHEDULE,
            lambda: _get(f"people/{player_id}/stats",
                         {"stats": "statSplits", "group": "hitting",
                          "season": season, "sitCodes": "vl,vr"}))
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for block in data.get("stats", []):
        for split in block.get("splits", []):
            code = (split.get("split") or {}).get("code")
            st = split.get("stat", {}) or {}
            if code in ("vl", "vr") and st:
                out[code] = {"avg": st.get("avg"), "slg": st.get("slg"),
                             "plateAppearances": st.get("plateAppearances")}
    return out


def people_handedness(player_ids: list[int]) -> dict[int, dict]:
    """Live bats/throws for a batch of player ids from the people endpoint —
    the source that fills handedness for players the committed retrosheet map
    misses (recent debuts). Returns {id: {"bats": 'L|R|B', "throws": 'L|R',
    "name": full_name}}. Batched (StatsAPI accepts a comma list of ids) and
    cached statically since handedness never changes."""
    ids = sorted({int(p) for p in player_ids if p})
    if not ids:
        return {}
    key = "statsapi:people:" + ",".join(str(i) for i in ids)
    data = cached_json(
        key, _TTL_STATIC,
        lambda: _get("people", {"personIds": ",".join(str(i) for i in ids)}))
    out: dict[int, dict] = {}
    for p in data.get("people", []):
        pid = p.get("id")
        if pid is None:
            continue
        bats = (p.get("batSide") or {}).get("code")
        # StatsAPI codes switch-hitters 'S'; the committed retrosheet map uses
        # 'B' — normalize so the merged dataset keeps one convention.
        if bats == "S":
            bats = "B"
        out[int(pid)] = {
            "bats": bats,
            "throws": (p.get("pitchHand") or {}).get("code"),
            "name": p.get("fullName"),
        }
    return out


def _ip_to_outs(ip) -> int | None:
    """statsapi 'inningsPitched' like '5.2' = 5 innings + 2 outs -> total outs."""
    if ip is None:
        return None
    s = str(ip)
    try:
        whole, frac = s.split(".") if "." in s else (s, "0")
        return int(whole) * 3 + int(frac)
    except ValueError:
        return None


def pitcher_recent_workload(player_id: int, season: int, n: int = 5) -> dict | None:
    """Recent per-start pitch counts + innings for a pitcher, so expected
    innings can reflect a workload the season average misses (a starter
    building up from the IL, a rookie on a pitch limit, a stretched-out opener).

    Returns {"starts": [{date, pitches, outs}], "pitch_ceiling": int,
    "pitches_per_out": float, "n_starts": int} over the last ``n`` starts, or
    None if we can't get a usable log. Prefers true starts (gamesStarted==1);
    falls back to all outings when a pitcher has too few logged starts (call-ups
    still being stretched), since that's exactly whose ceiling we care about."""
    if not player_id:
        return None
    try:
        data = cached_json(
            f"statsapi:gamelog:{player_id}:{season}:pitching", _TTL_SCHEDULE,
            lambda: _get(f"people/{player_id}/stats",
                         {"stats": "gameLog", "group": "pitching",
                          "season": season}))
    except Exception:
        return None
    splits = (data.get("stats") or [{}])[0].get("splits", []) or []
    rows = []
    for s in splits:  # gameLog is chronological (oldest first)
        st = s.get("stat", {})
        pitches = st.get("numberOfPitches")
        outs = _ip_to_outs(st.get("inningsPitched"))
        if pitches is None or outs is None:
            continue
        rows.append({"date": s.get("date"), "pitches": int(pitches),
                     "outs": outs, "gs": bool(st.get("gamesStarted"))})
    starts = [r for r in rows if r["gs"]]
    pool = starts if len(starts) >= 2 else rows   # too few starts -> use all
    pool = pool[-n:]
    if not pool:
        return None
    tot_pitches = sum(r["pitches"] for r in pool)
    tot_outs = sum(r["outs"] for r in pool) or 1
    return {
        "starts": [{"date": r["date"], "pitches": r["pitches"], "outs": r["outs"]}
                   for r in pool],
        "n_starts": len(pool),
        "pitch_ceiling": max(r["pitches"] for r in pool),
        "pitches_per_out": tot_pitches / tot_outs,
    }


def game_officials(game_pk: int) -> dict:
    """Umpire crew for a game from the boxscore ``officials`` block, keyed by
    role. Returns e.g. {"home_plate": {"name": ..., "id": ...}, "first_base":
    ...}. The home-plate ump is the one that matters for K/run tendency. Empty
    dict if the crew isn't posted yet (assignments appear a few hours pre-game)."""
    try:
        data = cached_json(f"statsapi:box:{game_pk}", _TTL_SCHEDULE,
                           lambda: _get(f"game/{game_pk}/boxscore"))
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for o in data.get("officials", []) or []:
        role = (o.get("officialType") or "").lower().replace(" ", "_")
        person = o.get("official", {}) or {}
        if role and person.get("fullName"):
            out[role] = {"name": person.get("fullName"), "id": person.get("id")}
    return out


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
