import hashlib
import json
import tempfile
from pathlib import Path

import lightgbm as lgb
import osrparse
import pandas as pd
from slider import Beatmap

from osu_beatmap_and_replay_input import ROOT
from osu_diagnosis import (
    BANDED_METRICS,
    MIN_OBJECTS_PER_BUCKET,
    OVERALL_METRICS,
    add_diagnosis_columns,
    banded_values,
    overall_values,
    percentile_in_population,
)
from osu_prediction import (
    JUDGEMENT_CODES,
    OBJECT_FEATURES,
    SKILL_FEATURES,
    actual_play_accuracy,
    add_difficulty_features,
    player_skill,
    predicted_play_accuracy,
)
from osu_simulation import simulate_replay
from parquet_converter import ROW_SCHEMA, object_rows

DATA_DIR = ROOT / "data"
BASELINE_FILE = DATA_DIR / "baseline.json"
MODEL_FILE = DATA_DIR / "model.txt"

MIN_MATCH_RATE = 0.95
PROFILE_PLAYER = "you"
MISS_CODE = 3


def load_baseline(path=BASELINE_FILE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_model(path=MODEL_FILE):
    return lgb.Booster(model_file=str(path))


def save_upload(upload, suffix):
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.write(upload.getbuffer())
    handle.close()
    return Path(handle.name)


def pair_uploads(uploads):
    replays, beatmaps = [], {}
    for upload in uploads:
        name = upload.name.lower()
        if name.endswith(".osr"):
            replays.append(upload)
        elif name.endswith(".osu"):
            path = save_upload(upload, ".osu")
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            beatmaps[digest] = (upload.name, path)

    paired, unmatched = [], []
    for upload in replays:
        path = save_upload(upload, ".osr")
        try:
            replay = osrparse.Replay.from_path(path)
        except Exception:
            unmatched.append((upload.name, "could not be parsed"))
            continue
        if replay.beatmap_hash not in beatmaps:
            unmatched.append((upload.name, "no matching .osu uploaded"))
            continue
        beatmap_name, beatmap_path = beatmaps[replay.beatmap_hash]
        paired.append((upload.name, replay, beatmap_name, beatmap_path))
    return paired, unmatched


def analyse(paired):
    frames, report = [], []
    for replay_name, replay, beatmap_name, beatmap_path in paired:
        if replay.mode != osrparse.GameMode.STD:
            report.append({"replay": replay_name, "map": beatmap_name,
                           "match": None, "used": False, "note": "not osu!standard"})
            continue

        beatmap = Beatmap.from_path(beatmap_path)
        result = simulate_replay(replay, beatmap)

        note, used = "", True
        if result.truncated:
            used, note = False, "frames do not cover the map"
        elif result.match_rate < MIN_MATCH_RATE:
            used, note = False, "reconstruction below 95%"

        report.append({
            "replay": replay_name,
            "map": f"{beatmap.artist} - {beatmap.title} [{beatmap.version}]",
            "match": result.match_rate, "used": used, "note": note})

        if used:
            rows = object_rows(replay, beatmap, result, replay_name,
                              replay.beatmap_hash)
            frame = pd.DataFrame(rows, columns=[field.name for field in ROW_SCHEMA])
            frame["player"] = PROFILE_PLAYER
            frames.append(frame)

    if not frames:
        return None, report

    combined = pd.concat(frames, ignore_index=True)
    combined["is_slider"] = (combined["object_type"] == "slider").astype("float32")
    combined["judgement_code"] = combined["judgement"].map(
        JUDGEMENT_CODES).astype("int8")
    combined = add_difficulty_features(combined)
    return add_diagnosis_columns(combined), report


def rank_metrics(frame, baseline):
    distributions = baseline["distributions"]
    ranked = []

    for spec in BANDED_METRICS:
        values = banded_values(frame, spec, MIN_OBJECTS_PER_BUCKET)
        if values.empty:
            continue
        for (_, band), value in values.items():
            population = distributions.get(f"{spec['key']}|{band}")
            if not population:
                continue
            compared = abs(value) if spec["rank_on_abs"] else value
            percentile = percentile_in_population(population, compared,
                                                  spec["lower_is_better"])
            if percentile is None:
                continue
            ranked.append({"what": f"{spec['label']} · {band}", "value": value,
                           "unit": spec["unit"], "decimals": spec["decimals"],
                           "percentile": percentile})

    overall = overall_values(frame)
    for spec in OVERALL_METRICS:
        series = overall.get(spec["key"])
        if series is None or PROFILE_PLAYER not in series.index:
            continue
        population = distributions.get(f"{spec['key']}|overall")
        if not population:
            continue
        percentile = percentile_in_population(population, series[PROFILE_PLAYER],
                                              spec["lower_is_better"])
        if percentile is None:
            continue
        ranked.append({"what": spec["label"], "value": series[PROFILE_PLAYER],
                       "unit": spec["unit"], "decimals": spec["decimals"],
                       "percentile": percentile})

    return sorted(ranked, key=lambda row: row["percentile"])


def format_rows(ranked):
    return pd.DataFrame([{
        "": row["what"],
        "your value": f"{row['value']:.{row['decimals']}f} {row['unit']}".strip(),
        "percentile": f"{row['percentile']:.0f}",
    } for row in ranked])


def format_report(report):
    frame = pd.DataFrame(report)
    if frame.empty:
        return frame
    frame["match"] = frame["match"].map(
        lambda value: "—" if value is None else f"{value:.1%}")
    return frame


def predict(frame, model):
    skill = player_skill(frame)
    rows = frame.join(skill, on="player")
    rows = rows.fillna({name: skill[name].mean() for name in SKILL_FEATURES})

    probabilities = model.predict(rows[OBJECT_FEATURES + SKILL_FEATURES])

    predicted = predicted_play_accuracy(rows, probabilities)
    actual = actual_play_accuracy(rows)
    expected_misses = pd.Series(probabilities[:, MISS_CODE],
                                index=rows.index).groupby(rows["replay_id"]).sum()

    return pd.DataFrame({
        "predicted accuracy": (predicted * 100).round(2),
        "actual accuracy": (actual * 100).round(2),
        "expected misses": expected_misses.round(1),
    })
