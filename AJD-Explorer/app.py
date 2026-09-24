import json
import os
import re
import sqlite3
from datetime import datetime
from io import BytesIO

import folium
import pandas as pd
import streamlit as st
from folium.plugins import MarkerCluster
from openai import OpenAI
from streamlit_folium import st_folium
from display_names import (
    consolidate_feature_type_names,
    consolidate_consultant_names,
    normalize_consultant_name,
    normalize_district_name,
    normalize_feature_type,
)


DB_PATH = "data/ajds.sqlite"


def get_last_jd_update():
    """Use the latest stored JD update time, independent of deploys."""
    if not os.path.exists(DB_PATH):
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            latest = conn.execute(
                "SELECT MAX(COALESCE(NULLIF(last_updated, ''), "
                "NULLIF(date_added, ''))) FROM ajds"
            ).fetchone()[0]
        return datetime.fromisoformat(latest) if latest else None
    except (sqlite3.Error, ValueError, TypeError):
        return None


# ------------------------------------------------------------
# Page configuration
# ------------------------------------------------------------

st.set_page_config(
    page_title="AJD Explorer",
    layout="wide",
)

SEARCH_CHUNK_SIZE = 4_500
SEARCH_CHUNK_OVERLAP = 700
MAX_PDF_PASSAGES = 24
MAX_PASSAGES_PER_AJD = 3


# ------------------------------------------------------------
# Normalization helpers
# ------------------------------------------------------------

def normalize_text(value):
    if value is None:
        return ""

    value = str(value).strip()

    if value.lower() in {"none", "null", "nan", "n/a", "na"}:
        return ""

    return value


def normalize_outcome(value):
    value = normalize_text(value)
    text = value.lower()

    if not text:
        return "Unknown"

    if "no waters" in text or "dry land" in text:
        return "No Waters Present"

    if "non-jurisdictional" in text or "nonjurisdictional" in text:
        return "Non-Jurisdictional Features"

    if "jurisdictional waters present" in text:
        return "Jurisdictional Waters Present"

    if "jurisdictional" in text and "non" not in text:
        return "Jurisdictional Waters Present"

    return value

def normalize_district(value):
    value = normalize_text(value)
    name = value.lower().removesuffix(" district").strip()

    if name == "galveston":
        return "Galveston District"
    if name == "fort worth":
        return "Fort Worth District"

    return value

def normalize_consultant(value):
    value = normalize_text(value)
    text = value.lower()

    if not text:
        return ""

    if "marcia appia" in text:
        return "Marcia Appia Engineers, LLC"

    if "raba kistner" in text:
        return "Raba Kistner"

    if "swca" in text:
        return "SWCA Environmental Consultants"

    if "freese" in text and "nichols" in text:
        return "Freese and Nichols"

    if "power engineers" in text:
        return "Power Engineers"

    if "kimley" in text and "horn" in text:
        return "Kimley-Horn"

    if "dewberry" in text:
        return "Dewberry Engineers, Inc."

    if "benchmark" in text:
        return "Benchmark Ecological Services Inc."

    if "harris county flood control" in text:
        return "Harris County Flood Control District"

    return value


def normalize_approved_by(value):
    value = normalize_text(value)
    text = value.lower()

    if not text:
        return ""

    if "andria" in text and "davis" in text:
        return "Andria Davis"

    if ("kara" in text or "karie" in text) and "vick" in text:
        return "Kara Vick Clark"

    if "marie" in text and "taylor" in text:
        return "K. Marie Taylor"

    if ("matthew" in text or "matther" in text) and "kimmel" in text:
        return "Matthew Kimmel"

    return value


def normalize_prepared_by(value):
    value = normalize_text(value)
    text = value.lower()

    if not text:
        return ""

    if "bogrand" in text or "bogard" in text:
        return "Ashley Bogard"

    if "avettin" in text or "avertin" in text or "wiki" in text:
        return "Avettin Wore"

    return value


# ------------------------------------------------------------
# Data loading
# ------------------------------------------------------------

