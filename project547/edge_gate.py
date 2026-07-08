"""Per-market demonstrated-edge gate — wager only where we've proven an edge.

The old curated process treated model EV as edge uniformly: anything clearing the
2-6% band got pushed, in every market and sport. But our edge is wildly uneven by
market — a 2-6% model-vs-market disagreement on an *efficient* market is noise,
not edge, and those are the plays that lost. This gate makes curation and staking
conditional on **demonstrated, market-specific edge**, measured by realized
closing-line value (CLV) — the leading indicator the whole thesis rests on
(positive CLV = our number beat the close = real edge, ahead of the noisy ROI).

For each (sport, market) it rolls the graded results ledger and classifies:

  CLEARED   — enough CLV sample AND the one-sided lower confidence bound of
              the mean CLV clears the floor -> full curation + full stake
  GATED     — enough (a higher bar) CLV sample and avg CLV clearly negative
              -> proven no edge -> suppressed (PASS, zero stake)
  PROBATION — not enough sample yet to judge -> bet small to gather CLV, capped
              below the top tier

CLEAR is variance-aware (audit #21): with per-bet CLV σ of a few percent, the
point estimate at n=30 has an SE of ~0.5-1%, so marginal markets used to clear
or miss on pure noise and then draw full Kelly. The rule is
``avg_clv − GATE_Z·SE >= config.GATE_CLV_FLOOR`` (one-sided 90% lower bound on
the mean; SE from the per-row CLV sample std, n >= GATE_CLEAR_MIN still
required). We bound the mean CLV rather than Wilson-bound the
already-computed ``clv_pos_rate`` because the floor is expressed in CLV units
and mean CLV is what staking cares about; a stats dict without variance info
(legacy caller) falls back to the point estimate.

Gate stats are restricted to the curated EV band (``ev < config.STALE_EV``):
the ledger grades every EV >= MIN_EDGE bet, including the >= 8% "verify" tier
the curation layer never pushes (plays._tier — stale lines with the worst CLV
by the system's own thesis), and those rows must not be able to gate a
curated market off or clear it.

Asymmetric on purpose: easy-ish to clear on positive CLV, hard to gate a market
off (needs more, clearly-negative evidence) so we don't kill a market on noise.
The window is rolling, so a market re-earns (or loses) its status as data accrues.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from . import config, results

CLEARED = "cleared"
PROBATION = "probation"
GATED = "gated"

# One-sided z for the CLEAR lower confidence bound (~90%). See module docstring.
GATE_Z = 1.28

# Backtest-driven hard hold (docs/MODEL_REPAIR.md P1/P5 + the T0.1 market-
# baseline table in docs/research/PROJECTIONS_ROADMAP.md): markets with a
# demonstrated negative walk-forward ROI at the close — even after calibration
# they lose ~15-20% (efficient markets we don't beat). The T0.1 instrument
# condemned NBA/NFL/NHL *totals* exactly as hard as those moneylines (pure
# market beats the model monotonically on both), so both are held to
# projections-only regardless of live CLV. Escape hatch (same for every pair):
# remove it once fit_calibration / the open→close CLV backtest shows the
# market clears positive rolling out-of-sample CLV/ROI.
HELD_MARKETS = {
    ("NBA", "moneyline"),
    ("NFL", "moneyline"),
    ("NHL", "moneyline"),
    ("NBA", "total"),
    ("NFL", "total"),
    ("NHL", "total"),
}


def _in_window(row_date: str, asof: str, window_days: int) -> bool:
    if not window_days:
        return True
    try:
        d = date.fromisoformat(str(row_date)[:10])
        a = date.fromisoformat(str(asof)[:10])
    except (TypeError, ValueError):
        return True
    return (a - d).days <= window_days and d <= a


def _in_ev_band(r: dict) -> bool:
    """Only bets the curation layer could actually push count toward gate
    stats: EV below the STALE_EV "verify" bar. Rows without an EV (legacy)
    are kept."""
    ev = r.get("ev")
    if ev is None:
        return True
    try:
        return float(ev) < config.STALE_EV
    except (TypeError, ValueError):
        return True


def market_stats(ledger: list[dict] | None = None, asof: str | None = None,
                 window_days: int | None = None) -> dict:
    """Rolling per-(sport, market) record from the graded ledger. Returns
    ``{(sport, market): {n, clv_n, avg_clv, clv_sd, clv_lb, clv_pos_rate,
    roi, win_rate}}``. ``clv_lb`` is the one-sided lower confidence bound of
    the mean CLV (avg − GATE_Z·SE) that CLEAR gating uses."""
    if ledger is None:
        ledger = results.load_ledger()
    window_days = config.GATE_WINDOW_DAYS if window_days is None else window_days
    if asof is None:
        dates = [str(r.get("date")) for r in ledger if r.get("date")]
        asof = max(dates) if dates else date.today().isoformat()

    acc: dict = {}
    for r in ledger:
        if "pnl" not in r or not _in_window(r.get("date"), asof, window_days) \
                or not _in_ev_band(r):
            continue
        key = (r.get("sport"), r.get("market"))
        a = acc.setdefault(key, {"n": 0, "clv_n": 0, "clv_sum": 0.0,
                                 "clv_sq": 0.0, "clv_pos": 0, "pnl": 0.0,
                                 "wins": 0, "decided": 0})
        a["n"] += 1
        a["pnl"] += r.get("pnl", 0.0) or 0.0
        if r.get("won") is not None:
            a["decided"] += 1
            a["wins"] += 1 if r.get("won") else 0
        clv = r.get("clv")
        if clv is not None:
            a["clv_n"] += 1
            a["clv_sum"] += clv
            a["clv_sq"] += clv * clv
            a["clv_pos"] += 1 if clv > 0 else 0

    out = {}
    for key, a in acc.items():
        n = a["clv_n"]
        avg = (a["clv_sum"] / n) if n else None
        sd = lb = None
        if n >= 2:
            var = max(0.0, (a["clv_sq"] - n * avg * avg) / (n - 1))
            sd = math.sqrt(var)
            lb = avg - GATE_Z * sd / math.sqrt(n)
        out[key] = {
            "n": a["n"], "clv_n": n,
            "avg_clv": round(avg, 4) if avg is not None else None,
            "clv_sd": round(sd, 4) if sd is not None else None,
            "clv_lb": round(lb, 4) if lb is not None else None,
            "clv_pos_rate": round(a["clv_pos"] / n, 4) if n else None,
            "roi": round(a["pnl"] / a["n"], 4) if a["n"] else None,
            "win_rate": round(a["wins"] / a["decided"], 4) if a["decided"] else None,
        }
    return out


def classify(stat: dict | None) -> str:
    """Gate verdict from a market's rolling stats (see module docstring)."""
    if not stat:
        return PROBATION
    clv_n = stat.get("clv_n", 0)
    avg = stat.get("avg_clv")
    if avg is None:
        return PROBATION
    # CLEAR on the one-sided lower confidence bound of mean CLV, so a noisy
    # marginal market can't clear (and draw full Kelly) on sampling luck.
    # Stats dicts without variance info fall back to the point estimate.
    lb = stat.get("clv_lb")
    if lb is None:
        lb = avg
    if clv_n >= config.GATE_CLEAR_MIN and lb >= config.GATE_CLV_FLOOR:
        return CLEARED
    # gate off only on a bigger, clearly-negative sample (don't kill on noise)
    if clv_n >= config.GATE_OFF_MIN and avg <= config.GATE_OFF_CLV:
        return GATED
    return PROBATION


