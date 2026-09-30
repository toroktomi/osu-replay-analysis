import csv
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

import osrparse
import pyarrow as pa
import pyarrow.parquet as pq
from slider import Beatmap

from osu_beatmap_and_replay_input import ROOT
from osu_simulation import (
    BEATMAP_DIR,
    REPLAY_DIR,
    build_objects,
    read_difficulty,
    simulate_replay,
)

sys.stdout.reconfigure(encoding="utf-8")

INDEX_CSV = ROOT / "index.csv"
OUTPUT_DIR = ROOT / "data"
OUTPUT_FILE = OUTPUT_DIR / "objects.parquet"
PLAYER_COUNTS_FILE = OUTPUT_DIR / "player_counts.json"

MIN_MATCH_RATE = 0.95
ROW_GROUP_SIZE = 50000
MIN_REPLAYS_PER_PLAYER = 10
MAX_REPLAYS_PER_PLAYER = 25
PLAYER_SAMPLE_SEED = 20260929
MIN_BPM = 5.0
MAX_BPM = 1200.0

ROW_SCHEMA = pa.schema([
    ("replay_id", pa.string()),
    ("player", pa.string()),
    ("beatmap_hash", pa.string()),
    ("client", pa.int32()),
    ("mods", pa.int32()),
    ("object_index", pa.int32()),
    ("object_type", pa.string()),
    ("time_ms", pa.float32()),
    ("x", pa.float32()),
    ("y", pa.float32()),
    ("time_since_prev_ms", pa.float32()),
    ("jump_distance_px", pa.float32()),
    ("angle_deg", pa.float32()),
    ("local_bpm", pa.float32()),
    ("effective_ar_ms", pa.float32()),
    ("window_300_ms", pa.float32()),
    ("window_100_ms", pa.float32()),
    ("window_50_ms", pa.float32()),
    ("radius_px", pa.float32()),
    ("judgement", pa.string()),
    ("hit_error_ms", pa.float32()),
    ("cursor_distance_px", pa.float32()),
    ("key_used", pa.string()),
    ("key_interval_ms", pa.float32()),
])


def red_timing_points(beatmap):
    points = [
        (point.offset.total_seconds() * 1000.0, point.ms_per_beat)
        for point in beatmap.timing_points
        if point.parent is None and point.ms_per_beat > 0
    ]
    points.sort()
    return points


def local_bpm(timing_points, time_ms, rate):
    if not timing_points:
        return None

    ms_per_beat = timing_points[0][1]
    for offset, beat_length in timing_points:
        if offset > time_ms:
            break
        ms_per_beat = beat_length

    if ms_per_beat <= 0:
        return None

    bpm = 60000.0 / ms_per_beat * rate
    if not math.isfinite(bpm) or bpm < MIN_BPM or bpm > MAX_BPM:
        return None
    return bpm


def corner_angle(previous, current, following):
    incoming_x = previous.x - current.x
    incoming_y = previous.y - current.y
    outgoing_x = following.x - current.x
    outgoing_y = following.y - current.y

    incoming_length = math.hypot(incoming_x, incoming_y)
    outgoing_length = math.hypot(outgoing_x, outgoing_y)
    if incoming_length == 0.0 or outgoing_length == 0.0:
        return None

    cosine = (incoming_x * outgoing_x + incoming_y * outgoing_y) / (
        incoming_length * outgoing_length)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def object_rows(replay, beatmap, result, replay_id, beatmap_hash):
    mods = int(replay.mods)
    difficulty = read_difficulty(beatmap, mods)
    objects, _ = build_objects(beatmap, mods)
    timing_points = red_timing_points(beatmap)
    rate = difficulty.clock_rate

    rows = []
    for verdict in result.verdicts:
        current = objects[verdict.object_index]
        previous = objects[verdict.object_index - 1] if verdict.object_index > 0 else None
        before = objects[verdict.object_index - 2] if verdict.object_index > 1 else None

        time_since_prev = None
        jump_distance = None
        if previous is not None:
            time_since_prev = (current.time - previous.time) / rate
            jump_distance = math.hypot(current.x - previous.x, current.y - previous.y)

        angle = None
        if before is not None and previous is not None:
            angle = corner_angle(before, previous, current)

        rows.append((
            replay_id,
            replay.username,
            beatmap_hash,
            replay.game_version,
            mods,
            verdict.object_index,
            current.object_type,
            current.time / rate,
            current.x,
            current.y,
            time_since_prev,
            jump_distance,
            angle,
            local_bpm(timing_points, current.time, rate),
            difficulty.preempt / rate,
            difficulty.window_300,
            difficulty.window_100,
            difficulty.window_50,
            difficulty.radius,
            verdict.judgement,
            None if verdict.hit_error is None else verdict.hit_error / rate,
            verdict.cursor_distance,
            verdict.key,
            None if verdict.key_interval is None else verdict.key_interval / rate,
        ))

    return rows


