"""
app.py

Streamlit front-end for the sales pipeline Q&A agent. Run locally with:
    streamlit run app.py

Deployed on Streamlit Community Cloud, this file is the entry point.
"""

import json
import os
import sys

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

# Make src/ importable regardless of where streamlit is launched from.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from pipeline_agent import ensure_database_exists, get_summary_stats, run_agent_turn  # noqa: E402

load_dotenv()  # populates os.environ from .env for local dev; no-op if absent


def get_api_key() -> str | None:
    """Prefer Streamlit Cloud's secrets manager; fall back to .env locally.

    st.secrets raises if no secrets.toml/secrets config exists at all
    (e.g. plain local dev with only a .env file), so we catch that and
    fall back rather than letting the app crash on startup.
    """
    try:
        return st.secrets["OPENAI_API_KEY"]
    except Exception:
        return os.environ.get("OPENAI_API_KEY")


st.set_page_config(
    page_title="Sales Pipeline Agent",
    page_icon="📊",
    layout="wide",
)

# A little custom CSS on top of the config.toml theme: card styling for the
# stat tiles and sidebar buttons. Streamlit doesn't expose a style API for
# this, so injecting scoped CSS via st.markdown(unsafe_allow_html=True) is
# the standard escape hatch - target Streamlit's own data-testid attributes
# rather than fighting its generated class names, which change across
# versions.
st.markdown(
    """
    <style>
    [data-testid="stMetric"] {
        background-color: #f9f9f7;
        border: 1px solid rgba(11,11,11,0.10);
        border-radius: 10px;
        padding: 16px 18px;
    }
    [data-testid="stMetricLabel"] { color: #52514e; }
    section[data-testid="stSidebar"] .stButton button {
        border-radius: 8px;
        text-align: left;
        border: 1px solid rgba(11,11,11,0.10);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📊 Sales Pipeline Q&A Agent")
st.caption(
    "Ask questions in plain English. An LLM agent writes real SQL, runs it "
    "against a live SQLite database, and answers from the actual result — "
    "not from a guess."
)

# --- One-time setup: build the database if this is a fresh deploy ---
# data/ is git-ignored by design (see PROJECT_PLAN.md), so a freshly
# cloned environment (like this one on Streamlit Cloud) starts without
# pipeline.db. ensure_database_exists() regenerates it on first run only.
with st.spinner("Setting up database (first run only)..."):
    ensure_database_exists()

api_key = get_api_key()
if not api_key:
    st.error(
        "No OPENAI_API_KEY found. Set it in a local .env file, or in this "
        "app's Secrets if deployed on Streamlit Cloud."
    )
    st.stop()

client = OpenAI(api_key=api_key)

# st.cache_data memoizes the function's return value for this session.
# Without it, get_summary_stats() (3 SQL queries) would re-run on every
# single rerun - and Streamlit reruns the whole script on every chat
# message, button click, anything. The data doesn't change mid-session,
# so there's no reason to repeat the work each time.
@st.cache_data
def load_summary_stats():
    return get_summary_stats()


stats = load_summary_stats()

tile1, tile2, tile3, tile4 = st.columns(4)
tile1.metric("Open Pipeline Value", f"${stats['open_pipeline_value']:,.0f}")
tile2.metric("Win Rate", f"{stats['win_rate_pct']}%")
tile3.metric("Deals Won", f"{stats['deals_won']}")
tile4.metric("Stalled Deals (30d+)", f"{stats['stalled_deals']}")

st.divider()

# --- Session state: survives Streamlit's rerun-on-every-interaction model ---
# `chat_history` is what we render as chat bubbles (display-friendly).
# `api_messages` is the raw OpenAI-format conversation (includes tool_calls
# and tool_result blocks) that actually gets sent to the model each turn -
# these are deliberately two separate lists, since the display format and
# the API's required format aren't the same shape.
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "api_messages" not in st.session_state:
    st.session_state.api_messages = []

# --- Sidebar: schema reference + example questions ---
with st.sidebar:
    st.header("About")
    st.markdown(
        "This agent has **read-only** access to a sales CRM database "
        "(`leads`, `reps`, `activities`). Every query is enforced "
        "read-only at the SQLite engine level - not just by asking the "
        "model nicely - so it's safe to point at arbitrary questions."
    )

    st.header("Try asking")
    example_questions = [
        "Which rep has the highest win rate?",
        "How many leads came from each source?",
        "What's the average sales cycle for won deals?",
        "Which open deals have had no activity in 30+ days?",
        "Compare average deal value for members vs non-members.",
    ]
    clicked_example = None
    for question in example_questions:
        if st.button(question, use_container_width=True):
            clicked_example = question

    st.header("Schema")
    st.code(
        "leads(lead_id, company_name, lead_source, rep_id,\n"
        "      stage, deal_value, is_member,\n"
        "      created_date, close_date)\n"
        "reps(rep_id, rep_name, region, hire_date)\n"
        "activities(activity_id, lead_id, rep_id,\n"
        "           activity_date, activity_type)",
        language="text",
    )

# --- Render existing conversation ---
AVATARS = {"user": "🧑‍💼", "assistant": "📊"}

for turn in st.session_state.chat_history:
    with st.chat_message(turn["role"], avatar=AVATARS.get(turn["role"])):
        st.write(turn["content"])
        # Show the SQL trail for assistant turns that used the tool -
        # this is the "showcase the mechanism, don't hide it" part.
        if turn.get("trace"):
            with st.expander("How I got this answer"):
                for step in turn["trace"]:
                    st.code(step["sql"], language="sql")
                    parsed = json.loads(step["result"])
                    st.json(parsed)

# --- Input: either typed, or an example button click ---
typed_prompt = st.chat_input("Ask a question about the sales pipeline...")
prompt = typed_prompt or clicked_example

if prompt:
    st.session_state.chat_history.append({"role": "user", "content": prompt})
    st.session_state.api_messages.append({"role": "user", "content": prompt})

    with st.spinner("Thinking..."):
        answer, trace = run_agent_turn(client, st.session_state.api_messages)

    st.session_state.chat_history.append(
        {"role": "assistant", "content": answer, "trace": trace}
    )

    # Streamlit reruns the whole script on the next interaction anyway, but
    # calling it explicitly here redraws immediately with the new turn
    # instead of waiting for the user's next click.
    st.rerun()