def gate_table(ledger: list[dict] | None = None, asof: str | None = None,
               window_days: int | None = None) -> dict:
    """``{(sport, market): {status, ...stats}}`` for every market with history."""
    stats = market_stats(ledger, asof, window_days)
    return {k: {"status": classify(v), **v} for k, v in stats.items()}


def status_for(sport: str, market: str, table: dict | None = None) -> str:
    """Gate status for one market. Unknown / no-history markets are PROBATION
    (bet small to gather CLV, never a top-tier play until proven)."""
    if (sport, market) in HELD_MARKETS:
        return GATED           # backtest-proven loser: projections-only, no stake
    if table is None:
        table = gate_table()
    entry = table.get((sport, market))
    return entry["status"] if entry else PROBATION


# ---------------------------------------------------------------------------
# Effects on the wagering flow
# ---------------------------------------------------------------------------

# Ordered play_tier "kind"s, best->worst, used to cap a tier by gate status.
_TIER_ORDER = ["verify", "core", "watch", "lean", "pass"]


def cap_tier(kind: str, status: str) -> str:
    """Cap a play_tier ``kind`` by the market's gate status.

    - GATED     -> "pass" (no demonstrated edge; never curated)
    - PROBATION -> at most "lean" (unproven; small, non-headline plays only)
    - CLEARED   -> unchanged
    ``verify`` (a too-large-edge warning) is preserved either way — it's a caution,
    not a recommendation.
    """
    if kind == "verify":
        return kind
    if status == GATED:
        return "pass"
    if status == PROBATION and kind == "core":
        return "lean"
    return kind


def stake_mult(status: str) -> float:
    """Kelly multiplier by gate status: full when cleared, reduced on probation
    (enough to gather CLV), zero when gated off."""
    return {CLEARED: 1.0, PROBATION: config.GATE_PROBATION_STAKE, GATED: 0.0}.get(
        status, config.GATE_PROBATION_STAKE)


