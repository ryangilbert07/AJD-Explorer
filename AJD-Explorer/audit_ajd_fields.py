import sqlite3
from pathlib import Path

DB_PATH = Path("data/ajds.sqlite")

FIELDS = [
    "swg_number",
    "issue_date",
    "county",
    "state",
    "project_name",
    "applicant",
    "consultant",
    "nearest_waterbody",
    "feature_summary",
    "jurisdictional_waters",
    "non_jurisdictional_features",
    "jurisdictional_reasoning",
    "latitude",
    "longitude",
    "jd_type",
    "prepared_by",
    "approved_by",
    "district",
    "determination_outcome",
    "regulatory_framework",
    "receiving_water",
    "pdf_url",
]

if not DB_PATH.exists():
    raise FileNotFoundError(f"Database not found: {DB_PATH}")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

columns = {
    row[1]
    for row in cur.execute("PRAGMA table_info(ajds)").fetchall()
}

total = cur.execute("SELECT COUNT(*) FROM ajds").fetchone()[0]

print("=" * 72)
print("AJD FIELD AUDIT")
print("=" * 72)
print(f"Total records: {total}")
print()

for field in FIELDS:
    if field not in columns:
        print(f"{field}")
        print("  Column not present in database")
        print()
        continue

    missing = cur.execute(
        f"""
        SELECT COUNT(*)
        FROM ajds
        WHERE {field} IS NULL
           OR TRIM(CAST({field} AS TEXT)) = ''
        """
    ).fetchone()[0]

    populated = total - missing

    unique = cur.execute(
        f"""
        SELECT COUNT(DISTINCT TRIM(CAST({field} AS TEXT)))
        FROM ajds
        WHERE {field} IS NOT NULL
          AND TRIM(CAST({field} AS TEXT)) <> ''
        """
    ).fetchone()[0]

    print(field)
    print(f"  Populated: {populated}/{total}")
    print(f"  Missing:   {missing}/{total}")
    print(f"  Unique:    {unique}")

    top_values = cur.execute(
        f"""
        SELECT TRIM(CAST({field} AS TEXT)) AS value, COUNT(*) AS n
        FROM ajds
        WHERE {field} IS NOT NULL
          AND TRIM(CAST({field} AS TEXT)) <> ''
        GROUP BY value
        ORDER BY n DESC, value
        LIMIT 8
        """
    ).fetchall()

    if top_values:
        print("  Top values:")
        for value, count in top_values:
            display = value.replace("\n", " ")
            if len(display) > 100:
                display = display[:97] + "..."
            print(f"    {count:>4}  {display}")

    print()

conn.close()
