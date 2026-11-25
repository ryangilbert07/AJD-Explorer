"""Run once in the AJD-Explorer folder to group consultant and feature labels."""

import ast
from pathlib import Path


folder = Path(__file__).resolve().parent
app_path = folder / "app.py"
helper_path = folder / "display_names.py"

if not app_path.exists() or not helper_path.exists():
    raise SystemExit("Put this script and display_names.py beside app.py, then rerun.")

changes = (
    (
        "from streamlit_folium import st_folium\n",
        "from streamlit_folium import st_folium\n"
        "from display_names import (\n"
        "    consolidate_consultant_names,\n"
        "    normalize_consultant_name,\n"
        "    normalize_feature_type,\n"
        ")\n",
    ),
    (
        '                normalize_text(feature.get("feature_type", ""))\n',
        '                normalize_feature_type(feature.get("feature_type", ""))\n',
    ),
    (
        '        consultant = normalize_consultant(row.get("consultant", ""))\n',
        '        consultant = normalize_consultant_name(row.get("consultant", ""))\n',
    ),
    (
        '                    "Feature Type": normalize_text(\n',
        '                    "Feature Type": normalize_feature_type(\n',
    ),
    (
        '    return pd.DataFrame(records), pd.DataFrame(feature_records)\n',
        '    ajd_df = pd.DataFrame(records)\n'
        '    if not ajd_df.empty:\n'
        '        ajd_df["Consultant"] = consolidate_consultant_names(ajd_df["Consultant"])\n'
        '    return ajd_df, pd.DataFrame(feature_records)\n',
    ),
    (
        '                feature in str(value)\n',
        '                feature in [part.strip() for part in str(value).split(",")]\n',
    ),
)

original = app_path.read_text(encoding="utf-8")
updated = original
for before, after in changes:
    if after in updated:
        continue
    if updated.count(before) != 1:
        raise SystemExit(f"Could not identify one safe replacement for: {before.strip()}")
    updated = updated.replace(before, after, 1)

ast.parse(helper_path.read_text(encoding="utf-8"), str(helper_path))
ast.parse(updated, str(app_path))
if updated == original:
    print("Filters already updated. No changes needed.")
else:
    app_path.write_text(updated, encoding="utf-8")
    print("Updated app.py. Consultant filter and feature filter/chart now share labels.")
