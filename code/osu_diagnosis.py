import sys

import pandas as pd
import pyarrow.parquet as pq

from osu_beatmap_and_replay_input import ROOT

sys.stdout.reconfigure(encoding="utf-8")

DATA_FILE = ROOT / "data" / "objects_players.parquet"

MIN_OBJECTS_PER_BUCKET = 40
MIN_PLAYERS_PER_BUCKET = 12

BPM_EDGES = [0, 130, 150, 170, 190, 210, 240, 10000]
BPM_LABELS = ["<130", "130-150", "150-170", "170-190", "190-210", "210-240", "240+"]

JUMP_EDGES = [0, 50, 100, 150, 200, 300, 10000]
JUMP_LABELS = ["<50px", "50-100", "100-150", "150-200", "200-300", "300+"]

AR_EDGES = [0, 350, 450, 600, 900, 10000]
AR_LABELS = ["<350ms", "350-450", "450-600", "600-900", "900+"]


BANDED_METRICS = [
    {"key": "consistency_bpm", "label": "tapping consistency by BPM",
     "band": "bpm_band", "column": "hit_error_ms", "agg": "std",
     "lower_is_better": True, "rank_on_abs": False, "unit": "ms",
     "order": BPM_LABELS, "decimals": 2},
    {"key": "bias_bpm", "label": "timing bias by BPM",
     "band": "bpm_band", "column": "hit_error_ms", "agg": "mean",
     "lower_is_better": True, "rank_on_abs": True, "unit": "ms",
     "order": BPM_LABELS, "decimals": 2},
    {"key": "aim_jump", "label": "aim error by jump distance",
     "band": "jump_band", "column": "aim_error_ratio", "agg": "mean",
     "lower_is_better": True, "rank_on_abs": False, "unit": "r",
     "order": JUMP_LABELS, "decimals": 3},
    {"key": "hitrate_jump", "label": "hit rate by jump distance",
     "band": "jump_band", "column": "hit", "agg": "mean",
     "lower_is_better": False, "rank_on_abs": False, "unit": "",
     "order": JUMP_LABELS, "decimals": 4},
    {"key": "consistency_ar", "label": "tapping consistency by reading time",
     "band": "ar_band", "column": "hit_error_ms", "agg": "std",
     "lower_is_better": True, "rank_on_abs": False, "unit": "ms",
     "order": AR_LABELS, "decimals": 2},
]

OVERALL_METRICS = [
    {"key": "alternation", "label": "alternation rate",
     "lower_is_better": False, "unit": "", "decimals": 2},
    {"key": "imbalance", "label": "K1/K2 imbalance",
     "lower_is_better": True, "unit": "", "decimals": 2},
    {"key": "stamina_drift", "label": "stamina drift",
     "lower_is_better": True, "unit": "ms", "decimals": 2},
]


def add_diagnosis_columns(frame):
    frame["aim_error_ratio"] = frame["cursor_distance_px"] / frame["radius_px"]
    frame["hit"] = (frame["judgement"] != "miss").astype("float32")

    last_index = frame.groupby("replay_id")["object_index"].transform("max")
    frame["map_position"] = frame["object_index"] / last_index.clip(lower=1)

    frame["bpm_band"] = pd.cut(frame["local_bpm"], BPM_EDGES,
                               labels=BPM_LABELS, right=False)
    frame["jump_band"] = pd.cut(frame["jump_distance_px"], JUMP_EDGES,
                                labels=JUMP_LABELS, right=False)
    frame["ar_band"] = pd.cut(frame["effective_ar_ms"], AR_EDGES,
                              labels=AR_LABELS, right=False)
    return frame


def load_rows(path=DATA_FILE):
    return add_diagnosis_columns(pq.read_table(path).to_pandas())


def bucket_values(frame, band_column, value_column, aggregation):
    grouped = frame.groupby(["player", band_column], observed=True)[value_column]
    values = grouped.agg(aggregation)
    counts = grouped.count()
    return values[counts >= MIN_OBJECTS_PER_BUCKET].dropna()


def ordinal(value):
    number = int(round(value))
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def percentile_against_population(ranking_values, player, lower_is_better):
    ranked = {}
    for band, band_values in ranking_values.groupby(level=1, observed=True):
        population = band_values.droplevel(1)
        if len(population) < MIN_PLAYERS_PER_BUCKET or player not in population.index:
            continue
        target = population[player]
        if lower_is_better:
            beaten = (population > target).sum()
        else:
            beaten = (population < target).sum()
        ranked[band] = (beaten / len(population) * 100.0, len(population))
    return ranked


def banded_report(frame, player, band_column, value_column, aggregation,
                  lower_is_better, unit, order, rank_on_abs=False, decimals=2):
    values = bucket_values(frame, band_column, value_column, aggregation)
    ranking_values = values.abs() if rank_on_abs else values
    ranked = percentile_against_population(ranking_values, player, lower_is_better)

    lines = []
    for band in order:
        if band not in ranked:
            continue
        percentile, population = ranked[band]
        shown = values[(player, band)]
        lines.append(f"    {str(band):<10} {shown:>9.{decimals}f} {unit:<4} "
                     f"{ordinal(percentile):>6} percentile   (n={population} players)")
    return lines


