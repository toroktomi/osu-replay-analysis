import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.metrics import roc_auc_score

from osu_beatmap_and_replay_input import ROOT

sys.stdout.reconfigure(encoding="utf-8")

DATA_FILE = ROOT / "data" / "objects_players.parquet"

JUDGEMENT_CODES = {"300": 0, "100": 1, "50": 2, "miss": 3}
JUDGEMENT_VALUES = np.array([300.0, 100.0, 50.0, 0.0])
MISS_CODE = 3

TEST_BEATMAP_FRACTION = 0.25
RANDOM_SEED = 20260929

DENSITY_WINDOW_MS = 1000.0

OBJECT_FEATURES = [
    "time_since_prev_ms",
    "jump_distance_px",
    "angle_deg",
    "local_bpm",
    "effective_ar_ms",
    "window_300_ms",
    "window_50_ms",
    "radius_px",
    "x",
    "y",
    "is_slider",
    "velocity_px_per_ms",
    "rhythm_change",
    "local_density",
    "recent_aim_load",
    "map_position",
]

SKILL_FEATURES = [
    "skill_timing_sigma",
    "skill_timing_bias",
    "skill_aim_ratio",
    "skill_miss_rate",
    "skill_mean_abs_error",
]

LGB_PARAMS = {
    "objective": "multiclass",
    "num_class": 4,
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbosity": -1,
    "seed": RANDOM_SEED,
}
NUM_ROUNDS = 400


def add_difficulty_features(frame):
    frame = frame.sort_values(["replay_id", "time_ms"]).reset_index(drop=True)

    gaps = frame["time_since_prev_ms"].replace(0.0, np.nan)
    frame["velocity_px_per_ms"] = frame["jump_distance_px"] / gaps

    previous_gap = frame.groupby("replay_id")["time_since_prev_ms"].shift()
    frame["rhythm_change"] = gaps / previous_gap.replace(0.0, np.nan)

    last_index = frame.groupby("replay_id")["object_index"].transform("max")
    frame["map_position"] = frame["object_index"] / last_index.clip(lower=1)

    times = frame["time_ms"].to_numpy(dtype="float64")
    jumps = np.nan_to_num(frame["jump_distance_px"].to_numpy(dtype="float64"))
    density = np.zeros(len(frame), dtype="float32")
    aim_load = np.zeros(len(frame), dtype="float32")

    for positions in frame.groupby("replay_id", sort=False).indices.values():
        window_times = times[positions]
        window_jumps = jumps[positions]
        start = np.searchsorted(window_times,
                                window_times - DENSITY_WINDOW_MS, side="left")
        offsets = np.arange(len(window_times))
        density[positions] = offsets - start
        cumulative = np.concatenate([[0.0], np.cumsum(window_jumps)])
        aim_load[positions] = cumulative[offsets + 1] - cumulative[start]

    frame["local_density"] = density
    frame["recent_aim_load"] = aim_load
    return frame


def load_rows(path=DATA_FILE):
    frame = pq.read_table(path).to_pandas()
    frame["is_slider"] = (frame["object_type"] == "slider").astype("float32")
    frame["judgement_code"] = frame["judgement"].map(JUDGEMENT_CODES).astype("int8")
    frame["aim_error_ratio"] = frame["cursor_distance_px"] / frame["radius_px"]
    return add_difficulty_features(frame)


def split_by_beatmap(frame):
    beatmaps = np.asarray(sorted(frame["beatmap_hash"].unique()), dtype=object)
    generator = np.random.default_rng(RANDOM_SEED)
    generator.shuffle(beatmaps)

    cut = int(len(beatmaps) * (1.0 - TEST_BEATMAP_FRACTION))
    train_beatmaps = set(beatmaps[:cut])

    is_train = frame["beatmap_hash"].isin(train_beatmaps)
    return frame[is_train].copy(), frame[~is_train].copy()


def player_skill(train_rows):
    grouped = train_rows.groupby("player")
    skill = pd.DataFrame({
        "skill_timing_sigma": grouped["hit_error_ms"].std(),
        "skill_timing_bias": grouped["hit_error_ms"].mean(),
        "skill_aim_ratio": grouped["aim_error_ratio"].mean(),
        "skill_miss_rate": grouped["judgement_code"].apply(
            lambda codes: (codes == MISS_CODE).mean()),
        "skill_mean_abs_error": grouped["hit_error_ms"].apply(
            lambda errors: errors.abs().mean()),
    })
    return skill


def attach_skill(rows, skill):
    joined = rows.join(skill, on="player")
    return joined.fillna({name: skill[name].mean() for name in SKILL_FEATURES})


def accuracy_from_counts(counts):
    total = counts.sum()
    if total == 0:
        return np.nan
    return float((counts * JUDGEMENT_VALUES).sum() / (300.0 * total))


def actual_play_accuracy(rows):
    counted = rows.groupby(["replay_id", "judgement_code"]).size().unstack(
        fill_value=0).reindex(columns=[0, 1, 2, 3], fill_value=0)
    weighted = counted.to_numpy() * JUDGEMENT_VALUES
    return pd.Series(weighted.sum(axis=1) / (300.0 * counted.to_numpy().sum(axis=1)),
                     index=counted.index)


def predicted_play_accuracy(rows, probabilities):
    expected = probabilities @ JUDGEMENT_VALUES
    frame = pd.DataFrame({"replay_id": rows["replay_id"].to_numpy(),
                          "expected": expected})
    grouped = frame.groupby("replay_id")["expected"]
    return grouped.mean() / 300.0


