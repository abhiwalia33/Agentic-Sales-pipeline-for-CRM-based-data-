"""
clean_data.py

ETL step: reads the messy raw CRM CSVs (data/raw/), standardizes them, and
loads the cleaned result into a local SQLite database (data/pipeline.db).

Cleaning steps:
  1. Standardize stage labels ("new"/"NEW" -> "New", "Closed Won" -> "Won", ...)
  2. Parse messy dates. Known EXACT formats are tried first, in this fixed
     order: %Y-%m-%d, %d/%m/%Y, %d-%m-%Y. Only if none of those match do we
     fall back to dateutil's fuzzy parser (dayfirst=True). Trying the exact
     formats first matters: dateutil with dayfirst=True will happily (and
     incorrectly) swap day/month on a string like "2026-03-04" if you hand
     it straight to the fuzzy parser, even though that string is unambiguous
     ISO format. Exact-format-first avoids that class of silent corruption.
  3. Impute missing deal_value using the MEDIAN grouped by lead_source.
  4. Deduplicate leads on (normalized company name, rep_id, deal_value) --
     not company name alone. With a limited pool of synthetic company names,
     two unrelated leads can coincidentally share a normalized name; matching
     on name alone would wrongly merge them. Requiring rep_id AND deal_value
     to also match is what correctly isolates true re-entered duplicates.
"""

import os
import re
import sqlite3
from datetime import datetime

import pandas as pd
from dateutil import parser as dateutil_parser

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(BASE_DIR, "data", "raw")
DB_PATH = os.path.join(BASE_DIR, "data", "pipeline.db")

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]

SUFFIX_WORDS = {
    "inc", "incorporated", "llc", "corp", "corporation", "co", "company",
    "ltd", "limited", "group", "holdings", "industries", "solutions",
    "partners",
}


def stage_normalize(raw):
    """Map any messy stage label to one of the canonical stage names."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "Unknown"
    s = str(raw).strip().lower()
    if not s:
        return "Unknown"
    if "won" in s:
        return "Won"
    if "lost" in s:
        return "Lost"
    if "negot" in s:
        return "Negotiation"
    if "propos" in s:
        return "Proposal"
    if "qualif" in s:
        return "Qualified"
    if "contact" in s:
        return "Contacted"
    if "new" in s:
        return "New"
    return str(raw).strip().title()


def parse_date(value):
    """Parse a messy date string into a date object, or None if blank/unparseable.

    Tries exact known formats first (in a fixed order), and only falls back
    to dateutil's fuzzy dayfirst parser if none of those match.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s:
        return None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    try:
        return dateutil_parser.parse(s, dayfirst=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def normalize_company(name):
    """Lowercase, strip punctuation, drop common legal-suffix words, and
    collapse whitespace -- so 'Acme Inc.' / 'ACME, INCORPORATED' / 'acme inc'
    all normalize to the same key."""
    s = str(name).lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = [t for t in s.split() if t not in SUFFIX_WORDS]
    return "".join(tokens)


def to_iso(d):
    return d.isoformat() if d is not None else None


def main():
    leads = pd.read_csv(os.path.join(RAW_DIR, "leads.csv"), dtype=str)
    reps = pd.read_csv(os.path.join(RAW_DIR, "reps.csv"), dtype=str)
    activities = pd.read_csv(os.path.join(RAW_DIR, "activities.csv"), dtype=str)

    print("=== clean_data.py ===")
    print(f"Loaded raw: {len(leads)} leads, {len(reps)} reps, {len(activities)} activities")

    # --- 1. Standardize stage labels ---
    raw_stage_values = sorted(leads["stage"].dropna().unique().tolist())
    leads["stage"] = leads["stage"].apply(stage_normalize)
    print(f"Standardized {len(raw_stage_values)} raw stage labels -> "
          f"{sorted(leads['stage'].unique().tolist())}")

    # --- 2. Parse dates ---
    leads["created_date"] = leads["created_date"].apply(parse_date)
    leads["close_date"] = leads["close_date"].apply(parse_date)
    reps["hire_date"] = reps["hire_date"].apply(parse_date)
    activities["activity_date"] = activities["activity_date"].apply(parse_date)
    print("Parsed created_date / close_date / hire_date / activity_date "
          "(ISO, DD/MM/YYYY, DD-MM-YYYY -> date objects)")

    # --- 3. Impute missing deal_value using median grouped by lead_source ---
    leads["deal_value"] = pd.to_numeric(leads["deal_value"], errors="coerce")
    missing_before = int(leads["deal_value"].isna().sum())

    group_medians = leads.groupby("lead_source")["deal_value"].transform("median")
    overall_median = leads["deal_value"].median()
    leads["deal_value"] = leads["deal_value"].fillna(group_medians).fillna(overall_median)
    leads["deal_value"] = leads["deal_value"].round(2)
    print(f"Imputed {missing_before} missing deal_value(s) using per-lead_source medians")

    leads["is_member"] = pd.to_numeric(leads["is_member"], errors="coerce").fillna(0).astype(int)

    # --- 4. Deduplicate leads ---
    leads["_norm_company"] = leads["company_name"].apply(normalize_company)
    leads["_dedup_key"] = list(zip(leads["_norm_company"], leads["rep_id"], leads["deal_value"]))

    before = len(leads)
    # Raw file order already has originals before their re-entered duplicates,
    # so keep="first" (no re-sort) preserves the original as the survivor.
    leads = leads.drop_duplicates(subset="_dedup_key", keep="first").copy()
    after = len(leads)
    print(f"Deduplicated leads: {before} -> {after} ({before - after} duplicate(s) removed)")

    leads.drop(columns=["_norm_company", "_dedup_key"], inplace=True)

    # Drop activities that belonged only to removed duplicate lead rows
    valid_lead_ids = set(leads["lead_id"])
    activities_before = len(activities)
    activities = activities[activities["lead_id"].isin(valid_lead_ids)].copy()
    if activities_before != len(activities):
        print(f"Dropped {activities_before - len(activities)} orphaned activity row(s)")

    # --- Convert dates to ISO strings for SQLite storage ---
    leads["created_date"] = leads["created_date"].apply(to_iso)
    leads["close_date"] = leads["close_date"].apply(to_iso)
    reps["hire_date"] = reps["hire_date"].apply(to_iso)
    activities["activity_date"] = activities["activity_date"].apply(to_iso)

    # --- Load into SQLite ---
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    try:
        leads.to_sql("leads", conn, index=False)
        reps.to_sql("reps", conn, index=False)
        activities.to_sql("activities", conn, index=False)
        conn.commit()
    finally:
        conn.close()

    print(f"\nWrote cleaned tables to {DB_PATH}")
    print(f"  leads:      {len(leads)} rows")
    print(f"  reps:       {len(reps)} rows")
    print(f"  activities: {len(activities)} rows")
    print("\nStage distribution after cleaning:")
    print(leads["stage"].value_counts().to_string())


if __name__ == "__main__":
    main()
