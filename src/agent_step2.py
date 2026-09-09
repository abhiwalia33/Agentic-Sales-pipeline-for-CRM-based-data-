"""
agent_step2.py

Step 2: give the model ONE tool (query_database) and do a single manual
round trip: ask -> model requests a tool call -> we run it for real ->
we send the result back -> model gives a final answer.

This is NOT a loop yet (that's step 3). This script only handles the
model asking for a tool exactly once.
"""

import json
import sqlite3

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI()

DB_PATH = "data/pipeline.db"


def query_database(sql: str) -> str:
    """Run a read-only SQL query against pipeline.db and return the rows as JSON text.

    This is a REAL Python function. The model can never run this itself -
    it can only ask us (by name) to run it with certain arguments. We are
    the ones who actually call sqlite3 here.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us read columns by name
    try:
        cursor = conn.execute(sql)
        rows = [dict(row) for row in cursor.fetchall()]
        return json.dumps(rows)
    except sqlite3.Error as e:
        # If the model writes bad SQL, don't crash - tell it what went wrong
        # as the tool result, so it can see the error like a human would.
        return json.dumps({"error": str(e)})
    finally:
        conn.close()


# This is the schema that DESCRIBES query_database to the model.
# The model reads this description and decides when/how to call it -
# it never sees your Python code, only this description.
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
                "activities(activity_id, lead_id, rep_id, activity_date, activity_type)."
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

user_question = "How many deals did rep REP004 win?"

messages = [{"role": "user", "content": user_question}]

# --- First API call: give the model the tool, see if it wants to use it ---
response = client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
    tools=tools,
)

assistant_message = response.choices[0].message

if not assistant_message.tool_calls:
    # The model answered directly without needing the tool (shouldn't
    # happen here, but worth handling / noticing if it does).
    print("Model answered without calling a tool:")
    print(assistant_message.content)
else:
    # The model wants to call a tool. It can technically request more than
    # one, but we're only handling exactly one call in this step-2 script.
    tool_call = assistant_message.tool_calls[0]

    # tool_call.function.arguments is a JSON *string* the model generated -
    # always parse it, never trust it as already-valid Python.
    arguments = json.loads(tool_call.function.arguments)
    generated_sql = arguments["sql"]

    print("Model generated this SQL:")
    print(generated_sql)
    print()

    # Actually run it for real.
    tool_result = query_database(generated_sql)

    print("Query result:")
    print(tool_result)
    print()

    # --- Second API call: send the tool result back, get the final answer ---
    # messages must now include, in order:
    #   1. the original user message (already in `messages`)
    #   2. the assistant's message that requested the tool call
    #   3. a new "tool" message with the result, tagged with the same tool_call_id
    messages.append(assistant_message)
    messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": tool_result,
        }
    )

    followup = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=tools,
    )

    print("Final answer:")
    print(followup.choices[0].message.content)
