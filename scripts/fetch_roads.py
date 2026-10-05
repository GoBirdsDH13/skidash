#!/usr/bin/env python3
"""
SkiDash road fetcher. Runs in GitHub Actions every ~15 min and writes roads.json.

Sources
  Alberta 511 events           https://511.alberta.ca/api/v2/get/event        (needs AB511_KEY)
  Alberta 511 road conditions  https://511.alberta.ca/api/v3/get/winterroads  (needs AB511_KEY)
  DriveBC Open511 events       https://api.open511.gov.bc.ca/events           (no key)

An event or condition is attached to a route segment only when BOTH its highway
number matches the segment AND one of its points (lat/lon, secondary point, or
decoded polyline) falls inside the segment's box. Standard library only.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = "SkiDash/2 (personal ski dashboard; github pages)"
AB_KEY = os.environ.get("AB511_KEY", "").strip()

# ------------------------------------------------------------------ segments
# Boxes are lat (min,max), lon (min,max). Hwy 1 is split at the junctions each
# hill turns off at, so an event near Lake Louise never shows under Nakiska.
SEGMENTS = {
    "hwy1_east":      {"label": "Hwy 1 east to Calgary", "route": "1",
                       "lat": (51.03, 51.13), "lon": (-114.48, -114.19), "prov": ["AB"]},
    "hwy1_to40":      {"label": "Hwy 1, Hwy 22 to Hwy 40", "route": "1",
                       "lat": (51.00, 51.20), "lon": (-115.06, -114.46), "prov": ["AB"]},
    "hwy1_40_banff":  {"label": "Hwy 1, Hwy 40 to Banff", "route": "1",
                       "lat": (51.00, 51.22), "lon": (-115.61, -115.03), "prov": ["AB"]},
    "hwy1_banff_sun": {"label": "Hwy 1, Banff to Sunshine turnoff", "route": "1",
                       "lat": (51.10, 51.22), "lon": (-115.71, -115.58), "prov": ["AB"]},
    "hwy1_sun_castle": {"label": "Hwy 1, Sunshine turnoff to Castle Jct", "route": "1",
                       "lat": (51.10, 51.30), "lon": (-115.94, -115.69), "prov": ["AB"]},
    "hwy1_castle_ll": {"label": "Hwy 1, Castle Jct to Lake Louise", "route": "1",
                       "lat": (51.24, 51.46), "lon": (-116.20, -115.91), "prov": ["AB"]},
    "hwy40":          {"label": "Hwy 40 to Nakiska", "route": "40",
                       "lat": (50.92, 51.06), "lon": (-115.18, -115.00), "prov": ["AB"]},
    "hwy93s":         {"label": "Hwy 93 South, Kootenay to Radium", "route": "93",
                       "lat": (50.60, 51.28), "lon": (-116.12, -115.85), "prov": ["AB", "BC"]},
    "hwy95_inv":      {"label": "Hwy 95, Radium to Invermere", "route": "95",
                       "lat": (50.46, 50.64), "lon": (-116.10, -115.98), "prov": ["BC"]},
    "hwy22_south":    {"label": "Hwy 22 south to Lundbreck", "route": "22",
                       "lat": (49.56, 51.095), "lon": (-114.52, -113.95), "prov": ["AB"]},
    "hwy2_south":     {"label": "Hwy 2 south to Fort Macleod", "route": "2",
                       "lat": (49.69, 50.90), "lon": (-114.10, -113.35), "prov": ["AB"]},
    "hwy3_macleod":   {"label": "Hwy 3, Fort Macleod to Lundbreck", "route": "3",
                       "lat": (49.50, 49.80), "lon": (-114.18, -113.38), "prov": ["AB"]},
    "hwy3_crowsnest": {"label": "Hwy 3, Lundbreck to Fernie", "route": "3",
                       "lat": (49.40, 49.75), "lon": (-115.12, -114.15), "prov": ["AB", "BC"]},
    # Jasper / Marmot Basin via the Icefields Parkway (reuses Hwy 1 to Lake Louise)
    "hwy93n_icefields": {"label": "Hwy 93 North, Icefields Parkway to Jasper", "route": "93",
                       "lat": (51.40, 52.90), "lon": (-118.20, -115.95), "prov": ["AB"]},
    "hwy93a_marmot":  {"label": "Hwy 93A to Marmot Basin", "route": "93A",
                       "lat": (52.68, 52.92), "lon": (-118.15, -117.88), "prov": ["AB"]},
    # Grande Prairie / Nitehawk via QEII north and Hwy 43
    "hwy2_north":     {"label": "Hwy 2 north (QEII) to Hwy 43", "route": "2",
                       "lat": (51.00, 53.60), "lon": (-114.10, -113.30), "prov": ["AB"]},
    "hwy43_gp":       {"label": "Hwy 43 to Grande Prairie", "route": "43",
                       "lat": (53.50, 55.30), "lon": (-119.10, -113.90), "prov": ["AB"]},
    "hwy40_gp":       {"label": "Hwy 40 south to Nitehawk", "route": "40",
                       "lat": (54.90, 55.12), "lon": (-118.95, -118.55), "prov": ["AB"]},
}

# Each hill lists one or more route options; each option is an ordered list of segments.
HILL_ROUTES = {
    "Nakiska":     [{"name": "Hwy 1 and Hwy 40", "segments": ["hwy1_to40", "hwy40"]}],
    "Norquay":     [{"name": "Hwy 1", "segments": ["hwy1_to40", "hwy1_40_banff"]}],
    "Sunshine":    [{"name": "Hwy 1", "segments": ["hwy1_to40", "hwy1_40_banff", "hwy1_banff_sun"]}],
    "Lake Louise": [{"name": "Hwy 1", "segments": ["hwy1_to40", "hwy1_40_banff", "hwy1_banff_sun",
                                                   "hwy1_sun_castle", "hwy1_castle_ll"]}],
    "Panorama":    [{"name": "Hwy 1 and Hwy 93 South", "segments": ["hwy1_to40", "hwy1_40_banff",
                                                   "hwy1_banff_sun", "hwy1_sun_castle", "hwy93s", "hwy95_inv"]}],
    "Fernie":      [{"name": "Via Hwy 22", "segments": ["hwy22_south", "hwy3_crowsnest"]},
                    {"name": "Via Hwy 2 and Fort Macleod", "segments": ["hwy1_east", "hwy2_south",
                                                   "hwy3_macleod", "hwy3_crowsnest"]}],
    "Marmot Basin": [{"name": "Hwy 1 and Icefields Parkway",
                     "segments": ["hwy1_to40", "hwy1_40_banff", "hwy1_banff_sun",
                                  "hwy1_sun_castle", "hwy1_castle_ll",
                                  "hwy93n_icefields", "hwy93a_marmot"]}],
    "Nitehawk":    [{"name": "Hwy 2 North and Hwy 43",
                     "segments": ["hwy2_north", "hwy43_gp", "hwy40_gp"]}],
    "WinSport":    [{"name": "Hwy 1 east", "segments": ["hwy1_east"]}],
}

# "Hwy 1", "HWY-24", "Highway 93", "Route 3". The lookahead stops "1A" matching "1".
ROUTE_RE = re.compile(r"(?:hwy|highway|route)[\s.\-]*0*(\d+)(?![\dA-Za-z])", re.IGNORECASE)
# Lettered highways kept distinct (93A, 1A, 22X) so 93A never matches plain 93.
ROUTE_LETTER_RE = re.compile(r"(?:hwy|highway|route)[\s.\-]*0*(\d+[A-Za-z])(?![\dA-Za-z])", re.IGNORECASE)
BC_CLOSED_RE = re.compile(r"\broad closed\b|\bhighway closed\b|closed in both directions|full closure",
                          re.IGNORECASE)
BAD_COND_RE = re.compile(r"snow|ice|icy|slush|covered|frost|drift|closed|poor", re.IGNORECASE)


def routes_in(*texts):
    found = set()
    for t in texts:
        if not t:
            continue
        t = str(t)
        found.update(ROUTE_RE.findall(t))
        found.update(x.upper() for x in ROUTE_LETTER_RE.findall(t))
        if "trans-canada" in t.lower() or "trans canada" in t.lower():
            found.add("1")
    return found


def decode_polyline(s):
    """Google encoded polyline, precision 5 -> [(lat, lon), ...]."""
    pts, idx, lat, lon = [], 0, 0, 0
    if not s or not isinstance(s, str):
        return pts
    try:
        while idx < len(s):
            for which in (0, 1):
                shift = result = 0
                while True:
                    b = ord(s[idx]) - 63
                    idx += 1
                    result |= (b & 0x1F) << shift
                    shift += 5
                    if b < 0x20:
                        break
                delta = ~(result >> 1) if result & 1 else result >> 1
                if which == 0:
                    lat += delta
                else:
                    lon += delta
            pts.append((lat / 1e5, lon / 1e5))
    except IndexError:
        pass  # truncated polyline: keep the points decoded so far
    return pts


def in_box(seg, lat, lon):
    return seg["lat"][0] <= lat <= seg["lat"][1] and seg["lon"][0] <= lon <= seg["lon"][1]


def match_segments(routes, points):
    hits = []
    for key, seg in SEGMENTS.items():
        seg_routes = seg["route"] if isinstance(seg["route"], (list, tuple)) else (seg["route"],)
        if any(r in routes for r in seg_routes) and any(in_box(seg, la, lo) for la, lo in points):
            hits.append(key)
    return hits


TYPE_WORDS = {"accidentsandincidents": "incident", "closures": "closure", "roadwork": "roadwork",
              "specialevents": "special event", "generalinfo": "info"}


def humanize(t):
    """'accidentsAndIncidents' -> 'incident', 'laneClosure' -> 'lane closure'."""
    t = str(t).strip()
    if t.lower() in TYPE_WORDS:
        return TYPE_WORDS[t.lower()]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t).replace("_", " ").lower()


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

# ------------------------------------------------------------------ Alberta


def fetch_ab_events():
    url = "https://511.alberta.ca/api/v2/get/event?" + urllib.parse.urlencode(
        {"key": AB_KEY, "format": "json", "lang": "en"})
    data = get_json(url)
    if not isinstance(data, list):
        raise ValueError("unexpected response shape")
    now = time.time()
    out = []
    for ev in data:
        road, desc = ev.get("RoadwayName", ""), ev.get("Description", "")
        pts = []
        for la, lo in ((ev.get("Latitude"), ev.get("Longitude")),
                       (ev.get("LatitudeSecondary"), ev.get("LongitudeSecondary"))):
            la, lo = num(la), num(lo)
            if la is not None and lo is not None and (la, lo) != (0.0, 0.0):
                pts.append((la, lo))
        pts += decode_polyline(ev.get("EncodedPolyline"))
        segs = match_segments(routes_in(road), pts)
        if not segs:
            continue
        start = ev.get("StartDate")
        out.append({
            "prov": "AB", "road": str(road)[:80],
            "type": humanize(ev.get("EventSubType") or ev.get("EventType") or "event"),
            "desc": str(desc or "").strip()[:300],
            "closed": bool(ev.get("IsFullClosure")),
            "severity": ev.get("Severity"),
            "planned_start": start if isinstance(start, (int, float)) and start > now else None,
            "updated": ev.get("LastUpdated"),
            "segments": segs,
        })
    return out


def fetch_ab_conditions():
    url = "https://511.alberta.ca/api/v3/get/winterroads?" + urllib.parse.urlencode(
        {"key": AB_KEY, "format": "json", "lang": "en"})
    data = get_json(url)
    if not isinstance(data, list):
        raise ValueError("unexpected response shape")
    out = []
    for c in data:
        road = c.get("RoadwayName", "")
        polys = c.get("EncodedPolyline")
        if isinstance(polys, str):
            polys = [polys]
        pts = []
        for p in polys or []:
            pts += decode_polyline(p)
        segs = match_segments(routes_in(road), pts)
        if not segs:
            continue
        primary = str(c.get("Primary Condition") or "").strip()
        secondary = c.get("Secondary Conditions") or []
        if isinstance(secondary, str):
            secondary = [secondary]
        text = " ".join([primary] + [str(s) for s in secondary])
        out.append({
            "road": str(road)[:80],
            "where": str(c.get("LocationDescription") or "")[:120],
            "primary": primary, "secondary": [str(s) for s in secondary][:4],
            "visibility": c.get("Visibility"),
            "closed": primary.lower() == "closed",
            "poor": bool(BAD_COND_RE.search(text)),
            "updated": c.get("LastUpdated"),
            "segments": segs,
        })
    return out

# ------------------------------------------------------------------ BC


def flatten_coords(c):
    """Yield (lat, lon) from any GeoJSON coordinates nesting."""
    if isinstance(c, (list, tuple)) and len(c) >= 2 and all(isinstance(x, (int, float)) for x in c[:2]):
        yield (float(c[1]), float(c[0]))
    elif isinstance(c, (list, tuple)):
        for x in c:
            yield from flatten_coords(x)


def fetch_bc_events():
    # Box covers the Kootenay/Radium/Invermere and Elk Valley ends of our routes.
    url = "https://api.open511.gov.bc.ca/events?" + urllib.parse.urlencode(
        {"format": "json", "status": "ACTIVE", "limit": 500, "bbox": "-116.4,49.3,-114.0,51.5"})
    out, pages = [], 0
    while url and pages < 10:
        data = get_json(url)
        pages += 1
        for ev in data.get("events", []):
            roads = ev.get("roads") or []
            names = " ".join(str(r.get("name", "")) for r in roads)
            desc = ev.get("description") or ""
            pts = list(flatten_coords((ev.get("geography") or {}).get("coordinates")))
            segs = match_segments(routes_in(names), pts)
            if not segs:
                continue
            states = {str(r.get("state", "")).upper() for r in roads}
            out.append({
                "prov": "BC", "road": names[:80],
                "type": str(ev.get("event_type") or "event").replace("_", " ").lower(),
                "desc": str(desc).strip()[:300],
                "closed": "CLOSED" in states or bool(BC_CLOSED_RE.search(desc)),
                "severity": ev.get("severity"),
                "planned_start": None,
                "updated": ev.get("updated"),
                "segments": segs,
            })
        nxt = (data.get("pagination") or {}).get("next_url")
        url = urllib.parse.urljoin("https://api.open511.gov.bc.ca/", nxt) if nxt else None
    return out

# ------------------------------------------------------------------ main


def run(name, fn, needs_key=False):
    if needs_key and not AB_KEY:
        return {"status": "no_key", "message": "Add the AB511_KEY repository secret to enable."}, []
    try:
        items = fn()
        return {"status": "ok", "count": len(items)}, items
    except Exception as e:  # report, never crash the build
        msg = str(e)
        if AB_KEY:
            msg = msg.replace(AB_KEY, "***")
        return {"status": "error", "message": msg[:200]}, []


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "roads.json"
    s_ab, ab = run("ab", fetch_ab_events, needs_key=True)
    s_abc, abc = run("ab_conditions", fetch_ab_conditions, needs_key=True)
    s_bc, bc = run("bc", fetch_bc_events)

    segments = {}
    for key, seg in SEGMENTS.items():
        segments[key] = {
            "label": seg["label"], "prov": seg["prov"],
            "events": [e for e in ab + bc if key in e["segments"]],
            "conditions": [c for c in abc if key in c["segments"]],
        }
    doc = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {"ab_events": s_ab, "ab_conditions": s_abc, "bc_events": s_bc},
        "segments": segments,
        "hills": HILL_ROUTES,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(json.dumps(doc["sources"]))
    print(f"wrote {out_path}: {sum(len(s['events']) for s in segments.values())} segment-events")


if __name__ == "__main__":
    main()
