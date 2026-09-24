"""Display names for the AJD consultant filter and dashboard."""

import re
from collections import Counter
from difflib import SequenceMatcher


def _name_key(value):
    # For comparison only: ignore punctuation, spacing, and legal suffixes.
    key = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    return re.sub(r"(?:\s+(?:inc|incorporated|llc|pllc))+$", "", key)


# These aliases refer to verified spelling or legal-name variants in the AJD
# database. Keep similarly named but distinct companies out of this mapping.
ALIASES = {
    "a m wetland consulting services": "A&M Wetland Consulting Services",
    "berg oliver associates": "Berg & Oliver Associates",
    "cypress environmental consulting": "Cypress Environmental Consulting",
    "horizon environmental services": "Horizon Environmental Services",
    "integrated environmental solutions": "Integrated Environmental Solutions",
    "nexus renewables": "Nexus Renewables",
    "parnell engineering": "Parnell Engineering",
    "triton environmental solutions": "Triton Environmental Solutions",
    "stantec": "Stantec",
    "stantec consulting services": "Stantec",
    "kimley horn": "Kimley-Horn",
    "kimley horn associates": "Kimley-Horn",
    "marcia appia": "Marcia Appia Engineers, LLC",
    "marcia appia engineers": "Marcia Appia Engineers, LLC",
    "marcia appia consultant engineers": "Marcia Appia Engineers, LLC",
    "marcia appia civil infrastructure consultant engineers": "Marcia Appia Engineers, LLC",
    "raba kistner": "Raba Kistner",
    "raba kistner a kiwa company": "Raba Kistner",
    "swca environmental consultants": "SWCA Environmental Consultants",
    "freese and nichols": "Freese and Nichols",
    "freese nichols": "Freese and Nichols",
    "dewberry engineers": "Dewberry Engineers, Inc.",
    "benchmark ecological services": "Benchmark Ecological Services Inc.",
    "harris county flood control district": "Harris County Flood Control District",
    "harris county flood control district hcfcd": "Harris County Flood Control District",
    "power engineers": "Power Engineers",
}


def normalize_consultant_name(value):
    """Return one filter label for known aliases; preserve unknown names."""
    if value is None:
        return ""
    name = re.sub(r"\s+", " ", str(value)).strip(" ,.;")
    if name.casefold() in {"", "none", "null", "nan", "n/a", "na"}:
        return ""
    if re.match(
        r"^(?:wetland|aquatic resources?) delineation report\s+(?:received|dated)\b",
        name,
        flags=re.IGNORECASE,
    ):
        return ""

    key = _name_key(name)
    if key in ALIASES:
        return ALIASES[key]

    # Other names get conservative formatting cleanup. Do not guess which
    # company they refer to from fuzzy spelling alone.
    return name


def _close_names(first, second):
    """Match a minor spelling difference, with a shared company-name token."""
    a, b = _name_key(first), _name_key(second)
    if not a or not b:
        return False
    if a == b:
        return True

    words_a, words_b = a.split(), b.split()
    generic = {
        "environmental", "environment", "consultant", "consultants",
        "consulting", "engineer", "engineers", "engineering", "service",
        "services", "associates", "group", "company", "the", "and",
    }
    distinctive_a = {word for word in words_a if word not in generic}
    distinctive_b = {word for word in words_b if word not in generic}
    if not distinctive_a or not distinctive_b:
        return False

    # A shared distinctive word or a near-identical first word protects
    # different companies that happen to offer the same services.
    shared = distinctive_a & distinctive_b
    first_word_close = SequenceMatcher(
        None, words_a[0], words_b[0]
    ).ratio() >= 0.88
    if not shared and not first_word_close:
        return False
    if min(len(a), len(b)) / max(len(a), len(b)) < 0.78:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.87


def consolidate_consultant_names(values):
    """Give records with near-matching company names the same filter label."""
    normalized = [normalize_consultant_name(value) for value in values]
    counts = Counter(name for name in normalized if name)
    labels = []
    chosen = {}

    # Known canonical names win; otherwise the most common spelling wins.
    preferred = set(ALIASES.values())
    for name in sorted(
        counts,
        key=lambda item: (item not in preferred, -counts[item], item.casefold()),
    ):
        match = next((label for label in labels if _close_names(name, label)), None)
        if match is None:
            labels.append(name)
            chosen[name] = name
        else:
            chosen[name] = match

    return [chosen.get(name, "") for name in normalized]


def normalize_feature_type(value):
    """Standardize equivalent AJD feature labels for filters and charts."""
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    if name.casefold() in {"", "none", "null", "nan", "n/a", "na"}:
        return ""
    key = name.casefold().strip(" .")

    if key in {"pem", "pem wetland", "wetland (pem)",
               "palustrine emergent", "palustrine emergent wetland"}:
        return "PEM Wetland"
    if key in {"pfo", "pfo wetland", "wetland (pfo)",
               "palustrine forested", "palustrine forested wetland"}:
        return "PFO Wetland"
    if key in {"pss", "pss wetland", "wetland (pss)",
               "palustrine scrub-shrub", "palustrine scrub-shrub wetland"}:
        return "PSS Wetland"

    simple = {
        "wetland": "Wetland", "wetlands": "Wetland",
        "ditch": "Ditch", "ditches": "Ditch",
        "pond": "Pond", "ponds": "Pond",
        "stream": "Stream", "streams": "Stream",
        "swale": "Swale", "swales": "Swale",
        "tributary": "Tributary", "tributaries": "Tributary",
        "open water": "Open Water",
        "dry land": "Dry Land",
        "ephemeral stream": "Ephemeral Stream",
        "intermittent stream": "Intermittent Stream",
        "perennial stream": "Perennial Stream",
        "drainage ditch": "Drainage Ditch",
        "roadside ditch": "Roadside Ditch",
        "livestock pond": "Livestock Pond",
        "borrow pit": "Borrow Pit",
        "erosional feature": "Erosional Feature",
        "non-adjacent wetland": "Non-Adjacent Wetland",
    }
    return simple.get(key, name)


def normalize_district_name(value):
    """Show abbreviated and full district names as the same option."""
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    key = re.sub(r"[^a-z]+", " ", name.casefold()).strip()
    if key in {"galveston", "galveston district", "usace galveston district"}:
        return "Galveston District"
    if key in {"fort worth", "fort worth district", "usace fort worth district"}:
        return "Fort Worth District"
    return name


# BEGIN DATA-DRIVEN FEATURE LABELS V4
from collections import Counter as _FeatureCounter
from difflib import SequenceMatcher as _FeatureMatcher

_original_feature_normalizer = normalize_feature_type


def normalize_feature_type(value):
    """Recognize any PEM/PFO/PSS subtype with or without 'wetland'."""
    name = str(value or "").strip()
    code = re.fullmatch(
        r"(p(?:em|fo|ss)\d*[a-z]?)(?:\s+wetland)?",
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
    codes_a = re.findall(r"\bp(?:em|fo|ss)\d+[a-z]?\b", a)
    codes_b = re.findall(r"\bp(?:em|fo|ss)\d+[a-z]?\b", b)
    if codes_a != codes_b or re.findall(r"\d+", a) != re.findall(r"\d+", b):
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
