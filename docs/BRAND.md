# 360Five — brand & voice

The one true source for EdgeCash's sports-projection work. This file is the
brand bible: the name, the story, the voice, and the rules. When in doubt about
tone or copy anywhere in the product, defer to this. For the *visual* system
(tokens, type, components, the Edge Card anatomy), see `docs/DESIGN_SPEC.md`.

## The name

**360Five.**

Three meanings that resolve into one equation:

- **360°** — every card is a full-circle research sheet. Nothing left to look up
  elsewhere.
- **Five** — the **5 W's** every wager raises: **Who, What, When, Where, Why.**
- **360 + 5 = 365** — we cover sports **every day the model runs.** The model
  never rests.

That equation *is* the brand: **360° of research + the 5 W's = 365.**

**Hierarchy.** *360Five* is the **product brand** — every surface a person sees
(the app, the cards, briefs, reports). *EdgeCash* is the **house/studio** behind
it. **54.7** is retired as a public name but kept as the **methodology number**
— the win rate that separates break-even from professional (see Brand pillars),
and it lives on inside the confidence system. The Python package stays
`project547`; the GitHub repo stays `OneSourceProjections` to preserve history.

## The card is the brand

360Five is not a dashboard or a feed. It is a single research sheet — the
**Edge Card** — that answers every question a bet raises, in the order a sharp
actually asks them. The card reads in **three acts**:

1. **The Answer** — the bet ticket. Side, price, book, ¼-Kelly stake, grade.
   A two-second read for anyone who just wants the play.
2. **The Receipts** — calibration (predicted vs. actual), EV, the closing-line
   record on *this* market, and a stress test on the biggest single lever.
   Proof the number is earned, not asserted.
3. **The Proof** — the matchup: teams, conditions, clickable lineups → props,
   and the curated stat tables. You came for analysis, not just a pick, so we
   never hide the work.

A scanner stops after act 1. A skeptic reads act 2. A grinder studies act 3.
Nobody is forced through the data, but it is never hidden. **Lead with the
play. Back it with everything.**

## Tagline

> **360° of research. 5 questions. 365 days.**

Short forms: *"Every angle. Every day."* · *"Beat the number."* ·
*"52.4% pays the house. 54.7% pays you."*

## What we are

A straight shooter. A math rebel. We hand you **data, projections, and tools**
plus a little wagering advice — and the choice is always yours. We are the
opposite of a tout.

## Persona — the Anti-Guru Math Rebel

**Josh Pate, not Colin Cowherd.**

That contrast *is* the brand. One analyst holds his opinions loosely, shows his
reasoning, and tells you how confident he is — so when he misses, you shrug; he
never sold you certainty. The other trades in hot takes and absolute calls. We
are unapologetically the first guy.

- **Anti-guru.** No rooftop promises, no "lock of the week," no personality cult.
  The math is the star, not a face. We'd rather be *useful* than *loud*.
- **Holds opinions loosely.** Every take ships with its confidence and the number
  behind it. "Model likes this, here's how much, your call" — never "trust me."
- **Math rebel.** We rebel against the tout-industrial complex: we publish our
  losses, we tell you when to *pass*, and we hand you the tools to disagree with
  us (Edge Builder). Transparency is the rebellion.
- **Earns trust on a losing week.** The whole point. A guru is only as good as
  his last pick; we're as good as our *process*, shown in the open.

If a line of copy would sound at home from a screaming tout, cut it. If it sounds
like a sharp friend showing you the spreadsheet, ship it.

## Brand pillars

1. **Receipts over promises.** Every projection is graded and the record is
   public — wins *and* losses. CLV and Brier, not "lock of the century." This is
   why the stat tables stay on the card: cutting the proof would make us the
   thing we're defined against.
2. **The number is the pitch.** We sell edges, probabilities, and closing-line
   value. No hype, no screaming from rooftops.
3. **Tools, not tips.** Projections, a Build-Your-Own-Edge lab, AI breakdowns
   with conservative/standard/aggressive postures. You pull the trigger.
4. **Respect the math.** No bankroll fantasies. Units, EV, variance, the long
   run. Quarter-Kelly, not "bet your mortgage." Break-even at −110 is 52.4%; a
   professional lives around **54.7%** — that band is where our headline plays sit.
5. **Show the work.** Open methodology (`docs/`), honest validation logs that
   include the things that *didn't* work (see `docs/ACCURACY_ROADMAP.md`).

## The 5 W's — what every card must answer

| W | The question | On the card |
|---|---|---|
| **Who** | is playing, and pitching? | teams, records, form; confirmed starters; clickable lineups → props; injuries |
| **What** | exactly do I bet? | side + number, best price + book, ¼-Kelly stake (units + $), grade |
| **When** | and is it still live? | first pitch; lineup-lock timestamp; line movement; days rest |
| **Where** | do conditions tilt it? | venue + park factor; weather & wind; plate-ump tendency; home/away |
| **Why** | does the edge exist? | model % vs de-vigged fair %; EV; calibration; curated matchup stats; our CLV record here |

If a reader can't answer all five from the graphic alone, the card isn't done.

## Voice & tone

- **Plain and numeric.** Lead with the number that justifies the take.
- **Calm, dry, a little contrarian.** Confidence from evidence, not volume.
- **Honest about uncertainty.** Name the risk, the sample size, the stale line.
  A *huge* edge is a warning to VERIFY, not a lock to chase.
- **Lab notebook, not sales floor.** "Here's what the model says and how sure
  it is," never "trust me."

### Say this
- "Model 55%, market implies 51%. Small edge, quarter-Kelly."
- "Passed the slate — nothing cleared the bar today."
- "We were wrong on this one; here's the graded result."

### Never say this
- "Lock," "guaranteed," "can't-miss," "easy money."
- "Turn $100 into $10,000." Any bankroll promise.
- Hiding or quietly deleting losing picks.
- Hype emoji walls. Stars-for-hype are banned; advantage is a measured edge.

## Visual cues (summary — full system in `docs/DESIGN_SPEC.md`)
- **Mark:** the 360Five seal — a 360° ring around a **5**.
- **Ground:** petrol ink `#0a1216` (dark, go-live) / almanac paper `#ece5d5` (light).
- **Accent:** brass `#d3ac57` — the *only* decorative accent, so green (`#4cc07e`,
  edge) and red (`#e46a60`, fade) mean exactly one thing each.
- **Type:** Oswald display · DM Sans body · JetBrains Mono for every number
  (the "precision instrument" read), always tabular.

## Legal footer (use everywhere bets are shown)
> _360Five — model estimates, not financial advice. Personal research.
> Bet responsibly._
