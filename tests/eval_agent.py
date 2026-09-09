"""
eval_agent.py

A very simple eval suite for the sales pipeline agent.

This is the exact same check we did by hand, over and over, throughout
this project's build: ask the agent a question, compare its answer
against a number we already verified directly with SQL. The only
difference here is it's a script you re-run with one command, instead of
re-testing by eye every time you change the tool description or prompt.

Run it:
    python tests/eval_agent.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dotenv import load_dotenv
from openai import OpenAI

from pipeline_agent import ensure_database_exists, run_agent_turn

load_dotenv()

# Diagnostic (temporary): print whether the key arrived at all, and its
# length - never the value itself. This runs before OpenAI() so it shows
# up right here in "Run eval suite" output, even when the client init
# below fails - no need to hunt for a separate step in the CI UI.
_key = os.environ.get("OPENAI_API_KEY")
print(f"[diagnostic] OPENAI_API_KEY present: {bool(_key)}, length: {len(_key) if _key else 0}")

client = OpenAI()

# Each case: a question, and a substring we expect to see in the final
# answer. The expected values are all numbers we already verified by
# running the real SQL directly against pipeline.db earlier in this
# project - this is just re-checking that the agent still gets them right.
CASES = [
    {
        "question": "How many deals did rep REP004 win?",
        "expected_substring": "17",
    },
    {
        "question": "How many leads came from the Referral source?",
        "expected_substring": "142",
    },
    {
        "question": "What is the overall win rate across all closed deals?",
        "expected_substring": "48.9",
    },
    {
        "question": "How many deals are currently in the Negotiation stage?",
        "expected_substring": "28",
    },
]


def main():
    # data/ is git-ignored, so a fresh checkout (CI, a new clone) has no
    # pipeline.db yet - same bootstrap app.py relies on for Streamlit Cloud.
    ensure_database_exists()

    print(f"Running {len(CASES)} eval cases against gpt-4o...\n")
    passed_count = 0

    for i, case in enumerate(CASES, start=1):
        messages = [{"role": "user", "content": case["question"]}]
        answer, _trace = run_agent_turn(client, messages)

        passed = case["expected_substring"] in answer
        status = "PASS" if passed else "FAIL"

        print(f"[{status}] Case {i}: {case['question']}")
        print(f"        expected to contain: {case['expected_substring']}")
        print(f"        actual answer: {answer}\n")

        if passed:
            passed_count += 1

    print(f"{passed_count}/{len(CASES)} passed")

    # A non-zero exit code is how a CI step (or you, from the terminal)
    # tells "did this run fail" without reading the printed output -
    # this is what makes the script automatable later.
    if passed_count < len(CASES):
        sys.exit(1)


if __name__ == "__main__":
    main()
