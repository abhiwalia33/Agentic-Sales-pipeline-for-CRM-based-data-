"""
pipeline_agent.py

The core agentic Q&A logic, refactored out of agent_step4.py into a
reusable module - both a CLI and the Streamlit app import from here,
so the tool-use loop and safety guardrails exist in exactly one place.
"""

import json
import os
import re
import sqlite3

from openai import OpenAI

# Absolute path, anchored to this file's location - not the current working
# directory. A relative path like "data/pipeline.db" only works if you
# happen to run the script from the repo root; this works regardless of
# where `streamlit run` or the CLI is invoked from.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_BASE_DIR, "data", "pipeline.db")


def ensure_database_exists() -> None:
    """Build pipeline.db on the fly if it isn't there yet.

    data/ is git-ignored by design (see PROJECT_PLAN.md) - it's meant to
    be regenerated from the scripts, not committed. That's fine for local
    dev where you've already run the pipeline once, but a freshly cloned
    deployment (Streamlit Cloud, a new machine, CI) starts with no data/
    folder at all. Rather than requiring a manual setup step, we call the
    same generate -> clean pipeline the README already documents.
    """
    if os.path.exists(DB_PATH):
        return

    import sys

    sys.path.insert(0, os.path.join(_BASE_DIR, "src"))
    import generate_data
    import clean_data

    generate_data.main()
    clean_data.main()
MAX_ROWS = 200
MAX_ITERATIONS = 10
MODEL = "gpt-4o"

TOOLS = [
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
                "will be rejected. "
                "Results are capped at 200 rows. For any count, total, sum, "
                "average, or rate/percentage, compute it directly in SQL "
                "(COUNT, SUM, AVG, GROUP BY) rather than selecting raw rows - "
                "a truncated sample of raw rows is NOT representative of the "
                "full data and must never be used to estimate a statistic."
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


def query_database(sql: str) -> str:
    """Same two-layer-safe query function from step 4, unchanged."""
    stripped = sql.strip()

    if not re.match(r"^\s*SELECT\b", stripped, re.IGNORECASE):
        return json.dumps(
            {"error": "Only SELECT statements are allowed. Query was rejected before running."}
        )

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(stripped)
        all_rows = cursor.fetchall()
        rows = [dict(row) for row in all_rows[:MAX_ROWS]]

        result = {"rows": rows}
        if len(all_rows) > MAX_ROWS:
            result["truncated"] = True
            result["total_rows_matched"] = len(all_rows)
            result["rows_returned"] = MAX_ROWS

        return json.dumps(result)
    except sqlite3.Error as e:
        return json.dumps({"error": str(e)})
    finally:
        conn.close()


def get_summary_stats() -> dict:
    """Deterministic top-line KPIs, queried directly - no LLM involved.

    Not every number on the page needs to go through the agent. These are
    fixed, well-defined facts (not open-ended questions), so querying them
    directly is faster, free (no API call), and can't be subtly wrong the
    way an LLM-generated query occasionally can be. Reserve the agent for
    what it's actually for: open-ended natural-language questions.
    """
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        open_pipeline_value = conn.execute(
            "SELECT SUM(deal_value) FROM leads "
            "WHERE stage NOT IN ('Won', 'Lost')"
        ).fetchone()[0] or 0

        won, closed = conn.execute(
            "SELECT SUM(CASE WHEN stage='Won' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN stage IN ('Won','Lost') THEN 1 ELSE 0 END) "
            "FROM leads"
        ).fetchone()

        stalled_deals = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT l.lead_id, MAX(a.activity_date) AS last_activity
                FROM leads l
                LEFT JOIN activities a ON a.lead_id = l.lead_id
                WHERE l.stage NOT IN ('Won', 'Lost')
                GROUP BY l.lead_id
                HAVING last_activity IS NULL
                    OR julianday('now') - julianday(last_activity) >= 30
            )
            """
        ).fetchone()[0]

        return {
            "open_pipeline_value": open_pipeline_value,
            "win_rate_pct": round(100 * won / closed, 1) if closed else 0,
            "deals_won": won,
            "stalled_deals": stalled_deals,
        }
    finally:
        conn.close()


def run_agent_turn(client: OpenAI, messages: list) -> tuple[str, list[dict]]:
    """Run the full tool-use loop for ONE new user turn.

    `messages` must already include the new user message appended at the
    end, plus any prior conversation history (for multi-turn chat).
    Mutates `messages` in place (appending assistant/tool turns as they
    happen) so the caller's history stays in sync for the next turn.

    Returns:
        final_answer: the model's closing text response.
        trace: a list of {"sql": ..., "result": ...} dicts, one per tool
            call made during this turn - this is what the Streamlit UI
            will show in a "how I got this answer" expander, so the app
            demonstrates the tool-use mechanism instead of hiding it.
    """
    trace: list[dict] = []

    for _ in range(MAX_ITERATIONS):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
        )
        assistant_message = response.choices[0].message

        if not assistant_message.tool_calls:
            messages.append({"role": "assistant", "content": assistant_message.content})
            return assistant_message.content, trace

        messages.append(assistant_message)

        for tool_call in assistant_message.tool_calls:
            arguments = json.loads(tool_call.function.arguments)
            generated_sql = arguments["sql"]
            tool_result = query_database(generated_sql)

            trace.append({"sql": generated_sql, "result": tool_result})

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )

    return (
        f"Stopped after {MAX_ITERATIONS} iterations without a final answer.",
        trace,
    )
