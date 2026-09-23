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
from verification_agent import compare_results, verify_answer

load_dotenv()
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


def run_verification_cases() -> tuple[int, int]:
    """Two checks on the verification layer (src/verification_agent.py),
    originally proved out by hand in src/test_verify_answer.py - now run
    automatically so a future change can't silently break either one.

    Case A (live call): two independently-generated queries for the same
    question very likely use different column aliases for the same value
    - verify_answer() should still say verified=True, since only the
    values are compared, not the column names.

    Case B (no LLM call): compare_results() against a manually-constructed
    second_result holding a genuinely different value. This is the case
    that proves Case A passing isn't just the comparison being permissive
    by accident - it should say verified=False here.
    """
    passed = 0
    total = 2

    question = "How many deals did rep REP004 win?"
    first_sql = "SELECT COUNT(*) AS won_deals FROM leads WHERE rep_id = 'REP004' AND stage = 'Won'"
    first_result = '{"rows": [{"won_deals": 17}]}'

    result = verify_answer(client, question, first_sql, first_result)
    ok = result["verified"] is True
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] Verification Case A: alias mismatch, same value (expect verified=True)")
    print(f"        second SQL generated: {result['second_sql']}")
    print(f"        second result: {result['second_result']}\n")
    if ok:
        passed += 1

    mismatched_second_result = '{"rows": [{"count": 16}]}'
    comparison = compare_results(first_result, mismatched_second_result)
    ok = comparison["verified"] is False
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] Verification Case B: genuine value mismatch, 17 vs 16 (expect verified=False)")
    print(f"        first result:  {first_result}")
    print(f"        second result: {mismatched_second_result} (manually constructed)\n")
    if ok:
        passed += 1

    return passed, total


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

    print(f"{passed_count}/{len(CASES)} agent cases passed")

    print("\nRunning verification-layer cases...\n")
    v_passed, v_total = run_verification_cases()
    print(f"{v_passed}/{v_total} verification cases passed")

    total_passed = passed_count + v_passed
    total_cases = len(CASES) + v_total
    print(f"\n{total_passed}/{total_cases} total")

    # A non-zero exit code is how a CI step (or you, from the terminal)
    # tells "did this run fail" without reading the printed output -
    # this is what makes the script automatable later.
    if total_passed < total_cases:
        sys.exit(1)


if __name__ == "__main__":
    main()
