# osu! Replay Analysis

Re-simulates osu!standard replays note by note, then uses the result to show a player
what they are weak at and to predict their accuracy on maps they have not played.

**Live app:** [osu-replay-analysis.streamlit.app](https://osu-replay-analysis-eujut2agcapg6zphveuwnw.streamlit.app/)
(free hosting: if it shows a "Zzzz" page, click the button and wait about 30 seconds)

## Results

| | |
|---|---|
| Re-simulated hit judgements vs. the counts stored in the replay | **97.8% median agreement** over 373 replays (mean 91.0%) |
| Per-note dataset | **5.8M hit objects** from 7,554 plays, 598 players, 4,753 beatmaps |
| Accuracy prediction on unplayed maps (mean absolute error) | **2.68 points**, vs. 3.57 using the player's own average and 4.32 using the global average |
| Predicting which notes will be missed | **0.843 AUC** |

## Using the app

1. Upload **replays (`.osr`) together with the beatmap (`.osu`) each one was played on**.
   The app pairs them automatically using the beatmap hash stored in the replay, and
   lists any replay whose beatmap is missing.
   - Replays: press F2 on the results screen in osu!stable to export one to `osu!/Replays`.
   - Beatmaps: the `.osu` file for the difficulty you played, in `osu!/Songs/<map folder>/`.
2. **How many:** upload **at least 10, ideally 15–20, on different maps.** Each skill measure
   is split into bands (for example BPM ranges), and a band is only ranked once your uploads
   contain 40+ notes in it. A single replay usually fills only a few bands. For the accuracy
   prediction alone, about 3 replays already give most of the benefit (see below).
3. The app re-simulates every replay and shows how well each one reconstructed. Replays
   below 95% agreement, or whose input data stops before the map ends, are left out of
   your profile and the reason is shown.
4. You get:
   - **Weakest areas** (below the 40th percentile) and **strengths** (75th and above),
     ranked against the 598 players in the dataset, on 8 measures:
     tapping consistency by BPM, timing bias by BPM, aim error by jump distance,
     hit rate by jump distance, tapping consistency by reading time (approach rate),
     alternation rate, K1/K2 balance and stamina drift (first third vs. last third of a map).
   - **Predicted vs. actual accuracy** for each uploaded replay. Your profile is built from
     the same replays, so this is a sanity check, not a test on unseen maps. The 2.68
     figure above comes from the offline evaluation, where the test maps were never seen.

Percentiles come from a precomputed population summary (`data/baseline.json`) and the
trained model (`data/model.txt`), so the app does not need the full dataset.

## How the re-simulation works

An `.osr` replay does not store which notes were hit. It only stores cursor positions,
key states over time and four totals (300 / 100 / 50 / miss). The per-note results have to
be rebuilt by replaying the inputs against the beatmap with osu!stable's rules. The four
stored totals then act as a free check of every reconstruction.

1. Frame times are stored as deltas; a running sum turns them into timestamps.
2. A press is a key going from up to down. K1 and M1 always arrive together, so each pair
   counts as one key.
3. Hit windows and circle radius come from the map's OD and CS, with mods applied
   (Hard Rock and Easy scaling, Hard Rock playfield flip, DT/HT clock rate).
4. Presses are matched to notes with one pointer to the earliest unresolved note
   (osu!'s "notelock"). A press outside the circle does nothing; a note becomes a miss only
   when its 50-window has passed.
5. Spinners are excluded from press matching and counted as 300s. Sliders are judged on
   their head only.

**Problems found while validating:**
- The first frame carries a negative time delta (the lead-in), and nothing in the file
  says whether to count it. The code simulates both options and keeps the one that matches
  the stored totals. On 100 replays, 73% needed it kept and 27% dropped; handling it raised
  the mean match from 63.6% to 83.6%.
- Some replays' input data ends before the map does. These are flagged as truncated
  and excluded instead of counting as near-total misses.
- A full slider simulation (follow circle, ticks, tail) was built and tested on 276 replays.
  It made agreement worse on 199 and better on 66, so it was removed. Slider-heavy maps still
  reconstruct about 3.5 points worse (median) than circle-heavy ones; the cause is not known.

**By mod** (373 usable replays):

| mod | n | median match |
|---|---|---|
| none | 234 | 98.3% |
| DT | 92 | 96.5% |
| HR | 28 | 96.5% |
| HR+DT | 7 | 87.9% |
| EZ | 7 | 99.5% |
| HT | 2 | too few to judge |

## Dataset

Replays and beatmaps come from the Kaggle dataset
[`skihikingkevin/ordr-replay-dump`](https://www.kaggle.com/datasets/skihikingkevin/ordr-replay-dump).
Only replays that reconstruct at 95% or better produce rows, so wrong judgements do not
reach the features or labels.

Each row is one hit object (24 columns): timing and position, the pattern around it (gap to
the previous note, jump distance, angle, local BPM, reading time), the allowed slack (hit
windows, radius) and the outcome (judgement, timing error, cursor distance, key used).
Times are divided by the clock rate so DT plays are comparable with nomod ones.

Players are sampled so that each has 10+ replays (median 13), because the per-player skill
features need several plays. About 65% of candidate replays were usable.

## Prediction model

LightGBM multiclass model predicting P(300 / 100 / 50 / miss) for each note, from 16
pattern features and 5 player-skill features (timing spread, timing bias, aim error,
miss rate, mean absolute timing error). A play's predicted accuracy is the expected value
over its notes.

- **Split by beatmap:** 25% of beatmaps are held out, so no map appears in both training
  and test data (the script prints the overlap, which is 0).
- **Skill features use training plays only**, so a player's test plays never leak into
  their profile.

| method | MAE (accuracy points) |
|---|---|
| Global average accuracy | 4.32 |
| Player's own average accuracy | 3.57 |
| LightGBM on play-level summaries | 2.99 |
| **Per-note model** | **2.68** |

**Ablation:** pattern features alone reach 4.06 MAE (0.795 miss AUC), skill features alone
3.84 MAE (0.689 AUC), both together 2.68 MAE (0.843 AUC). The pattern features mostly decide
*where* misses happen; the skill features mostly decide *how many*.

**How much data a new player needs:** in a separate experiment, 50 players were held out
completely and profiled from their first N replays. With no replays the model is no better
than the global average; 1 replay brings MAE to about 3.35 and 3 replays to about 2.8.
Beyond that the gain was small on one dataset and continued down to 2.34 at 10 replays
on another.

**More data did not help:** training on 4.4M rows from 597 players vs. 2.1M rows from 200
players gave almost the same error on the same test set (2.238 vs. 2.252 MAE) once every
test player had a profile. (These MAEs differ from the 2.68 above because they use a
different split.)

## Limitations

- osu!standard only, with osu!stable rules. Sliders are judged on their head.
- About 5% of replays cannot be reconstructed with either time convention.
- The dataset comes from a replay-rendering service, so it leans toward plays people
  wanted to show off.
- With no replays from a player, predictions are no better than the global average.

## Code

```
code/
  osu_rules.py                      constants and formulas: hit windows, radius, AR, mods
  osu_beatmap_and_replay_input.py   loading .osr/.osu files, frame times, key presses
  osu_simulation.py                 hit detection, lead-in handling, truncation check
  parquet_converter.py              per-note extraction to Parquet, sampled by player
  osu_diagnosis.py                  skill measures and population percentiles
  osu_prediction.py                 LightGBM model, baselines, ablation
  osu_personalisation.py            held-out player experiment
  build_baseline.py                 builds data/baseline.json and data/model.txt
  app_logic.py                      upload pairing, analysis and ranking for the app
app.py                              Streamlit interface
```

## Running it locally

```
pip install -r requirements.txt
python -m streamlit run app.py
```

To rebuild the dataset and model, download the Kaggle dataset (layout: `index.csv`,
`replays/osr/<hash>.osr`, `beatmaps/<hash>.osu`), then:

```
python code/parquet_converter.py 600 out.parquet --cap=25    extract per-note rows
python code/osu_prediction.py                               train, baselines, ablation
python code/osu_personalisation.py                          held-out player experiment
python code/build_baseline.py                               baseline and model for the app
```
