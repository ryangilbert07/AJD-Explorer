"""Keep full PDF OCR out of the cached dashboard while preserving OCR search.

Run once from the AJD-Explorer directory with the project Python interpreter.
Makes app.py.before_ocr_memory.py as a local backup, then edits app.py.
"""

import ast
from pathlib import Path


path = Path(__file__).resolve().parent / "app.py"
backup = path.with_name("app.py.before_ocr_memory.py")
if not path.exists():
    raise SystemExit("Place this script beside app.py in the AJD-Explorer folder.")

original = path.read_text(encoding="utf-8")
marker = "# OCR memory optimization: read one JD at a time"
if marker in original:
    print("OCR memory optimization is already installed.")
    raise SystemExit(0)

updated = original


def replace_once(before, after):
    global updated
    if updated.count(before) != 1:
        raise SystemExit(f"App layout differs near {before[:80]!r}; no changes made.")
    updated = updated.replace(before, after, 1)


replace_once(
    '''        raw_df = pd.read_sql_query(
            "SELECT * FROM ajds ORDER BY swg_number DESC",
            conn,
        )''',
    '''        # The main page needs structured fields, not every PDF's OCR.
        column_names = [
            column[1] for column in conn.execute("PRAGMA table_info(ajds)")
            if column[1] != "full_ocr_text"
        ]
        raw_df = pd.read_sql_query(
            f"SELECT {', '.join(column_names)} FROM ajds ORDER BY swg_number DESC",
            conn,
        )''',
)

replace_once(
    '                "Full OCR Text": normalize_text(row.get("full_ocr_text", "")),',
    '                "Full OCR Text": "",  # Retrieved from SQLite only when needed.',
)

replace_once(
    '''# ------------------------------------------------------------
# AI search helpers
# ------------------------------------------------------------
''',
    '''# ------------------------------------------------------------
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


''',
)

replace_once(
    '''    for _, row in source_df.iterrows():
        jd_candidates = []''',
    '''    for row in iter_ocr_rows(source_df):
        jd_candidates = []''',
)

replace_once(
    '''    scored_matches = []

    for _, row in source_df.iterrows():''',
    '''    scored_matches = []

    for row in iter_ocr_rows(source_df):''',
)

replace_once(
    '''                "row": row,
                "matched_terms": matched_terms,''',
    '''                "row": row.drop(labels=["Full OCR Text"]),
                "matched_terms": matched_terms,''',
)

replace_once(
    '''if keyword:
    keyword_lower = keyword.lower()
    filtered = filtered[
        filtered.apply(
            lambda row: keyword_lower in str(row).lower(),
            axis=1,
        )
    ]''',
    '''if keyword:
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
    ]''',
)

replace_once(
    '''        with st.expander("Full OCR Text"):
            st.write(selected_row["Full OCR Text"])

        with st.expander("Full Database Record"):
            st.json(selected_row["Full Record"])''',
    '''        with st.expander("Full OCR Text"):
            st.write(get_ocr_text(selected_row["JD ID"]))

        with st.expander("Full Database Record"):
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                full_record = conn.execute(
                    "SELECT * FROM ajds WHERE swg_number = ?",
                    (selected_row["JD ID"],),
                ).fetchone()
            st.json(dict(full_record) if full_record else {})''',
)

# The default bulk exports contain structured AJD fields. The selected-JD
# Full OCR Text and Full Database Record expanders still show original OCR.
replace_once(
    '''        .drop(columns=["Full Record"], errors="ignore")''',
    '''        .drop(columns=["Full Record", "Full OCR Text"], errors="ignore")''',
)
replace_once(
    '''            columns=["Full Record"],''',
    '''            columns=["Full Record", "Full OCR Text"],''',
)

ast.parse(updated, str(path))
if backup.exists():
    raise SystemExit(f"Backup already exists: {backup}; no changes made.")
backup.write_text(original, encoding="utf-8")
path.write_text(updated, encoding="utf-8")
print(f"Updated {path.name}; backup: {backup.name}")
print("Dashboard loads structured fields; keyword and question search still inspect PDF OCR.")
print("Bulk CSV/Excel exports now omit Full OCR Text to keep them small.")