def score(name, predicted, actual):
    joined = pd.concat([predicted.rename("predicted"),
                        actual.rename("actual")], axis=1).dropna()
    error = (joined["predicted"] - joined["actual"]) * 100.0
    return {
        "name": name,
        "mae": error.abs().mean(),
        "median": error.abs().median(),
        "bias": error.mean(),
        "n": len(joined),
    }


def play_level_frame(rows, skill):
    aggregated = rows.groupby("replay_id").agg(
        player=("player", "first"),
        objects=("object_index", "size"),
        mean_time_since_prev=("time_since_prev_ms", "mean"),
        mean_jump=("jump_distance_px", "mean"),
        mean_angle=("angle_deg", "mean"),
        mean_bpm=("local_bpm", "mean"),
        max_bpm=("local_bpm", "max"),
        mean_ar=("effective_ar_ms", "mean"),
        window_300=("window_300_ms", "first"),
        radius=("radius_px", "first"),
        slider_share=("is_slider", "mean"),
    )
    return aggregated.join(skill, on="player")


def main():
    data_file = DATA_FILE
    for argument in sys.argv[1:]:
        if argument.endswith(".parquet"):
            data_file = DATA_FILE.parent / argument
    print(f"data: {data_file.name}")
    frame = load_rows(data_file)
    train_rows, test_rows = split_by_beatmap(frame)

    skill = player_skill(train_rows)
    train = attach_skill(train_rows, skill)
    test = attach_skill(test_rows, skill)

    features = OBJECT_FEATURES + SKILL_FEATURES

    print(f"rows       train {len(train):,}   test {len(test):,}")
    print(f"beatmaps   train {train['beatmap_hash'].nunique():,}   "
          f"test {test['beatmap_hash'].nunique():,}")
    print(f"overlap    {len(set(train['beatmap_hash']) & set(test['beatmap_hash']))}")
    print(f"plays      train {train['replay_id'].nunique():,}   "
          f"test {test['replay_id'].nunique():,}")
    print()

    ablations = {
        "geometry only": OBJECT_FEATURES,
        "skill only": SKILL_FEATURES,
        "both": features,
    }
    ablation_results = []
    for name, subset in ablations.items():
        trained = lgb.train(LGB_PARAMS,
                            lgb.Dataset(train[subset], label=train["judgement_code"]),
                            num_boost_round=NUM_ROUNDS)
        ablation_probabilities = trained.predict(test[subset])
        ablation_results.append((
            name,
            roc_auc_score((test["judgement_code"].to_numpy() == MISS_CODE).astype(int),
                          ablation_probabilities[:, MISS_CODE]),
            predicted_play_accuracy(test, ablation_probabilities),
        ))
        if name == "both":
            model = trained
            probabilities = ablation_probabilities

    predicted_codes = probabilities.argmax(axis=1)
    object_accuracy = (predicted_codes == test["judgement_code"].to_numpy()).mean()
    miss_auc = roc_auc_score(
        (test["judgement_code"].to_numpy() == MISS_CODE).astype(int),
        probabilities[:, MISS_CODE])

    print(f"per-object miss AUC   {miss_auc:.4f}")
    print(f"per-object accuracy   {object_accuracy:.4f}")
    print()

    actual = actual_play_accuracy(test)
    per_object = predicted_play_accuracy(test, probabilities)

    train_actual = actual_play_accuracy(train)
    global_mean = train_actual.mean()

    train_players = train.groupby("replay_id")["player"].first()
    player_history = train_actual.groupby(train_players).mean()
    test_players = test.groupby("replay_id")["player"].first()
    player_baseline = test_players.map(player_history).fillna(global_mean)

    play_train = play_level_frame(train, skill)
    play_test = play_level_frame(test, skill)
    play_features = [c for c in play_train.columns if c != "player"]

    play_model = lgb.train(
        {"objective": "regression", "learning_rate": 0.05, "num_leaves": 31,
         "min_data_in_leaf": 20, "verbosity": -1, "seed": RANDOM_SEED},
        lgb.Dataset(play_train[play_features],
                    label=train_actual.reindex(play_train.index)),
        num_boost_round=300)
    play_predicted = pd.Series(play_model.predict(play_test[play_features]),
                               index=play_test.index)

    results = [
        score("global mean", pd.Series(global_mean, index=actual.index), actual),
        score("player historical mean", player_baseline, actual),
        score("LightGBM play-level", play_predicted, actual),
        score("per-object model", per_object, actual),
    ]

    print("PLAY ACCURACY PREDICTION       MAE   median     bias      n")
    for row in results:
        print(f"  {row['name']:<22} {row['mae']:>8.3f} {row['median']:>8.3f} "
              f"{row['bias']:>8.3f} {row['n']:>6}")

    print()
    print("ABLATION (same split, same rounds)      MAE   missAUC")
    for name, auc, predicted in ablation_results:
        row = score(name, predicted, actual)
        print(f"  {name:<22} {row['mae']:>12.3f} {auc:>9.4f}")

    print()
    print("top features (gain):")
    gains = pd.Series(model.feature_importance("gain"), index=features)
    for name, gain in gains.sort_values(ascending=False).head(12).items():
        print(f"  {name:<24} {gain / gains.sum() * 100:>6.2f}%")


if __name__ == "__main__":
    main()
