"""Re-extract consultant names with source evidence and confidence.

Run locally after setting OPENAI_API_KEY. The script updates only consultant
fields and does not overwrite the other AJD extraction results.
"""

import argparse
import json
import re
import sqlite3

from openai import OpenAI

from enrich_ajds import DB_PATH, AI_MODEL, setup_new_columns


SOURCE_TERMS = (
    "section 8",
    "sources of information",
    "supporting data",
    "prepared by",
    "prepared for",
    "consultant",
    "authorized agent",
    "agent for",
    "delineation report",
    "aquatic resources report",
    "wetland report",
)


def build_consultant_context(full_text, max_chars=55_000):
    """Prioritize Section 8/source citations and end-of-PDF map title blocks."""
    text = str(full_text or "")
    if len(text) <= max_chars:
        return text

    windows = []
    lowered = text.lower()

    for term in SOURCE_TERMS:
        for match in re.finditer(re.escape(term), lowered):
            start = max(0, match.start() - 2_000)
            end = min(len(text), match.end() + 4_000)
            windows.append(text[start:end])

    unique_windows = []
    seen = set()

    for window in windows:
        if window in seen:
            continue
        seen.add(window)
        unique_windows.append(window)

    separator = "\n\n--- RELEVANT DOCUMENT EXCERPT ---\n\n"
    body_budget = max_chars - 20_000 - len(separator)
    body = separator.join(unique_windows)[:body_budget]

    # Always reserve room for map title blocks near the end of the PDF.
    return f"{body}{separator}{text[-20_000:]}"[:max_chars]


def extract_consultant(client, swg_number, full_text):
    context = build_consultant_context(full_text)

    prompt = f"""
Identify the private environmental consultant or authorized agent associated
with this USACE Approved Jurisdictional Determination.

Return ONLY valid JSON with this structure:
{{
  "consultant": null,
  "consultant_source": "Not found",
  "consultant_page": null,
  "consultant_confidence": "low",
  "consultant_evidence": null
}}

Rules:
1. First inspect Section 8, Sources of Information, Supporting Data, or cited
   delineation/aquatic-resource reports.
2. If those do not identify the consultant, inspect map title blocks and
   preparation notes near the end of the PDF.
3. Do not confuse the applicant, landowner, surveyor, USACE reviewer, or an
   unrelated civil engineer with the environmental consultant.
4. Use consultant_source from: "Section 8 / Sources", "Map title block",
   "Supporting report", "Cover or transmittal", "Other", or "Not found".
5. Provide consultant_page only when an explicit === PAGE N === marker supports
   it. Otherwise use null.
6. High confidence requires an explicit consultant, agent, or prepared-by
   attribution. Medium confidence may use a clearly labeled report or map title
   block. If evidence is ambiguous, return consultant as null and confidence low.
7. consultant_evidence must be a short phrase showing the attribution.

AJD: {swg_number}

DOCUMENT TEXT:
{context}
""".strip()

    response = client.responses.create(model=AI_MODEL, input=prompt)
    raw = response.output_text.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "consultant": None,
            "consultant_source": "Not found",
            "consultant_page": None,
            "consultant_confidence": "low",
            "consultant_evidence": None,
        }

    if result.get("consultant_confidence") == "low":
        result["consultant"] = None

    return result


def save_consultant(conn, swg_number, result):
    conn.execute(
        """
        UPDATE ajds
        SET consultant = ?,
            consultant_source = ?,
            consultant_page = ?,
            consultant_confidence = ?,
            consultant_evidence = ?
        WHERE swg_number = ?
        """,
        (
            result.get("consultant") or "",
            result.get("consultant_source") or "Not found",
            str(result.get("consultant_page") or ""),
            result.get("consultant_confidence") or "low",
            result.get("consultant_evidence") or "",
            swg_number,
        ),
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--swg-number", help="Process one exact AJD number")
    args = parser.parse_args()

    if not args.dry_run:
        setup_new_columns()
    client = OpenAI()
    conn = sqlite3.connect(DB_PATH)

    query = """
        SELECT swg_number, full_ocr_text, consultant
        FROM ajds
        WHERE full_ocr_text IS NOT NULL
          AND TRIM(full_ocr_text) <> ''
    """

    parameters = []
    if args.only_missing:
        query += " AND (consultant IS NULL OR TRIM(consultant) = '')"
    if args.swg_number:
        query += " AND swg_number = ?"
        parameters.append(args.swg_number)

    query += " ORDER BY swg_number"

    if args.limit:
        query += " LIMIT ?"
        parameters.append(args.limit)

    rows = conn.execute(query, parameters).fetchall()
    print(f"Consultant records to process: {len(rows)}")

    for index, (swg_number, full_text, existing_consultant) in enumerate(rows, start=1):
        result = extract_consultant(client, swg_number, full_text)
        print(
            f"[{index}/{len(rows)}] {swg_number}: "
            f"{result.get('consultant') or 'Not found'} "
            f"({result.get('consultant_confidence', 'low')}; "
            f"{result.get('consultant_source', 'Not found')}); "
            f"existing: {existing_consultant or 'Not found'}"
        )

        if not args.dry_run:
            if result.get("consultant") and result.get("consultant_confidence") in {"high", "medium"}:
                save_consultant(conn, swg_number, result)
            else:
                print("  No supported replacement; existing value preserved.")

    conn.close()


if __name__ == "__main__":
    main()
