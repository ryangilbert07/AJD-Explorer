"""Recheck blank consultants once per OCR version and resume in later batches.

Run in the AJD-Explorer folder, after reenrich_consultants.py is available.
First: python review_missing_consultants.py --seed-previous
Then:  python review_missing_consultants.py --limit 30

Only consultant fields and the consultant_review_attempts table are changed.
The first command records the 24 unsuccessful checks from the user's prior
30-record batch. If OCR changes later, the record becomes eligible again.
"""

import argparse
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ajds.sqlite"

# These IDs were reported as Not found in the completed 30-record batch.
PREVIOUS_NOT_FOUND = (
    "SWF-2020-00498", "SWF-2021-00540", "SWF-2021-00547",
    "SWF-2021-00560", "SWF-2024-00381", "SWF-2024-00479",
    "SWF-2025-00059", "SWF-2025-00118", "SWF-2025-00120",
    "SWF-2025-00141", "SWF-2025-00153", "SWF-2025-00311",
    "SWF-2025-00319", "SWF-2025-00356", "SWF-2025-00426",
    "SWF-2025-00429", "SWF-2025-00489", "SWG-2002-02761",
    "SWG-2003-02165", "SWG-2004-00935", "SWG-2007-00255",
    "SWG-2007-01648", "SWG-2007-01943", "SWG-2008-00414",
)


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def setup(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS consultant_review_attempts (
            swg_number TEXT PRIMARY KEY,
            ocr_sha256 TEXT NOT NULL,
            outcome TEXT NOT NULL,
            reviewed_at TEXT NOT NULL
        )
    """)
    conn.commit()


def mark(conn, swg_number, full_text, outcome):
    conn.execute("""
        INSERT INTO consultant_review_attempts
            (swg_number, ocr_sha256, outcome, reviewed_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(swg_number) DO UPDATE SET
            ocr_sha256 = excluded.ocr_sha256,
            outcome = excluded.outcome,
            reviewed_at = excluded.reviewed_at
    """, (
        swg_number, digest(full_text), outcome,
        datetime.now().isoformat(timespec="seconds"),
    ))
    conn.commit()


def seed_previous(conn):
    count = 0
    for swg_number in PREVIOUS_NOT_FOUND:
        row = conn.execute(
            "SELECT full_ocr_text, consultant FROM ajds WHERE swg_number = ?",
            (swg_number,),
        ).fetchone()
        if row and row[0] and not (row[1] or "").strip():
            mark(conn, swg_number, row[0], "Not found")
            count += 1
    print(f"Recorded {count} prior Not found checks; no JD fields changed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-previous", action="store_true")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    if not DB_PATH.exists():
        parser.error(f"Database not found: {DB_PATH}")

    with sqlite3.connect(DB_PATH) as conn:
        setup(conn)
        if args.seed_previous:
            seed_previous(conn)
            return

        rows = conn.execute("""
            SELECT a.swg_number, a.full_ocr_text
            FROM ajds AS a
            WHERE a.full_ocr_text IS NOT NULL
              AND TRIM(a.full_ocr_text) <> ''
              AND (a.consultant IS NULL OR TRIM(a.consultant) = '')
            ORDER BY a.swg_number
        """).fetchall()
        previous = dict(conn.execute(
            "SELECT swg_number, ocr_sha256 FROM consultant_review_attempts"
        ).fetchall())
        todo = [
            (swg_number, full_text)
            for swg_number, full_text in rows
            if previous.get(swg_number) != digest(full_text)
        ][:args.limit]
        print(f"New consultant checks to process: {len(todo)}")
        if not todo:
            return

        from openai import OpenAI
        from reenrich_consultants import extract_consultant, save_consultant
        client = OpenAI()
        for index, (swg_number, full_text) in enumerate(todo, 1):
            try:
                result = extract_consultant(client, swg_number, full_text)
            except Exception as exc:
                print(f"[{index}/{len(todo)}] {swg_number}: error ({exc}); will retry later")
                continue

            name = (result.get("consultant") or "").strip()
            confidence = result.get("consultant_confidence") or "low"
            source = result.get("consultant_source") or "Not found"
            supported = bool(name and confidence in {"high", "medium"})
            print(f"[{index}/{len(todo)}] {swg_number}: "
                  f"{name if supported else 'Not found'} ({confidence}; {source})")
            if args.dry_run:
                continue
            if supported:
                save_consultant(conn, swg_number, result)
            mark(conn, swg_number, full_text, "Found" if supported else "Not found")

        if args.dry_run:
            print("Preview only; checks were not marked complete.")


if __name__ == "__main__":
    main()
