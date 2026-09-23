"""
verification_agent.py

Standalone, not wired into app.py or run_agent_turn yet. A second,
independent agent that re-derives an answer from scratch and checks it
against the first agent's result - catching cases where the first SQL
was subtly wrong even though it ran without error (the class of bug the
eval suite already found once: a plausible-looking query that isn't
actually correct).

"Independent" specifically means: this call gets only the question, not
the first query's SQL or result - so it can't just agree with what it's
handed. It has to derive its own SQL the same way the first agent did.
"""

import json

from openai import OpenAI

from pipeline_agent import MODEL, TOOLS, query_database

VERIFICATION_SYSTEM_PROMPT = (
    "You are a SQL verification agent. Given this question, independently "
    "generate a SQL query to answer it - do not assume any prior query is "
    "correct."
)


def _row_value_tuples(rows: list) -> list:
    """Reduce a list of row-dicts to a sorted list of sorted value-tuples,
    so column-NAME differences (aliases) and ordering differences (row
    order, column order within a row) don't affect the comparison - only
    the actual data values do.

    Two independently-generated-but-correct queries routinely pick
    different aliases for the same column (e.g. `won_deals` vs
    `won_deals_count`), which made the earlier dict-equality comparison
    report `verified: False` on two answers that actually agreed. This
    strips names entirely and compares values only.

    Each value is converted with str() before sorting/comparing - not just
    as a sort key but as the value itself - since a single row can mix
    types (e.g. a string company_name alongside a numeric deal_value),
    which Python can't sort with `<` directly. This also sidesteps int-vs-
    numeric-string mismatches, which aren't expected to occur here (SQLite
    column types round-trip consistently through query_database's JSON
    encoding regardless of alias), so stringifying doesn't introduce any
    real risk of a false positive in practice.
    """
    return sorted(tuple(sorted(str(v) for v in row.values())) for row in rows)


def compare_results(first_result: str, second_result: str) -> dict:
    """The error-check + value comparison verify_answer() runs on two
    query_database() JSON strings, factored out so it can be exercised
    directly - e.g. against a manually-constructed second_result - without
    needing a live LLM call to produce one. verify_answer() below is just
    this function plus "go generate a real second_result first."
    """
    first_parsed = json.loads(first_result)
    second_parsed = json.loads(second_result)
    first_error = first_parsed.get("error")
    second_error = second_parsed.get("error")

    if first_error is not None or second_error is not None:
        # Either side failing to run is never a verification, regardless
        # of whether the two (non-)results happen to be equal - two
        # queries that both errored are not "agreeing," they're both
        # uninformative, and reporting that as verified would hide a
        # real problem (e.g. a query malformed enough that neither the
        # first nor second attempt could run it).
        verified = False
    else:
        # Value-only comparison (see _row_value_tuples) - not the whole
        # JSON blob, and not dict-equality on the rows either, since a
        # truncated/total_rows_matched flag or a differing column alias
        # can legitimately differ between two syntactically different-
        # but-equivalent queries while the actual data still agrees.
        first_values = _row_value_tuples(first_parsed.get("rows", []))
        second_values = _row_value_tuples(second_parsed.get("rows", []))
        verified = first_values == second_values

    return {"verified": verified, "first_error": first_error, "second_error": second_error}


def verify_answer(client: OpenAI, question: str, first_sql: str, first_result: str) -> dict:
    """Independently re-derive SQL for `question` and compare against the
    first agent's result.

    Deliberately does NOT see `first_sql` - only `question` goes into the
    verification call's messages. first_sql/first_result are accepted as
    parameters purely so this function can include them in its return
    value for comparison/display; they play no role in generating the
    second query.

    tool_choice is forced to query_database (rather than left as "auto"
    like the first agent) so this call is guaranteed to always produce a
    second SQL query to compare. The first agent's loop needs "auto" so
    it can end the conversation with a plain-text answer once it has
    enough information; this call's entire job is "produce one comparable
    query," so there's no case where ending without one is useful here.
    """
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": VERIFICATION_SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        tools=TOOLS,
        tool_choice={"type": "function", "function": {"name": "query_database"}},
    )

    tool_call = response.choices[0].message.tool_calls[0]
    arguments = json.loads(tool_call.function.arguments)
    second_sql = arguments["sql"]

    second_result = query_database(second_sql)
    comparison = compare_results(first_result, second_result)

    return {
        "verified": comparison["verified"],
        "first_sql": first_sql,
        "second_sql": second_sql,
        "first_result": first_result,
        "second_result": second_result,
        "first_error": comparison["first_error"],
        "second_error": comparison["second_error"],
    }
