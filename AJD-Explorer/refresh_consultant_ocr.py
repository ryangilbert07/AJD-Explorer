r"""Refresh saved PDF OCR for selected JDs without changing extracted JD fields.

Examples (run from the AJD-Explorer project directory):
  .\.venv\Scripts\python.exe refresh_consultant_ocr.py SWF-2021-00540
  .\.venv\Scripts\python.exe refresh_consultant_ocr.py SWF-2021-00540 --apply

The default run previews OCR without changing the SQLite database. --apply
creates a SQLite backup and updates only full_ocr_text. Consultant extraction
is a separate step; run reenrich_consultants.py for the selected JD afterward.
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ajds.sqlite"


def find_pdf(saved_path, swg_number):
    candidates = [ROOT / "pdfs" / f"{swg_number}.pdf"]
    if saved_path:
        candidates.insert(0, Path(saved_path))
        candidates.append(ROOT / saved_path)
    return next((path for path in candidates if path.is_file()), None)


def make_backup(connection):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = DB_PATH.with_name(f"ajds_before_ocr_{stamp}.sqlite")
    with sqlite3.connect(destination) as backup:
        connection.backup(backup)
    print(f"Database backup: {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("swg_number", nargs="+", help="One or more exact JD numbers")
    parser.add_argument("--apply", action="store_true", help="Save OCR to database")
    args = parser.parse_args()

    if not DB_PATH.is_file():
        parser.error(f"Database not found: {DB_PATH}")

    # Import only when running: scrape_ajds.py may initialize an API client.
    from scrape_ajds import extract_pdf_text

    with sqlite3.connect(DB_PATH) as conn:
        if args.apply:
            make_backup(conn)

        for swg_number in dict.fromkeys(args.swg_number):
            row = conn.execute(
                "SELECT local_pdf_path, full_ocr_text FROM ajds WHERE swg_number = ?",
                (swg_number,),
            ).fetchone()
            if row is None:
                print(f"{swg_number}: no matching database record; skipped")
                continue

            pdf = find_pdf(row[0], swg_number)
            if pdf is None:
                print(f"{swg_number}: no local PDF; skipped")
                continue

            old_text = row[1] or ""
            print(f"\n{swg_number}: {pdf}")
            try:
                new_text = extract_pdf_text(str(pdf))
            except Exception as exc:
                print(f"OCR failed; stored OCR preserved: {exc}")
                continue

            old_maps = old_text.count("[MAP TITLE BLOCK]")
            new_maps = new_text.count("[MAP TITLE BLOCK]")
            print(
                f"OCR characters: {len(old_text):,} -> {len(new_text):,}; "
                f"map blocks: {old_maps} -> {new_maps}"
            )

            # Do not replace substantial existing OCR with an empty or broken run.
            if len(new_text.strip()) < max(500, int(len(old_text.strip()) * 0.65)):
                print("New OCR is much shorter; stored OCR preserved.")
                continue

            if args.apply:
                conn.execute(
                    "UPDATE ajds SET full_ocr_text = ? WHERE swg_number = ?",
                    (new_text, swg_number),
                )
                conn.commit()
                print("Saved refreshed OCR; other JD fields unchanged.")
            else:
                print("Preview only; add --apply to save refreshed OCR.")


if __name__ == "__main__":
    main()