@st.cache_data
def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame(), pd.DataFrame()

    conn = sqlite3.connect(DB_PATH)

    try:
        # The main page needs structured fields, not every PDF's OCR.
        column_names = [
            column[1] for column in conn.execute("PRAGMA table_info(ajds)")
            if column[1] != "full_ocr_text"
        ]
        raw_df = pd.read_sql_query(
            f"SELECT {', '.join(column_names)} FROM ajds ORDER BY swg_number DESC",
            conn,
        )
    finally:
        conn.close()

    records = []
    feature_records = []

    for _, row in raw_df.iterrows():
        try:
            data = json.loads(normalize_text(row.get("extracted_json", "")) or "{}")
        except Exception:
            data = {}

        features = data.get("features", [])

        if not isinstance(features, list):
            features = []

        feature_types = sorted(
            {
                normalize_feature_type(feature.get("feature_type", ""))
                for feature in features
                if isinstance(feature, dict)
                and normalize_text(feature.get("feature_type", ""))
            }
        )

        statuses = sorted(
            {
                normalize_outcome(feature.get("jurisdictional_status", ""))
                for feature in features
                if isinstance(feature, dict)
                and normalize_text(feature.get("jurisdictional_status", ""))
            }
        )

        jd_id = normalize_text(row.get("swg_number", ""))

        try:
            year = jd_id.split("-")[1]
        except Exception:
            year = ""

        combined_text = " ".join(
            [
                normalize_text(row.get("feature_summary", "")),
                normalize_text(row.get("jurisdictional_reasoning", "")),
                normalize_text(row.get("jurisdictional_waters", "")),
                normalize_text(row.get("non_jurisdictional_features", "")),
            ]
        ).lower()

        fallback_outcome = "Unknown"
        waters_present = "Unclear"

        jurisdictional_waters_text = normalize_text(
            row.get("jurisdictional_waters", "")
        ).lower()

        if "no waters" in combined_text or "dry land" in combined_text:
            fallback_outcome = "No Waters Present"
            waters_present = "No"
        elif (
            "non-jurisdictional" in combined_text
            and "jurisdictional" not in jurisdictional_waters_text
        ):
            fallback_outcome = "Non-Jurisdictional Features"
            waters_present = "No"
        elif (
            "jurisdictional" in combined_text
            or "waters of the united states" in combined_text
        ):
            fallback_outcome = "Jurisdictional Waters Present"
            waters_present = "Yes"

        determination_outcome = normalize_outcome(
            row.get("determination_outcome", "")
        )

        if determination_outcome == "Unknown":
            determination_outcome = fallback_outcome

        if determination_outcome == "Jurisdictional Waters Present":
            waters_present = "Yes"
        elif determination_outcome in {
            "Non-Jurisdictional Features",
            "No Waters Present",
        }:
            waters_present = "No"

        if "relatively permanent" in combined_text or "rpw" in combined_text:
            primary_basis = "Relatively Permanent Water / RPW"
        elif "tnw" in combined_text or "traditional navigable water" in combined_text:
            primary_basis = "Traditional Navigable Water / TNW"
        elif "adjacent" in combined_text:
            primary_basis = "Adjacent Wetland"
        elif "dry land" in combined_text or "no waters" in combined_text:
            primary_basis = "No Aquatic Resources / Dry Land"
        elif "ditch" in combined_text:
            primary_basis = "Ditch Analysis"
        else:
            primary_basis = ""

        consultant = normalize_consultant_name(row.get("consultant", ""))
        prepared_by = normalize_prepared_by(row.get("prepared_by", ""))
        approved_by = normalize_approved_by(row.get("approved_by", ""))

        latitude = normalize_text(row.get("latitude", ""))
        longitude = normalize_text(row.get("longitude", ""))

        quality_issues = []

        if not latitude or not longitude:
            quality_issues.append("Missing Coordinates")

        if not feature_types:
            quality_issues.append("No Features Extracted")

        if not normalize_text(row.get("county", "")):
            quality_issues.append("Missing County")

        if not normalize_text(row.get("pdf_url", "")):
            quality_issues.append("Missing PDF Link")

        if not normalize_text(row.get("feature_summary", "")):
            quality_issues.append("Missing AI Summary")

        if not normalize_text(row.get("jurisdictional_reasoning", "")):
            quality_issues.append("Missing Jurisdictional Reasoning")

        quality_flag = "Good" if not quality_issues else "; ".join(quality_issues)

        records.append(
            {
                "JD ID": jd_id,
                "Project Name": normalize_text(row.get("project_name", "")),
                "County": normalize_text(row.get("county", "")),
                "State": normalize_text(row.get("state", "")),
                "Year": year,
                "JD Date": normalize_text(row.get("issue_date", "")),
                "JD Type": normalize_text(row.get("jd_type", "")),
                "USACE District": normalize_district(row.get("district", ""))
                or "Galveston",
                "Prepared By": prepared_by,
                "Approved By": approved_by,
                "Applicant": normalize_text(row.get("applicant", "")),
                "Consultant": consultant,
                "Consultant Source": normalize_text(
                    row.get("consultant_source", "")
                ),
                "Consultant Page": normalize_text(
                    row.get("consultant_page", "")
                ),
                "Consultant Confidence": normalize_text(
                    row.get("consultant_confidence", "")
                ),
                "Consultant Evidence": normalize_text(
                    row.get("consultant_evidence", "")
                ),
                "Feature Type(s)": ", ".join(feature_types),
                "Feature Status(es)": ", ".join(statuses),
                "Waters Present": waters_present,
                "Determination Outcome": determination_outcome,
                "Primary Determination Basis": primary_basis,
                "TNW / Receiving Water": (
                    normalize_text(row.get("receiving_water", ""))
                    or normalize_text(row.get("nearest_waterbody", ""))
                ),
                "AI Summary": normalize_text(row.get("feature_summary", "")),
                "Jurisdictional Reasoning": normalize_text(
                    row.get("jurisdictional_reasoning", "")
                ),
                "PDF Link": normalize_text(row.get("pdf_url", "")),
                "Latitude": latitude,
                "Longitude": longitude,
                "Quality Flag": quality_flag,
                "Full OCR Text": "",  # Retrieved from SQLite only when needed.
                "Full Record": row.to_dict(),
            }
        )

        for feature in features:
            if not isinstance(feature, dict):
                continue

            feature_records.append(
                {
                    "JD ID": jd_id,
                    "County": normalize_text(row.get("county", "")),
                    "State": normalize_text(row.get("state", "")),
                    "Feature ID": normalize_text(feature.get("feature_id", "")),
                    "Feature Type": normalize_feature_type(
                        feature.get("feature_type", "")
                    ),
                    "Jurisdictional Status": normalize_outcome(
                        feature.get("jurisdictional_status", "")
                    ),
                    "Basis": normalize_text(feature.get("basis", "")),
                    "Reasoning": normalize_text(feature.get("reasoning", "")),
                    "PDF Link": normalize_text(row.get("pdf_url", "")),
                }
            )

    feature_labels = consolidate_feature_type_names(
        [item["Feature Type"] for item in feature_records]
    )
    feature_types_by_jd = {}
    for item, label in zip(feature_records, feature_labels):
        item["Feature Type"] = label
        if label:
            feature_types_by_jd.setdefault(item["JD ID"], set()).add(label)
    for item in records:
        if item["JD ID"] in feature_types_by_jd:
            item["Feature Type(s)"] = ", ".join(
                sorted(feature_types_by_jd[item["JD ID"]])
            )

    ajd_df = pd.DataFrame(records)
    if not ajd_df.empty:
        ajd_df["Consultant"] = consolidate_consultant_names(ajd_df["Consultant"])
        ajd_df["USACE District"] = ajd_df["USACE District"].map(normalize_district_name)
    return ajd_df, pd.DataFrame(feature_records)


# ------------------------------------------------------------
# Export helpers
# ------------------------------------------------------------

def make_excel_download(ajd_df, feature_df):
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        ajd_df.to_excel(writer, index=False, sheet_name="AJDs")
        feature_df.to_excel(writer, index=False, sheet_name="Features")

    return output.getvalue()


def marker_color(outcome):
    text = str(outcome).lower()

    if "jurisdictional waters present" in text:
        return "green"

    if "non-jurisdictional" in text:
        return "red"

    if "no waters" in text:
        return "gray"

    return "blue"


# ------------------------------------------------------------
# AI search helpers
# ------------------------------------------------------------

# OCR memory optimization: read one JD at a time, including during search.
def get_ocr_text(jd_id):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT full_ocr_text FROM ajds WHERE swg_number = ?", (jd_id,)
        ).fetchone()
    return (row[0] or "") if row else ""


def iter_ocr_rows(source_df):
    """Attach OCR to one row at a time; never cache the full OCR dataset."""
    with sqlite3.connect(DB_PATH) as conn:
        for _, metadata in source_df.iterrows():
            row = metadata.copy()
            result = conn.execute(
                "SELECT full_ocr_text FROM ajds WHERE swg_number = ?",
                (row["JD ID"],),
            ).fetchone()
            row["Full OCR Text"] = (result[0] or "") if result else ""
            yield row



