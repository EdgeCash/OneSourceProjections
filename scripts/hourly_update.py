"""Hourly update: pull FantasyPros + BettingPros, snapshot odds, project the
upcoming slate(s), grade finished games, and rewrite the site's data file.

Run by .github/workflows/hourly.yml. After this commits data/output and the
snapshot/ledger files, the Streamlit app redeploys on push.

What it does each run:
  1. Snapshot current BettingPros odds for today+tomorrow (builds our
     own open/close history -> closing lines -> CLV going forward).
  2. Project today and tomorrow's slates (FantasyPros + BettingPros pulled
     inside the pipeline) and archive each for later grading.
  3. Grade games that finished (yesterday/today) into the results ledger.
  4. Write data/output/latest.json with both slates + the live performance
     summary.

Usage:
    python scripts/hourly_update.py [--date YYYY-MM-DD] [--no-snapshot]
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onesource import (notify, pipeline, playerlogs, plays,  # noqa: E402
                       results, snapshots)
from onesource.config import OUTPUT_DIR  # noqa: E402
from onesource.sports import active_sports, default_slate_date  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("hourly")

ET = ZoneInfo("America/New_York")


APP_URL = "https://onesourceprojections.streamlit.app"


def _notify_dfs_card(date: str, card: dict) -> None:
    """Push a +EV PrizePicks/Underdog card to put in before lines move."""
    if not notify.configured():
        return
    ev = max([x for x in (card.get("power_ev"), card.get("flex_ev"))
              if x is not None], default=None)
    be = card.get("breakeven")
    legs_txt = "\n".join(
        f"- {l['player']} {l['side']} {l['line']:g} {l['market']} "
        f"@ {l.get('line_source', 'DFS')} ({(l.get('edge') or 0) * 100:+.0f}%)"
        for l in card["legs"])
    head = (f"🎯 Play {card['size']}-pick"
            + (f" · {ev:+.0%} EV" if ev is not None else "")
            + (f" (each leg needs {be:.0%})" if be is not None else "")
            + f" — {date}")
    notify.send(legs_txt, title=head, tags=["dart", "moneybag"],
                priority="high", click=f"{APP_URL}/?section=DFS")
    log.info("pushed DFS card (%s legs) for %s", card["size"], date)


def _notify_game_plays(date: str, smashes: list[dict]) -> None:
    """Push smash-level moneyline/total plays (EV >= SMASH_EDGE)."""
    if not notify.configured():
        return
    lines = []
    for c in smashes[:8]:
        flag = " ⚠️verify" if c.get("verify") else ""
        lines.append(f"- {c['sport']} {c['game']}: {c['pick']} {c['market']} "
                     f"({c['ev'] * 100:+.0f}% EV){flag}")
    head = f"💥 {len(smashes)} smash game play{'s' if len(smashes) != 1 else ''} — {date}"
    notify.send("\n".join(lines), title=head, tags=["boom"],
                priority="high", click=f"{APP_URL}/?section=PLAYS")
    log.info("pushed %d game smashes for %s", len(smashes), date)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="anchor date (default: today ET)")
    ap.add_argument("--no-snapshot", action="store_true")
    args = ap.parse_args()

    today = (datetime.fromisoformat(args.date).date() if args.date
             else datetime.now(ET).date())
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)
    upcoming = [today.isoformat(), tomorrow.isoformat()]

    # 1) snapshot odds (closing-line history)
    if not args.no_snapshot:
        for d in upcoming:
            try:
                counts = snapshots.snapshot(d)
                log.info("snapshot %s: %s", d, counts)
            except Exception as e:
                log.error("snapshot %s failed: %s", d, e)

    try:
        n = snapshots.compact()
        if n:
            log.info("compacted %d old snapshot files", n)
    except Exception as e:
        log.error("snapshot compaction failed: %s", e)

    # 2) project upcoming slates and archive them
    slates = {}
    for d in upcoming:
        try:
            blob = pipeline.run(d, write=False)["sports"]
            slates[d] = blob
            results.archive_projections(d, blob)
        except Exception as e:
            log.error("projection %s failed: %s", d, e)
            slates[d] = {}

    # 2b) log first-qualify DFS legs, then push notifications ONLY for plays
    #     worth making: a +EV PrizePicks/Underdog card, or a smash game bet.
    #     Dedup against already-pushed keys so the same play never re-pings.
    notified = plays.load_notified()
    pushed_keys: set = set()
    for d in upcoming:
        try:
            plays.log_qualifying(d, slates.get(d, {}))  # track every candidate

            card = plays.playable_card(d)               # best +EV slip
            if card:
                keys = {plays._leg_key(d, leg) for leg in card["legs"]}
                if keys - notified:                     # has a leg not yet pushed
                    _notify_dfs_card(d, card)
                    plays.mark_played(d, card)          # = what we actually play
                    pushed_keys |= keys

            smashes = [c for c in plays.game_play_candidates(d, slates.get(d, {}))
                       if c["key"] not in notified]
            if smashes:
                _notify_game_plays(d, smashes)
                pushed_keys |= {c["key"] for c in smashes}
        except Exception as e:
            log.error("play notify %s failed: %s", d, e)
    if pushed_keys:
        plays.mark_notified(pushed_keys)

    # 3) grade finished games and ingest box scores. Grading sweeps a short
    #    window (idempotent) so missed runs or late-posting finals still get
    #    picked up; box-score ingest stays on yesterday+today (heavier).
    graded = results.grade_recent(today.isoformat(), days=4)
    try:
        graded_legs = plays.grade_plays(today.isoformat(), days=4)
        if graded_legs:
            log.info("graded %d DFS legs", graded_legs)
    except Exception as e:
        log.error("DFS leg grading failed: %s", e)
    ingested = 0
    for d in (yesterday.isoformat(), today.isoformat()):
        for sport in active_sports(d):
            try:
                ingested += playerlogs.ingest(sport, d)
            except Exception as e:
                log.error("box-score ingest %s %s failed: %s", sport, d, e)
    log.info("graded %d new rows, ingested %d player logs", graded, ingested)

    # 4) write the combined site data file
    perf = results.performance()
    primary = default_slate_date(upcoming, slates) or today.isoformat()
    from onesource.clients import oddsapi
    out = {
        "generated_at": datetime.now(ET).isoformat(),
        "primary_date": primary,
        "dates": upcoming,
        "slates": slates,
        "performance": perf,
        "odds_api_credits": oddsapi.credits_remaining(),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "latest.json").write_text(json.dumps(out, indent=1, default=str))
    log.info("wrote latest.json | primary=%s | in-season=%s | perf=%s",
             primary, active_sports(primary), perf["overall"])
    print(f"OK: slates {upcoming}, graded {graded}, "
          f"record {perf['overall']}")


if __name__ == "__main__":
    main()
