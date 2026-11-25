"""One-time VS Code installer for data-driven AJD feature labels."""

import ast
from pathlib import Path


root = Path(__file__).resolve().parent
helper_path = root / "display_names.py"
app_path = root / "app.py"
if not helper_path.exists() or not app_path.exists():
    raise SystemExit("Place this script beside app.py and display_names.py.")

helper = helper_path.read_text(encoding="utf-8")
app = app_path.read_text(encoding="utf-8")
if "def normalize_feature_type(" not in helper:
    raise SystemExit("display_names.py lacks normalize_feature_type; no files changed.")

marker = "# BEGIN DATA-DRIVEN FEATURE LABELS V4"
feature_code = '''
# BEGIN DATA-DRIVEN FEATURE LABELS V4
from collections import Counter as _FeatureCounter
from difflib import SequenceMatcher as _FeatureMatcher

_original_feature_normalizer = normalize_feature_type


def normalize_feature_type(value):
    """Recognize any PEM/PFO/PSS subtype with or without 'wetland'."""
    name = str(value or "").strip()
    code = re.fullmatch(
        r"(p(?:em|fo|ss)\\d*[a-z]?)(?:\\s+wetland)?",
        name,
        flags=re.IGNORECASE,
    )
    if code:
        return f"{code.group(1).upper()} Wetland"
    return _original_feature_normalizer(value)


def _feature_names_match(first, second):
    a = re.sub(r"[^a-z0-9]+", " ", first.casefold()).strip()
    b = re.sub(r"[^a-z0-9]+", " ", second.casefold()).strip()
    if not a or not b:
        return False
    if a == b:
        return True

    # A one-character difference in a feature code or numbered ID can mean
    # a separate feature; keep those separate.
    codes_a = re.findall(r"\\bp(?:em|fo|ss)\\d+[a-z]?\\b", a)
    codes_b = re.findall(r"\\bp(?:em|fo|ss)\\d+[a-z]?\\b", b)
    if codes_a != codes_b or re.findall(r"\\d+", a) != re.findall(r"\\d+", b):
        return False

    kinds = ("wetland", "ditch", "pond", "stream", "swale", "tributary",
             "channel", "canal", "resource", "water")
    if {word for word in a.split() if word in kinds} != {
        word for word in b.split() if word in kinds
    }:
        return False

    qualifiers = ("ephemeral", "intermittent", "perennial", "upland",
                  "forested", "emergent", "scrub", "shrub", "jurisdictional",
                  "non", "roadside", "stock", "drainage")
    if {word for word in a.split() if word in qualifiers} != {
        word for word in b.split() if word in qualifiers
    }:
        return False
    return _FeatureMatcher(None, a, b).ratio() >= 0.90


def consolidate_feature_type_names(values):
    """Group labels found in the current database, including new spellings."""
    names = [normalize_feature_type(value) for value in values]
    counts = _FeatureCounter(name for name in names if name)
    labels = []
    chosen = {}
    for name in sorted(
        counts,
        key=lambda item: (-counts[item],
                          -sum(part[:1].isupper() for part in item.split()),
                          item.casefold()),
    ):
        match = next(
            (label for label in labels if _feature_names_match(name, label)),
            None,
        )
        if match is None:
            match = name
            labels.append(name)
        chosen[name] = match
    return [chosen.get(name, "") for name in names]
# END DATA-DRIVEN FEATURE LABELS V4
'''
if marker not in helper:
    helper = helper.rstrip() + "\n\n" + feature_code

def replace_once(before, after):
    global app
    if after in app:
        return
    if app.count(before) != 1:
        raise SystemExit(f"Cannot safely find app.py line: {before.strip()}")
    app = app.replace(before, after, 1)


if "consolidate_feature_type_names" not in app:
    if "from display_names import (" in app:
        replace_once(
            "from display_names import (\n",
            "from display_names import (\n    consolidate_feature_type_names,\n",
        )
    else:
        replace_once(
            "from streamlit_folium import st_folium\n",
            "from streamlit_folium import st_folium\n"
            "from display_names import consolidate_feature_type_names, normalize_feature_type\n",
        )

if "normalize_feature_type" not in app:
    raise SystemExit("app.py needs to import normalize_feature_type; no files changed.")

replace_once(
    '                normalize_text(feature.get("feature_type", ""))\n',
    '                normalize_feature_type(feature.get("feature_type", ""))\n',
)
replace_once(
    '                    "Feature Type": normalize_text(\n',
    '                    "Feature Type": normalize_feature_type(\n',
)
replace_once(
    '                feature in str(value)\n',
    '                feature in [part.strip() for part in str(value).split(",")]\n',
)

data_block = '''    feature_labels = consolidate_feature_type_names(
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

'''
if "feature_types_by_jd" not in app:
    if "    ajd_df = pd.DataFrame(records)\n" in app:
        app = app.replace("    ajd_df = pd.DataFrame(records)\n",
                          data_block + "    ajd_df = pd.DataFrame(records)\n", 1)
    elif "    return pd.DataFrame(records), pd.DataFrame(feature_records)\n" in app:
        app = app.replace("    return pd.DataFrame(records), pd.DataFrame(feature_records)\n",
                          data_block + "    return pd.DataFrame(records), pd.DataFrame(feature_records)\n", 1)
    else:
        raise SystemExit("Cannot safely find data return in app.py; no files changed.")

ast.parse(helper, str(helper_path))
ast.parse(app, str(app_path))
helper_path.write_text(helper, encoding="utf-8")
app_path.write_text(app, encoding="utf-8")

from display_names import consolidate_feature_type_names

examples = ("PEM", "PEM Wetland", "PEM1A", "PEM1A wetland",
            "Aquatic Resource", "Aquatic resource", "Wetland", "wetland",
            "PEM1A Open Water")
for before, after in zip(examples, consolidate_feature_type_names(examples)):
    print(f"{before} -> {after}")
print("Updated local feature filter and chart. Restart Streamlit to view changes.")
