"""
generate_data.py

Generates a realistic, DELIBERATELY MESSY synthetic CRM dataset for the
ai-sales-pipeline project: reps, leads (deals), and activities.

Stdlib-only (random, csv, datetime, os) -- no external dependencies.

Messiness features baked in on purpose (see PROJECT_PLAN.md):
  - Inconsistent date formats (ISO, DD/MM/YYYY, DD-MM-YYYY)
  - Inconsistent stage labels ("New"/"new", "Won"/"won"/"Closed Won", ...)
  - ~5% missing deal values
  - ~3% duplicate leads: same deal re-entered under a slightly different
    company-name spelling, but with the SAME rep_id and deal_value as the
    original. Duplicates are only ever generated from original leads, never
    from other duplicates (no chained re-spellings).
  - Activity frequency/recency correlates with deal stage & recency:
      * open deals: mostly recent activity, ~8% get zero activity ever,
        a further slice is genuinely "stalled" (last touched 40-120 days ago)
      * closed deals (Won/Lost): activity is clustered in the run-up to the
        close date, not spread randomly across the deal's whole lifetime.

Output: data/raw/reps.csv, data/raw/leads.csv, data/raw/activities.csv
"""

import csv
import os
import random
from datetime import date, timedelta

random.seed(42)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(BASE_DIR, "data", "raw")

TODAY = date.today()

NUM_REPS = 15
NUM_LEADS_BASE = 1000
DUPLICATE_RATE = 0.03
MISSING_VALUE_RATE = 0.05

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Avery",
    "Priya", "Wei", "Sofia", "Liam", "Noah", "Emma", "Olivia", "Diego",
    "Fatima", "Hana", "Lucas", "Maya",
]
LAST_NAMES = [
    "Smith", "Johnson", "Patel", "Kim", "Garcia", "Chen", "Müller", "Rossi",
    "Nguyen", "Silva", "Kowalski", "Okafor", "Andersson", "Ivanov", "Tanaka",
]
REGIONS = ["North America", "EMEA", "APAC", "LATAM"]

COMPANY_WORDS = [
    "Acme", "Globex", "Initech", "Umbrella", "Stark", "Wayne", "Wonka",
    "Hooli", "Soylent", "Massive Dynamic", "Cyberdyne", "Vandelay",
    "Pied Piper", "Aperture", "Black Mesa", "Gringotts", "Oscorp",
    "LexCorp", "Tyrell", "Weyland", "Northwind", "Contoso", "Fabrikam",
    "Zenith", "Quantum", "Pinnacle", "Vertex", "Horizon", "Summit",
    "Meridian", "Atlas", "Beacon", "Cascade", "Delta Point", "Ember",
    "Frontier", "Granite", "Ironclad", "Junction", "Keystone",
]
COMPANY_SUFFIXES = [
    "Inc", "LLC", "Corp", "Co", "Group", "Holdings", "Industries",
    "Solutions", "Ltd", "Partners",
]

LEAD_SOURCES = [
    "Website", "Referral", "Cold Call", "Trade Show", "Partner",
    "Social Media", "Webinar",
]
LEAD_SOURCE_WEIGHTS = [0.28, 0.15, 0.17, 0.10, 0.12, 0.10, 0.08]
# (low, mode, high) for a triangular deal-value distribution per source --
# gives each lead_source a distinct median, which is what makes grouped-
# median imputation in clean_data.py meaningful.
SOURCE_VALUE_PARAMS = {
    "Website": (2000, 18000, 60000),
    "Referral": (5000, 42000, 130000),
    "Cold Call": (1000, 11000, 35000),
    "Trade Show": (3000, 26000, 85000),
    "Partner": (4000, 36000, 110000),
    "Social Media": (1000, 9000, 28000),
    "Webinar": (2000, 17000, 50000),
}
SOURCE_MEMBER_PROB = {
    "Website": 0.25,
    "Referral": 0.55,
    "Cold Call": 0.10,
    "Trade Show": 0.30,
    "Partner": 0.50,
    "Social Media": 0.20,
    "Webinar": 0.30,
}

OPEN_STAGES = ["New", "Contacted", "Qualified", "Proposal", "Negotiation"]
OPEN_STAGE_WEIGHTS = [0.32, 0.26, 0.20, 0.13, 0.09]

STAGE_LABEL_VARIANTS = {
    "New": ["New", "new", "NEW"],
    "Contacted": ["Contacted", "contacted", "CONTACTED"],
    "Qualified": ["Qualified", "qualified", "QUALIFIED"],
    "Proposal": ["Proposal", "proposal", "Proposal Sent"],
    "Negotiation": ["Negotiation", "negotiation", "Negotiating"],
    "Won": ["Won", "won", "Closed Won", "CLOSED WON"],
    "Lost": ["Lost", "lost", "Closed Lost"],
}

