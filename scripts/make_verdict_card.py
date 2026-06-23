"""Render a shareable "True Bill Verdict Card" for a single play — the
screenshot-perfect 1080×1350 organic-marketing unit from the brand playbook.
Built with the real palette/fonts (`app/theme.py`) and live `latest.json`.

The trust strip (Conviction Record: sample size · ROI · units · CLV) and the
filed-at timestamp are always present — they ARE the proof. `--redact` ships the
public-teaser variant: the charge is sealed, but the evidence, verdict, and
record stay visible (the paywall becomes the ad).

    python scripts/make_verdict_card.py [--redact] [--story] [-o card.html]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from app import theme, ui  # noqa: E402
from onesource import config  # noqa: E402

ET = ZoneInfo("America/New_York")


def _slates(data: dict) -> dict:
    return data.get("slates") or (
        {data.get("date", "latest"): data["sports"]} if "sports" in data else {})


def _top_play(data: dict) -> dict | None:
    """The day's loudest charge: highest-EV row off the Daily Docket."""
    slates = _slates(data)
    primary = data.get("primary_date") or next(iter(slates), None)
    board = ui.build_best_bets(slates.get(primary, {}), config.MIN_EDGE)
    if board.empty:
        return None
    board = board[pd.to_numeric(board["ev"], errors="coerce") < 0.30]
    if board.empty:
        return None
    r = board.iloc[0].to_dict()
    r["_date"] = primary
    return r


def _record_strip(perf: dict) -> str:
    o = (perf or {}).get("overall", {}) or {}

    def f(v, fmt, d="—"):
        return fmt.format(v) if isinstance(v, (int, float)) and pd.notna(v) else d

    n = o.get("clv_bets") or o.get("bets") or 0
    roi = f(o.get("roi_pct"), "{:+.1f}%")
    units = f(o.get("units"), "{:+.1f}u")
    beat = o.get("clv_beat_rate")
    beat_s = f(beat * 100 if isinstance(beat, (int, float)) else None, "{:.0f}%")
    avg_clv = f(o.get("avg_clv_pct"), "{:+.1f}%")
    return (
        "<div class='rec'>"
        "<div class='rec-h'>CONVICTION RECORD · LAST {n} GRADED</div>"
        "<div class='rec-row'>"
        f"<span>ROI <b>{roi}</b></span><span>{units}</span>"
        f"<span>CLV <b>{avg_clv}</b></span><span>beat close <b>{beat_s}</b></span>"
        "</div></div>"
    ).replace("{n}", f"{int(n):,}")


def _evidence(r: dict) -> list[str]:
    bits = []
    mp = pd.to_numeric(r.get("model_prob"), errors="coerce")
    ev = pd.to_numeric(r.get("ev"), errors="coerce")
    if pd.notna(mp):
        bits.append(f"The People's model: <b>{mp * 100:.0f}%</b> to cash")
    if pd.notna(ev):
        bits.append(f"Edge vs. the price: <b>{ev * 100:+.1f}% EV</b>")
        score = min(10, max(0, ev * 100))
        bits.append(f"Strength of the case: <b>{score:.0f}/10</b>")
    return bits[:3]


def build(data: dict, redact: bool, story: bool) -> str:
    r = _top_play(data) or {
        "sport": "MLB", "bet": "Braves Team Total OVER 4.5", "game": "NYM @ ATL",
        "price": -106, "model_prob": 0.61, "ev": 0.058, "kelly": 0.014,
        "_date": "2026-06-23"}
    gen = str(data.get("generated_at", ""))[:16].replace("T", " ")
    try:
        ts = pd.Timestamp(gen) if gen else pd.Timestamp.now(ET)
    except Exception:
        ts = pd.Timestamp.now(ET)
    docket_no = f"{(r.get('_date') or ts.strftime('%Y-%m-%d'))}-{abs(hash(r.get('bet',''))) % 900 + 100}"

    caption = f"IN RE: {r.get('game', '')}".strip().upper()
    charge = str(r.get("bet", ""))
    price = ui.fmt_american(r.get("price"))
    ev = pd.to_numeric(r.get("ev"), errors="coerce")
    if pd.notna(ev) and ev >= max(0.06, config.MIN_EDGE * 2):
        stamp = theme.stamp("TRUE BILL", "true-bill")
    elif pd.notna(ev) and ev >= config.MIN_EDGE:
        stamp = theme.stamp("INDICTED", "indicted")
    else:
        stamp = theme.stamp("DISMISSED", "dismissed")

    k = pd.to_numeric(r.get("kelly"), errors="coerce")
    units = f"{k * 100:.1f}u" if pd.notna(k) and k > 0 else "—"

    if redact:
        charge_html = ("<div class='charge sealed'><span class='bar'></span>"
                       f"{theme.stamp('SEALED', 'sealed')}</div>")
        price_html = "<span class='redact'>▮▮▮▮▮</span>"
    else:
        charge_html = f"<div class='charge'>{charge}</div>"
        price_html = f"<b>{price}</b>"

    evidence = "".join(f"<li>{b}</li>" for b in _evidence(r))
    record = _record_strip(data.get("performance", {}))
    h = 1920 if story else 1350

    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>True Bill — verdict card</title>{theme.css('docket')}
