#!/usr/bin/env python3
"""
SkiDash base-depth fetcher. Runs in GitHub Actions and writes snow.json.

Source: each resort's OnTheSnow snow-report page. OnTheSnow is a Next.js site,
so the report data is embedded in a <script id="__NEXT_DATA__"> JSON blob. We
parse that blob rather than scraping rendered text, then deep-search it for the
report fields (base/mid/summit depth, surface, status, last-updated).

Fail-safe by design: a value is emitted ONLY when found with a known unit.
Anything uncertain is left null and the page shows "not reported" - it never
displays a guessed number or a wrong unit. Standard library only.

NOTE (2026): unverified against live data until a resort opens and reports a
depth. Built defensively so the worst case is a missing number, never a wrong
one. Confirm the first real reading in-season, then lock the field mapping.
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (SkiDash; personal ski dashboard) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")

# name -> OnTheSnow skireport URL. WinSport is included; its depth is usually
# unreported (man-made snow), which the fail-safe handles cleanly.
RESORTS = {
    "Nakiska":     "https://www.onthesnow.com/alberta/nakiska-ski-area/skireport",
    "Norquay":     "https://www.onthesnow.com/alberta/ski-banff-norquay/skireport",
    "Lake Louise": "https://www.onthesnow.com/alberta/lake-louise/skireport",
    "Sunshine":    "https://www.onthesnow.com/alberta/banff-sunshine/skireport",
    "Fernie":      "https://www.onthesnow.com/british-columbia/fernie-alpine/skireport",
    "Panorama":    "https://www.onthesnow.com/british-columbia/panorama-mountain/skireport",
    "Marmot Basin": "https://www.onthesnow.com/alberta/marmot-basin/skireport",
    "WinSport":    "https://www.onthesnow.com/alberta/winsport/skireport",
}

NEXT_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

# Which depth we're after -> regex the JSON key must match.
DEPTH_RE = {
    "base":   re.compile(r"base.*depth|depth.*base|^base$", re.I),
    "mid":    re.compile(r"mid.*depth|depth.*mid|^mid$", re.I),
    "summit": re.compile(r"summit.*depth|depth.*summit|top.*depth|^summit$", re.I),
}
SURFACE_RE = re.compile(r"surface", re.I)
STATUS_RE = re.compile(r"(opening|resort)?status|openclosed", re.I)
UPDATED_RE = re.compile(r"(report|snow).*updated|last.?updated|updateddt|reportdate", re.I)
UNIT_SIB_RE = re.compile(r"unit|measure", re.I)
CM_HINT = re.compile(r"\bcm\b|centimet|metric", re.I)
IN_HINT = re.compile(r"inch|imperial|\bin\b|\"|''", re.I)


def fetch_html(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def walk(node, path=()):
    """Yield (parent_dict, key, value, path) for every dict entry in the tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield (node, str(k), v, path + (str(k),))
            yield from walk(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, path + (str(i),))


def as_number(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v)
        if m:
            return float(m.group())
    return None


def norm(text):
    """Split camelCase/underscores so 'baseDepthCm' -> 'base depth cm' for word-boundary tests."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(text))
    return re.sub(r"[_\-]+", " ", s).lower()


def unit_from(text):
    t = norm(text)
    if CM_HINT.search(t):
        return "cm"
    if IN_HINT.search(t):
        return "in"
    return None


def sibling_unit(parent, key):
    """A sibling field like baseDepthUnit / unit / measurementUnit naming the unit."""
    for k, v in parent.items():
        if k == key or not isinstance(v, str):
            continue
        if UNIT_SIB_RE.search(k):
            u = unit_from(v)
            if u:
                return u
    return None


def to_cm(num, unit):
    return round(num) if unit == "cm" else round(num * 2.54)


def find_depth(data, which, page_unit):
    """
    Return (value_cm, unit_source) or (None, None). Accepts a value only when the
    unit is determinable, in priority order: value object > key name > sibling
    field > page-level default. Bare numbers with no unit signal are rejected.
    """
    rx = DEPTH_RE[which]
    fallback = None  # (num, "page") if we ever find a bare number
    for parent, key, val, path in walk(data):
        if not rx.search(key):
            continue
        if isinstance(val, dict):
            cm = as_number(val.get("cm") or val.get("centimeters") or val.get("metric"))
            inch = as_number(val.get("in") or val.get("inches") or val.get("imperial"))
            if cm is not None:
                return (round(cm), "object")
            if inch is not None:
                return (to_cm(inch, "in"), "object")
            continue
        num = as_number(val)
        if num is None:
            continue
        u = unit_from(key) or sibling_unit(parent, key)
        if u:
            return (to_cm(num, u), "key/sibling")
        if fallback is None and page_unit:
            fallback = (to_cm(num, page_unit), "page-default")
    return fallback if fallback else (None, None)


def find_page_unit(data):
    for _, key, val, _ in walk(data):
        if re.search(r"measurementsystem|unitsystem|defaultunit|\bunits?\b", key, re.I) \
                and isinstance(val, str):
            u = unit_from(val)
            if u:
                return u
    return None


def first_value(data, rx):
    for _, key, val, _ in walk(data):
        if rx.search(key) and isinstance(val, (str, int, float)) and not isinstance(val, bool):
            s = str(val).strip()
            if s:
                return s
    return None


def parse_report(html):
    m = NEXT_RE.search(html)
    if not m:
        raise ValueError("no __NEXT_DATA__ block on page")
    data = json.loads(m.group(1))

    page_unit = find_page_unit(data)
    base_cm, base_src = find_depth(data, "base", page_unit)
    mid_cm, _ = find_depth(data, "mid", page_unit)
    summit_cm, _ = find_depth(data, "summit", page_unit)

    return {
        "base_cm": base_cm,            # accepted only with a known unit, else None
        "mid_cm": mid_cm,
        "summit_cm": summit_cm,
        "unit_source": base_src,       # object | key/sibling | page-default | None
        "surface": first_value(data, SURFACE_RE),
        "status": first_value(data, STATUS_RE),
        "updated": first_value(data, UPDATED_RE),
    }


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "snow.json"
    resorts = {}
    for name, url in RESORTS.items():
        try:
            rep = parse_report(fetch_html(url))
            rep["error"] = None
        except Exception as e:  # never crash the build
            rep = {"base_cm": None, "mid_cm": None, "summit_cm": None,
                   "unit_ok": False, "surface": None, "status": None,
                   "updated": None, "error": str(e)[:160]}
        rep["source"] = url
        resorts[name] = rep

    doc = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resorts": resorts,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    got = sum(1 for r in resorts.values() if r["base_cm"] is not None)
    print(f"wrote {out_path}: base depth for {got}/{len(resorts)} resorts")


if __name__ == "__main__":
    main()
