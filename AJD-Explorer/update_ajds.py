import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from scrape_ajds import (
    BASE_URL,
    JD_PAGE_HTML,
    CHROME_PROFILE_DIR,
    setup_folders,
    setup_chrome_preferences,
    setup_database,
    download_pdf_with_chrome,
    extract_pdf_text,
    extract_with_ai,
    save_record,
)

from enrich_ajds import (
    setup_new_columns,
    extract_extra_fields,
    update_record,
)


DB_PATH = Path("data/ajds.sqlite")
IGNORE_FILE = Path("ignore_ajds.txt")

# Include the 2025 Basis Forms section and every newer Basis Forms section.
CUTOFF_BASIS_YEAR = 2025


def normalize_jd_number(value: str) -> str:
    """Normalize SWG identifiers to SWG-YYYY-NNNNN format when possible."""
    text = str(value or "").strip().upper()
    match = re.search(r"SWG[-_ ]?(\d{4})[-_ ]?(\d+)", text)

    if not match:
        return text

    year = match.group(1)
    sequence = match.group(2).zfill(5)
    return f"SWG-{year}-{sequence}"


def load_ignore_list() -> set[str]:
    """
    Read ignored JD IDs from ignore_ajds.txt.

    Blank lines and lines beginning with # are ignored.
    """
    if not IGNORE_FILE.exists():
        IGNORE_FILE.write_text(
            "# Add one JD ID per line.\n"
            "# Example:\n"
            "# SWG-2024-00285\n",
            encoding="utf-8",
        )
        return set()

    ignored = set()

    for raw_line in IGNORE_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        ignored.add(normalize_jd_number(line))

    return ignored


def get_existing_jd_numbers() -> set[str]:
    """Return all JD IDs already stored in SQLite."""
    if not DB_PATH.exists():
        return set()

    conn = sqlite3.connect(DB_PATH)

    try:
        rows = conn.execute(
            "SELECT swg_number FROM ajds WHERE swg_number IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    return {
        normalize_jd_number(row[0])
        for row in rows
        if row and row[0]
    }


def get_pdf_links_from_basis_sections() -> list[dict]:
    """
    Parse the saved USACE Jurisdictional Determinations webpage and collect
    PDF links from the 2025 Basis Forms section and all newer Basis Forms
    sections.
    """
    html_path = Path(JD_PAGE_HTML)

    if not html_path.exists():
        raise FileNotFoundError(
            f"{html_path} was not found.\n"
            "Open the USACE Galveston Jurisdictional Determinations webpage, "
            "save it as jd_page.html in this project folder, and run again."
        )

    html = html_path.read_text(
        encoding="utf-8",
        errors="ignore",
    )

    soup = BeautifulSoup(html, "html.parser")
    discovered = []
    current_section_year = None

    # Walk through headings and links in document order.
    for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "a"]):
        text = element.get_text(" ", strip=True)

        heading_match = re.search(
            r"\b(20\d{2})\s+Basis Forms\b",
            text,
            flags=re.IGNORECASE,
        )

        if element.name in {"h1", "h2", "h3", "h4", "h5"}:
            if heading_match:
                current_section_year = int(heading_match.group(1))
            continue

        if element.name != "a":
            continue

        if current_section_year is None:
            continue

        if current_section_year < CUTOFF_BASIS_YEAR:
            continue

        href = element.get("href", "").strip()

        if ".pdf" not in href.lower():
            continue

        pdf_url = urljoin(BASE_URL, href)

        jd_match = re.search(
            r"SWG[-_ ]?\d{4}[-_ ]?\d+",
            f"{text} {pdf_url}",
            flags=re.IGNORECASE,
        )

        if not jd_match:
            print(f"Skipping PDF with no recognizable SWG number: {pdf_url}")
            continue

        jd_number = normalize_jd_number(jd_match.group(0))

        discovered.append(
            {
                "swg_number": jd_number,
                "pdf_url": pdf_url,
                "basis_year": current_section_year,
            }
        )

    # Deduplicate by JD number, preferring the first link found.
    unique_by_jd = {}

    for item in discovered:
        unique_by_jd.setdefault(item["swg_number"], item)

    return list(unique_by_jd.values())


