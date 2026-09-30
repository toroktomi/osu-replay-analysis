import sys
from pathlib import Path

import streamlit as st
from streamlit.runtime import exists as streamlit_runtime_exists

if not streamlit_runtime_exists():
    sys.exit("This is a Streamlit app. Start it with:"
             "\n    python -m streamlit run app.py")

sys.path.insert(0, str(Path(__file__).resolve().parent / "code"))

from app_logic import (
    MIN_OBJECTS_PER_BUCKET,
    analyse,
    format_report,
    format_rows,
    load_baseline,
    load_model,
    pair_uploads,
    predict,
    rank_metrics,
)

WEAKNESS_THRESHOLD = 40.0
STRENGTH_THRESHOLD = 75.0

cached_baseline = st.cache_resource(load_baseline)
cached_model = st.cache_resource(load_model)

st.set_page_config(page_title="osu! Replay Analysis", layout="centered")
st.title("osu! Replay Analysis")
st.caption("Drop in several replays together with their beatmaps. "
           "Each replay is matched to its beatmap automatically. "
           "More replays means more of your profile can be measured.")

uploads = st.file_uploader("`.osr` and `.osu` files",
                           accept_multiple_files=True, type=["osr", "osu"])

baseline = cached_baseline()

if not uploads:
    st.info(f"Compared against {baseline['players']} players, "
            f"{baseline['replays']:,} plays and {baseline['objects']:,} hit objects.")
    st.stop()

paired, unmatched = pair_uploads(uploads)

for name, reason in unmatched:
    st.warning(f"{name} — {reason}")

if not paired:
    st.stop()

with st.spinner(f"Re-simulating {len(paired)} replays…"):
    frame, report = analyse(paired)

st.subheader("Replays read")
st.caption("Every replay is re-simulated and checked against the score stored "
           "inside it. Anything below 95% is excluded from your profile.")
st.dataframe(format_report(report), hide_index=True, width="stretch")

if frame is None:
    st.error("No replay reconstructed cleanly enough to profile.")
    st.stop()

ranked = rank_metrics(frame, baseline)

if not ranked:
    st.warning("Not enough data to rank anything yet — upload more replays. "
               f"Each bucket needs {MIN_OBJECTS_PER_BUCKET}+ hit objects.")
else:
    weaknesses = [row for row in ranked if row["percentile"] < WEAKNESS_THRESHOLD]
    strengths = [row for row in ranked if row["percentile"] >= STRENGTH_THRESHOLD]

    st.subheader("Your weakest areas")
    if weaknesses:
        st.dataframe(format_rows(weaknesses), hide_index=True, width="stretch")
    else:
        st.write(f"Nothing below the {WEAKNESS_THRESHOLD:.0f}th percentile.")

    st.subheader("Your strengths")
    if strengths:
        st.dataframe(format_rows(strengths[::-1]), hide_index=True, width="stretch")
    else:
        st.write(f"Nothing above the {STRENGTH_THRESHOLD:.0f}th percentile.")

    with st.expander("All measured buckets"):
        st.dataframe(format_rows(ranked), hide_index=True, width="stretch")

st.subheader("Accuracy prediction")
st.caption("Predicted from pattern difficulty and your profile, next to what you "
           "actually scored. The profile is built from these same replays, so "
           "predictions improve as you add more.")
st.dataframe(predict(frame, cached_model()), width="stretch")

st.caption(f"Population: {baseline['players']} players, "
           f"{baseline['objects']:,} hit objects. "
           f"Buckets need {MIN_OBJECTS_PER_BUCKET}+ objects to be ranked.")