ACTIVITY_TYPES = ["Call", "Email", "Meeting", "Demo", "Proposal Sent", "Follow-up"]

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]


def messy_date(d):
    """Format a date object using a randomly chosen, realistically messy format."""
    fmt = random.choice(DATE_FORMATS)
    return d.strftime(fmt)


SUFFIX_PUNCT_VARIANT = {"Inc": "Inc.", "Corp": "Corp.", "Co": "Co.", "Ltd": "Ltd."}
SUFFIX_WORD_VARIANT = {"Inc": "Incorporated", "Corp": "Corporation", "Co": "Company", "Ltd": "Limited"}


def vary_company_name(name):
    """Return a slightly-differently-spelled version of a company name that
    normalizes back to the same value (punctuation / suffix-word / case /
    whitespace variants only -- so clean_data.py's normalizer can catch it).

    Suffix substitutions only ever touch the last word (the legal suffix)
    directly via dict lookup -- never chained string .replace() calls, which
    can corrupt each other (e.g. "Corp" -> "Corporation" -> a later ".replace
    ("Co", "Company")" then matches the "Co" prefix *inside* "Corporation"
    and mangles it into "Companyrporation").
    """
    words = name.split(" ")
    last = words[-1]

    variations = [
        lambda: name.replace(" ", "  ", 1),
        lambda: name + " ",
        lambda: name.upper(),
        lambda: name.lower(),
        lambda: name.replace(",", "") + ",",
    ]
    if last in SUFFIX_PUNCT_VARIANT:
        variations.append(lambda: " ".join(words[:-1] + [SUFFIX_PUNCT_VARIANT[last]]))
    if last in SUFFIX_WORD_VARIANT:
        variations.append(lambda: " ".join(words[:-1] + [SUFFIX_WORD_VARIANT[last]]))

    fn = random.choice(variations)
    result = fn()
    if result == name:
        result = name + "."
    return result


def make_reps():
    reps = []
    for i in range(1, NUM_REPS + 1):
        rep_id = f"REP{i:03d}"
        name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        region = random.choice(REGIONS)
        hire_date = TODAY - timedelta(days=random.randint(200, 2500))
        reps.append({
            "rep_id": rep_id,
            "rep_name": name,
            "region": region,
            "hire_date": messy_date(hire_date),
        })
    return reps


def compute_deal_value(lead_source):
    low, mode, high = SOURCE_VALUE_PARAMS[lead_source]
    return round(random.triangular(low, mode, high), -2)


def make_leads(reps):
    leads = []
    lead_id_counter = 1

    for _ in range(NUM_LEADS_BASE):
        lead_id = f"L{lead_id_counter:05d}"
        lead_id_counter += 1

        company_name = f"{random.choice(COMPANY_WORDS)} {random.choice(COMPANY_SUFFIXES)}"
        rep = random.choice(reps)
        lead_source = random.choices(LEAD_SOURCES, weights=LEAD_SOURCE_WEIGHTS)[0]
        is_member = 1 if random.random() < SOURCE_MEMBER_PROB[lead_source] else 0

        created_date_obj = TODAY - timedelta(days=random.randint(10, 400))

        stage_roll = random.random()
        if stage_roll < 0.45:
            canonical_stage = random.choices(OPEN_STAGES, weights=OPEN_STAGE_WEIGHTS)[0]
            close_date_obj = None
        elif stage_roll < 0.72:
            canonical_stage = "Won"
            cycle_days = random.randint(10, 150)
            close_date_obj = min(created_date_obj + timedelta(days=cycle_days), TODAY)
        else:
            canonical_stage = "Lost"
            cycle_days = random.randint(5, 120)
            close_date_obj = min(created_date_obj + timedelta(days=cycle_days), TODAY)

        deal_value = compute_deal_value(lead_source)
        is_missing_value = random.random() < MISSING_VALUE_RATE
        stage_label = random.choice(STAGE_LABEL_VARIANTS[canonical_stage])

        lead = {
            "lead_id": lead_id,
            "company_name": company_name,
            "lead_source": lead_source,
            "rep_id": rep["rep_id"],
            "stage": stage_label,
            "deal_value": "" if is_missing_value else deal_value,
            "is_member": is_member,
            "created_date": messy_date(created_date_obj),
            "close_date": messy_date(close_date_obj) if close_date_obj else "",
            # internal-only fields, used for activity generation & duplication,
            # stripped before writing leads.csv
            "_stage_canonical": canonical_stage,
            "_created_date_obj": created_date_obj,
            "_close_date_obj": close_date_obj,
            "_is_duplicate": False,
        }
        leads.append(lead)

    # --- Duplicate leads: sampled ONLY from the original pool above, so a
    # duplicate can never itself be duplicated (no "_DUP_DUP" chains). ---
    originals_pool = list(leads)
    num_dupes = round(DUPLICATE_RATE * NUM_LEADS_BASE)
    dupe_sources = random.sample(originals_pool, num_dupes)

    duplicates = []
    for orig in dupe_sources:
        dup = dict(orig)
        dup["lead_id"] = f"L{lead_id_counter:05d}"
        lead_id_counter += 1
        dup["company_name"] = vary_company_name(orig["company_name"])
        dup["_is_duplicate"] = True
        # rep_id and deal_value are intentionally left identical to `orig`
        duplicates.append(dup)

    leads.extend(duplicates)
    return leads, num_dupes


