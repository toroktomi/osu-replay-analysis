import sys
from pathlib import Path

import osrparse
from slider import Beatmap
from slider.beatmap import Circle, Slider, Spinner

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
REPLAY_FILE = ROOT / "test.osr"
BEATMAP_FILE = ROOT / "test.osu"

replay = osrparse.Replay.from_path(REPLAY_FILE)
beatmap = Beatmap.from_path(BEATMAP_FILE)

print("REPLAY")
print(f"mode           {replay.mode}")
print(f"game version   {replay.game_version}")
print(f"player         {replay.username}")
print(f"beatmap md5    {replay.beatmap_hash}")
print(f"mods           {replay.mods!r} = {int(replay.mods)}")
print(f"300 / 100 / 50 {replay.count_300} / {replay.count_100} / {replay.count_50}")
print(f"geki / katu    {replay.count_geki} / {replay.count_katu}")
print(f"miss           {replay.count_miss}")
print(f"score          {replay.score:,}")
print(f"max combo      {replay.max_combo}")
print(f"perfect        {replay.perfect}")
print(f"timestamp      {replay.timestamp}")
print(f"frames         {len(replay.replay_data)}")
print()
print("FIRST 10 FRAMES")
print(f"{'delta':>8} {'x':>8} {'y':>8} {'keys':>6}")
for frame in replay.replay_data[:10]:
    print(f"{frame.time_delta:>8} {frame.x:>8.1f} {frame.y:>8.1f} {int(frame.keys):>6}")

print()
print("LAST 3 FRAMES")
for frame in replay.replay_data[-3:]:
    print(f"{frame.time_delta:>8} {frame.x:>8.1f} {frame.y:>8.1f} {int(frame.keys):>6}")

print()
print("BEATMAP")
print(f"artist         {beatmap.artist}")
print(f"title          {beatmap.title}")
print(f"version        {beatmap.version}")
print(f"creator        {beatmap.creator}")
print(f"HP / OD        {beatmap.hp_drain_rate} / {beatmap.overall_difficulty}")
print(f"AR / CS        {beatmap.approach_rate} / {beatmap.circle_size}")

hit_objects = beatmap.hit_objects()
kinds = {"circle": 0, "slider": 0, "spinner": 0, "other": 0}
for h in hit_objects:
    if isinstance(h, Circle):
        kinds["circle"] += 1
    elif isinstance(h, Slider):
        kinds["slider"] += 1
    elif isinstance(h, Spinner):
        kinds["spinner"] += 1
    else:
        kinds["other"] += 1

print(f"hit objects    {len(hit_objects)}  {kinds}")

print()
print("FIRST 10 HIT OBJECTS")
print(f"{'time_ms':>10} {'x':>7} {'y':>7}  type")
for ho in hit_objects[:10]:
    time_ms = ho.time.total_seconds() * 1000
    print(f"{time_ms:>10.0f} {ho.position.x:>7.0f} {ho.position.y:>7.0f}  {type(ho).__name__}")

print()
print("LAST HIT OBJECT")
last = hit_objects[-1]
print(f"{last.time.total_seconds() * 1000:>10.0f} {last.position.x:>7.0f} "
      f"{last.position.y:>7.0f}  {type(last).__name__}")