def build_search_text(row):
    return " ".join(
        [
            str(row.get("JD ID", "")),
            str(row.get("Project Name", "")),
            str(row.get("County", "")),
            str(row.get("State", "")),
            str(row.get("USACE District", "")),
            str(row.get("Applicant", "")),
            str(row.get("Consultant", "")),
            str(row.get("Consultant Source", "")),
            str(row.get("Consultant Evidence", "")),
            str(row.get("Prepared By", "")),
            str(row.get("Approved By", "")),
            str(row.get("Feature Type(s)", "")),
            str(row.get("Feature Status(es)", "")),
            str(row.get("Waters Present", "")),
            str(row.get("Determination Outcome", "")),
            str(row.get("Primary Determination Basis", "")),
            str(row.get("TNW / Receiving Water", "")),
            str(row.get("AI Summary", "")),
            str(row.get("Jurisdictional Reasoning", "")),
            str(row.get("Full OCR Text", "")),
        ]
    ).lower()


def normalize_query_terms(question):
    cleaned = re.sub(r"[^a-z0-9\- ]+", " ", question.lower())

    stop_words = {
        "find",
        "show",
        "who",
        "how",
        "which",
        "what",
        "where",
        "when",
        "why",
        "the",
        "and",
        "are",
        "was",
        "were",
        "does",
        "did",
        "have",
        "has",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "about",
        "all",
        "ajd",
        "ajds",
        "mention",
        "mentioning",
        "involving",
        "involve",
        "please",
        "give",
        "list",
        "records",
        "record",
        "pdf",
        "pdfs",
        "document",
        "documents",
    }

    terms = [
        term
        for term in cleaned.split()
        if len(term) > 2 and term not in stop_words
    ]

    # Add common singular/plural variants for exact keyword searches.
    expanded = set(terms)

    for term in terms:
        if term.endswith("s") and len(term) > 4:
            expanded.add(term[:-1])
        elif not term.endswith("s"):
            expanded.add(term + "s")

    # Add useful regulatory acronym/phrase variants.
    joined = " ".join(terms)

    if "relatively permanent water" in cleaned or "rpw" in cleaned:
        expanded.update(
            {
                "rpw",
                "relatively permanent water",
                "relatively permanent waters",
            }
        )

    if "non jurisdictional" in cleaned or "non-jurisdictional" in question.lower():
        expanded.update(
            {
                "non-jurisdictional",
                "non jurisdictional",
                "nonjurisdictional",
            }
        )

    return sorted(expanded, key=len, reverse=True)


