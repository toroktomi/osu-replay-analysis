PLAYFIELD_WIDTH = 512
PLAYFIELD_HEIGHT = 384

M1 = 1
M2 = 2
K1 = 4
K2 = 8
SMOKE = 16

KEY_1 = M1 | K1
KEY_2 = M2 | K2

MOD_NOFAIL = 1
MOD_EASY = 2
MOD_TOUCHDEVICE = 4
MOD_HIDDEN = 8
MOD_HARDROCK = 16
MOD_SUDDENDEATH = 32
MOD_DOUBLETIME = 64
MOD_RELAX = 128
MOD_HALFTIME = 256
MOD_NIGHTCORE = 512
MOD_FLASHLIGHT = 1024
MOD_AUTOPLAY = 2048
MOD_SPUNOUT = 4096
MOD_AUTOPILOT = 8192
MOD_PERFECT = 16384

DOUBLETIME_RATE = 1.5
HALFTIME_RATE = 0.75

HARDROCK_CS_MULTIPLIER = 1.3
HARDROCK_DIFFICULTY_MULTIPLIER = 1.4
EASY_MULTIPLIER = 0.5
MAX_DIFFICULTY_SETTING = 10.0


def window_300(od):
    return 80.0 - 6.0 * od


def window_100(od):
    return 140.0 - 8.0 * od


def window_50(od):
    return 200.0 - 10.0 * od


def timing_windows(od):
    return window_300(od), window_100(od), window_50(od)


def circle_radius(cs):
    return 54.4 - 4.48 * cs


def approach_rate_preempt(ar):
    if ar < 5.0:
        return 1200.0 + 600.0 * (5.0 - ar) / 5.0
    if ar > 5.0:
        return 1200.0 - 750.0 * (ar - 5.0) / 5.0
    return 1200.0


def clock_rate(mods):
    if mods & (MOD_DOUBLETIME | MOD_NIGHTCORE):
        return DOUBLETIME_RATE
    if mods & MOD_HALFTIME:
        return HALFTIME_RATE
    return 1.0


def judge(hit_error_ms, od):
    w300, w100, w50 = timing_windows(od)
    error = abs(hit_error_ms)
    if error <= w300:
        return "300"
    if error <= w100:
        return "100"
    if error <= w50:
        return "50"
    return None
