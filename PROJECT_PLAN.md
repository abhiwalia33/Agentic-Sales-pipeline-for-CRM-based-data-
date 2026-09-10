# AI Sales Pipeline — Project Plan

A small end-to-end CRM/sales-pipeline analytics project: synthetic messy CRM
data in, a clean SQLite warehouse and Power BI-ready CSVs out.

## Goal

Simulate a realistic sales pipeline analytics workflow:

1. **Generate** messy synthetic CRM data (leads/deals, reps, activities) —
   the kind of data quality issues a real CRM export actually has.
2. **Clean** it with a proper ETL script (standardize labels, parse dates,
   impute missing values, deduplicate) and load it into SQLite.
3. **Export** the cleaned tables to CSV for Power BI.
4. **Analyze** it with a set of standard sales-ops SQL queries.

## Pipeline

```
src/generate_data.py   -->  data/raw/{leads,reps,activities}.csv
src/clean_data.py       -->  data/pipeline.db  (SQLite: leads, reps, activities)
src/export_for_powerbi.py -> data/clean/{leads,reps,activities}.csv
src/analysis_queries.sql   (run against data/pipeline.db)
```

Run in order:

```bash
python src/generate_data.py
python src/clean_data.py
python src/export_for_powerbi.py
```

`data/` is generated output and is git-ignored — re-run the pipeline any
time to regenerate it. `generate_data.py` seeds `random` for reproducibility.

## Data model

**reps** — `rep_id, rep_name, region, hire_date`

**leads** (one row per deal) — `lead_id, company_name, lead_source, rep_id,
stage, deal_value, is_member, created_date, close_date`
- `stage` (canonical, post-cleaning): `New, Contacted, Qualified, Proposal,
  Negotiation, Won, Lost`. `Won`/`Lost` are closed; everything else is open.
- `is_member` — 1/0, whether the account is an existing membership customer.
- `close_date` is blank for open deals.

**activities** — `activity_id, lead_id, rep_id, activity_date, activity_type`
(`Call, Email, Meeting, Demo, Proposal Sent, Follow-up`)

## Deliberate messiness (`generate_data.py`)

- **Inconsistent date formats** — each date is written in one of `%Y-%m-%d`,
  `%d/%m/%Y`, or `%d-%m-%Y`, chosen at random per field.
- **Inconsistent stage labels** — e.g. `New`/`new`/`NEW`,
  `Won`/`won`/`Closed Won`/`CLOSED WON`.
- **~5% missing `deal_value`** — left blank.
- **~3% duplicate leads** — a deal re-entered under a slightly different
  company-name spelling (punctuation, suffix-word, case, or whitespace
  variant), but with the **same `rep_id` and `deal_value`** as the original.
  Duplicates are sampled only from the original pool, never from other
  duplicates, so there's no chained re-spelling.
- **Activity realism** — activity volume/recency correlates with deal state
  instead of being random noise:
  - Closed deals (`Won`/`Lost`) get activity clustered in the run-up to
    their `close_date`, with the last touch 0-5 days before closing.
  - Open deals: ~8% get **zero** activity ever; a further slice is
    genuinely stalled (last activity 40-120 days ago); the rest are
    actively worked (last activity within the last 0-25 days).
  - This matters for the "stalled deals" query — if activity were random,
    nearly every open deal would look equally (in)active.

## Cleaning logic (`clean_data.py`)

- **Stage normalization**: lowercase + substring match (`"won" in s` etc.)
  maps every label variant to its canonical form.
- **Date parsing**: tries exact `datetime.strptime` formats first, in order
  — `%Y-%m-%d`, then `%d/%m/%Y`, then `%d-%m-%Y` — and only falls back to
  `dateutil.parser.parse(s, dayfirst=True)` if none match. This order
  matters: handing an unambiguous ISO string straight to dateutil with
  `dayfirst=True` can silently swap day/month; trying the known exact
  formats first avoids that.
- **Missing `deal_value` imputation**: filled with the **median deal_value
  for that lead's `lead_source`** (not a single global median), falling
  back to the overall median only if a whole source group were empty.
- **Deduplication**: leads are matched on `(normalized_company_name,
  rep_id, deal_value)` — not company name alone. With a limited pool of
  synthetic company names, two genuinely unrelated leads can coincidentally
  share a normalized name; requiring `rep_id` and `deal_value` to also
  match is what correctly isolates true re-entered duplicates from that
  kind of false positive. Normalization lowercases, strips punctuation,
  drops common legal-suffix words (`inc`, `llc`, `corp`, ...), and collapses
  whitespace.
- Cleaned tables are loaded into `data/pipeline.db` (SQLite), replacing any
  existing file.