def iter_ocr_chunks(text, chunk_size=SEARCH_CHUNK_SIZE, overlap=SEARCH_CHUNK_OVERLAP):
    """Yield overlapping OCR chunks without discarding text later in a PDF."""
    text = normalize_text(text)

    if not text:
        return

    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)

        if end < text_length:
            boundary = max(
                text.rfind("\n", start + chunk_size // 2, end),
                text.rfind(". ", start + chunk_size // 2, end),
            )
            if boundary > start:
                end = boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            yield chunk

        if end >= text_length:
            break

        start = max(end - overlap, start + 1)


def _passage_score(chunk, terms, question):
    """Rank OCR passages using phrase, term-frequency, and concept coverage."""
    chunk_lower = chunk.lower()
    score = 0
    concepts_matched = set()

    for term in terms:
        count = chunk_lower.count(term)
        if not count:
            continue

        concepts_matched.add(term)
        weight = 8 if " " in term else 3
        score += weight * min(count, 4)

    cleaned_question = re.sub(r"\s+", " ", question.lower()).strip()
    if cleaned_question and cleaned_question in chunk_lower:
        score += 20

    score += len(concepts_matched) * 4
    return score


def retrieve_pdf_passages(
    question,
    source_df,
    max_passages=MAX_PDF_PASSAGES,
    max_per_ajd=MAX_PASSAGES_PER_AJD,
):
    """Search OCR text from every currently filtered PDF and return evidence."""
    if source_df.empty:
        return []

    terms = normalize_query_terms(question)
    candidates = []

    for row in iter_ocr_rows(source_df):
        jd_candidates = []

        for chunk_number, chunk in enumerate(
            iter_ocr_chunks(row.get("Full OCR Text", "")),
            start=1,
        ):
            score = _passage_score(chunk, terms, question)
            if score <= 0:
                continue

            jd_candidates.append(
                {
                    "score": score,
                    "jd_id": row.get("JD ID", ""),
                    "county": row.get("County", ""),
                    "state": row.get("State", ""),
                    "outcome": row.get("Determination Outcome", ""),
                    "pdf_url": row.get("PDF Link", ""),
                    "chunk_number": chunk_number,
                    "text": chunk,
                }
            )

        jd_candidates.sort(key=lambda item: item["score"], reverse=True)
        candidates.extend(jd_candidates[:max_per_ajd])

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[:max_passages]


def get_openai_api_key():
    """Support both Render environment variables and Streamlit secrets."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if api_key:
        return api_key

    try:
        return str(st.secrets.get("OPENAI_API_KEY", "")).strip()
    except Exception:
        return ""


def retrieve_relevant_records(question, source_df, ai_record_limit=20):
    """
    Search every AJD, including Full OCR Text.

    Returns:
        all_matches: every matching AJD for the results table
        ai_rows: highest-ranked subset sent to OpenAI for summarization
    """
    if source_df.empty:
        return [], []

    terms = normalize_query_terms(question)

    # Phrase-level filters that should be treated as required context.
    question_lower = question.lower()
    required_county = None

    counties = [
        county
        for county in source_df["County"].dropna().unique()
        if county
    ]

    for county in counties:
        county_text = str(county).lower()

        if county_text in question_lower:
            required_county = str(county)
            break

    required_district = None

    for district in source_df["USACE District"].dropna().unique():
        district_text = str(district).lower()

        if district_text and district_text in question_lower:
            required_district = str(district)
            break

        short_name = district_text.replace(" district", "")

        if short_name and short_name in question_lower:
            required_district = str(district)
            break

    scored_matches = []

    for row in iter_ocr_rows(source_df):
        if required_county and row.get("County", "") != required_county:
            continue

        if required_district and row.get("USACE District", "") != required_district:
            continue

        search_text = build_search_text(row)

        matched_terms = [
            term
            for term in terms
            if term in search_text
        ]

        if not matched_terms:
            continue

        # Weight matches in stronger structured fields more heavily than OCR-only matches.
        structured_text = " ".join(
            [
                str(row.get("Feature Type(s)", "")),
                str(row.get("Feature Status(es)", "")),
                str(row.get("Determination Outcome", "")),
                str(row.get("Primary Determination Basis", "")),
                str(row.get("TNW / Receiving Water", "")),
                str(row.get("AI Summary", "")),
                str(row.get("Jurisdictional Reasoning", "")),
            ]
        ).lower()

        score = 0

        for term in matched_terms:
            score += 3 if term in structured_text else 1

        # Reward records matching more distinct concepts.
        score += len(set(matched_terms))

        scored_matches.append(
            {
                "score": score,
                "row": row.drop(labels=["Full OCR Text"]),
                "matched_terms": matched_terms,
            }
        )

    scored_matches.sort(
        key=lambda item: (
            item["score"],
            str(item["row"].get("JD Date", "")),
        ),
        reverse=True,
    )

    all_matches = [item["row"] for item in scored_matches]

    if not all_matches:
        # For broad analytical questions without a meaningful keyword,
        # provide the filtered dataset rather than arbitrary OCR matches.
        broad_question_markers = {
            "most",
            "compare",
            "count",
            "how many",
            "top",
            "common",
        }

        if any(marker in question_lower for marker in broad_question_markers):
            all_matches = [
                row
                for _, row in source_df.iterrows()
            ]

    ai_rows = all_matches[:ai_record_limit]

    return all_matches, ai_rows


def ask_database(question, relevant_rows, feature_df, pdf_passages):
    api_key = get_openai_api_key()
    if not api_key:
        raise KeyError("OPENAI_API_KEY")

    client = OpenAI(api_key=api_key)

    context_blocks = []

    for row in relevant_rows:
        jd_id = row["JD ID"]

        related_features = (
            feature_df[feature_df["JD ID"] == jd_id]
            if not feature_df.empty
            else pd.DataFrame()
        )

        features_text = ""

        if not related_features.empty:
            features_text = related_features[
                [
                    "Feature ID",
                    "Feature Type",
                    "Jurisdictional Status",
                    "Basis",
                    "Reasoning",
                ]
            ].to_string(index=False)

        context_blocks.append(
            f"""
JD ID: {row['JD ID']}
County: {row['County']}
State: {row['State']}
Year: {row['Year']}
Project Name: {row['Project Name']}
Applicant: {row['Applicant']}
Consultant: {row['Consultant']}
Consultant Source: {row.get('Consultant Source', '')}
Consultant Confidence: {row.get('Consultant Confidence', '')}
Prepared By: {row['Prepared By']}
Approved By: {row['Approved By']}
Feature Types: {row['Feature Type(s)']}
Feature Statuses: {row['Feature Status(es)']}
Waters Present: {row['Waters Present']}
Determination Outcome: {row['Determination Outcome']}
Primary Basis: {row['Primary Determination Basis']}
TNW / Receiving Water: {row['TNW / Receiving Water']}
AI Summary: {row['AI Summary']}
Jurisdictional Reasoning: {row['Jurisdictional Reasoning']}
PDF Link: {row['PDF Link']}

Extracted Features:
{features_text}
""".strip()
        )

    context = "\n\n---\n\n".join(context_blocks)

    passage_blocks = []

    for index, passage in enumerate(pdf_passages, start=1):
        passage_blocks.append(
            f"""
[Evidence {index}]
JD ID: {passage['jd_id']}
County/State: {passage['county']}, {passage['state']}
Determination Outcome: {passage['outcome']}
PDF Link: {passage['pdf_url']}
OCR Passage: {passage['text']}
""".strip()
        )

    passage_context = "\n\n---\n\n".join(passage_blocks)

    prompt = f"""
You are the document research assistant for AJD Explorer, an unofficial
collection of USACE Approved Jurisdictional Determinations.

Answer the user's question using only the database records provided below.

Rules:
- Begin with: "Based on the extracted database records..."
- Do not make official legal or regulatory conclusions.
- Base factual claims on the retrieved OCR evidence passages whenever possible.
- The retrieval searched the full stored OCR text of every PDF in the currently
  filtered dataset, not merely the beginning of each document.
- Cite supporting evidence inline as [Evidence 1], [Evidence 2], and so on.
- Do not invent page numbers; legacy OCR does not preserve page boundaries.
- Summarize the most relevant AJDs from the supplied evidence.
- For each AJD discussed, include the JD ID, county/state, determination outcome, and PDF link.
- Format links using Markdown, for example: [Open PDF](https://example.com/file.pdf).
- If the supplied records do not answer the question, say so clearly.
- Be concise but useful.

User question:
{question}

Database records:
{context}

Retrieved full-PDF OCR evidence:
{passage_context}
""".strip()

    response = client.responses.create(
        model="gpt-4o-mini",
        input=prompt,
    )

    return response.output_text


def set_chat_question(question):
    st.session_state["chat_question"] = question


# ------------------------------------------------------------
# Load database
# ------------------------------------------------------------

df, features_df = load_data()

if df.empty:
    st.error(
        f"No AJD records could be loaded. Confirm that {DB_PATH} exists "
        "and contains an ajds table."
    )
    st.stop()


# ------------------------------------------------------------
# Sidebar filters
# ------------------------------------------------------------

st.sidebar.header("Filters")

keyword = st.sidebar.text_input("Keyword Search")

county_filter = st.sidebar.multiselect(
    "County / Parish",
    sorted(value for value in df["County"].dropna().unique() if value),
)

state_filter = st.sidebar.multiselect(
    "State",
    sorted(value for value in df["State"].dropna().unique() if value),
)

year_filter = st.sidebar.multiselect(
    "Year",
    sorted(value for value in df["Year"].dropna().unique() if value),
)

outcome_filter = st.sidebar.multiselect(
    "Determination Outcome",
    sorted(
        value
        for value in df["Determination Outcome"].dropna().unique()
        if value
    ),
)

feature_options = sorted(
    {
        feature.strip()
        for value in df["Feature Type(s)"]
        for feature in str(value).split(",")
        if feature.strip()
    }
)

feature_filter = st.sidebar.multiselect(
    "Feature Type",
    feature_options,
)

status_options = sorted(
    {
        status.strip()
        for value in df["Feature Status(es)"]
        for status in str(value).split(",")
        if status.strip()
    }
)

status_filter = st.sidebar.multiselect(
    "Feature Jurisdictional Status",
    status_options,
)

district_filter = st.sidebar.multiselect(
    "USACE District",
    sorted(
        value
        for value in df["USACE District"].dropna().unique()
        if value
    ),
)

with st.sidebar.expander("Advanced Filters"):
    consultant_filter = st.multiselect(
        "Consultant",
        sorted(
            value
            for value in df["Consultant"].dropna().unique()
            if value
        ),
    )

    quality_filter = st.multiselect(
        "Quality Flag",
        sorted(
            value
            for value in df["Quality Flag"].dropna().unique()
            if value
        ),
    )
    prepared_by_filter = st.multiselect(
        "Prepared By",
        sorted(
            value
            for value in df["Prepared By"].dropna().unique()
            if value
        ),
    )

    approved_by_filter = st.multiselect(
        "Approved By",
        sorted(
            value
            for value in df["Approved By"].dropna().unique()
            if value
        ),
    )

    waters_filter = st.multiselect(
        "Waters Present",
        sorted(
            value
            for value in df["Waters Present"].dropna().unique()
            if value
        ),
    )

    basis_filter = st.multiselect(
        "Primary Determination Basis",
        sorted(
            value
            for value in df["Primary Determination Basis"].dropna().unique()
            if value
        ),
    )

    receiving_water_contains = st.text_input(
        "TNW / Receiving Water Contains"
    )


# ------------------------------------------------------------
# Apply filters
# ------------------------------------------------------------

filtered = df.copy()

if keyword:
    keyword_lower = keyword.lower()
    # Search OCR inside SQLite, keeping it out of the Streamlit dataframe.
    with sqlite3.connect(DB_PATH) as conn:
        ocr_matches = {
            row[0] for row in conn.execute(
                "SELECT swg_number FROM ajds "
                "WHERE instr(lower(full_ocr_text), ?) > 0",
                (keyword_lower,),
            )
        }
    filtered = filtered[
        filtered.apply(
            lambda row: keyword_lower in str(row).lower()
            or row["JD ID"] in ocr_matches,
            axis=1,
        )
    ]

if county_filter:
    filtered = filtered[filtered["County"].isin(county_filter)]

if state_filter:
    filtered = filtered[filtered["State"].isin(state_filter)]

if district_filter:
    filtered = filtered[
        filtered["USACE District"].isin(district_filter)
    ]

if year_filter:
    filtered = filtered[filtered["Year"].isin(year_filter)]

if outcome_filter:
    filtered = filtered[
        filtered["Determination Outcome"].isin(outcome_filter)
    ]

if feature_filter:
    filtered = filtered[
        filtered["Feature Type(s)"].apply(
            lambda value: any(
                feature in [part.strip() for part in str(value).split(",")]
                for feature in feature_filter
            )
        )
    ]

if status_filter:
    filtered = filtered[
        filtered["Feature Status(es)"].apply(
            lambda value: any(
                status in str(value)
                for status in status_filter
            )
        )
    ]

if consultant_filter:
    filtered = filtered[
        filtered["Consultant"].isin(consultant_filter)
    ]

if quality_filter:
    filtered = filtered[
        filtered["Quality Flag"].isin(quality_filter)
    ]

if prepared_by_filter:
    filtered = filtered[
        filtered["Prepared By"].isin(prepared_by_filter)
    ]

if approved_by_filter:
    filtered = filtered[
        filtered["Approved By"].isin(approved_by_filter)
    ]

if waters_filter:
    filtered = filtered[
        filtered["Waters Present"].isin(waters_filter)
    ]

if basis_filter:
    filtered = filtered[
        filtered["Primary Determination Basis"].isin(basis_filter)
    ]

if receiving_water_contains:
    filtered = filtered[
        filtered["TNW / Receiving Water"]
        .astype(str)
        .str.lower()
        .str.contains(receiving_water_contains.lower(), na=False)
    ]

visible_features = (
    features_df[features_df["JD ID"].isin(filtered["JD ID"])]
    if not features_df.empty
    else pd.DataFrame()
)


# ------------------------------------------------------------
# Page header
# ------------------------------------------------------------

st.title("AJD Explorer")
st.caption(
    "Search, compare, and explore publicly available USACE Approved "
    "Jurisdictional Determinations."
)

last_jd_update = get_last_jd_update()
if last_jd_update:
    st.caption(
        f"JDs last updated: "
        f"{last_jd_update.strftime('%Y-%m-%d %H:%M')}"
    )

st.warning(
    "Unofficial research tool. Data were extracted from publicly available "
    "USACE AJD PDFs using OCR and AI-assisted parsing. Verify all information "
    "against the original PDF before relying on it for regulatory, legal, or "
    "permitting decisions."
)

# ------------------------------------------------------------
# Database coverage
# ------------------------------------------------------------

st.subheader("Database Coverage")

coverage_columns = st.columns(4)

coverage_columns[0].metric(
    "AJDs in Database",
    len(df),
)

galveston_count = len(
    df[df["USACE District"] == "Galveston District"]
)

fort_worth_count = len(
    df[df["USACE District"] == "Fort Worth District"]
)

coverage_columns[1].metric(
    "Galveston District",
    galveston_count,
)

coverage_columns[2].metric(
    "Fort Worth District",
    fort_worth_count,
)

if last_jd_update:
    coverage_columns[3].metric(
        "Last Updated",
        last_jd_update.strftime("%b %d, %Y"),
    )
else:
    coverage_columns[3].metric(
        "Last Updated",
        "Unknown",
    )


# ------------------------------------------------------------
# Ask the Database
# ------------------------------------------------------------

st.divider()
st.header("Ask the Database")

st.info(
    f"Ask questions across the complete stored OCR text of the {len(filtered):,} "
    "AJD PDFs currently selected by the sidebar filters. Answers include source "
    "PDFs and evidence references and should be verified against the originals."
)

st.write("### Popular Questions")

q1, q2, q3, q4 = st.columns(4)

with q1:
    st.button(
        "RPWs in Fort Bend County",
        on_click=set_chat_question,
        args=(
            "Find AJDs mentioning relatively permanent waters "
            "in Fort Bend County.",
        ),
        width="stretch",
    )

with q2:
    st.button(
        "AJDs mentioning irrigation",
        on_click=set_chat_question,
        args=("Find AJDs mentioning irrigation.",),
        width="stretch",
    )

with q3:
    st.button(
        "Non-jurisdictional ditches",
        on_click=set_chat_question,
        args=("Find non-jurisdictional ditches.",),
        width="stretch",
    )

with q4:
    st.button(
        "AJDs mentioning culverts",
        on_click=set_chat_question,
        args=("Find AJDs mentioning culverts.",),
        width="stretch",
    )

user_question = st.text_area(
    "Ask a question",
    key="chat_question",
    height=110,
    placeholder=(
        "Example: Which non-jurisdictional wetland determinations "
        "were issued in Harris County?"
    ),
)

if "chat_answer" not in st.session_state:
    st.session_state["chat_answer"] = ""

if "chat_records" not in st.session_state:
    st.session_state["chat_records"] = []

if "chat_match_count" not in st.session_state:
    st.session_state["chat_match_count"] = 0

if "chat_ai_record_count" not in st.session_state:
    st.session_state["chat_ai_record_count"] = 0

if st.button(
    "Ask Database",
    type="primary",
    width="content",
):
    if not user_question.strip():
        st.warning("Please enter a question.")
    elif filtered.empty:
        st.warning(
            "No AJDs match the current filters. Clear or change the "
            "sidebar filters and try again."
        )
    else:
        try:
            with st.spinner(
                "Searching all filtered PDF text and generating an answer..."
            ):
                all_matches, ai_rows = retrieve_relevant_records(
                    user_question,
                    filtered,
                    ai_record_limit=20,
                )

                pdf_passages = retrieve_pdf_passages(
                    user_question,
                    filtered,
                )

                if not all_matches and not pdf_passages:
                    answer = (
                        "Based on the extracted database records, no AJDs "
                        "matched the search terms in the currently filtered dataset."
                    )
                else:
                    if not ai_rows and pdf_passages:
                        matched_ids = {
                            passage["jd_id"] for passage in pdf_passages
                        }
                        ai_rows = [
                            row
                            for _, row in filtered.iterrows()
                            if row.get("JD ID", "") in matched_ids
                        ][:20]
                        all_matches = ai_rows

                    answer = ask_database(
                        user_question,
                        ai_rows,
                        visible_features,
                        pdf_passages,
                    )

            # Store the complete matching set so the table shows every result,
            # while OpenAI only summarizes the highest-ranked subset.
            st.session_state["chat_answer"] = answer
            st.session_state["chat_records"] = [
                row.to_dict() for row in all_matches
            ]
            st.session_state["chat_match_count"] = len(all_matches)
            st.session_state["chat_ai_record_count"] = len(ai_rows)

        except KeyError:
            st.error(
                "The OpenAI API key is missing. Add OPENAI_API_KEY to "
                ".streamlit/secrets.toml locally or as an environment variable "
                "in the hosting service."
            )
        except Exception as error:
            st.error(
                "Something went wrong while answering the question."
            )
            st.exception(error)

# Always render the saved answer after a rerun.
if st.session_state["chat_answer"]:
    st.write("### Answer")
    st.markdown(st.session_state["chat_answer"])

    match_count = st.session_state.get("chat_match_count", 0)
    ai_count = st.session_state.get("chat_ai_record_count", 0)

    st.write(f"### Matching AJDs ({match_count})")

    if match_count > ai_count and ai_count > 0:
        st.caption(
            f"All {match_count} matching AJDs are shown below. "
            f"The AI summary used the {ai_count} highest-ranked matches "
            "to stay within context limits."
        )

    used_df = pd.DataFrame(st.session_state["chat_records"])

    display_columns = [
        "JD ID",
        "County",
        "State",
        "Determination Outcome",
        "Feature Type(s)",
        "Consultant",
        "Consultant Source",
        "Consultant Confidence",
        "PDF Link",
    ]

    available_columns = [
        column
        for column in display_columns
        if column in used_df.columns
    ]

    if not used_df.empty:
        st.dataframe(
            used_df[available_columns],
            width="stretch",
            hide_index=True,
            column_config={
                "PDF Link": st.column_config.LinkColumn("PDF Link")
            },
        )

    if st.button("Clear Answer", width="content"):
        st.session_state["chat_answer"] = ""
        st.session_state["chat_records"] = []
        st.session_state["chat_match_count"] = 0
        st.session_state["chat_ai_record_count"] = 0
        st.rerun()


# ------------------------------------------------------------
# Summary metrics
# ------------------------------------------------------------

st.divider()
st.subheader("Summary Metrics")

metric_columns = st.columns(9)

metric_columns[0].metric("AJDs", len(filtered))
metric_columns[1].metric("Counties", filtered["County"].nunique())
metric_columns[2].metric("States", filtered["State"].nunique())
metric_columns[3].metric("Features", len(visible_features))
metric_columns[4].metric(
    "Waters Present",
    len(filtered[filtered["Waters Present"] == "Yes"]),
)
metric_columns[5].metric(
    "Consultants",
    filtered["Consultant"]
    .replace("", pd.NA)
    .dropna()
    .nunique(),
)
metric_columns[6].metric(
    "Needs Review",
    len(filtered[filtered["Quality Flag"] != "Good"]),
)
metric_columns[7].metric(
    "With Coordinates",
    len(
        filtered[
            (filtered["Latitude"] != "")
            & (filtered["Longitude"] != "")
        ]
    ),
)
metric_columns[8].metric(
    "With Consultant",
    len(filtered[filtered["Consultant"] != ""]),
)


# ------------------------------------------------------------
# Map
# ------------------------------------------------------------

st.divider()
st.subheader("AJD Map")

map_df = filtered.copy()
map_df["lat"] = pd.to_numeric(
    map_df["Latitude"],
    errors="coerce",
)
map_df["lon"] = pd.to_numeric(
    map_df["Longitude"],
    errors="coerce",
)
map_df = map_df.dropna(subset=["lat", "lon"])

if not map_df.empty:
    center_lat = map_df["lat"].mean()
    center_lon = map_df["lon"].mean()

    ajd_map = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=7,
        control_scale=True,
        tiles=None,
    )

    folium.TileLayer(
        "OpenStreetMap",
        name="Street Map",
    ).add_to(ajd_map)

    folium.TileLayer(
        tiles=(
            "https://basemap.nationalmap.gov/arcgis/rest/services/"
            "USGSTopo/MapServer/tile/{z}/{y}/{x}"
        ),
        attr="USGS The National Map",
        name="USGS Topographic Map",
        overlay=False,
        control=True,
    ).add_to(ajd_map)

    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        attr="Esri",
        name="Aerial Imagery",
    ).add_to(ajd_map)

    folium.TileLayer(
        tiles=(
            "https://services.arcgisonline.com/ArcGIS/rest/services/"
            "Reference/World_Boundaries_and_Places/MapServer/"
            "tile/{z}/{y}/{x}"
        ),
        attr="Esri",
        name="Aerial Labels",
        overlay=True,
        control=True,
    ).add_to(ajd_map)

    marker_cluster = MarkerCluster(
        name="AJD Locations"
    ).add_to(ajd_map)

    for _, row in map_df.iterrows():
        color = marker_color(row["Determination Outcome"])

        popup_html = f"""
        <b>{row['JD ID']}</b><br>
        <b>County:</b> {row['County']}<br>
        <b>State:</b> {row['State']}<br>
        <b>Outcome:</b> {row['Determination Outcome']}<br>
        <b>Prepared By:</b> {row['Prepared By']}<br>
        <b>Approved By:</b> {row['Approved By']}<br>
        <b>Consultant:</b> {row['Consultant']}<br>
        <b>Feature Types:</b> {row['Feature Type(s)']}<br>
        <b>Quality:</b> {row['Quality Flag']}<br>
        <b>Summary:</b> {row['AI Summary']}<br>
        <a href="{row['PDF Link']}" target="_blank">Open PDF</a>
        """

        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=7,
            popup=folium.Popup(
                popup_html,
                max_width=500,
            ),
            tooltip=row["JD ID"],
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
        ).add_to(marker_cluster)

    folium.LayerControl().add_to(ajd_map)

    st_folium(
        ajd_map,
        height=650,
        width="stretch",
        returned_objects=[],
    )
else:
    st.info(
        "No valid coordinates are available for the current filters."
    )


# ------------------------------------------------------------
# Tabs
# ------------------------------------------------------------

st.divider()

tabs = st.tabs(
    [
        "Overview",
        "AJD Table",
        "Feature Explorer",
        "Detail View",
        "Downloads",
        "About / Methodology",
    ]
)


with tabs[0]:
    st.subheader("Analytics")

    analytics_left, analytics_right = st.columns(2)

    with analytics_left:
        st.write("### AJDs by County / Parish")

        county_counts = (
            filtered["County"]
            .replace("", pd.NA)
            .dropna()
            .value_counts()
            .reset_index()
        )
        county_counts.columns = ["County / Parish", "Count"]

        if not county_counts.empty:
            st.bar_chart(
                county_counts.set_index("County / Parish")
            )
        else:
            st.info("No county data available.")

    with analytics_right:
        st.write("### Determination Outcomes")

        outcome_counts = (
            filtered["Determination Outcome"]
            .replace("", pd.NA)
            .dropna()
            .value_counts()
            .reset_index()
        )
        outcome_counts.columns = ["Outcome", "Count"]

        if not outcome_counts.empty:
            st.bar_chart(outcome_counts.set_index("Outcome"))
        else:
            st.info("No determination outcome data available.")

    consultant_col, preparer_col, approver_col = st.columns(3)

    with consultant_col:
        st.write("### Top Consultants")

        consultant_counts = (
            filtered["Consultant"]
            .replace("", pd.NA)
            .dropna()
            .value_counts()
            .head(10)
            .reset_index()
        )
        consultant_counts.columns = ["Consultant", "Count"]

        if not consultant_counts.empty:
            st.bar_chart(
                consultant_counts.set_index("Consultant")
            )
        else:
            st.info("No consultant data available.")

    with preparer_col:
        st.write("### Top Preparers")

        preparer_counts = (
            filtered["Prepared By"]
            .replace("", pd.NA)
            .dropna()
            .value_counts()
            .head(10)
            .reset_index()
        )
        preparer_counts.columns = ["Prepared By", "Count"]

        if not preparer_counts.empty:
            st.bar_chart(
                preparer_counts.set_index("Prepared By")
            )
        else:
            st.info("No preparer data available.")

    with approver_col:
        st.write("### Top Approvers")

        approver_counts = (
            filtered["Approved By"]
            .replace("", pd.NA)
            .dropna()
            .value_counts()
            .head(10)
            .reset_index()
        )
        approver_counts.columns = ["Approved By", "Count"]

        if not approver_counts.empty:
            st.bar_chart(
                approver_counts.set_index("Approved By")
            )
        else:
            st.info("No approver data available.")

    st.write("### Feature Type Frequency")

    if not visible_features.empty:
        feature_counts = (
            visible_features["Feature Type"]
            .replace("", pd.NA)
            .dropna()
            .value_counts()
            .head(20)
        )

        if not feature_counts.empty:
            st.bar_chart(feature_counts)
        else:
            st.info("No feature type data available.")
    else:
        st.info("No feature data available.")


with tabs[1]:
    st.subheader("AJD Table")

    table_columns = [
        "JD ID",
        "County",
        "State",
        "Year",
        "JD Date",
        "USACE District",
        "Prepared By",
        "Approved By",
        "Consultant",
        "Consultant Source",
        "Consultant Confidence",
        "Feature Type(s)",
        "Feature Status(es)",
        "Waters Present",
        "Determination Outcome",
        "Primary Determination Basis",
        "TNW / Receiving Water",
        "Quality Flag",
        "PDF Link",
    ]

    available_table_columns = [
        column
        for column in table_columns
        if column in filtered.columns
    ]

    st.dataframe(
        filtered[available_table_columns],
        width="stretch",
        hide_index=True,
        column_config={
            "PDF Link": st.column_config.LinkColumn("PDF Link")
        },
    )


with tabs[2]:
    st.subheader("Feature Explorer")

    if not visible_features.empty:
        st.dataframe(
            visible_features,
            width="stretch",
            hide_index=True,
            column_config={
                "PDF Link": st.column_config.LinkColumn("PDF Link")
            },
        )
    else:
        st.info(
            "No extracted features were found for the current filters."
        )


with tabs[3]:
    st.subheader("Detail View")

    if filtered.empty:
        st.warning(
            "No records match the selected filters."
        )
    else:
        selected_jd = st.selectbox(
            "Select a JD ID to view details",
            filtered["JD ID"].tolist(),
        )

        selected_row = filtered[
            filtered["JD ID"] == selected_jd
        ].iloc[0]

        st.header(selected_row["JD ID"])
        st.caption(
            f"{selected_row['County']}, "
            f"{selected_row['State']} | "
            f"{selected_row['JD Date']}"
        )

        outcome_text = (
            f"Determination Outcome: "
            f"{selected_row['Determination Outcome']}"
        )

        if selected_row["Waters Present"] == "Yes":
            st.success(outcome_text)
        elif selected_row["Waters Present"] == "No":
            st.warning(outcome_text)
        else:
            st.info(outcome_text)

        detail_col_1, detail_col_2, detail_col_3 = st.columns(3)

        with detail_col_1:
            st.write(
                "**County / Parish:**",
                selected_row["County"],
            )
            st.write(
                "**State:**",
                selected_row["State"],
            )
            st.write(
                "**JD Date:**",
                selected_row["JD Date"],
            )
            st.write(
                "**USACE District:**",
                selected_row["USACE District"],
            )

        with detail_col_2:
            st.write(
                "**Prepared By:**",
                selected_row["Prepared By"],
            )
            st.write(
                "**Approved By:**",
                selected_row["Approved By"],
            )
            st.write(
                "**JD Type:**",
                selected_row["JD Type"],
            )
            st.write(
                "**Quality Flag:**",
                selected_row["Quality Flag"],
            )

        with detail_col_3:
            st.write(
                "**Primary Basis:**",
                selected_row["Primary Determination Basis"],
            )
            st.write(
                "**TNW / Receiving Water:**",
                selected_row["TNW / Receiving Water"],
            )
            st.write(
                "**Feature Type(s):**",
                selected_row["Feature Type(s)"],
            )
            st.write(
                "**Waters Present:**",
                selected_row["Waters Present"],
            )

        optional_metadata = []

        if selected_row["Project Name"]:
            optional_metadata.append(
                ("Project Name", selected_row["Project Name"])
            )

        if selected_row["Applicant"]:
            optional_metadata.append(
                ("Applicant", selected_row["Applicant"])
            )

        if selected_row["Consultant"]:
            optional_metadata.append(
                ("Consultant", selected_row["Consultant"])
            )

        if selected_row.get("Consultant Source", ""):
            optional_metadata.append(
                ("Consultant Source", selected_row["Consultant Source"])
            )

        if selected_row.get("Consultant Page", ""):
            optional_metadata.append(
                ("Consultant Page", selected_row["Consultant Page"])
            )

        if selected_row.get("Consultant Confidence", ""):
            optional_metadata.append(
                (
                    "Consultant Confidence",
                    selected_row["Consultant Confidence"].title(),
                )
            )

        if selected_row.get("Consultant Evidence", ""):
            optional_metadata.append(
                ("Consultant Evidence", selected_row["Consultant Evidence"])
            )

        if optional_metadata:
            with st.expander("Additional Record Details"):
                for label, value in optional_metadata:
                    st.write(f"**{label}:**", value)

        st.write("### AI Summary")
        st.write(selected_row["AI Summary"])

        st.write("### Jurisdictional Reasoning")
        st.write(selected_row["Jurisdictional Reasoning"])

        selected_features = (
            visible_features[
                visible_features["JD ID"] == selected_jd
            ]
            if not visible_features.empty
            else pd.DataFrame()
        )

        if not selected_features.empty:
            st.write("### Extracted Features")

            st.dataframe(
                selected_features,
                width="stretch",
                hide_index=True,
                column_config={
                    "PDF Link": st.column_config.LinkColumn(
                        "PDF Link"
                    )
                },
            )

        if selected_row["PDF Link"]:
            st.link_button(
                "Open Original PDF",
                selected_row["PDF Link"],
            )

        with st.expander("Full OCR Text"):
            st.write(get_ocr_text(selected_row["JD ID"]))

        with st.expander("Full Database Record"):
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                full_record = conn.execute(
                    "SELECT * FROM ajds WHERE swg_number = ?",
                    (selected_row["JD ID"],),
                ).fetchone()
            st.json(dict(full_record) if full_record else {})


with tabs[4]:
    st.subheader("Downloads")

    st.write(
        "Download the currently filtered results."
    )

    csv_ajds = (
        filtered
        .drop(columns=["Full Record", "Full OCR Text"], errors="ignore")
        .to_csv(index=False)
        .encode("utf-8")
    )

    st.download_button(
        label="Download Filtered AJDs as CSV",
        data=csv_ajds,
        file_name="filtered_ajds.csv",
        mime="text/csv",
    )

    if not visible_features.empty:
        csv_features = (
            visible_features
            .to_csv(index=False)
            .encode("utf-8")
        )

        st.download_button(
            label="Download Filtered Features as CSV",
            data=csv_features,
            file_name="filtered_features.csv",
            mime="text/csv",
        )

    excel_file = make_excel_download(
        filtered.drop(
            columns=["Full Record", "Full OCR Text"],
            errors="ignore",
        ),
        visible_features,
    )

    st.download_button(
        label="Download Filtered AJDs and Features as Excel",
        data=excel_file,
        file_name="filtered_ajd_database.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )

    st.divider()
    st.write("### Database Update Workflow")

    st.info(
        "Update the database locally, test the app, and then push "
        "the updated SQLite database to GitHub. Streamlit Community "
        "Cloud should redeploy automatically."
    )

    st.code(
        """# 1. Run the updater
py update_ajds.py

# 2. Confirm no AJDs remain missing
py update_ajds.py

# 3. Test locally
py -m streamlit run app.py

# 4. In GitHub Desktop
#    - Commit data/ajds.sqlite
#    - Push origin
""",
        language="powershell",
    )


with tabs[5]:
    st.header("About / Methodology")

    st.markdown(
        """
### Data Source

AJD Explorer is based on publicly available USACE Approved Jurisdictional Determination documents. Current coverage includes records from the Galveston and Fort Worth Districts, and the district-neutral name allows the collection to expand over time.

### Processing Method

Source PDFs were downloaded, OCR processed, and parsed using AI-assisted extraction. Extracted fields include JD ID, county, state, project name, applicant, consultant, USACE reviewer information, feature types, determination outcome, jurisdictional reasoning, and PDF links. The question tool searches chunks drawn from the complete stored OCR text of each PDF in the selected dataset and supplies the highest-ranked passages to the answer model.

### Limitations

OCR and AI-assisted extraction may produce errors or omissions. The dashboard should be used as a research aid, not as the official jurisdictional determination record.

### Recommended Use

Use this tool for regulatory research, due diligence, trend analysis, and locating similar AJDs. Always open and verify the original PDF before relying on extracted information.

### Quality Flags

Records marked as needing review may have missing coordinates, missing county information, missing PDF links, missing AI summaries, missing jurisdictional reasoning, or limited extracted feature information.
"""
    )
