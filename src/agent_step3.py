"""
agent_step3.py

Step 3: the real agentic loop.

Step 2 hardcoded "exactly one tool call, exactly two API calls." That only
works because we happened to pick a question answerable in one query. Some
questions need the model to call the tool multiple times (or call it with
several queries in the same turn) before it has enough to answer.

The fix: instead of writing the "call -> check -> respond" pattern out by
hand once, wrap it in a loop that keeps repeating until the model stops
asking for tools.

Still using a hardcoded question here on purpose (step 3.5 makes it
interactive) - easier to debug the loop mechanics against a known-answer
question first.
"""

import json
import sqlite3

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI()

DB_PATH = "data/pipeline.db"


def query_database(sql: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(sql)
        rows = [dict(row) for row in cursor.fetchall()]
        return json.dumps(rows)
    except sqlite3.Error as e:
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
                # Grounding fix #2 (2026-09-09): the model computed win rate as
                # won / ALL leads (including still-open ones), giving 35.85% for
                # REP014 instead of the correct 59.38%. It also missed that REP014
                # and REP012 are tied for highest - "the highest" implied a unique
                # answer that doesn't exist. Root cause was the same as the
                # 'Closed Won' bug: the model applied a plausible-sounding but
                # wrong assumption (a generic formula) instead of this project's
                # actual convention, because nothing told it the convention.
                # Lesson: business-logic definitions need the same explicit
                # grounding as literal data values - don't assume "common sense"
                # math matches your domain's actual definition.
                "Business definitions: 'win rate' means Won deals divided by "
                "CLOSED deals only (stage IN ('Won','Lost')) - do NOT divide by "
                "all leads, since open leads (New/Contacted/Qualified/Proposal/"
                "Negotiation) haven't been won or lost yet."
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

user_question = user_question = input("Ask a question about the sales pipeline: ")

messages = [{"role": "user", "content": user_question}]

# Safety valve: if something goes wrong and the model just keeps asking for
# tools forever, we don't want to loop (and spend money) infinitely. This
# cap is unrelated to your data - it's a guard against a runaway loop.
MAX_ITERATIONS = 10

for iteration in range(MAX_ITERATIONS):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=tools,
    )
    assistant_message = response.choices[0].message

    # --- Exit condition: the model has no more tool calls to make ---
    # This is the ONLY way the loop ends normally. Step 2 had this as the
    # "else" branch of an if/else; here it's what breaks the loop.
    if not assistant_message.tool_calls:
        print("Final answer:")
        print(assistant_message.content)
        break

    # The model wants to call the tool (possibly more than once this turn).
    # We must record the assistant's own turn in `messages` before we can
    # append tool results - the API requires the tool-call request to
    # appear in history before its matching tool-result message.
    messages.append(assistant_message)

    # Step 2 only ever handled tool_calls[0]. Here we handle ALL of them -
    # the model can ask for several queries in a single turn (e.g. one
    # query per rep it wants to compare), and each needs its own result
    # message tagged with its own tool_call_id.
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

    # No `break` here - the loop goes back to the top, sends the growing
    # `messages` list (now including this round's results) back to the
    # model, and the model decides again: answer now, or ask for more data.
else:
    # This `else` belongs to the `for` loop (Python quirk: a for/else runs
    # only if the loop finished WITHOUT hitting `break`) - meaning we hit
    # MAX_ITERATIONS without the model ever giving a final answer.
    print(f"Stopped after {MAX_ITERATIONS} iterations without a final answer.")