def is_curatable(status: str) -> bool:
    """Whether a market may produce a pushed/curated play at all."""
    return status != GATED


# ---------------------------------------------------------------------------
# Conviction — rank the board by *proven* edge, not raw disagreement (EV).
#
# The audit's central finding: raw model EV (disagreement with the price) is not
# edge; realized CLV is. Two plays at the same EV are not equal — the one in a
# market that reliably beats the close is the real play. ``conviction`` is the
# rank key the curated board sorts on, built from data the gate already rolls up
# (``market_stats``): the market's lower-bound mean CLV, discounted for sample
# size and zeroed unless the play sits in that market's positive-CLV band.
#
# ``ev_in_band`` takes the play's positive-CLV band. A ``band`` of None is the
# global sharp band (config.SHARP_EV_MIN..SHARP_EV_MAX); ``ev_bands`` fits a
# per-market band from the ledger (Component 2). See docs/CURATION_DESIGN.md.
# ---------------------------------------------------------------------------

# Component 2 — per-market EV band fit from the ledger.
# The global sharp band is one hand-set guess applied to every market, but where
# realized CLV actually turns positive differs by market. ``ev_bands`` bins each
# market's graded bets by EV and returns the longest contiguous EV run whose mean
# CLV clears the floor. Falls back to the global band until a market has enough
# per-bin sample, so it activates automatically as clean CLV accrues (same
# graceful cold start as conviction).
EV_BAND_MIN_PER_BIN = 20      # CLV-graded bets in an EV bin before it's trusted
_EV_BAND_STEP = 0.01          # bin width in EV (one percentage point)


def _global_band() -> tuple[float, float]:
    return (config.SHARP_EV_MIN, config.SHARP_EV_MAX)


def ev_bands(ledger: list[dict] | None = None, asof: str | None = None,
             window_days: int | None = None) -> dict:
    """``{(sport, market): (lo, hi)}`` — the EV range where each market reliably
    beats the close, fit from realized CLV by EV bin over the curated range
    [SHARP_EV_MIN, STALE_EV). Only markets with enough per-bin sample appear;
    callers fall back to the global band for the rest."""
    if ledger is None:
        ledger = results.load_ledger()
    window_days = config.GATE_WINDOW_DAYS if window_days is None else window_days
    if asof is None:
        dates = [str(r.get("date")) for r in ledger if r.get("date")]
        asof = max(dates) if dates else date.today().isoformat()

    acc: dict = {}    # (sport, market) -> {bin_idx: [n, clv_sum]}
    for r in ledger:
        if "pnl" not in r or not _in_window(r.get("date"), asof, window_days):
            continue
        clv, ev = r.get("clv"), r.get("ev")
        if clv is None or ev is None:
            continue
        try:
            ev = float(ev)
        except (TypeError, ValueError):
            continue
        if ev < config.SHARP_EV_MIN or ev >= config.STALE_EV:
            continue
        b = int((ev - config.SHARP_EV_MIN) / _EV_BAND_STEP)
        bins = acc.setdefault((r.get("sport"), r.get("market")), {})
        cell = bins.setdefault(b, [0, 0.0])
        cell[0] += 1
        cell[1] += clv

    out = {}
    for key, bins in acc.items():
        good = sorted(b for b, (n, s) in bins.items()
                      if n >= EV_BAND_MIN_PER_BIN and s / n > config.GATE_CLV_FLOOR)
        if not good:
            continue
        # longest contiguous run of positive-CLV bins
        runs, cur = [], [good[0]]
        for b in good[1:]:
            if b == cur[-1] + 1:
                cur.append(b)
            else:
                runs.append(cur)
                cur = [b]
        runs.append(cur)
        run = max(runs, key=len)
        lo = round(config.SHARP_EV_MIN + run[0] * _EV_BAND_STEP, 4)
        hi = round(config.SHARP_EV_MIN + (run[-1] + 1) * _EV_BAND_STEP, 4)
        out[key] = (lo, hi)
    return out


def ev_band(sport: str, market: str, bands: dict | None = None) -> tuple[float, float]:
    """The (lo, hi) positive-CLV band for one market — fitted if available,
    else the global sharp band."""
    if bands and (sport, market) in bands:
        return bands[(sport, market)]
    return _global_band()


def ev_in_band(ev: float, sport: str = "", market: str = "",
               band: tuple | None = None) -> bool:
    """Whether a play's EV sits in its positive-CLV band. ``band`` None -> the
    global sharp band (config.SHARP_EV_MIN..SHARP_EV_MAX)."""
    try:
        ev = float(ev)
    except (TypeError, ValueError):
        return False
    lo, hi = band if band else _global_band()
    return lo <= ev <= hi


