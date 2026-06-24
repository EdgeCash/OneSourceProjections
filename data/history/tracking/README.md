# Projection tracking history

Append-only JSONL stores written by `project547/tracking.py` (via the hourly
job, `scripts/hourly_update.py`):

- `projections.jsonl` — one pre-game snapshot per (date, event, source), where
  source ∈ {model, market, bettingpros, blend}. The de-vigged market and
  BettingPros baselines are logged for every sport, modeled or not.
- `outcomes.jsonl` — one final per (date, event), recorded once games finish.

`tracking.summary()` joins them into per-source accuracy / Brier / lift-over-
market — surfaced on the **Ledger** tab. This is how we prove our model beats
the baseline, across every sport, from day one.

Backfill or run by hand: `PYTHONPATH=. python scripts/track.py all [YYYY-MM-DD]`.
