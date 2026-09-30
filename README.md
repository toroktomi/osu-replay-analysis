# osu! Replay Analysis

Reconstructing per-note performance from osu!standard replay files, to tell a player
**what they are bad at** and **how they will do on a map they have never played**.

**[Try it live](https://osu-replay-analysis-eujut2agcapg6zphveuwnw.streamlit.app/)** —
drop in your own replays and their beatmaps.

## Results

| | |
|---|---|
| Hit detection re-simulated from raw replay inputs | **97.8% median** agreement with the score stored in the replay |
| Per-note dataset extracted | **5.8M hit objects**, 7,554 plays, 598 players, 4,753 beatmaps |
| Predicting a play's accuracy | **2.68 accuracy points MAE**, against 4.32 for the population average |
| Predicting *where* a player will miss | **0.843 AUC** per hit object |
| Personal data needed to profile a new player | **~3 replays**, worth 1-2 accuracy points |

The diagnosis output, for a real player in the dataset:

```
TAPPING CONSISTENCY by BPM        TIMING BIAS by BPM
  <130      13.60 ms  60th %ile     170-190   -6.33 ms  10th %ile
  170-190   13.19 ms  65th %ile
  190-210    7.81 ms  98th %ile
  210-240    7.08 ms  99th %ile
```

Near the best in the population above 190 BPM, average below 170, and consistently
**6 ms early** at 170–190 BPM — worse than 90% of players. That is a specific, fixable
weakness, which is what a single accuracy percentage can never give you.

## The core problem

An osu! replay (`.osr`) does not record which notes you hit or how accurately. It stores
only cursor positions, key states and four totals (300 / 100 / 50 / miss). Every per-note
judgement has to be re-derived by re-implementing osu!'s hit detection and replaying the
inputs against the beatmap.

That comes with a free correctness test: the replay header holds the true final counts, so
every reconstruction grades itself with no manual labelling.

## Goals

1. **Diagnosis** — find a player's weaknesses and express them as percentiles against a
   population, not raw numbers.
2. **Prediction** — from a player's recent replays, predict their accuracy on an unplayed
   map, and where in it they will struggle.

## Status

| Step | | Result |
|---|---|---|
| 1 | Profile the dataset | 381,570 replays; 82.9% of rows from players with 10+ replays, so per-player modelling is viable |
| 2 | One replay end to end | 99.6% match on the test replay |
| 3 | Validate across many replays, fix what breaks | 97.8% median match; three bugs found and fixed |
| 4 | Mod handling, validated per mod | HR flip and EZ/HR scaling verified; no mod catastrophically broken |
| 5 | Extract one row per hit object | 5.8M rows to parquet, streamed |
| 6 | Diagnosis with population percentiles | 7 metrics, ranked against 598 players |
| 7 | Prediction model and baselines | Beats all three baselines |
| 8 | App that takes replays and shows the diagnosis | Built (Streamlit) |

---

## How the simulation works

1. **Absolute times.** Replay frames store the time *since the previous frame*. A running
   sum converts them to timestamps on the same clock as the beatmap.
2. **Presses.** A press is a key *transition* — down in this frame, not down in the last.
   Holding a key across several frames is one press, not several.
3. **Windows and radius** from the map's difficulty settings:
   ```
   300 window  = 80  - 6*OD   ms
   100 window  = 140 - 8*OD   ms
   50 window   = 200 - 10*OD  ms   (also the miss boundary)
   radius      = 54.4 - 4.48*CS    osu!pixels
   ```
4. **Judging.** Walk the presses in time order with a single pointer to the earliest
   unresolved note:
   - notes whose 50-window has passed are misses
   - a press before the note's window opens resolves nothing
   - a press with the cursor outside the circle resolves nothing — the note stays pending
   - otherwise the timing error is judged against the three windows
5. **Compare** with the header: `match rate = sum(min(reconstructed, header)) / total`.

## Problems found and how they were solved

**Notelock.** A press resolves the *earliest* unresolved note, not the nearest. On a fast
stream several notes sit inside the timing window at once, and matching to the nearest
cascades wrongly through the rest of the map. Solved with a single forward-only pointer
instead of any search.

**A misplaced click is not a miss.** Pressing with the cursor outside the circle does
nothing at all. A note only becomes a miss when its window elapses unresolved.

**Every keyboard tap counted twice.** osu! records a keyboard press as keyboard *and* mouse
together — K1 always arrives with M1. Checking them separately doubled the press count.
Solved by treating each pair as one logical key, verified in the data: K1 never appears
without M1 in any frame.

**The negative first time delta.** Every replay's first frame carries a negative delta —
the lead-in before the first note, around 1.8 seconds. Nothing in the file says whether it
should be counted, and getting it wrong shifts the whole timeline so every note is missed.
Solved by simulating both ways and keeping whichever reproduces the header. Across 100
replays, **73% needed it kept and 27% needed it dropped**. This fix alone raised mean match
rate from 63.6% to 83.6%.

**Spinners.** A spinner is completed by spinning, never by a press, so it blocks every
later press until its window expires and cascades the rest of the map into misses. Solved
by removing spinners from press matching and crediting them as 300s.

**Truncated replays.** Some replays' frame data ends before the map does while still
claiming every note was hit. Solved by comparing the last frame time with the last note
time under both conventions and flagging replays whose frames never reach the end, rather
than letting them average in as near-total misses.

## Validation

400 non-fail replays with a natural mix of mods. 27 were flagged as truncated and
excluded, leaving 373:

| | mean | median | ≥ 95% match |
|---|---|---|---|
| **373 usable replays** | 90.95% | **97.76%** | 68% |

Broken down by the mods that affect hit detection:

| mod | n | mean | median | ≥ 95% |
|---|---|---|---|---|
| none | 234 | 92.42% | 98.32% | 71% |
| DT | 92 | 89.85% | 96.52% | 63% |
| HR | 28 | 86.32% | 96.48% | 61% |
| HR+DT | 7 | 88.66% | 87.88% | 14% |
| EZ | 7 | 87.02% | 99.48% | 86% |
| HT | 2 | 46.28% | 46.28% | 0% |

No mod is catastrophically broken, which is what this table exists to prove — a mistake in
the HardRock playfield flip or the CS scaling would show up here as a collapse, not a
2-point dip. HR and DT sit slightly below nomod. HR+DT is the worst cell and HT has a
sample of 2; both are open questions rather than established results.

By ratio of circles to sliders in the map:

| circles / objects | n | mean | median |
|---|---|---|---|
| < 50% | 60 | 88.47% | 95.21% |
| 50–65% | 155 | 92.62% | 97.51% |
| 65–80% | 123 | 91.07% | 98.61% |
| 80–100% | 35 | 87.36% | **99.05%** |

Medians climb steadily with circle ratio, so **the simplified slider handling does cost
accuracy** — about 4 points of median between the most slider-heavy and most circle-heavy
maps. See [What did not work](#what-did-not-work).

An earlier truncation check on a separate 100-replay sample flagged 7% of replays with
perfect separation: every flagged replay scored under 7% match, and no usable replay was
wrongly flagged.

**About 5% of replays still fail** despite full frame coverage, fitting neither time
convention. That is the main open problem in the simulation.

Match rate is then used as a **filter**, not just a score: only replays reconstructing at
≥95% produce per-note rows, so wrong judgements never reach a feature or a label.

## The per-note dataset

24 columns, one row per hit object: identity, position and timing, the geometry demanded
(gap since the previous note, cursor travel, corner angle, local BPM, reading time), the
slack allowed (three windows, hit radius), and the outcome (judgement, signed timing error,
cursor distance at the press, which key).

All times are divided by the clock rate, so a DT row is comparable with a nomod row —
otherwise the model learns "DT means bad timing" instead of learning difficulty.

Written streaming, in 50,000-row groups, so memory stays flat regardless of dataset size.

**Sampling matters more than it looks.** A first extraction walked the index in file order
and produced 1,289 players with one replay each — useless for anything per-player. The
converter now counts replays per player first, then samples players with 10+ replays and
takes up to a capped number each:

```
5,810,761 rows   7,554 plays   598 players   4,753 beatmaps   161 MB
replays per player:  median 13   (410 of 598 players have 10+)
```

Yield is about 65%: of 11,661 candidate replays, 7,554 reconstructed cleanly enough to use,
3,394 fell below 95% match and 681 were flagged as truncated.

## Diagnosis

Statistics, not machine learning. For a chosen player, errors are bucketed by context and
each bucket is ranked against every other player: tapping consistency and timing bias by
BPM, aim error as a fraction of circle radius and hit rate by jump distance, consistency by
reading time, K1/K2 balance and alternation rate, and stamina drift from the first third of
a map to the last.

A bucket is suppressed unless it has **40+ objects and 12+ players**, otherwise the
percentile is noise.

Per-player spread in timing consistency runs from roughly 7 ms to 22 ms across the
population, so there is real signal to rank against.

## Prediction

LightGBM multiclass over per-note features plus five per-player skill features, predicting
P(300/100/50/miss), then summing expected values into a predicted accuracy.

Two rules, both enforced in code:

- **Split by beatmap**, never by random object. Objects from the same play on both sides of
  the split would leak the answer. Train/test beatmap overlap is asserted to be 0.
- **Player skill features are computed on the training split only**, or the model reads a
  player's test performance out of their own features.

Trained on **5.8M hit objects** from 7,554 plays, 598 players and 4,753 beatmaps.

### Against baselines

A model without baselines means nothing. In increasing order of difficulty:

| | MAE | median | bias |
|---|---|---|---|
| Global mean accuracy | 4.315 | 3.408 | +0.498 |
| Player's own historical mean | 3.568 | 2.289 | +0.176 |
| LightGBM on play-level summaries | 2.988 | 1.778 | +0.454 |
| **Per-object model** | **2.676** | **1.582** | +0.323 |

Per-object miss AUC **0.8431**, per-object accuracy 0.9119.

Accuracy on the same map varies 1–3% run to run, so this is close to the irreducible noise
floor. A bigger model is not the useful next step — and neither is more data, which is
measured below.

### What the model actually learns

Pattern difficulty and player skill are **strongly complementary** — neither alone gets
within one accuracy point of the pair:

| features | MAE | miss AUC |
|---|---|---|
| Pattern geometry only | 4.062 | **0.7950** |
| Player skill only | **3.842** | 0.6893 |
| Both | **2.676** | **0.8431** |

The split held in the same direction across three differently-sampled datasets. Geometry
alone has the better **miss AUC** — it knows *where* in a map errors happen. Skill alone has
the better **MAE** — it knows *how many* there will be. Skill sets the level; geometry
distributes it.

### More data does not help — profile coverage does

Three datasets were extracted with different sampling shapes:

| | objects | players | beatmaps | median replays/player |
|---|---|---|---|---|
| deep | 2.1M | 200 | 2,018 | 14 |
| wide | 4.2M | 597 | 3,743 | 10 |
| full | 5.8M | 598 | 4,753 | 13 |

Comparing their reported MAE would be meaningless, because each has its own test population
and a dataset with more thinly-profiled players is simply harder to predict. So the
comparison was done properly instead: one split of the largest dataset, two models trained
on different amounts of *the same* training data, both scored on **the same test set**.

| training data | rows | beatmaps | MAE | miss AUC |
|---|---|---|---|---|
| all 597 players | 4,365,159 | 3,564 | 2.238 | 0.8448 |
| 200 deep players | 2,093,975 | 1,904 | 2.252 | 0.8441 |

**0.015 accuracy points.** Doubling the training data and the map count bought nothing.

Scored on the *unrestricted* test set the gap looked like 1.10 points — but the smaller
model had profiles for only 40% of test players, and the rest fell back to population
averages. Once every test player has a profile under both models, the difference vanishes.
The apparent benefit of more data was entirely profile coverage.

This also disproves the obvious explanation for the cold-start weakness below: going from
2,018 to 4,753 beatmaps did not make the model better at predicting a player it knows
nothing about.

### How much personal data a new player needs

150 players to train, 50 held out entirely. Skill features for the strangers are built from
their first N replays; every row below is scored on the **same 212 plays**, so only the
personal information changes.

| profile replays | MAE (full) | MAE (deep) |
|---|---|---|
| none (population average) | 4.244 | 4.654 |
| 1 | 3.351 | 3.244 |
| 3 | **2.812** | 2.785 |
| 5 | 2.828 | 2.572 |
| 10 | 2.831 | **2.341** |
| *global mean, no model* | *3.704* | *4.502* |

**About three replays is enough** — the curve is flat from there on the larger dataset.
Nobody has to grind data for this, since osu!stable already saves an `.osr` for every play
ever made.

Run on two datasets the effect measures **1.4 and 2.3 accuracy points**, on ~200 test plays
each. The honest statement is that personalisation is worth somewhere between one and two
accuracy points; the spread between measurements is wider than the precision of either, so a
single figure would be false confidence.

## What did not work

**Full slider simulation — removed, but the question is still open.** Sliders are judged on
their head like a circle, with the rest granted. A complete implementation of osu!stable's
slider scoring — head, follow circle, tick and tail points, verdict from the fraction
collected — was built and measured against that approximation over 10 replays. It was
better on **none**, worse on 6. Three follow-break rules were tried; requiring continuous
following beat sampling each tick independently, so stable does punish releasing
mid-slider, but even the best variant lost, so the code was removed.

Re-tested on 276 replays after validation showed match rate climbing with circle ratio,
the result was worse still — and informatively so:

| circles / objects | n | head only | with sliders | delta |
|---|---|---|---|---|
| < 50% | 43 | 95.27% | 87.35% | **−7.92%** |
| 50–65% | 121 | 97.19% | 91.00% | −6.19% |
| 65–80% | 87 | 98.61% | 95.07% | −3.54% |
| 80–100% | 25 | 98.77% | 97.79% | −0.97% |

Better on 66 replays, worse on 199. The harm scales with how much slider logic actually
runs, which is the signature of a wrong model rather than a missing one — most likely the
requirement that a key stay held across every frame between tick points, which punishes
players who alternate through a slider.

So two things are true at once. Slider-heavy maps do reconstruct worse, by about 3.5 points
of median, so the approximation costs something real. And the fraction-of-tick-points model,
tested at two scales, is not the explanation. Head-only stays, and the cause of the
slider-heavy gap is an open question.

**An expectation that did not replicate.** Early on I expected difficulty modelling to
dominate and personalisation to be a small adjustment worth around one accuracy point.
Feature importances suggested otherwise, so rather than argue from them I ran the holdout
experiment above. It measured 2.3 points, and the ablation showed the two halves are
roughly equal. Adding four more difficulty features — cursor velocity, local note density,
recent aim load, position in the map — improved overall MAE from 2.46 to 2.35 but did not
shift that balance.

## Limitations

- osu!standard only, judged by osu!stable's rules.
- **Cold start.** With no replays at all from a player the model is no better than guessing
  the global average — 4.24 MAE against 3.70 for the constant, so slightly worse. Map
  breadth was the obvious suspect and was tested directly: 2.4x more beatmaps changed
  nothing. The cause is unresolved. In practice the case does not arise, since a real user
  always arrives with their own replays, but it does mean this is a personalisation model
  that uses pattern context rather than a difficulty model with a personal adjustment.
- About 5% of replays cannot be reconstructed at all and are excluded.
- Slider-heavy maps reconstruct about 3.5 points worse by median than circle-heavy ones. The
  cause is unidentified — a full slider-tick simulation was tested twice and made it worse,
  not better.
- Replays in the dataset were submitted to a replay-rendering service, so they skew toward
  plays people wanted to show off. Not a representative sample of normal play.
- 13.7% of dates in the index are unparseable, so "first N replays" above is a stable
  arbitrary order, not chronological. It answers how *many* replays are needed, not how a
  player changes over time.

## Code

```
code/
  osu_rules.py                      osu! constants and formulas: windows, radius,
                                    approach-rate preempt, clock rate, key and mod bits
  osu_beatmap_and_replay_input.py   loading .osr/.osu, frame times, press detection
  osu_simulation.py                 hit detection, time-origin handling, truncation check
  parquet_converter.py              per-note extraction, streamed to parquet
  osu_diagnosis.py                  bucketed metrics and population percentiles
  osu_prediction.py                 LightGBM model, baselines, ablation
  osu_personalisation.py            held-out-player experiment
  build_baseline.py                 saves the population baseline and trained model
  app_logic.py                      upload pairing, analysis and ranking for the app
app.py                              Streamlit interface
```

`simulate_replay(replay, beatmap)` returns the reconstructed counts, the header counts, the
match rate, which time convention won, frame coverage, the truncation flag, and a per-note
verdict with its signed timing error.

## Running it

```
pip install -r requirements.txt
```

Data comes from the Kaggle dataset
[`skihikingkevin/ordr-replay-dump`](https://www.kaggle.com/datasets/skihikingkevin/ordr-replay-dump)
and is not included here. Expected layout:

```
index.csv
replays/osr/<replayHash>.osr
beatmaps/<beatmapHash>.osu
test.osr, test.osu            one replay and its beatmap, for quick checks
```

```
python code/osu_simulation.py                       reconstruct the test replay
python code/parquet_converter.py 600 out.parquet --cap=25    extract by player
python code/osu_diagnosis.py "<player name>"        diagnosis report
python code/osu_prediction.py                       train, baselines, ablation
python code/osu_personalisation.py                  held-out-player experiment
python code/build_baseline.py                       baseline + model for the app
python -m streamlit run app.py                      the app
```

## The app

**Live at
[osu-replay-analysis.streamlit.app](https://osu-replay-analysis-eujut2agcapg6zphveuwnw.streamlit.app/)**

A Streamlit page that takes a pile of `.osr` and `.osu` files, pairs each replay to its
beatmap by the MD5 stored in the replay, re-simulates every one, and shows:

- **how well each replay reconstructed**, so nothing is hidden — anything under 95% is
  excluded from the profile with the reason shown
- **weakest areas first**, ranked by percentile against the population
- **strengths** above the 75th percentile
- **predicted accuracy** next to what was actually scored

Percentiles come from a precomputed 51 KB baseline rather than the full dataset, so the app
starts instantly. Upload 15–20 replays rather than one: a single play leaves most metric
buckets under the 40-object minimum, and a profile built from one replay predicts far worse
than one built from ten.
