# Calibration & MARKET_SHRINK drift — 2026-07-06

Walk-forward backtest per sport. Calibration metrics use the full recent game history; the shrink recommendation maximizes total units (ML+total+spread) on the matched closing lines and only fires when ≥ 150 matched bets exist (closing-line odds are current-season only, so out-of-season sports accumulate sample until they qualify).

| Sport | Szns | Games | Brier | favhit | tot_bias | tot_MAE | cur→rec shrink | matched | recommendation |
|---|---|---|---|---|---|---|---|---|---|
| MLB | 2023–2026 | 8443 | 0.2479 | 0.538 | -0.109 | 3.566 | 0.65→0.8 | 419 | move 0.65 -> 0.8 |
| WNBA | 2023–2026 | 846 | 0.2154 | 0.6608 | -0.32 | 13.445 | 0.5 | 104 | hold 0.5 (sample<150) |
| NBA | 2023–2026 | 4092 | 0.2071 | 0.672 | -0.438 | 15.215 | 0.5→0.8 | 5485 | move 0.5 -> 0.8 |
| NFL | 2022–2025 | 976 | 0.2183 | 0.6465 | -0.26 | 10.51 | 0.5→0.8 | 1816 | move 0.5 -> 0.8 |
| NCAAF | 2022–2025 | 44 | 0.2518 | 0.5682 | 2.003 | 12.018 | 0.5 | 0 | hold 0.5 (sample<150) |
| NHL | 2022–2025 | 4532 | 0.2413 | 0.5724 | -0.213 | 1.866 | 0.5→0.8 | 4856 | move 0.5 -> 0.8 |

## Drift alerts

- **MLB**: shrink 0.65 → **0.8** — total units +53.97 → +65.29 over 377 bets (ML 13.82%→16.82%, tot -1.87%→1.72%).
- **NBA**: shrink 0.5 → **0.8** — total units -383.01 → -307.16 over 2323 bets (ML -16.99%→-21.29%, tot -0.34%→-3.65%).
- **NFL**: shrink 0.5 → **0.8** — total units -124.16 → -43.23 over 425 bets (ML -15.1%→-16.07%, tot -4.24%→-9.81%).
- **NHL**: shrink 0.5 → **0.8** — total units -203.54 → -44.27 over 1010 bets (ML -10.08%→-10.51%, tot -1.89%→0.29%).

## Per-sport shrink sweeps

### MLB (n=8443, matched=419)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | +44.53 | 410 | 9.57% | 0.28% | 21.08% |
| 0.5 | +49.82 | 419 | 12.08% | -1.12% | 20.82% |
| 0.65 | +53.97 | 411 | 13.82% | -1.87% | 22.18% |
| 0.8 | +65.29 | 377 | 16.82% | 1.72% | 27.28% |

### WNBA (n=846, matched=104)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | +0.08 | 104 | 4.78% | -6.01% | -1.03% |
| 0.5 | +5.07 | 98 | 16.84% | -2.4% | -3.91% |
| 0.65 | +2.65 | 93 | 15.79% | -14.01% | -0.59% |
| 0.8 | -0.99 | 77 | 6.46% | -11.77% | -4.62% |

### NBA (n=4092, matched=5485)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | -376.47 | 5485 | -15.19% | 0.08% | None% |
| 0.5 | -383.01 | 4814 | -16.99% | -0.34% | None% |
| 0.65 | -366.57 | 3918 | -17.81% | -1.83% | None% |
| 0.8 | -307.16 | 2323 | -21.29% | -3.65% | None% |

### NFL (n=976, matched=1816)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | -135.56 | 1816 | -13.85% | -3.65% | -4.23% |
| 0.5 | -124.16 | 1480 | -15.1% | -4.24% | -4.22% |
| 0.65 | -102.22 | 1084 | -16.21% | -6.28% | -1.97% |
| 0.8 | -43.23 | 425 | -16.07% | -9.81% | 10.8% |

### NCAAF (n=44, matched=0)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | +0.0 | 0 | None% | None% | None% |
| 0.5 | +0.0 | 0 | None% | None% | None% |
| 0.65 | +0.0 | 0 | None% | None% | None% |
| 0.8 | +0.0 | 0 | None% | None% | None% |

### NHL (n=4532, matched=4856)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | -298.76 | 4856 | -11.0% | -2.61% | None% |
| 0.5 | -203.54 | 3915 | -10.08% | -1.89% | None% |
| 0.65 | -132.38 | 2756 | -11.89% | -0.21% | None% |
| 0.8 | -44.27 | 1010 | -10.51% | 0.29% | None% |
