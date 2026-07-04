"""Streamlit-free data assembly for the Edge Card site.

These are ports of the ``app/dashboard.py`` helpers that build the ``matchup`` /
``data`` / ``best_line`` inputs the sheet renders from. The Streamlit
``@st.cache_data`` decorators are replaced with ``functools.lru_cache`` — the
bodies are otherwise faithful, since they already call pure ``project547``
modules. Everything here is a pure function of the committed data + reference
tables, so it runs headless in the hourly Action.
"""
from __future__ import annotations

import functools
import json
import math

# --- NaN-safe JSON load -----------------------------------------------------
# latest.json is written by Python and contains ~1900 bare `NaN` tokens (valid
# for Python's json, invalid for a browser). We load with Python (fine) and
# sanitize to None on the way in so every downstream consumer — and any JSON we
# re-emit for the client — is clean.

def _denan(obj):
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, dict):
        return {k: _denan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_denan(v) for v in obj]
    return obj


def load_latest(path: str) -> dict:
    with open(path) as f:
        raw = json.load(f)          # Python parses NaN happily
    return _denan(raw)              # ...then we strip it to None


# --- edge gate --------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def market_stats() -> dict:
    """{"SPORT|market": {n, clv_n, avg_clv, ...}} — realized CLV per market."""
    try:
        from project547 import edge_gate
        return {f"{s}|{m}": v for (s, m), v in edge_gate.market_stats().items()}
    except Exception:
        return {}


@functools.lru_cache(maxsize=1)
def gate_table() -> dict:
    try:
        from project547 import edge_gate
        return edge_gate.gate_table()
    except Exception:
        return {}


# --- calibration receipt ----------------------------------------------------

@functools.lru_cache(maxsize=16)
def market_calibration(sport: str) -> dict | None:
    """Per-market {pred, actual, n} from the graded ledger. Predicted = mean
    logged model_prob for the side bet; actual = realized hit-rate. Markets
    without a logged model_prob fall back to actual-only + a DATA GAP."""
    try:
        from project547 import results
        led = [r for r in results.load_ledger()
               if r.get("sport") == sport and r.get("won") is not None]
    except Exception:
        return None
    ms = market_stats()
    out, any_data = {}, False
    for label, mk in (("Moneyline", "moneyline"), ("Total", "total"),
                      ("Run Line", "spread")):
        rows = [r for r in led if r.get("market") == mk
                and isinstance(r.get("model_prob"), (int, float))]
        if rows:
            pred = sum(r["model_prob"] for r in rows) / len(rows)
            actual = sum(1 for r in rows if r["won"]) / len(rows)
            out[label] = {"pred": round(pred, 4), "actual": round(actual, 4),
                          "n": len(rows)}
            any_data = True
        else:
            s = ms.get(f"{sport}|{mk}") or {}
            out[label] = {"pred": None, "actual": s.get("win_rate"),
                          "n": s.get("n")}
            any_data = any_data or s.get("win_rate") is not None
    return out if any_data else None


# --- pitching table (MLB) ---------------------------------------------------

@functools.lru_cache(maxsize=4)
def pitcher_stats(season: int) -> dict:
    """{norm_name: FanGraphs pitching row} for the pitching strip."""
    try:
        from project547 import pipeline
        df = pipeline._pitcher_table(season)
        return {r.get("norm_name"): r for r in df.to_dict("records")
                if r.get("norm_name")}
    except Exception:
        return {}


# --- team matchup breakdown -------------------------------------------------

@functools.lru_cache(maxsize=256)
def _matchup_cached(sport: str, home: str, away: str, asof: str,
                    window: str) -> dict:
    try:
        from project547 import teamstats
        return teamstats.matchup(sport, home, away, asof, window=window) or {}
    except Exception:
        return {}


def matchup(sport: str, home: str, away: str, asof: str,
            window: str = "l15") -> dict:
    return _matchup_cached(sport, home or "", away or "", asof or "", window)