<style>
  body{{margin:0;background:#1a1a1d;display:flex;justify-content:center;
    align-items:flex-start;padding:24px;font-family:var(--body);}}
  .card{{position:relative;width:1080px;height:{h}px;background:
    radial-gradient(1200px 600px at 10% -8%, rgba(var(--acc-rgb),0.10), transparent 55%),
    var(--bg);border:1px solid var(--line);border-top:6px solid var(--elec);
    box-sizing:border-box;padding:60px 64px 52px;color:var(--text);overflow:hidden;}}
  .mast{{display:flex;justify-content:space-between;align-items:baseline;
    border-bottom:3px solid var(--vivid);padding-bottom:16px;}}
  .mast .b{{font-family:var(--disp);font-weight:700;font-size:34px;letter-spacing:4px;}}
  .mast .b .dot{{color:var(--acc);}}
  .mast .u{{font-family:var(--mono);font-size:18px;letter-spacing:3px;color:var(--muted);}}
  .dno{{font-family:var(--mono);font-size:20px;letter-spacing:2px;color:var(--muted);
    margin:26px 0 8px;}}
  .cap{{font-family:var(--disp);font-weight:700;font-size:44px;letter-spacing:1px;
    line-height:1.12;color:#fff;margin:4px 0 26px;}}
  .lbl{{font-family:var(--mono);font-size:18px;letter-spacing:2px;color:var(--acc);
    text-transform:uppercase;margin-top:22px;}}
  .charge{{font-family:var(--disp);font-weight:600;font-size:40px;color:var(--text);
    margin-top:6px;letter-spacing:0.5px;}}
  .charge.sealed{{position:relative;height:54px;}}
  .charge .bar{{position:absolute;left:0;top:6px;width:62%;height:40px;background:#000;
    border:1px solid var(--line);}}
  .filed{{font-family:var(--mono);font-size:30px;margin-top:8px;color:var(--text2);}}
  .redact{{background:#000;color:#000;padding:0 10px;letter-spacing:4px;}}
  .stamp-wrap{{position:absolute;top:300px;right:70px;transform:scale(2.0);
    transform-origin:top right;}}
  .ev h4{{margin:6px 0;}}
  .ev ul{{list-style:none;padding:0;margin:6px 0;font-family:var(--body);font-size:28px;
    line-height:1.7;color:var(--text2);}}
  .ev b{{color:var(--text);}}
  .stake{{display:flex;gap:40px;font-family:var(--mono);font-size:26px;margin-top:24px;
    padding:18px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line);
    color:var(--text2);}}
  .stake b{{color:var(--acc);}}
  .rec{{position:absolute;left:64px;right:64px;bottom:108px;}}
  .rec-h{{font-family:var(--mono);font-size:18px;letter-spacing:2px;color:var(--muted);}}
  .rec-row{{display:flex;justify-content:space-between;font-family:var(--mono);
    font-size:30px;margin-top:8px;color:var(--text2);}}
  .rec-row b{{color:var(--pos);}}
  .foot{{position:absolute;left:64px;right:64px;bottom:48px;display:flex;
    justify-content:space-between;font-family:var(--mono);font-size:20px;
    color:var(--faint);border-top:1px solid var(--line);padding-top:16px;}}
</style></head>
<body><div class='card'>
  <div class='mast'><span class='b'>TRUE<span class='dot'>·</span>BILL</span>
    <span class='u'>SPECIAL WAGERS UNIT</span></div>
  <div class='dno'>DOCKET No. {docket_no}</div>
  <div class='cap'>{caption}</div>
  <div class='lbl'>The Charge</div>{charge_html}
  <div class='filed'>FILED AT &nbsp;{price_html}</div>
  <div class='stamp-wrap'>{stamp}</div>
  <div class='ev'><div class='lbl'>The Evidence</div><ul>{evidence}</ul></div>
  <div class='stake'><span>STAKE <b>{units}</b></span><span>¼-KELLY</span>
    <span>{r.get('sport','')}</span></div>
  {record}
  <div class='foot'><span>⚖ truebill · filed {ts.strftime('%-I:%M %p ET · %m/%d/%y')}</span>
    <span>model estimate · not advice · 21+</span></div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--redact", action="store_true", help="seal the charge (public teaser)")
    ap.add_argument("--story", action="store_true", help="1080×1920 story format")
    ap.add_argument("-o", "--out", default="verdict_card.html")
    args = ap.parse_args()
    path = config.OUTPUT_DIR / "latest.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    Path(args.out).write_text(build(data, args.redact, args.story))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
