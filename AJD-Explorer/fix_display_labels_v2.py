"""Fix the missing consultant label and merge Galveston district labels."""

import ast
from pathlib import Path


folder = Path(__file__).resolve().parent
helper_path = folder / "display_names.py"
app_path = folder / "app.py"

if not helper_path.exists() or not app_path.exists():
    raise SystemExit("Place this file beside app.py and display_names.py first.")

helper = helper_path.read_text(encoding="utf-8")
app = app_path.read_text(encoding="utf-8")

consultant_anchor = (
    '    if name.casefold() in {"", "none", "null", "nan", "n/a", "na"}:\n'
    '        return ""\n'
)
consultant_rule = (
    '    if re.match(\n'
    '        r"^(?:wetland|aquatic resources?) delineation report\\s+(?:received|dated)\\b",\n'
    '        name,\n'
    '        flags=re.IGNORECASE,\n'
    '    ):\n'
    '        return ""\n'
)
district_rule = '''\n\ndef normalize_district_name(value):
    """Show abbreviated and full district names as the same option."""
    name = re.sub(r"\\s+", " ", str(value or "")).strip()
    key = re.sub(r"[^a-z]+", " ", name.casefold()).strip()
    if key in {"galveston", "galveston district", "usace galveston district"}:
        return "Galveston District"
    if key in {"fort worth", "fort worth district", "usace fort worth district"}:
        return "Fort Worth District"
    return name
'''

if consultant_rule not in helper:
    if consultant_anchor not in helper:
        raise SystemExit("The existing display_names.py has an unexpected layout. No files changed.")
    helper = helper.replace(consultant_anchor, consultant_anchor + consultant_rule, 1)

if "def normalize_district_name(" not in helper:
    helper += district_rule

import_anchor = "    normalize_consultant_name,\n"
import_rule = "    normalize_district_name,\n"
if import_rule not in app:
    if "from display_names import (" not in app or import_anchor not in app:
        raise SystemExit("The existing app.py has an unexpected import. No files changed.")
    app = app.replace(import_anchor, import_anchor + import_rule, 1)

display_anchor = (
    '        ajd_df["Consultant"] = '
    'consolidate_consultant_names(ajd_df["Consultant"])\n'
)
display_rule = (
    '        ajd_df["USACE District"] = '
    'ajd_df["USACE District"].map(normalize_district_name)\n'
)
if display_rule not in app:
    if display_anchor not in app:
        raise SystemExit("The existing app.py has an unexpected data block. No files changed.")
    app = app.replace(display_anchor, display_anchor + display_rule, 1)

ast.parse(helper, str(helper_path))
ast.parse(app, str(app_path))
helper_path.write_text(helper, encoding="utf-8")
app_path.write_text(app, encoding="utf-8")

from display_names import normalize_consultant_name, normalize_district_name

assert normalize_consultant_name("Wetland delineation report received 27 March 2023") == ""
assert normalize_district_name("Galveston") == "Galveston District"
print("Fixed missing consultant and unified Galveston District labels.")
