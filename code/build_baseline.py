import json
import sys

import lightgbm as lgb

from osu_beatmap_and_replay_input import ROOT
from osu_diagnosis import (
    BANDED_METRICS,
    MIN_PLAYERS_PER_BUCKET,
    add_diagnosis_columns,
    banded_values,
    overall_values,
)
from osu_prediction import (
    DATA_FILE,
    LGB_PARAMS,
    NUM_ROUNDS,
    OBJECT_FEATURES,
    SKILL_FEATURES,
    load_rows,
    player_skill,
)

sys.stdout.reconfigure(encoding="utf-8")

OUTPUT_DIR = ROOT / "data"
BASELINE_FILE = OUTPUT_DIR / "baseline.json"
MODEL_FILE = OUTPUT_DIR / "model.txt"


def build_baseline(frame):
    population = {}

    for spec in BANDED_METRICS:
        values = banded_values(frame, spec)
        if spec["rank_on_abs"]:
            values = values.abs()
        for band, band_values in values.groupby(level=1, observed=True):
            players = band_values.droplevel(1).dropna()
            if len(players) < MIN_PLAYERS_PER_BUCKET:
                continue
            population[f"{spec['key']}|{band}"] = sorted(
                round(float(value), 6) for value in players)

    for key, series in overall_values(frame).items():
        players = series.dropna()
        if len(players) < MIN_PLAYERS_PER_BUCKET:
            continue
        population[f"{key}|overall"] = sorted(
            round(float(value), 6) for value in players)

    return {
        "players": int(frame["player"].nunique()),
        "objects": int(len(frame)),
        "replays": int(frame["replay_id"].nunique()),
        "beatmaps": int(frame["beatmap_hash"].nunique()),
        "distributions": population,
    }


def build_model(frame):
    skill = player_skill(frame)
    rows = frame.join(skill, on="player")
    rows = rows.fillna({name: skill[name].mean() for name in SKILL_FEATURES})

    features = OBJECT_FEATURES + SKILL_FEATURES
    return lgb.train(LGB_PARAMS,
                     lgb.Dataset(rows[features], label=rows["judgement_code"]),
                     num_boost_round=NUM_ROUNDS)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    data_file = DATA_FILE
    for argument in sys.argv[1:]:
        if argument.endswith(".parquet"):
            data_file = OUTPUT_DIR / argument

    frame = add_diagnosis_columns(load_rows(data_file))
    print(f"loaded {len(frame):,} rows from {data_file.name}, "
          f"{frame['player'].nunique()} players")

    baseline = build_baseline(frame)
    BASELINE_FILE.write_text(json.dumps(baseline), encoding="utf-8")
    print(f"baseline  {len(baseline['distributions'])} distributions  "
          f"{BASELINE_FILE.stat().st_size / 1024:.0f} KB")

    model = build_model(frame)
    model.save_model(str(MODEL_FILE))
    print(f"model     {MODEL_FILE.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
