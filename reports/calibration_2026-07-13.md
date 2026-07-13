# Calibration & MARKET_SHRINK drift — 2026-07-13

Walk-forward backtest per sport. Calibration metrics use the full recent game history; the shrink recommendation maximizes total units (ML+total+spread) on the matched closing lines and only fires when ≥ 150 matched bets exist (closing-line odds are current-season only, so out-of-season sports accumulate sample until they qualify).

| Sport | Szns | Games | Brier | favhit | tot_bias | tot_MAE | cur→rec shrink | matched | recommendation |
|---|---|---|---|---|---|---|---|---|---|
| MLB | 2023–2026 | 8413 | 0.247 | 0.5395 | -0.109 | 3.566 | 0.65 | 375 | hold 0.65 |
| WNBA | 2023–2026 | 846 | 0.2154 | 0.6608 | -0.32 | 13.445 | 0.5 | 104 | hold 0.5 (sample<150) |
| NBA | 2023–2026 | 4090 | 0.2066 | 0.6721 | -0.438 | 15.215 | 0.5→0.8 | 5404 | move 0.5 -> 0.8 |
| NFL | 2022–2025 | 974 | 0.2187 | 0.6437 | -0.26 | 10.51 | 0.5→0.8 | 1796 | move 0.5 -> 0.8 |
| NCAAF | 2022–2025 | 44 | 0.2514 | 0.5682 | 2.003 | 12.018 | 0.5 | 0 | hold 0.5 (sample<150) |
| NHL | 2022–2025 | 4532 | 0.241 | 0.5737 | -0.213 | 1.866 | 0.5→0.8 | 4479 | move 0.5 -> 0.8 |

## Drift alerts

- **NBA**: shrink 0.5 → **0.8** — total units -272.82 → -120.45 over 1675 bets (ML -13.09%→-13.33%, tot -0.34%→-3.65%).
- **NFL**: shrink 0.5 → **0.8** — total units -120.29 → -34.29 over 279 bets (ML -15.16%→-18.8%, tot -4.63%→-11.58%).
- **NHL**: shrink 0.5 → **0.8** — total units -169.88 → -40.44 over 549 bets (ML -10.66%→-16.88%, tot -0.82%→-1.79%).

## Per-sport shrink sweeps

### MLB (n=8413, matched=375)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | +28.12 | 375 | 23.72% | -7.02% | -3.51% |
| 0.5 | +28.85 | 357 | 24.14% | -5.89% | -5.86% |
| 0.65 | +29.02 | 336 | 27.14% | -10.1% | -6.03% |
| 0.8 | +20.59 | 271 | 22.77% | -10.73% | -5.38% |

### WNBA (n=846, matched=104)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | +3.24 | 104 | 7.27% | -0.26% | 0.75% |
| 0.5 | +3.48 | 99 | 11.41% | 0.08% | -4.66% |
| 0.65 | +4.72 | 90 | 14.76% | -7.89% | 2.33% |
| 0.8 | -1.18 | 70 | -1.1% | 1.27% | -6.2% |

### NBA (n=4090, matched=5404)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | -352.38 | 5404 | -14.71% | 0.08% | None% |
| 0.5 | -272.82 | 4629 | -13.09% | -0.34% | None% |
| 0.65 | -239.63 | 3574 | -13.44% | -1.83% | None% |
| 0.8 | -120.45 | 1675 | -13.33% | -3.65% | None% |

### NFL (n=974, matched=1796)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | -148.66 | 1796 | -15.34% | -3.51% | -5.44% |
| 0.5 | -120.29 | 1437 | -15.16% | -4.63% | -4.39% |
| 0.65 | -80.01 | 976 | -16.77% | -5.49% | -0.08% |
| 0.8 | -34.29 | 279 | -18.8% | -11.58% | -2.18% |

### NCAAF (n=44, matched=0)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | +0.0 | 0 | None% | None% | None% |
| 0.5 | +0.0 | 0 | None% | None% | None% |
| 0.65 | +0.0 | 0 | None% | None% | None% |
| 0.8 | +0.0 | 0 | None% | None% | None% |

### NHL (n=4532, matched=4479)
| shrink | total units | bets | ML roi | tot roi | spread roi |
|---|---|---|---|---|---|
| 0.3 | -238.9 | 4479 | -11.34% | -0.51% | None% |
| 0.5 | -169.88 | 3390 | -10.66% | -0.82% | None% |
| 0.65 | -107.04 | 2128 | -9.73% | -1.95% | None% |
| 0.8 | -40.44 | 549 | -16.88% | -1.79% | None% |
