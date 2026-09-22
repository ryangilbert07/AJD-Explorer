import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from scrape_ajds import (
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


SOURCE_PAGE = (
    "https://www.swf.usace.army.mil/Missions/Regulatory/"
    "Approved-Jurisdictional-Determinations/"
)

BASE_URL = "https://www.swf.usace.army.mil"
SWF_PAGE_HTML = Path("swf_jd_page.html")
STOP_AFTER_JD = "SWF-2024-00479"

DB_PATH = Path("data/ajds.sqlite")
IGNORE_FILE = Path("ignore_swf_ajds.txt")
SWF_CHROME_PROFILE_DIR = Path("chrome_profile_swf_downloads")


def normalize_jd_number(value: str) -> str:
    text = str(value or "").strip().upper()

    match = re.search(
        r"SWF[-_ ]?(\d{4})[-_ ]?(\d+)",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return text

    return f"SWF-{match.group(1)}-{match.group(2).zfill(5)}"


def load_ignore_list() -> set[str]:
    if not IGNORE_FILE.exists():
        IGNORE_FILE.write_text(
            "# Add one Fort Worth JD ID per line.\n"
            "# Example:\n"
            "# SWF-2025-00429\n",
            encoding="utf-8",
        )
        return set()

    ignored = set()

    for raw_line in IGNORE_FILE.read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        ignored.add(normalize_jd_number(line))

    return ignored


def get_existing_jd_numbers() -> set[str]:
    if not DB_PATH.exists():
        return set()

    conn = sqlite3.connect(DB_PATH)

    try:
        rows = conn.execute(
            """
            SELECT swg_number
            FROM ajds
            WHERE swg_number IS NOT NULL
            """
        ).fetchall()
    finally:
        conn.close()

    return {
        normalize_jd_number(row[0])
        for row in rows
        if row and row[0]
    }


def get_swf_links_through_cutoff() -> list[dict]:
    """
    Parse the locally saved Fort Worth AJD webpage in page order and collect
    all AJD links from the top through SWF-2024-00479, inclusive.
    """
    if not SWF_PAGE_HTML.exists():
        raise FileNotFoundError(
            f"{SWF_PAGE_HTML} was not found.\n\n"
            "Open the Fort Worth AJD webpage in Chrome:\n"
            f"{SOURCE_PAGE}\n\n"
            "Press Ctrl+S, choose 'Webpage, HTML Only', and save it in this "
            "project folder as swf_jd_page.html. Then run the updater again."
        )

    html = SWF_PAGE_HTML.read_text(
        encoding="utf-8",
        errors="ignore",
    )

    soup = BeautifulSoup(html, "html.parser")

    links = []
    cutoff_found = False

    # The saved HTML preserves anchor order, which matches the webpage list.
    for anchor in soup.find_all("a"):
        anchor_text = anchor.get_text(" ", strip=True)
        href = str(anchor.get("href", "")).strip()

        combined = f"{anchor_text} {href}"

        jd_match = re.search(
            r"SWF[-_ ]?\d{4}[-_ ]?\d+",
            combined,
            flags=re.IGNORECASE,
        )

        if not jd_match:
            continue

        jd_number = normalize_jd_number(jd_match.group(0))

        if not href:
            print(f"Skipping {jd_number}: link has no URL.")
            continue

        pdf_url = urljoin(BASE_URL, href)

        links.append(
            {
                "swg_number": jd_number,
                "pdf_url": pdf_url,
                "link_text": anchor_text,
            }
        )

        if jd_number == STOP_AFTER_JD:
            cutoff_found = True
            break

    if not cutoff_found:
        found_ids = [item["swg_number"] for item in links[-10:]]

        raise RuntimeError(
            f"The cutoff JD {STOP_AFTER_JD} was not found in "
            f"{SWF_PAGE_HTML}.\n"
            "Resave the current Fort Worth AJD webpage and try again.\n"
            f"Last IDs found before stopping: {found_ids}"
        )

    unique_links = []
    seen_jd_numbers = set()

    for item in links:
        jd_number = item["swg_number"]

        if jd_number in seen_jd_numbers:
            print(f"Duplicate listing skipped: {jd_number}")
            continue

        seen_jd_numbers.add(jd_number)
        unique_links.append(item)

    return unique_links


def force_fort_worth_district(jd_number: str) -> None:
    conn = sqlite3.connect(DB_PATH)

    try:
        conn.execute(
            """
            UPDATE ajds
            SET district = ?
            WHERE swg_number = ?
            """,
            ("Fort Worth District", jd_number),
        )
        conn.commit()
    finally:
        conn.close()



def download_swf_pdf(
    context,
    jd_number: str,
    pdf_url: str,
) -> str:
    """
    Save a Fort Worth PDF even when Chrome opens it in the built-in PDF viewer
    instead of triggering a Playwright download event.
    """
    pdf_dir = Path("pdfs")
    pdf_dir.mkdir(parents=True, exist_ok=True)

    output_path = pdf_dir / f"{jd_number}.pdf"
    page = context.new_page()

    try:
        print(f"Opening PDF through Chrome: {pdf_url}")

        response = page.goto(
            pdf_url,
            wait_until="domcontentloaded",
            timeout=120_000,
        )

        pdf_bytes = b""

        # In many cases the navigation response itself is the PDF.
        if response is not None:
            try:
                pdf_bytes = response.body()
            except Exception:
                pdf_bytes = b""

        # If Chrome displayed its PDF viewer or the navigation body was empty,
        # request the same URL through the browser context. This shares browser
        # cookies/session information and avoids the normal requests 403 issue.
        if not pdf_bytes.startswith(b"%PDF"):
            browser_response = context.request.get(
                pdf_url,
                timeout=120_000,
            )

            if not browser_response.ok:
                raise RuntimeError(
                    f"Browser PDF request returned HTTP "
                    f"{browser_response.status}."
                )

            pdf_bytes = browser_response.body()

        if not pdf_bytes.startswith(b"%PDF"):
            preview = pdf_bytes[:200].decode(
                "utf-8",
                errors="replace",
            )
            raise RuntimeError(
                "The Fort Worth link did not return a valid PDF. "
                f"Response began with: {preview!r}"
            )

        output_path.write_bytes(pdf_bytes)

        print(f"Saved valid PDF: {output_path}")
        return str(output_path)

    finally:
        try:
            page.close()
        except Exception:
            pass

def process_one_ajd(playwright, item: dict) -> bool:
    jd_number = item["swg_number"]
    pdf_url = item["pdf_url"]

    print(f"\nProcessing Fort Worth AJD: {jd_number}")
    print(f"Source: {pdf_url}")

    context = None

    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=os.path.abspath(
                SWF_CHROME_PROFILE_DIR
            ),
            channel="chrome",
            headless=False,
            accept_downloads=True,
            downloads_path=os.path.abspath("pdfs"),
        )

        local_pdf_path = download_swf_pdf(
            context,
            jd_number,
            pdf_url,
        )

        full_text = extract_pdf_text(local_pdf_path)

        if not full_text or not full_text.strip():
            print(f"No usable OCR text extracted for {jd_number}.")
            return False

        extracted = extract_with_ai(
            jd_number,
            pdf_url,
            full_text,
        )

        if isinstance(extracted, dict):
            extracted["district"] = "Fort Worth District"
            extracted["usace_district"] = "Fort Worth District"

        save_record(
            jd_number,
            pdf_url,
            local_pdf_path,
            full_text,
            extracted,
        )

        extra_fields = extract_extra_fields(
            jd_number,
            full_text,
        )

        if isinstance(extra_fields, dict):
            extra_fields["district"] = "Fort Worth District"
            extra_fields["usace_district"] = "Fort Worth District"

        update_record(
            jd_number,
            extra_fields,
        )

        force_fort_worth_district(jd_number)

        print(f"Saved and enriched Fort Worth AJD: {jd_number}")
        return True

    except KeyboardInterrupt:
        raise

    except Exception as error:
        print(f"Error processing {jd_number}: {error}")
        return False

    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


def print_summary(
    links_found: int,
    already_saved: int,
    ignored_found: int,
    processed: int,
    failed: list[str],
) -> None:
    current_total = 0
    fort_worth_total = 0

    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)

        try:
            current_total = conn.execute(
                "SELECT COUNT(*) FROM ajds"
            ).fetchone()[0]

            fort_worth_total = conn.execute(
                """
                SELECT COUNT(*)
                FROM ajds
                WHERE district = 'Fort Worth District'
                """
            ).fetchone()[0]
        finally:
            conn.close()

    print("\n" + "=" * 64)
    print("FORT WORTH AJD UPDATE COMPLETE")
    print("=" * 64)
    print(f"Unique webpage AJDs through cutoff:      {links_found}")
    print(f"Already saved:                           {already_saved}")
    print(f"Ignored from ignore_swf_ajds.txt:        {ignored_found}")
    print(f"New AJDs successfully processed:         {processed}")
    print(f"Failed AJDs:                             {len(failed)}")
    print(f"Fort Worth records in database:          {fort_worth_total}")
    print(f"All-district database size:              {current_total}")

    if failed:
        print("\nFailed Fort Worth JD IDs:")

        for jd_number in failed:
            print(f"  {jd_number}")

        print(
            "\nRun py update_swf_ajds.py again to retry failures. "
            "Successfully saved records will be skipped."
        )

    print("=" * 64)


