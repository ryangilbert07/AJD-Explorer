import json
import re
import sqlite3
from openai import OpenAI

DB_PATH = "data/ajds.sqlite"
AI_MODEL = "gpt-4o-mini"

client = OpenAI()

CONSULTANT_SOURCE_TERMS = (
    "section 8",
    "sources of information",
    "supporting data",
    "prepared by",
    "prepared for",
    "consultant",
    "authorized agent",
    "delineation report",
    "aquatic resources report",
    "wetland report",
)


def build_enrichment_context(full_text, max_chars=70_000):
    """Keep core AJD text plus likely consultant sources and final maps."""
    text = str(full_text or "")
    if len(text) <= max_chars:
        return text

    excerpts = [text[:30_000]]
    lowered = text.lower()

    for term in CONSULTANT_SOURCE_TERMS:
        for match in re.finditer(re.escape(term), lowered):
            start = max(0, match.start() - 1_500)
            end = min(len(text), match.end() + 3_000)
            excerpts.append(text[start:end])

    unique_excerpts = []
    seen = set()

    for excerpt in excerpts:
        if excerpt in seen:
            continue
        seen.add(excerpt)
        unique_excerpts.append(excerpt)

    separator = "\n\n--- RELEVANT DOCUMENT EXCERPT ---\n\n"
    body_budget = max_chars - 20_000 - len(separator)
    body = separator.join(unique_excerpts)[:body_budget]

    # Reserve room for supporting maps and title blocks appended to the PDF.
    return f"{body}{separator}{text[-20_000:]}"[:max_chars]


def setup_new_columns():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    new_columns = {
        "jd_type": "TEXT",
        "prepared_by": "TEXT",
        "approved_by": "TEXT",
        "consultant": "TEXT",
        "consultant_source": "TEXT",
        "consultant_page": "TEXT",
        "consultant_confidence": "TEXT",
        "consultant_evidence": "TEXT",
        "district": "TEXT",
        "determination_outcome": "TEXT",
        "regulatory_framework": "TEXT",
        "receiving_water": "TEXT"
    }

    for column, col_type in new_columns.items():
        try:
            cur.execute(f"ALTER TABLE ajds ADD COLUMN {column} {col_type}")
            print(f"Added column: {column}")
        except sqlite3.OperationalError:
            print(f"Column already exists: {column}")
    cur.execute("""
        UPDATE ajds SET district = 'Galveston District'
        WHERE LOWER(TRIM(district)) IN ('galveston', 'galveston district')
    """)
    cur.execute("""
        UPDATE ajds SET district = 'Fort Worth District'
        WHERE LOWER(TRIM(district)) IN ('fort worth', 'fort worth district')
    """)
    conn.commit()
    conn.close()


def extract_extra_fields(swg_number, full_text):
    document_context = build_enrichment_context(full_text)

    prompt = f"""
You are extracting structured data from a USACE Approved Jurisdictional Determination.

Return ONLY valid JSON. Do not include markdown.

Use null if the field is not found.

Extract these fields:

{{
  "jd_type": "",
  "prepared_by": "",
  "approved_by": "",
  "consultant": "",
  "consultant_source": "",
  "consultant_page": "",
  "consultant_confidence": "",
  "consultant_evidence": "",
  "district": "",
  "determination_outcome": "",
  "regulatory_framework": "",
  "receiving_water": ""
}}

Definitions:
- jd_type: examples include Approved Jurisdictional Determination, AJD, MFR, Basis Form, No Permit Required, etc.
- prepared_by: person who prepared the document, if listed.
- approved_by: person who signed or approved the determination, if listed.
- consultant: the private environmental consultant or authorized agent that
  prepared or supplied the delineation, aquatic resources report, supporting
  information, or maps. Do not use the applicant, landowner, surveyor, USACE
  reviewer, or engineering firm unless the document identifies that entity as
  the consultant/agent or preparer of the supporting environmental materials.
- consultant_source: use one of "Section 8 / Sources", "Map title block",
  "Supporting report", "Cover or transmittal", "Other", or "Not found".
- consultant_page: page number only when an explicit marker such as
  "=== PAGE 12 ===" identifies the page; otherwise null.
- consultant_confidence: "high", "medium", or "low". High requires an explicit
  consultant/agent/prepared-by attribution. Medium may rely on a clearly labeled
  report or map title block. Low is ambiguous and should normally leave
  consultant null.
- consultant_evidence: a short exact identifying phrase or close transcription
  showing why the consultant was selected.
- For consultant identification, inspect sources/supporting-data language in
  Section 8 of the AJD form first. If it does not identify the consultant,
  inspect report citations and the title blocks or preparation notes on maps
  near the end of the PDF.
- district: USACE district, usually Galveston District.
- determination_outcome: short plain-English outcome, such as No Waters Present, Jurisdictional Waters Present, Non-Jurisdictional Features, No Section 404 Waters, Section 10 Waters Present, etc.
- regulatory_framework: examples include Pre-2015 Regulatory Regime, Post-Sackett, 2023 Rule, Rapanos, Relatively Permanent Standard, etc.
- receiving_water: nearest TNW, RPW, traditional navigable water, receiving water, bay, river, canal, channel, or named waterbody.

SWG Number:
{swg_number}

OCR TEXT:
{document_context}
"""

    response = client.responses.create(
        model=AI_MODEL,
        input=prompt
    )

    raw = response.output_text.strip()

    try:
        return json.loads(raw)
    except Exception:
        print(f"Could not parse JSON for {swg_number}")
        return {}


def update_record(swg_number, extracted):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE ajds
        SET
            jd_type = ?,
            prepared_by = ?,
            approved_by = ?,
            consultant = ?,
            consultant_source = ?,
            consultant_page = ?,
            consultant_confidence = ?,
            consultant_evidence = ?,
            district = ?,
            determination_outcome = ?,
            regulatory_framework = ?,
            receiving_water = ?
        WHERE swg_number = ?
        """,
        (
            extracted.get("jd_type") or "",
            extracted.get("prepared_by") or "",
            extracted.get("approved_by") or "",
            extracted.get("consultant") or "",
            extracted.get("consultant_source") or "",
            extracted.get("consultant_page") or "",
            extracted.get("consultant_confidence") or "",
            extracted.get("consultant_evidence") or "",
            extracted.get("district") or "",
            extracted.get("determination_outcome") or "",
            extracted.get("regulatory_framework") or "",
            extracted.get("receiving_water") or "",
            swg_number
        )
    )

    conn.commit()
    conn.close()


def main():
    setup_new_columns()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT swg_number, full_ocr_text
        FROM ajds
        WHERE full_ocr_text IS NOT NULL
        """
    )

    rows = cur.fetchall()
    conn.close()

    print(f"Found {len(rows)} records to enrich.")

    for swg_number, full_text in rows:
        print(f"Enriching {swg_number}")

        extracted = extract_extra_fields(swg_number, full_text)
        update_record(swg_number, extracted)

    print("Done enriching AJD records.")


if __name__ == "__main__":
    main()
