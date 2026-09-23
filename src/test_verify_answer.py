"""
test_verify_answer.py (manual, standalone - NOT part of tests/eval_agent.py)

Two cases:

1. verify_answer() against a known-correct question, exercising the real
   LLM call end-to-end (same "REP004 win count" question as
   tests/eval_agent.py Case 1). Expected: verified True, even though the
   two independently-generated queries will very likely use different
   column aliases - that's exactly what _row_value_tuples()'s value-only
   comparison is for.

2. compare_results() directly, with a manually-constructed second_result
   holding a genuinely different value (16, not 17) - no LLM call needed
   for this one, since we're supplying second_result ourselves rather
   than deriving it. Expected: verified False. This is the case that
   proves the comparison isn't just permissive by accident (e.g. always
   returning True) - it still correctly catches a real mismatch.

Run:
    python src/test_verify_answer.py
"""

from dotenv import load_dotenv
from openai import OpenAI

from verification_agent import compare_results, verify_answer

load_dotenv()
client = OpenAI()

question = "How many deals did rep REP004 win?"
first_sql = "SELECT COUNT(*) AS won_deals FROM leads WHERE rep_id = 'REP004' AND stage = 'Won'"
first_result = '{"rows": [{"won_deals": 17}]}'

print("=== Case 1: known-correct question, live verify_answer() call ===")
print("Question:", question)
print()

result = verify_answer(client, question, first_sql, first_result)

print("First SQL:     ", result["first_sql"])
print("First result:  ", result["first_result"])
print("First error:   ", result["first_error"])
print()
print("Second SQL:    ", result["second_sql"])
print("Second result: ", result["second_result"])
print("Second error:  ", result["second_error"])
print()
print("Verified:", result["verified"])

print()
print("=== Case 2: manually-constructed second_result with a real mismatch ===")

mismatched_second_result = '{"rows": [{"count": 16}]}'
comparison = compare_results(first_result, mismatched_second_result)

print("First result:  ", first_result)
print("First error:   ", comparison["first_error"])
print()
print("Second result: ", mismatched_second_result, "(manually constructed, not agent-generated)")
print("Second error:  ", comparison["second_error"])
print()
print("Verified:", comparison["verified"], "(expected False - genuine value mismatch, 17 vs 16)")
