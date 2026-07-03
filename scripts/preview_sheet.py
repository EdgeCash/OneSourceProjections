"""Render a representative MLB Sharp Sheet to a standalone dark HTML file so we
can eyeball readability/contrast outside Streamlit. Not shipped — a dev aid."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ui  # noqa: E402

# dark palette :root (mirror app/dashboard._PALETTES["dark"])
p = dict(acc="#3b82f6", acc2="#6cb6ff", link="#6cb6ff", warn="#e3b341",
         bg="#0d1117", card="#161b22", card2="#1c2431", line="#2a3441",
         text="#e8eef5", muted="#9aa7b4", faint="#828f9e", good="#3fb950",
         neg="#f0776a", mid="#e3b341", sb1="#111722", sb2="#0a0e15",
         glow="0.16", shadow="0.55")
root = (":root{" + "".join(f"--{k}:{v};" for k, v in p.items())
        + "--disp:'Oswald',system-ui,sans-serif;--font:'DM Sans',system-ui,sans-serif;}")

g = {
    "away_team": "Los Angeles Dodgers", "home_team": "San Francisco Giants",
    "away_pitcher": "Yoshinobu Yamamoto", "home_pitcher": "Logan Webb",
    "away_pitcher_id": 808967, "home_pitcher_id": 657277,
    "game_time": "2026-07-03T22:15:00Z", "venue": "Oracle Park",
    "away_exp_runs": 4.7, "home_exp_runs": 3.9, "proj_total": 8.6,
    "home_win_prob": 0.44, "away_record": "58-29", "home_record": "45-42",
    "over_probs": {7.5: 0.61, 8.5: 0.52, 9.5: 0.40},
    "weather": {"temp": 61, "wind_mph": 12, "wind_dir_cf": -1},
    "moneyline": {"home": 128, "away": -152},
    "total_line": 8.5, "over_price": -108, "under_price": -112,
    "run_line": -1.5, "run_line_price": 135,
}
matchup = {"n_teams": 30, "window_label": "L15",
           "venue": "Oracle Park", "away_bullpen": {"level": "rested"},
           "home_bullpen": {"level": "moderate"}}
data = {
    "pitching": {
        "away_sp": {"name": "Yoshinobu Yamamoto", "hand": "R", "ip": 6.1,
                    "k9": 10.2, "bb9": 2.1, "xfip": 3.05, "tto_flag": True},
        "home_sp": {"name": "Logan Webb", "hand": "R", "ip": 6.4,
                    "k9": 8.4, "bb9": 1.7, "xfip": 3.31, "tto_flag": True},
    },
    "context": {"park_factor": 0.92,
                "umpire": {"name": "Angel Hernandez", "k_index": 1.04,
                           "runs_index": 0.97, "games": 210}},
    "uncertainty": {"total": [6.9, 8.6, 10.4]},
    "clv": {"moneyline": {"avg_clv": 1.8, "clv_n": 42},
            "total": {"avg_clv": None, "clv_n": 0}},
    "calibration": {"moneyline": {"pred": 0.55, "actual": 0.53, "n": 120}},
}

html = ui.sharp_sheet_html("MLB", g, matchup, data=data, min_edge=0.02, bankroll=1000)
out = Path(__file__).resolve().parent.parent / "scratch_sheet.html"
out.write_text(f"<!doctype html><html><head><meta charset='utf-8'>"
               f"<style>{root} body{{background:{p['bg']};margin:0;padding:24px;"
               f"color:var(--text);font-family:var(--font);}}</style>"
               f"</head><body>{html}</body></html>")
print(out)