## Analysis queries (`analysis_queries.sql`)

1. Pipeline value by stage
2. Win rate
3. Conversion rate by lead source
4. Average sales cycle length (Won deals)
5. Stalled deals (open + no activity in 30+ days)
6. Rep performance (deal count / win rate / won value per rep)
7. Member vs. non-member revenue

## Power BI

`export_for_powerbi.py` dumps the three cleaned tables to
`data/clean/{leads,reps,activities}.csv`. In Power BI Desktop: **Get Data >
Text/CSV**, import each file, then relate `leads.rep_id -> reps.rep_id` and
`activities.lead_id -> leads.lead_id` in the model view.

## Agentic Q&A layer

`app.py` (Streamlit) + `src/pipeline_agent.py` expose the cleaned data through
a natural-language Q&A agent: an LLM (OpenAI `gpt-4o`) writes SQL against
`pipeline.db` via a `query_database` tool, in a loop (`run_agent_turn`) that
continues until the model has enough to answer. Two independent layers of
enforcement keep it read-only regardless of what SQL text the model
generates: the SQLite connection itself is opened in read-only URI mode
(`?mode=ro` - the real guarantee, enforced by the database engine), plus a
fast keyword check that rejects non-`SELECT` statements before execution
(fails with a clear message rather than a confusing low-level DB error).
Top-line KPIs on the page are queried directly, deterministically - not
through the LLM - since fixed facts don't need an agent round-trip.

Run locally: `streamlit run app.py` (needs `OPENAI_API_KEY` in a local
`.env`; see `.env.example`). CLI versions of the same agent, built up
incrementally while learning tool-use, live in `src/agent_step1.py`
through `agent_step4.py`.

### Known limitations (found while building it)

- **No grounding beyond the tool description = wrong guesses.** Without an
  explicit list of canonical `stage` values in the tool description, the
  model guessed a plausible-but-wrong value (`'Closed Won'` instead of
  `'Won'`) and confidently reported 0 results as fact. Fixed by naming the
  exact values in the tool description - but this class of bug (schema/value
  hallucination) can recur for any column whose real values aren't obvious
  from the name.
- **Business-logic conventions aren't inferable either.** The model
  initially computed "win rate" as wins ÷ *all* leads (including open ones)
  instead of wins ÷ *closed* deals, a different formula than the one used
  throughout this project's SQL and Power BI. Fixed with an explicit
  definition in the tool description.
- **Truncated samples got treated as representative.** Caught by
  `tests/eval_agent.py`, not by hand: asked for the overall win rate, the
  model sometimes wrote a raw row-selecting query (546 matching rows)
  instead of an aggregate `COUNT`, hit the 200-row cap, and estimated the
  rate from that truncated, non-random sample (~51% instead of the real
  48.9% - `ORDER BY` wasn't specified, so the first 200 rows aren't a fair
  sample). Fixed by telling the tool description to always compute counts/
  sums/rates directly in SQL rather than estimating from raw rows - a
  genuinely new failure class from the two above (not a wrong fact or
  formula, but the model silently trusting a truncated sample).
- **Ties get silently dropped.** A "who has the highest X" question backed
  by `ORDER BY ... LIMIT 1` picks one arbitrary row when several are tied,
  without surfacing that a tie exists. Not fixed - documented as a known
  gap; the general fix would be re-querying for ties whenever the top value
  repeats.
- **Predictive extrapolation without flagging the assumption.** Asked to
  estimate how many "Negotiation"-stage leads would convert, the agent
  applied the *overall* blended win rate (48.9%, across every stage) to a
  more-advanced-stage subgroup that plausibly converts at a different rate
  - a real but shaky statistical assumption, presented without caveat. The
  schema can't actually answer the better version of this question (no
  stage-history tracking - see next bullet), so the honest fix is a
  transparency one: have the agent explicitly flag estimates/extrapolations
  as distinct from directly-queried facts, rather than trying to make the
  estimate itself more accurate.

## Possible next steps

- Add a `products`/`deal_line_items` table for multi-line deals.
- Track stage-change history instead of just current stage, to compute
  stage-to-stage conversion funnels - this would also let the agent answer
  stage-specific conversion questions (see "Known limitations" above)
  instead of extrapolating from the overall win rate.
- Parameterize `generate_data.py` volume/seed via CLI args.
- Add a `list_distinct_values(column)` tool so the agent can check its own
  assumptions about categorical data at runtime, instead of relying solely
  on grounding notes written into the tool description by hand.
- Teach the agent to flag estimates/extrapolations as distinct from
  directly-queried facts (see "Known limitations" above).
