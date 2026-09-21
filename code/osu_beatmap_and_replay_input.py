import sys
from pathlib import Path
from typing import NamedTuple

import osrparse
from slider import Beatmap

from osu_rules import KEY_1, KEY_2

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
REPLAY_FILE = ROOT / "test.osr"
BEATMAP_FILE = ROOT / "test.osu"


class Press(NamedTuple):
    time: int
    x: float
    y: float
    key: str


def load_and_open_osr_file_from_path(path):
    loaded_osr = osrparse.Replay.from_path(path)
    return loaded_osr


def load_and_open_osu_file_from_path(path):
    loaded_osu = Beatmap.from_path(path)
    return loaded_osu


def frame_times(frames, keep_lead_in=True):
    times = []
    running_total = 0
    for index, frame in enumerate(frames):
        delta = frame.time_delta
        if index == 0 and delta < 0 and not keep_lead_in:
            delta = 0
        running_total += delta
        times.append(running_total)
    return times


def build_presses(frames, times):
    presses = []
    previous_keys = 0
    for frame, time in zip(frames, times):
        keys = int(frame.keys)
        if keys & KEY_1 and not previous_keys & KEY_1:
            presses.append(Press(time, frame.x, frame.y, "K1"))
        if keys & KEY_2 and not previous_keys & KEY_2:
            presses.append(Press(time, frame.x, frame.y, "K2"))
        previous_keys = keys
    return presses


replay = load_and_open_osr_file_from_path(REPLAY_FILE)
beatmap = load_and_open_osu_file_from_path(BEATMAP_FILE)

times = frame_times(replay.replay_data)
presses = build_presses(replay.replay_data, times)
