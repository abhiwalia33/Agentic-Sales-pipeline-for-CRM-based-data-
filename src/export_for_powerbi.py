"""
export_for_powerbi.py

Exports the three cleaned tables from data/pipeline.db into plain CSV files
under data/clean/, ready to import into Power BI via Get Data > Text/CSV.
"""

import os
import sqlite3

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "pipeline.db")
OUT_DIR = os.path.join(BASE_DIR, "data", "clean")

TABLES = ["leads", "reps", "activities"]


def main():
    if not os.path.exists(DB_PATH):
        raise SystemExit(
            f"Database not found at {DB_PATH}. Run clean_data.py first."
        )

    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    print("=== export_for_powerbi.py ===")
    try:
        for table in TABLES:
            df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
            out_path = os.path.join(OUT_DIR, f"{table}.csv")
            df.to_csv(out_path, index=False)
            print(f"Exported {len(df):>5} rows, {len(df.columns)} columns -> {out_path}")
    finally:
        conn.close()

    print(f"\nDone. In Power BI: Get Data > Text/CSV, then import each CSV from {OUT_DIR}")


if __name__ == "__main__":
    main()