def rows_to_table(rows):
    columns = list(zip(*rows))
    return pa.table(
        {field.name: pa.array(column, type=field.type)
         for field, column in zip(ROW_SCHEMA, columns)},
        schema=ROW_SCHEMA,
    )


def index_rows():
    with open(INDEX_CSV, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["performance-IsFail"] != "False":
                continue
            yield row


def files_present(row):
    return ((REPLAY_DIR / (row["replayHash"] + ".osr")).exists()
            and (BEATMAP_DIR / (row["beatmapHash"] + ".osu")).exists())


def usable_index_rows():
    for row in index_rows():
        if files_present(row):
            yield row


def replays_per_player(use_cache=True):
    if use_cache and PLAYER_COUNTS_FILE.exists():
        return Counter(json.loads(PLAYER_COUNTS_FILE.read_text(encoding="utf-8")))

    counts = Counter()
    for row in usable_index_rows():
        counts[row["playerName"]] += 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PLAYER_COUNTS_FILE.write_text(json.dumps(counts), encoding="utf-8")
    return counts


def choose_players(player_limit, min_replays=MIN_REPLAYS_PER_PLAYER, use_cache=True):
    counts = replays_per_player(use_cache)
    eligible = sorted(name for name, n in counts.items() if n >= min_replays)
    if player_limit >= len(eligible):
        return set(eligible), counts
    sampler = random.Random(PLAYER_SAMPLE_SEED)
    return set(sampler.sample(eligible, player_limit)), counts


def candidate_replays(limit):
    taken = 0
    for row in usable_index_rows():
        yield row
        taken += 1
        if taken >= limit:
            return


def candidate_replays_by_player(players, cap=MAX_REPLAYS_PER_PLAYER):
    taken = Counter()
    for row in index_rows():
        name = row["playerName"]
        if name not in players or taken[name] >= cap:
            continue
        if not files_present(row):
            continue
        taken[name] += 1
        yield row


def extract(rows, output_file=OUTPUT_FILE, min_match_rate=MIN_MATCH_RATE,
            overwrite=False):
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists() and not overwrite:
        raise FileExistsError(
            f"{output_file} already exists; pass --overwrite to replace it")

    stats = {"seen": 0, "written": 0, "low_match": 0, "truncated": 0,
             "not_std": 0, "failed": 0, "rows": 0}
    buffer = []
    writer = pq.ParquetWriter(output_file, ROW_SCHEMA, compression="snappy")

    try:
        for row in rows:
            stats["seen"] += 1
            try:
                replay = osrparse.Replay.from_path(
                    REPLAY_DIR / (row["replayHash"] + ".osr"))
                if replay.mode != osrparse.GameMode.STD:
                    stats["not_std"] += 1
                    continue

                beatmap = Beatmap.from_path(
                    BEATMAP_DIR / (row["beatmapHash"] + ".osu"))
                result = simulate_replay(replay, beatmap)

                if result.truncated:
                    stats["truncated"] += 1
                    continue
                if result.match_rate < min_match_rate:
                    stats["low_match"] += 1
                    continue

                buffer.extend(object_rows(replay, beatmap, result,
                                          row["replayHash"], row["beatmapHash"]))
                stats["written"] += 1

                if len(buffer) >= ROW_GROUP_SIZE:
                    writer.write_table(rows_to_table(buffer))
                    stats["rows"] += len(buffer)
                    buffer = []
            except Exception:
                stats["failed"] += 1

        if buffer:
            writer.write_table(rows_to_table(buffer))
            stats["rows"] += len(buffer)
    finally:
        writer.close()

    return stats


if __name__ == "__main__":
    arguments = sys.argv[1:]
    overwrite = "--overwrite" in arguments
    positional = [a for a in arguments if not a.startswith("--")]

    count = int(positional[0]) if positional else 200
    output_file = OUTPUT_DIR / positional[1] if len(positional) > 1 else OUTPUT_FILE

    cap = MAX_REPLAYS_PER_PLAYER
    for argument in arguments:
        if argument.startswith("--cap="):
            cap = int(argument.split("=", 1)[1])

    if "--by-row" in arguments:
        rows = candidate_replays(count)
        print(f"sampling {count} replays in index order")
    else:
        players, counts = choose_players(count,
                                          use_cache="--rescan" not in arguments)
        available = sum(min(counts[name], cap) for name in players)
        print(f"chose {len(players)} players with >={MIN_REPLAYS_PER_PLAYER} replays, "
              f"up to {cap} each: {available} replays available")
        rows = candidate_replays_by_player(players, cap)

    stats = extract(rows, output_file, overwrite=overwrite)

    print(f"replays seen      {stats['seen']}")
    print(f"  written         {stats['written']}")
    print(f"  below {MIN_MATCH_RATE:.0%} match  {stats['low_match']}")
    print(f"  truncated       {stats['truncated']}")
    print(f"  not standard    {stats['not_std']}")
    print(f"  errored         {stats['failed']}")
    print(f"object rows       {stats['rows']:,}")
    print(f"output            {output_file}  "
          f"{output_file.stat().st_size / 1e6:.1f} MB")