def banded_values(frame, spec, min_objects=MIN_OBJECTS_PER_BUCKET):
    grouped = frame.groupby(["player", spec["band"]], observed=True)[spec["column"]]
    values = grouped.agg(spec["agg"])
    counts = grouped.count()
    return values[counts >= min_objects].dropna()


def overall_values(frame):
    alternation, imbalance, _ = alternation_and_balance(frame)
    _, _, drift = stamina(frame)
    return {"alternation": alternation, "imbalance": imbalance,
            "stamina_drift": drift}


def percentile_in_population(population, value, lower_is_better):
    if not len(population) or value is None or pd.isna(value):
        return None
    population = pd.Series(population, dtype="float64")
    if lower_is_better:
        beaten = (population > value).sum()
    else:
        beaten = (population < value).sum()
    return beaten / len(population) * 100.0


def alternation_and_balance(frame):
    hits = frame.dropna(subset=["key_used"]).sort_values(["replay_id", "object_index"])
    previous_key = hits.groupby("replay_id")["key_used"].shift()

    comparable = previous_key.notna()
    switched = comparable & (hits["key_used"] != previous_key)

    alternation = (switched.groupby(hits["player"]).sum()
                   / comparable.groupby(hits["player"]).sum())

    k1_share = hits["key_used"].eq("K1").groupby(hits["player"]).mean()
    imbalance = (k1_share - 0.5).abs() * 2.0
    return alternation, imbalance, k1_share


def stamina(frame):
    judged = frame.dropna(subset=["hit_error_ms"])
    early = judged[judged["map_position"] <= 1 / 3]
    late = judged[judged["map_position"] >= 2 / 3]

    early_sigma = early.groupby("player")["hit_error_ms"].std()
    late_sigma = late.groupby("player")["hit_error_ms"].std()
    return early_sigma, late_sigma, (late_sigma - early_sigma).dropna()


def rank_series(series, player, lower_is_better):
    population = series.dropna()
    if len(population) < MIN_PLAYERS_PER_BUCKET or player not in population.index:
        return None
    target = population[player]
    if lower_is_better:
        beaten = (population > target).sum()
    else:
        beaten = (population < target).sum()
    return target, beaten / len(population) * 100.0, len(population)


def report(frame, player):
    played = frame[frame["player"] == player]
    lines = [
        f"DIAGNOSIS: {player}",
        f"  {played['replay_id'].nunique()} replays, {len(played):,} hit objects, "
        f"{played['beatmap_hash'].nunique()} beatmaps",
        f"  population: {frame['player'].nunique()} players, {len(frame):,} objects",
        "",
        "  TAPPING CONSISTENCY by BPM  (timing error sigma, lower is better)",
    ]
    lines += banded_report(frame, player, "bpm_band", "hit_error_ms", "std",
                           True, "ms", BPM_LABELS)

    lines += ["", "  TIMING BIAS by BPM  (mean error, negative = early; "
              "ranked on distance from zero)"]
    lines += banded_report(frame, player, "bpm_band", "hit_error_ms", "mean",
                           True, "ms", BPM_LABELS, rank_on_abs=True)

    lines += ["", "  AIM ERROR by JUMP DISTANCE  (fraction of circle radius)"]
    lines += banded_report(frame, player, "jump_band", "aim_error_ratio", "mean",
                           True, "", JUMP_LABELS)

    lines += ["", "  HIT RATE by JUMP DISTANCE  (higher is better)"]
    lines += banded_report(frame, player, "jump_band", "hit", "mean",
                           False, "", JUMP_LABELS, decimals=4)

    lines += ["", "  TAPPING CONSISTENCY by READING TIME  (effective AR)"]
    lines += banded_report(frame, player, "ar_band", "hit_error_ms", "std",
                           True, "ms", AR_LABELS)

    alternation, imbalance, k1_share = alternation_and_balance(frame)
    early_sigma, late_sigma, drift = stamina(frame)

    lines += ["", "  OVERALL"]
    for label, series, lower, unit in (
        ("alternation rate", alternation, False, ""),
        ("K1/K2 imbalance", imbalance, True, ""),
        ("stamina drift", drift, True, "ms"),
    ):
        ranked = rank_series(series, player, lower)
        if ranked is None:
            continue
        target, percentile, population = ranked
        lines.append(f"    {label:<18} {target:>8.2f} {unit:<4} "
                     f"{ordinal(percentile):>6} percentile   (n={population})")

    if player in k1_share.index:
        lines.append(f"    K1 share           {k1_share[player]:>8.2f}")
    if player in early_sigma.index and player in late_sigma.index:
        lines.append(f"    sigma first third  {early_sigma[player]:>8.2f} ms")
        lines.append(f"    sigma last third   {late_sigma[player]:>8.2f} ms")

    return "\n".join(lines)


if __name__ == "__main__":
    frame = load_rows()

    if len(sys.argv) > 1:
        player = sys.argv[1]
    else:
        player = frame.groupby("player")["replay_id"].nunique().idxmax()

    print(report(frame, player))