def conviction(ev: float, sport: str, market: str, stat: dict | None,
               band: tuple | None = None) -> float:
    """Rank key for 'best suggested plays' — higher = more trustworthy edge.

    ``= clv_lb x sample_confidence`` when the play is in-band and the market's
    lower-bound mean CLV is positive; ``0.0`` otherwise. So a big-EV stale line
    in a thin market scores 0 and sorts below a modest in-band edge in a market
    that demonstrably beats the close. ``stat`` is the market's ``gate_table``
    entry (``avg_clv``/``clv_lb``/``clv_n``); ``None`` (no history) -> 0.0, i.e.
    an unproven play never outranks a proven one. ``band`` is the market's
    fitted EV band (``ev_band``); None -> the global band.
    """
    if not stat or not ev_in_band(ev, sport, market, band):
        return 0.0
    lb = stat.get("clv_lb")
    if lb is None:                    # legacy stats w/o variance -> point estimate
        lb = stat.get("avg_clv")
    if lb is None or lb <= 0:         # not (yet) beating the close -> no conviction
        return 0.0
    clv_n = stat.get("clv_n", 0) or 0
    sample_conf = (min(1.0, clv_n / config.GATE_CLEAR_MIN)
                   if config.GATE_CLEAR_MIN else 1.0)
    return round(lb * sample_conf, 5)


# ---------------------------------------------------------------------------
# Curation seed (docs/CURATION_DESIGN.md step 2) — a production-mode backtest of
# the CURRENT model scores historical slates and writes per-market CLV, so
# conviction has a prior from day one instead of waiting for the live ledger.
#
# Scope on purpose: the seed enriches CONVICTION (ranking/display) ONLY. It never
# touches market_stats/classify/stake_mult, so stake sizing stays live-ledger
# only — the backtest CLV (vs backfill consensus) is a weaker signal than the
# live close and must not size real money. And it supplements a market only while
# that market's live CLV is thin (< GATE_CLEAR_MIN); once live clears the bar the
# seed is ignored, so the prior self-retires. Off unless config.CURATION_SEED_ENABLED.
# ---------------------------------------------------------------------------

def conviction_prior() -> dict:
    """``{(sport, market): {clv_n, avg_clv, clv_sd, clv_lb}}`` from the seed
    file. Empty when disabled, missing, or unreadable."""
    if not config.CURATION_SEED_ENABLED:
        return {}
    try:
        import json
        raw = json.loads(config.CURATION_SEED_PATH.read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for row in raw.get("markets", []):
        key = (row.get("sport"), row.get("market"))
        if row.get("clv_n"):
            out[key] = {"clv_n": row["clv_n"], "avg_clv": row.get("avg_clv"),
                        "clv_sd": row.get("clv_sd"), "clv_lb": row.get("clv_lb")}
    return out


def _clv_moments(d: dict | None) -> tuple[int, float, float]:
    """(n, sum, sum_of_squares) reconstructed from a {clv_n, avg_clv, clv_sd}
    stat, so two CLV samples can be pooled without the raw observations."""
    if not d:
        return 0, 0.0, 0.0
    n = d.get("clv_n") or 0
    avg = d.get("avg_clv")
    if not n or avg is None:
        return 0, 0.0, 0.0
    sd = d.get("clv_sd")
    s = avg * n
    sq = (sd * sd * (n - 1) if (sd is not None and n >= 2) else 0.0) + n * avg * avg
    return n, s, sq


def blend_conviction(live: dict | None, seed: dict | None) -> dict | None:
    """The stat dict ``conviction`` should read: the live gate stats, pooled with
    the seed prior only while the live CLV sample is thin (< GATE_CLEAR_MIN).
    Returns ``live`` unchanged when there's no seed or live already clears the
    bar (self-retiring prior)."""
    if not seed:
        return live
    if ((live or {}).get("clv_n") or 0) >= config.GATE_CLEAR_MIN:
        return live
    n1, s1, sq1 = _clv_moments(live)
    n2, s2, sq2 = _clv_moments(seed)
    n = n1 + n2
    if not n:
        return live
    avg = (s1 + s2) / n
    sd = lb = None
    if n >= 2:
        var = max(0.0, ((sq1 + sq2) - n * avg * avg) / (n - 1))
        sd = math.sqrt(var)
        lb = avg - GATE_Z * sd / math.sqrt(n)
    out = dict(live or {})
    out.update({"clv_n": n, "avg_clv": round(avg, 4),
                "clv_sd": round(sd, 4) if sd is not None else None,
                "clv_lb": round(lb, 4) if lb is not None else None,
                "seeded": True})
    out.setdefault("n", n)
    return out
