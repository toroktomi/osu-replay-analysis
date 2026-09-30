import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

from osu_prediction import (
    DATA_FILE,
    LGB_PARAMS,
    NUM_ROUNDS,
    OBJECT_FEATURES,
    SKILL_FEATURES,
    actual_play_accuracy,
    load_rows,
    player_skill,
    predicted_play_accuracy,
    score,
)

sys.stdout.reconfigure(encoding="utf-8")

HELD_OUT_PLAYERS = 50
PROFILE_SIZES = (0, 1, 3, 5, 10)
FIXED_EVALUATION_FROM = 10
RANDOM_SEED = 20260929


def split_players(frame):
    players = np.asarray(sorted(frame["player"].unique()), dtype=object)
    generator = np.random.default_rng(RANDOM_SEED)
    generator.shuffle(players)
    held_out = set(players[:HELD_OUT_PLAYERS])
    return held_out


def replay_order(frame):
    order = {}
    for player, rows in frame.groupby("player"):
        order[player] = sorted(rows["replay_id"].unique())
    return order


def skill_for_profile(frame, profile_replays, population):
    if not profile_replays:
        return population
    rows = frame[frame["replay_id"].isin(profile_replays)]
    if rows.empty:
        return population
    computed = player_skill(rows)
    if computed.empty:
        return population
    values = computed.iloc[0]
    return {name: (values[name] if pd.notna(values[name]) else population[name])
            for name in SKILL_FEATURES}


def apply_skill(rows, skill_values):
    patched = rows.copy()
    for name in SKILL_FEATURES:
        patched[name] = skill_values[name]
    return patched


def main():
    data_file = DATA_FILE
    for argument in sys.argv[1:]:
        if argument.endswith(".parquet"):
            data_file = DATA_FILE.parent / argument
    print(f"data: {data_file.name}")
    frame = load_rows(data_file)
    held_out = split_players(frame)

    train_rows = frame[~frame["player"].isin(held_out)].copy()
    stranger_rows = frame[frame["player"].isin(held_out)].copy()

    train_skill = player_skill(train_rows)
    population = {name: float(train_skill[name].mean()) for name in SKILL_FEATURES}

    for name in SKILL_FEATURES:
        train_rows[name] = train_rows["player"].map(train_skill[name]).fillna(
            population[name])

    features = OBJECT_FEATURES + SKILL_FEATURES

    print(f"training players   {train_rows['player'].nunique()}")
    print(f"stranger players   {stranger_rows['player'].nunique()}")
    print(f"training rows      {len(train_rows):,}")
    print(f"stranger rows      {len(stranger_rows):,}")
    print()

    model = lgb.train(LGB_PARAMS,
                      lgb.Dataset(train_rows[features],
                                  label=train_rows["judgement_code"]),
                      num_boost_round=NUM_ROUNDS)

    order = replay_order(stranger_rows)
    actual = actual_play_accuracy(stranger_rows)
    train_global_mean = actual_play_accuracy(train_rows).mean()

    fixed_replays = set()
    for player, replays in order.items():
        fixed_replays.update(replays[FIXED_EVALUATION_FROM:])

    varying, fixed = [], []
    for profile_size in PROFILE_SIZES:
        evaluated_parts = []

        for player, replays in order.items():
            if len(replays) <= profile_size:
                continue
            profile = replays[:profile_size]
            remaining = replays[profile_size:]

            rows = stranger_rows[stranger_rows["replay_id"].isin(remaining)]
            if rows.empty:
                continue

            skill_values = skill_for_profile(stranger_rows, profile, population)
            evaluated_parts.append(apply_skill(rows, skill_values))

        evaluated = pd.concat(evaluated_parts)
        probabilities = model.predict(evaluated[features])
        predicted = predicted_play_accuracy(evaluated, probabilities)

        label = "none (population)" if profile_size == 0 else str(profile_size)
        varying.append(score(label, predicted, actual))

        common = evaluated[evaluated["replay_id"].isin(fixed_replays)]
        if not common.empty:
            common_probabilities = model.predict(common[features])
            common_predicted = predicted_play_accuracy(common, common_probabilities)
            fixed.append(score(label, common_predicted, actual))

    print("A. profile replays -> MAE on that player's REMAINING plays")
    print(f"  {'profile':<20} {'MAE':>7} {'median':>8} {'bias':>8} {'plays':>7}")
    for row in varying:
        print(f"  {row['name']:<20} {row['mae']:>7.3f} {row['median']:>8.3f} "
              f"{row['bias']:>8.3f} {row['n']:>7}")

    print()
    print(f"B. same, evaluated only on plays after the {FIXED_EVALUATION_FROM}th "
          f"(identical test set for every row)")
    print(f"  {'profile':<20} {'MAE':>7} {'median':>8} {'bias':>8} {'plays':>7}")
    for row in fixed:
        print(f"  {row['name']:<20} {row['mae']:>7.3f} {row['median']:>8.3f} "
              f"{row['bias']:>8.3f} {row['n']:>7}")

    fixed_actual = actual[actual.index.isin(fixed_replays)]
    baseline = score("global mean (no model)",
                     pd.Series(train_global_mean, index=fixed_actual.index),
                     fixed_actual)
    print(f"  {baseline['name']:<20} {baseline['mae']:>7.3f} "
          f"{baseline['median']:>8.3f} {baseline['bias']:>8.3f} {baseline['n']:>7}")

    if len(fixed) >= 2:
        gain = fixed[0]["mae"] - fixed[-1]["mae"]
        print()
        print(f"personalisation is worth {gain:.3f} accuracy points "
              f"({fixed[0]['mae']:.3f} -> {fixed[-1]['mae']:.3f}) "
              f"on {fixed[-1]['n']} plays from players the model never saw")


if __name__ == "__main__":
    main()