def main() -> None:
    setup_folders()
    setup_chrome_preferences()
    setup_database()
    setup_new_columns()

    ignored = load_ignore_list()
    existing = get_existing_jd_numbers()
    all_links = get_swf_links_through_cutoff()

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
        f"Found {len(all_links)} unique Fort Worth AJD links "
        f"from the top of the webpage through {STOP_AFTER_JD}."
    )
    print(f"Already in database: {len(already_saved)}")
    print(
        f"Ignored from ignore_swf_ajds.txt: "
        f"{len(ignored_on_page)}"
    )
    print(f"New/missing to process: {len(missing)}")

    if ignored:
        print("\nLoaded ignored Fort Worth JD IDs:")

        for jd_number in sorted(ignored):
            print(f"  {jd_number}")

    if not missing:
        print_summary(
            links_found=len(all_links),
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
        print("\nFort Worth update stopped by user with Ctrl+C.")

        existing_after_stop = get_existing_jd_numbers()

        remaining = [
            item["swg_number"]
            for item in missing
            if item["swg_number"] not in existing_after_stop
        ]

        print_summary(
            links_found=len(all_links),
            already_saved=len(already_saved),
            ignored_found=len(ignored_on_page),
            processed=processed,
            failed=remaining,
        )
        return

    print_summary(
        links_found=len(all_links),
        already_saved=len(already_saved),
        ignored_found=len(ignored_on_page),
        processed=processed,
        failed=failed,
    )


if __name__ == "__main__":
    main()
