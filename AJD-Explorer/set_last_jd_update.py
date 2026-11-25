"""Show the latest stored JD update time instead of the SQLite file time."""

import ast
from pathlib import Path


path = Path(__file__).resolve().parent / "app.py"
if not path.exists():
    raise SystemExit("Place this file beside app.py in the AJD-Explorer folder.")

app = path.read_text(encoding="utf-8")
if "def get_last_jd_update():" in app:
    print("JD update date is already installed.")
    raise SystemExit(0)

old_header = '''if os.path.exists(DB_PATH):
    db_modified = datetime.fromtimestamp(os.path.getmtime(DB_PATH))
    st.caption(
        f"Database last updated: "
        f"{db_modified.strftime('%Y-%m-%d %H:%M')}"
    )
'''
new_header = '''last_jd_update = get_last_jd_update()
if last_jd_update:
    st.caption(
        f"JDs last updated: "
        f"{last_jd_update.strftime('%Y-%m-%d %H:%M')}"
    )
'''
old_metric = '''if os.path.exists(DB_PATH):
    db_modified = datetime.fromtimestamp(os.path.getmtime(DB_PATH))
    coverage_columns[3].metric(
        "Last Updated",
        db_modified.strftime("%b %d, %Y"),
    )
'''
new_metric = '''if last_jd_update:
    coverage_columns[3].metric(
        "Last Updated",
        last_jd_update.strftime("%b %d, %Y"),
    )
'''
helper = '''def get_last_jd_update():
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


'''

for old in (old_header, old_metric):
    if app.count(old) != 1:
        raise SystemExit("Could not find both old date displays. No changes made.")
if app.count('DB_PATH = "data/ajds.sqlite"\n\n\n') != 1:
    raise SystemExit("Could not find the app database setting. No changes made.")

app = app.replace('DB_PATH = "data/ajds.sqlite"\n\n\n',
                  'DB_PATH = "data/ajds.sqlite"\n\n\n' + helper, 1)
app = app.replace(old_header, new_header, 1)
app = app.replace(old_metric, new_metric, 1)
ast.parse(app, str(path))
path.write_text(app, encoding="utf-8")
print("Last Updated now shows the most recent JD update stored in SQLite.")
