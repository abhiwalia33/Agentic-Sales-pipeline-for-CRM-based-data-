"""
agent_step4.py

Step 4: safety guardrails.

Builds on step 3's loop, but query_database() is now genuinely safe to
point at arbitrary, typed-by-anyone questions - not just the "nice"
test questions we've been using so far.
"""

import json
import re
import sqlite3

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI()

DB_PATH = "data/pipeline.db"

# How many rows we'll ever hand back to the model in one tool result.
# Without this, a vague question like "show me all leads" could dump
# ~1000 rows into the conversation - wasted tokens/cost, and it can
# push the model toward summarizing noise instead of computing an answer.
MAX_ROWS = 200


def query_database(sql: str) -> str:
    """Run a read-only SQL query against pipeline.db and return the rows as JSON.

    Two independent layers of defense against a write query slipping through:

    Layer 1 (real guarantee): the connection itself is opened in SQLite's
    read-only URI mode. Even if a DELETE/DROP/UPDATE/INSERT statement
    reaches conn.execute(), SQLite will refuse it at the database-engine
    level - "attempt to write a readonly database". This doesn't depend on
    us successfully detecting every dangerous pattern in the text.

    Layer 2 (fast, clear failure): a keyword check that rejects non-SELECT
    statements before we even try to execute them. This is NOT the real
    security boundary - keyword matching can be tricked (comments, odd
    casing, etc.) - it's here purely so a blocked query fails with an
    understandable message instead of a confusing low-level DB error.
    """
    stripped = sql.strip()

    # Layer 2: reject anything that isn't a SELECT up front.
    # re.match with IGNORECASE so "select", "SELECT", "Select" all pass.
    if not re.match(r"^\s*SELECT\b", stripped, re.IGNORECASE):
        return json.dumps(
            {"error": "Only SELECT statements are allowed. Query was rejected before running."}
        )

    # sqlite3.connect() with uri=True lets us pass query-string options in
    # the path. mode=ro opens the file read-only at the SQLite engine level -
    # this is Layer 1, the actual enforcement.
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(stripped)
        all_rows = cursor.fetchall()
        rows = [dict(row) for row in all_rows[:MAX_ROWS]]

        result = {"rows": rows}
        if len(all_rows) > MAX_ROWS:
            # Be honest with the model about truncation, so it doesn't
            # confidently report a total count as if it saw everything.
            result["truncated"] = True
            result["total_rows_matched"] = len(all_rows)
            result["rows_returned"] = MAX_ROWS

        return json.dumps(result)
    except sqlite3.Error as e:
        # Layer 1 failures (e.g. "attempt to write a readonly database")
        # land here too - the model sees a clear error either way.
        return json.dumps({"error": str(e)})
    finally:
        conn.close()


tools = [
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": (
                "Run a read-only SQL SELECT query against the sales pipeline "
                "database and return the matching rows as JSON. Tables: "
                "leads(lead_id, company_name, lead_source, rep_id, stage, "
                "deal_value, is_member, created_date, close_date), "
                "reps(rep_id, rep_name, region, hire_date), "
                "activities(activity_id, lead_id, rep_id, activity_date, activity_type). "
                "Canonical stage values (use these exact strings, case-sensitive): "
                "'New', 'Contacted', 'Qualified', 'Proposal', 'Negotiation', 'Won', 'Lost'. "
                "Business definitions: 'win rate' means Won deals divided by "
                "CLOSED deals only (stage IN ('Won','Lost')) - do NOT divide by "
                "all leads, since open leads (New/Contacted/Qualified/Proposal/"
                "Negotiation) haven't been won or lost yet. "
                "Only SELECT statements are permitted - INSERT/UPDATE/DELETE/DROP "
                "will be rejected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A single SQL SELECT statement.",
                    }
                },
                "required": ["sql"],
            },
        },
    }
]

user_question = input("Ask a question about the sales pipeline: ")
messages = [{"role": "user", "content": user_question}]

MAX_ITERATIONS = 10

for iteration in range(MAX_ITERATIONS):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=tools,
    )
    assistant_message = response.choices[0].message

    if not assistant_message.tool_calls:
        print("Final answer:")
        print(assistant_message.content)
        break

    messages.append(assistant_message)

    for tool_call in assistant_message.tool_calls:
        arguments = json.loads(tool_call.function.arguments)
        generated_sql = arguments["sql"]
        print(f"[iteration {iteration}] Model generated SQL: {generated_sql}")

        tool_result = query_database(generated_sql)
        print(f"[iteration {iteration}] Query result: {tool_result}")

        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            }
        )
else:
    print(f"Stopped after {MAX_ITERATIONS} iterations without a final answer.")