def make_activity(activity_id_counter, lead_id, rep_id, activity_date_obj):
    return {
        "activity_id": f"A{activity_id_counter:06d}",
        "lead_id": lead_id,
        "rep_id": rep_id,
        "activity_date": messy_date(activity_date_obj),
        "activity_type": random.choice(ACTIVITY_TYPES),
    }


def make_activities(leads):
    activities = []
    activity_id_counter = 1
    zero_activity_open = 0
    stalled_open = 0
    active_open = 0

    for lead in leads:
        if lead["_is_duplicate"]:
            # A re-entered duplicate record doesn't carry its own activity
            # trail -- it's the same deal, not a separately-worked lead.
            continue

        stage = lead["_stage_canonical"]
        created = lead["_created_date_obj"]
        lead_id = lead["lead_id"]
        rep_id = lead["rep_id"]

        if stage in ("Won", "Lost"):
            close = lead["_close_date_obj"]
            span_days = max((close - created).days, 1)
            n = random.randint(2, 10)
            dates = [created + timedelta(days=random.randint(0, span_days)) for _ in range(n)]
            last_offset = random.randint(0, 5)
            dates.append(max(created, close - timedelta(days=last_offset)))
            for d in dates:
                activities.append(make_activity(activity_id_counter, lead_id, rep_id, d))
                activity_id_counter += 1
            continue

        # Open deal
        roll = random.random()
        if roll < 0.08:
            zero_activity_open += 1
            continue
        elif roll < 0.08 + 0.15:
            stalled_open += 1
            last_offset = random.randint(40, 120)
            last_date = max(created, TODAY - timedelta(days=last_offset))
            span = max((last_date - created).days, 0)
            n = random.randint(1, 4)
            for _ in range(n):
                d = created + timedelta(days=random.randint(0, span))
                activities.append(make_activity(activity_id_counter, lead_id, rep_id, d))
                activity_id_counter += 1
            activities.append(make_activity(activity_id_counter, lead_id, rep_id, last_date))
            activity_id_counter += 1
        else:
            active_open += 1
            last_offset = random.randint(0, 25)
            last_date = max(created, TODAY - timedelta(days=last_offset))
            span = max((last_date - created).days, 0)
            n = random.randint(2, 8)
            for _ in range(n):
                d = created + timedelta(days=random.randint(0, span))
                activities.append(make_activity(activity_id_counter, lead_id, rep_id, d))
                activity_id_counter += 1
            activities.append(make_activity(activity_id_counter, lead_id, rep_id, last_date))
            activity_id_counter += 1

    print(
        f"  Open-deal activity mix -> zero activity: {zero_activity_open}, "
        f"stalled (40-120d): {stalled_open}, active (0-25d): {active_open}"
    )
    return activities


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fieldnames})


def main():
    os.makedirs(RAW_DIR, exist_ok=True)

    reps = make_reps()
    leads, num_dupes = make_leads(reps)
    activities = make_activities(leads)

    write_csv(
        os.path.join(RAW_DIR, "reps.csv"),
        reps,
        ["rep_id", "rep_name", "region", "hire_date"],
    )
    write_csv(
        os.path.join(RAW_DIR, "leads.csv"),
        leads,
        ["lead_id", "company_name", "lead_source", "rep_id", "stage",
         "deal_value", "is_member", "created_date", "close_date"],
    )
    write_csv(
        os.path.join(RAW_DIR, "activities.csv"),
        activities,
        ["activity_id", "lead_id", "rep_id", "activity_date", "activity_type"],
    )

    missing_count = sum(1 for l in leads if l["deal_value"] == "")
    print("\n=== generate_data.py summary ===")
    print(f"Reps:       {len(reps)}")
    print(f"Leads:      {len(leads)} ({NUM_LEADS_BASE} original + {num_dupes} duplicate)")
    print(f"Activities: {len(activities)}")
    print(f"Missing deal_value: {missing_count} ({missing_count / len(leads):.1%})")
    print(f"Raw CSVs written to: {RAW_DIR}")


if __name__ == "__main__":
    main()
