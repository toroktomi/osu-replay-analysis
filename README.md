# osu! Replay Analysis

Reconstructing per-note performance from osu!standard replay files, to tell a player
**what they are bad at** and **how they will do on a map they have never played**.

## The core problem

An osu! replay (`.osr`) does not record which notes you hit or how accurately. It
stores only cursor positions, key states and four total counts (300 / 100 / 50 / miss).
Every per-note judgement has to be re-derived by re-implementing osu!'s hit detection
and replaying the inputs against the beatmap.

That comes with a free correctness test: the replay header stores the true final
counts, so every reconstruction can be checked against them with no manual labelling.

## Goals

1. **Diagnosis** — find a player's weaknesses (tapping consistency by BPM, timing bias,
   aim error by jump distance, stamina) and express them as percentiles against the
   population.
2. **Prediction** — from a player's recent replays, predict their accuracy on an unplayed
   map, with a section-by-section breakdown of where they will struggle.

## Status

| Step | | Result |
|---|---|---|
| 1 | Profile the dataset | Done — 381,570 replays; 82.9% of rows come from players with 10+ replays, so per-player modelling is viable |
| 2 | One replay end to end | Done — 99.64% match on the test replay |
| 3 | Validate across many replays, fix what breaks | Done — see [Validation](#validation) |
| 4 | Mod handling checked by mod | Partly — HR/EZ go through the beatmap library's mod-aware API |
| 5 | Extract one row per hit object to parquet | Next |
| 6 | Diagnosis report with population percentiles | Planned |
| 7 | Accuracy prediction model and baselines | Planned |
| 8 | Small app that takes a replay and shows the diagnosis | Planned |

## How the simulation works

1. **Absolute times.** Replay frames store the time *since the previous frame*. A running
   sum converts them to timestamps on the same clock as the beatmap.
2. **Presses.** A press is a key *transition* — down in this frame, not down in the last.
   Holding a key for several frames is one press, not several.
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
5. **Compare** the reconstructed counts with the header:
   `match rate = sum(min(reconstructed, header)) / total`.

## Problems found and how they were solved

**Notelock.** A press resolves the *earliest* unresolved note, not the nearest one. On a
fast stream several notes are inside the timing window at once, and matching to the
nearest cascades wrongly through the rest of the map. Solved by a single forward-only
pointer instead of any search.

**A misplaced click is not a miss.** Pressing with the cursor outside the circle does
nothing. A note only becomes a miss when its window runs out unresolved.

**Every keyboard tap counted twice.** osu! records a keyboard key press as keyboard *and*
mouse button together (K1 always arrives with M1). Checking them separately doubled the
press count. Solved by treating each pair as one logical key. Verified in the data: K1
never appears without M1.

**The negative first time delta.** Every replay's first frame has a negative delta — the
lead-in before the first note, roughly 1.8 seconds. Nothing in the file says whether it
should be counted, and getting it wrong shifts the entire timeline so that every note is
missed. Solved by simulating both ways and keeping whichever reproduces the header.
Across 100 replays, 73% needed it kept and 27% needed it dropped. This fix alone raised
mean match rate from 63.6% to 83.6%.

**Spinners.** A spinner is completed by spinning, never by a press, so it blocks every
later press until its window expires. Solved by removing spinners from press matching and
crediting them as 300s.

**Truncated replays.** Some replays' frame data ends before the map does. Solved by
comparing the last frame time with the last note time under both conventions and flagging
replays whose frames never reach the end, instead of letting them average in as near-total
misses. 7% of replays were flagged, with no false positives.

**Sliders.** Sliders are judged on their head like a circle, with the rest granted. A full
simulation of slider ticks and tails — head, follow circle, fraction of points collected —
was implemented and measured against this approximation on 10 replays. It was better on
none, worse on 6, and worse in every band of circle-to-slider ratio, so it was removed.

## Validation

100 non-fail replays with a natural mix of mods:

| | n | mean | median | ≥ 95% match |
|---|---|---|---|---|
| All replays | 100 | 83.63% | 97.31% | 62% |
| Usable (frames cover the map) | 93 | 89.63% | 97.78% | 67% |
| Flagged as truncated | 7 | 3.91% | 3.93% | 0% |

Most replays reconstruct almost exactly. **5 of 100 still fail despite full frame
coverage** and fit neither time convention — that is the main open problem in the
simulation.

Downstream, match rate is used as a filter: only replays that reconstruct cleanly will
produce per-note rows, so wrong judgements do not leak into features or labels.

## Code

```
code/
  osu_rules.py                      osu! constants and formulas: windows, radius,
                                    approach-rate preempt, clock rate, key and mod bits
  osu_beatmap_and_replay_input.py   loading .osr/.osu files, frame times, press detection
  osu_simulation.py                 hit detection, time-origin handling, truncation check
```

`simulate_replay(replay, beatmap)` returns the reconstructed counts, the header counts,
the match rate, which time convention won, frame coverage, the truncation flag, and the
signed timing error for every hit note.

## Running it

Requires Python 3 and:

```
pip install osrparse slider
```

Data comes from the Kaggle dataset
[`skihikingkevin/ordr-replay-dump`](https://www.kaggle.com/datasets/skihikingkevin/ordr-replay-dump)
and is not included in this repository. Expected layout:

```
index.csv
replays/osr/<replayHash>.osr
beatmaps/<beatmapHash>.osu
test.osr, test.osu            one replay and its beatmap, used for quick checks
```

Run the simulation on the test pair:

```
python code/osu_simulation.py
```

## Limitations

- osu!standard only, judged by osu!stable's rules.
- Replays in the dataset were submitted to a replay-rendering service, so they skew toward
  plays people wanted to show off — full combos and memorable fails. They are not a
  representative sample of normal play.
- About 5% of replays cannot yet be reconstructed.
