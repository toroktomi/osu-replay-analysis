from typing import NamedTuple

from slider.beatmap import Spinner

from osu_beatmap_and_replay_input import (
    ROOT,
    build_presses,
    frame_times,
    load_and_open_osr_file_from_path,
    load_and_open_osu_file_from_path,
)
from osu_rules import (
    MOD_EASY,
    MOD_HARDROCK,
    approach_rate_preempt,
    circle_radius,
    clock_rate,
    judge,
    timing_windows,
)

BEATMAP_DIR = ROOT / "beatmaps"
REPLAY_DIR = ROOT / "replays" / "osr"

JUDGEMENTS = ("300", "100", "50", "miss")

COVERAGE_THRESHOLD = 1.0


class HitObject(NamedTuple):
    index: int
    time: float
    x: float
    y: float


class Result(NamedTuple):
    counts: dict
    header: dict
    match_rate: float
    keep_lead_in: bool
    coverage: float
    truncated: bool
    hit_errors: list
    times: list
    presses: list


class Difficulty(NamedTuple):
    od: float
    cs: float
    ar: float
    hp: float
    window_300: float
    window_100: float
    window_50: float
    radius: float
    preempt: float
    clock_rate: float


def read_difficulty(beatmap, mods=0):
    easy = bool(mods & MOD_EASY)
    hard_rock = bool(mods & MOD_HARDROCK)

    od = beatmap.od(easy=easy, hard_rock=hard_rock)
    cs = beatmap.cs(easy=easy, hard_rock=hard_rock)
    ar = beatmap.ar(easy=easy, hard_rock=hard_rock)
    hp = beatmap.hp(easy=easy, hard_rock=hard_rock)

    window_300, window_100, window_50 = timing_windows(od)

    return Difficulty(
        od,
        cs,
        ar,
        hp,
        window_300,
        window_100,
        window_50,
        circle_radius(cs),
        approach_rate_preempt(ar),
        clock_rate(mods),
    )


def simulate_from_paths(replay_path, beatmap_path):
    replay = load_and_open_osr_file_from_path(replay_path)
    beatmap = load_and_open_osu_file_from_path(beatmap_path)
    times = frame_times(replay.replay_data)
    presses = build_presses(replay.replay_data, times)
    return replay, beatmap, times, presses


def simulate(beatmap_hash, replay_hash):
    beatmap_path = BEATMAP_DIR / (beatmap_hash + ".osu")
    replay_path = REPLAY_DIR / (replay_hash + ".osr")
    return simulate_from_paths(replay_path, beatmap_path)


def simulate_test_files(replay_name, beatmap_name):
    return simulate_from_paths(ROOT / replay_name, ROOT / beatmap_name)


def build_objects(beatmap, mods=0):
    easy = bool(mods & MOD_EASY)
    hard_rock = bool(mods & MOD_HARDROCK)

    objects = []
    spinners = 0
    for hit_object in beatmap.hit_objects(easy=easy, hard_rock=hard_rock):
        if isinstance(hit_object, Spinner):
            spinners += 1
            continue
        objects.append(HitObject(
            len(objects),
            hit_object.time.total_seconds() * 1000.0,
            hit_object.position.x,
            hit_object.position.y,
        ))

    objects.sort(key=lambda hit_object: hit_object.time)
    return objects, spinners


def judge_presses(objects, presses, difficulty):
    counts = {name: 0 for name in JUDGEMENTS}
    hit_errors = []
    radius_squared = difficulty.radius * difficulty.radius

    pending = 0
    for press in presses:
        while pending < len(objects) and press.time > objects[pending].time + difficulty.window_50:
            counts["miss"] += 1
            pending += 1

        if pending == len(objects):
            break

        current = objects[pending]

        if press.time < current.time - difficulty.window_50:
            continue

        dx = press.x - current.x
        dy = press.y - current.y
        if dx * dx + dy * dy > radius_squared:
            continue

        hit_error = press.time - current.time
        counts[judge(hit_error, difficulty.od)] += 1
        hit_errors.append(hit_error)
        pending += 1

    counts["miss"] += len(objects) - pending
    return counts, hit_errors


def header_counts(replay):
    return {
        "300": replay.count_300,
        "100": replay.count_100,
        "50": replay.count_50,
        "miss": replay.count_miss,
    }


def match_rate(counts, header):
    total = sum(header.values())
    if total == 0:
        return 0.0
    return sum(min(counts[name], header[name]) for name in header) / total


def frame_coverage(times, objects):
    if not times or not objects:
        return 0.0
    last_object_time = objects[-1].time
    if last_object_time <= 0:
        return 1.0
    return times[-1] / last_object_time


def simulate_replay(replay, beatmap):
    mods = int(replay.mods)
    header = header_counts(replay)
    difficulty = read_difficulty(beatmap, mods)
    objects, spinners = build_objects(beatmap, mods)

    best = None
    best_coverage = 0.0
    for keep_lead_in in (True, False):
        times = frame_times(replay.replay_data, keep_lead_in)
        presses = build_presses(replay.replay_data, times)
        counts, hit_errors = judge_presses(objects, presses, difficulty)
        counts["300"] += spinners
        rate = match_rate(counts, header)
        coverage = frame_coverage(times, objects)
        best_coverage = max(best_coverage, coverage)
        if best is None or rate > best.match_rate:
            best = Result(counts, header, rate, keep_lead_in, coverage,
                          False, hit_errors, times, presses)

    return best._replace(
        coverage=best_coverage,
        truncated=best_coverage < COVERAGE_THRESHOLD,
    )


if __name__ == "__main__":
    replay, beatmap, times, presses = simulate_test_files("test.osr", "test.osu")
    result = simulate_replay(replay, beatmap)

    print("reconstructed", result.counts)
    print("header       ", result.header)
    print(f"match rate    {result.match_rate:.2%}")
    print(f"lead-in kept  {result.keep_lead_in}")
    print(f"coverage      {result.coverage:.1%}   truncated: {result.truncated}")