# --- best available line (line shop) ----------------------------------------

@functools.lru_cache(maxsize=64)
def _best_lines_cached(sport: str, date_sel: str) -> dict:
    try:
        from project547 import lineshop
        from project547.names import normalize  # noqa: F401 (parity w/ dashboard)
        return {" vs ".join(sorted(k)): v
                for k, v in lineshop.best_lines(sport, date_sel).items()}
    except Exception:
        return {}


def best_line_for(sport: str, g: dict, date_sel: str) -> dict:
    """{label: {price, book}} of the best price per market, or {}."""
    from project547.names import normalize
    best = _best_lines_cached(sport, date_sel)
    if not best:
        return {}
    key = " vs ".join(sorted({normalize(g.get("home_team", "")),
                              normalize(g.get("away_team", ""))}))
    rec = best.get(key)
    if not rec:
        return {}
    out: dict = {}
    ml = rec.get("moneyline") or {}
    for side in ("away_team", "home_team"):
        info = ml.get(normalize(g.get(side, "")))
        if info:
            out[f"{g.get(side, '').split()[-1]} ML"] = {
                "price": info.get("price"), "book": info.get("book")}
    tot = rec.get("total") or {}
    for side in ("over", "under"):
        info = tot.get(side)
        if info:
            ln = f" {info['line']:g}" if info.get("line") is not None else ""
            out[f"{side.title()}{ln}"] = {"price": info.get("price"),
                                          "book": info.get("book")}
    return out


# --- the sheet `data` object ------------------------------------------------

def sheet_data(sport: str, g: dict, mu: dict, date_sel: str) -> dict:
    """Assemble the Edge Card ``data`` extras (clv / calibration / pitching).
    Missing pieces stay absent so the renderer shows DATA GAP chips."""
    ms = market_stats()
    clv = {}
    for mk in ("moneyline", "total", "spread"):
        s = ms.get(f"{sport}|{mk}") or {}
        if s.get("clv_n"):
            clv[mk] = {"avg_clv": s.get("avg_clv"), "clv_n": s.get("clv_n")}
    pitching = None
    if sport == "MLB":
        from project547 import platoon
        from project547.names import normalize
        try:
            from project547.pipeline import starter_xfip, _lookup_float as _lf
        except Exception:
            starter_xfip = lambda *_: None       # noqa: E731
            _lf = lambda row, *ks: None           # noqa: E731
        pstats = pitcher_stats(int(str(date_sel)[:4]))
        pitching = {}
        for side in ("away", "home"):
            nm, pid = g.get(f"{side}_pitcher"), g.get(f"{side}_pitcher_id")
            if not isinstance(nm, str) or not nm.strip():
                continue
            row = pstats.get(normalize(nm)) or {}
            ip_tot, gs = _lf(row, "IP"), _lf(row, "GS")
            ip = (ip_tot / gs) if ip_tot and gs else None
            k9, bb9 = _lf(row, "K/9", "K9"), _lf(row, "BB/9", "BB9")
            xfip = _lf(row, "xFIP", "FIP")
            if xfip is None:
                try:
                    xfip = starter_xfip(nm)
                except Exception:
                    xfip = None
            try:
                hand = platoon.throws(nm, pid)
            except Exception:
                hand = None
            pitching[f"{side}_sp"] = {
                "name": nm, "id": pid, "hand": hand, "xfip": xfip, "ip": ip,
                "k9": k9, "bb9": bb9,
                "tto_flag": bool(ip and ip >= 5.8)}
            bp = (mu or {}).get(f"{side}_bullpen") or {}
            if bp:
                pitching[f"{side}_bullpen"] = {"fatigue": bp.get("level"),
                                               "proj_ip": bp.get("proj_ip")}
    return {"clv": clv or None, "calibration": market_calibration(sport),
            "pitching": pitching or None}