def process_one_ajd(playwright, item: dict) -> bool:
    """
    Download, OCR, extract, enrich, and save one AJD.

    A fresh Chrome context is created for each AJD. This prevents a closed
    browser context from causing every later AJD to fail.
    """
    swg_number = item["swg_number"]
    pdf_url = item["pdf_url"]

    print(f"\nProcessing new AJD: {swg_number}")
    print(f"Source: {pdf_url}")

    context = None

    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=os.path.abspath(CHROME_PROFILE_DIR),
            channel="chrome",
            headless=False,
            accept_downloads=True,
            downloads_path=os.path.abspath("pdfs"),
        )

        local_pdf_path = download_pdf_with_chrome(
            context,
            swg_number,
            pdf_url,
        )

        full_text = extract_pdf_text(local_pdf_path)

        if not full_text or not full_text.strip():
            print(f"No usable OCR text extracted for {swg_number}.")
            return False

        extracted = extract_with_ai(
            swg_number,
            pdf_url,
            full_text,
        )

        save_record(
            swg_number,
            pdf_url,
            local_pdf_path,
            full_text,
            extracted,
        )

        extra_fields = extract_extra_fields(
            swg_number,
            full_text,
        )

        update_record(
            swg_number,
            extra_fields,
        )

        print(f"Saved and enriched: {swg_number}")
        return True

    except KeyboardInterrupt:
        raise

    except Exception as error:
        print(f"Error processing {swg_number}: {error}")
        return False

    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


def print_summary(
    total_links: int,
    already_saved: int,
    ignored_found: int,
    processed: int,
    failed: list[str],
) -> None:
    current_total = 0

    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)

        try:
            current_total = conn.execute(
                "SELECT COUNT(*) FROM ajds"
            ).fetchone()[0]
        finally:
            conn.close()

    print("\n" + "=" * 60)
    print("AJD UPDATE COMPLETE")
    print("=" * 60)
    print(f"Links found in included Basis Forms sections: {total_links}")
    print(f"Already saved:                           {already_saved}")
    print(f"Ignored from ignore_ajds.txt:            {ignored_found}")
    print(f"New AJDs successfully processed:         {processed}")
    print(f"Failed AJDs:                             {len(failed)}")
    print(f"Current database size:                   {current_total}")

    if failed:
        print("\nFailed JD IDs:")
        for jd_number in failed:
            print(f"  {jd_number}")

        print(
            "\nRerun py update_ajds.py to retry failed records. "
            "Successfully saved records will be skipped."
        )

    print("=" * 60)


def main() -> None:
    setup_folders()
    setup_chrome_preferences()
    setup_database()
    setup_new_columns()

    ignored = load_ignore_list()
    existing = get_existing_jd_numbers()
    all_links = get_pdf_links_from_basis_sections()

    ignored_on_page = [
        item
        for item in all_links
        if item["swg_number"] in ignored
    ]

    already_saved = [
        item
        for item in all_links
        if item["swg_number"] in existing
    ]

    missing = [
        item
        for item in all_links
        if item["swg_number"] not in existing
        and item["swg_number"] not in ignored
    ]

    print(
        f"Found {len(all_links)} PDF links from "
        f"{CUTOFF_BASIS_YEAR}+ Basis Forms sections."
    )
    print(f"Already in database: {len(already_saved)}")
    print(f"Ignored from ignore_ajds.txt: {len(ignored_on_page)}")
    print(f"New/missing to process: {len(missing)}")

    if ignored:
        print("\nLoaded ignored JD IDs:")
        for jd_number in sorted(ignored):
            print(f"  {jd_number}")

    if not missing:
        print_summary(
            total_links=len(all_links),
            already_saved=len(already_saved),
            ignored_found=len(ignored_on_page),
            processed=0,
            failed=[],
        )
        return

    processed = 0
    failed = []

    try:
        with sync_playwright() as playwright:
            for item in missing:
                success = process_one_ajd(
                    playwright,
                    item,
                )

                if success:
                    processed += 1
                else:
                    failed.append(item["swg_number"])

    except KeyboardInterrupt:
        print("\nUpdate stopped by user with Ctrl+C.")

        # Anything already saved remains in SQLite and will be skipped next run.
        remaining_failed = [
            item["swg_number"]
            for item in missing
            if item["swg_number"] not in get_existing_jd_numbers()
        ]

        print_summary(
            total_links=len(all_links),
            already_saved=len(already_saved),
            ignored_found=len(ignored_on_page),
            processed=processed,
            failed=remaining_failed,
        )
        return

    print_summary(
        total_links=len(all_links),
        already_saved=len(already_saved),
        ignored_found=len(ignored_on_page),
        processed=processed,
        failed=failed,
    )


if __name__ == "__main__":
    main()
