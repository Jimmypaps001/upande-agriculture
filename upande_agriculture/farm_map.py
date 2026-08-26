"""Farm map: what is planted where, and which houses are about to run hot.

Geometry comes from upande_scp when that app is on the site (it owns the
surveyed polygons on Warehouse.custom_raw_geojson). The bundled survey is
Karen Roses' own map (exported from kaitet.local) -- it is not a generic
placeholder, so it only applies on a site that is actually Karen Roses.
Every other site without live geometry just gets an empty map rather than
someone else's farm.
"""

from __future__ import annotations

import datetime
import json
import math
import os

import frappe
from frappe.utils import getdate

from upande_agriculture.budget import (
    CYCLE_FIELDS,
    FARM_LAT,
    FARM_LON,
    _resolve_protocol,
    default_seasonal_factors,
)
from upande_agriculture.projection_calc import build_budget_year, iso_weeks_in_year

# Fallback only. The real centre is derived from whatever geometry we loaded,
# because the survey and the weather station are not the same farm.
DEFAULT_CENTRE = {"lat": FARM_LAT, "lon": FARM_LON, "zoom": 16.4}


def house(name: str | None) -> str:
    """'Main GH 02 - TFC' -> 'GH 02'. Warehouse names carry a company suffix
    and a 'Main' prefix that nobody on the farm says out loud."""
    txt = (name or "").split(" - ")[0].strip()
    if txt.lower().startswith("main "):
        txt = txt[5:].strip()
    return txt or (name or "")
SURGE_WEEKS = 4          # how far ahead "about to rake in a lot" looks
SURGE_BASE_WEEKS = 8     # the trailing window it is measured against
WEATHER_TTL = 30 * 60
GEOMETRY_TTL = 60 * 60


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

# The bundled survey. Karen Roses is three separate farms 3 km apart rather
# than one block of houses, so the file also carries the farm each house
# belongs to — without it the map is 34 unlabelled rectangles in a field.
BUNDLED_SURVEY = "karen_roses_geometry.json"


def _bundled_survey_enabled() -> bool:
    """Opt-in per site: the bundled file is Karen Roses' own survey, not a
    generic placeholder, so it must not appear on somebody else's site just
    because that site has no upande_scp geometry of its own."""
    return bool(frappe.conf.get("upande_agriculture_bundled_survey"))


def _bundled_file() -> dict:
    if not _bundled_survey_enabled():
        return {}
    # Not under fixtures/ — Frappe tries to import every JSON in there as a
    # document and migrate dies on a file that is plain app data.
    path = os.path.join(os.path.dirname(__file__), "data", BUNDLED_SURVEY)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _bundled_geometry() -> dict:
    """{warehouse: [polygon, ...]}, whichever shape the survey file uses."""
    raw = _bundled_file()
    geom = raw.get("geometry") if isinstance(raw, dict) else None
    if isinstance(geom, dict):
        return geom
    # Older surveys are a bare {name: polygons} map with no farm grouping.
    return {k: v for k, v in (raw or {}).items() if not k.startswith("_")}


def _bundled_farms() -> dict:
    raw = _bundled_file()
    return raw.get("farms") or {} if isinstance(raw, dict) else {}


def house_farms(geom: dict) -> dict:
    """{warehouse: farm}. The site's own custom_farm wins over the survey."""
    out = dict(_bundled_farms())
    if frappe.db.has_column("Warehouse", "custom_farm"):
        for r in frappe.db.get_all("Warehouse", filters={"custom_farm": ("is", "set")},
                                   fields=["name", "custom_farm"]):
            out[r["name"]] = r["custom_farm"]
    return {k: v for k, v in out.items() if k in geom}


def geometry_bounds(geom: dict) -> dict | None:
    """South-west / north-east corners, so the page can fit the whole survey.

    Karen Roses spans about 3.2 km; a fixed zoom of 16.4 shows roughly 500 m,
    so anything but a fitted bound puts two of the three farms off-screen.
    """
    pts = [pt for polys in geom.values() for poly in polys for ring in poly for pt in ring]
    if not pts:
        return None
    return {"south": min(p[1] for p in pts), "north": max(p[1] for p in pts),
            "west": min(p[0] for p in pts), "east": max(p[0] for p in pts)}


def _polygons_from_geojson(raw) -> list:
    """Every Polygon ring set inside a stored FeatureCollection.

    The field is hand-maintained, so it may arrive double-encoded or wrapped in
    quotes. Anything unparseable is skipped rather than allowed to break the map.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        txt = raw.strip()
        if txt.startswith('"') and txt.endswith('"'):
            txt = txt[1:-1].replace('\\"', '"')
        try:
            raw = json.loads(txt)
        except Exception:
            return []
    out = []
    feats = raw.get("features") if isinstance(raw, dict) else None
    for f in feats or []:
        g = (f or {}).get("geometry") or {}
        if g.get("type") == "Polygon":
            out.append(g.get("coordinates") or [])
        elif g.get("type") == "MultiPolygon":
            out.extend(g.get("coordinates") or [])
    return [p for p in out if p]


def greenhouse_geometry() -> dict:
    """{warehouse name: [polygon, ...]} — surveyed where possible."""
    cached = frappe.cache().get_value("uagri:map:geometry")
    if cached is not None:
        return cached

    out = {}
    if frappe.db.has_column("Warehouse", "custom_raw_geojson"):
        rows = frappe.db.get_all(
            "Warehouse",
            filters={"custom_raw_geojson": ("is", "set")},
            fields=["name", "custom_raw_geojson"],
        )
        for r in rows:
            polys = _polygons_from_geojson(r.get("custom_raw_geojson"))
            if polys:
                out[r["name"]] = polys

    if not out:
        out = _bundled_geometry()
    frappe.cache().set_value("uagri:map:geometry", out, expires_in_sec=GEOMETRY_TTL)
    return out


def _match_geometry(geom: dict, warehouse: str) -> list | None:
    """Line up a local warehouse with a surveyed one.

    Sites differ in company suffix (`Main GH 02 - TFC` vs `- MFL`), so fall back
    to matching on the house label alone.
    """
    if warehouse in geom:
        return geom[warehouse]
    want = house(warehouse).strip().lower()
    for name, polys in geom.items():
        if house(name).strip().lower() == want:
            return polys
    return None


def _centroid(polys: list) -> tuple[float, float] | None:
    pts = [pt for poly in polys for ring in poly for pt in ring]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _ring_area_m2(ring: list) -> float:
    """Shoelace on a local equirectangular projection — fine at field scale."""
    if len(ring) < 3:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    k = math.cos(math.radians(lat0))
    m_per_deg = 111_320.0
    total = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i][0] * k * m_per_deg, ring[i][1] * m_per_deg
        x2, y2 = ring[(i + 1) % len(ring)][0] * k * m_per_deg, ring[(i + 1) % len(ring)][1] * m_per_deg
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _footprint_m2(polys: list) -> float:
    return round(sum(_ring_area_m2(p[0]) for p in polys if p), 1)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

def _wind_dir(deg) -> str:
    if deg is None:
        return ""
    pts = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return pts[int((deg % 360) / 22.5 + 0.5) % 16]


@frappe.whitelist()
def weather_now() -> dict:
    """Current conditions plus a short outlook. Never raises — the map is the
    point, weather is decoration, and a dead API must not take the page down."""
    hit = frappe.cache().get_value("uagri:map:weather")
    if hit:
        return hit
    out = {"ok": False, "source": "unavailable"}
    try:
        import requests
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": FARM_LAT, "longitude": FARM_LON,
                "current": ("temperature_2m,relative_humidity_2m,wind_speed_10m,"
                            "wind_direction_10m,precipitation,weather_code,"
                            "apparent_temperature,cloud_cover"),
                "daily": ("temperature_2m_max,temperature_2m_min,precipitation_sum,"
                          "shortwave_radiation_sum,wind_speed_10m_max"),
                "timezone": "Africa/Nairobi", "forecast_days": 7,
            },
            timeout=8,
        )
        j = r.json() or {}
        c = j.get("current") or {}
        dly = j.get("daily") or {}
        days = []
        for i, day in enumerate(dly.get("time") or []):
            days.append({
                "date": day,
                "dow": datetime.date.fromisoformat(day).strftime("%a"),
                "tmax": round(dly["temperature_2m_max"][i]),
                "tmin": round(dly["temperature_2m_min"][i]),
                "rain": round(dly["precipitation_sum"][i] or 0, 1),
                "light": round(dly["shortwave_radiation_sum"][i] or 0, 1),
                "wind": round(dly["wind_speed_10m_max"][i] or 0),
            })
        out = {
            "ok": True, "source": "Open-Meteo · Naivasha",
            "temp": round(c.get("temperature_2m") or 0, 1),
            "feels": round(c.get("apparent_temperature") or 0, 1),
            "humidity": round(c.get("relative_humidity_2m") or 0),
            "wind": round(c.get("wind_speed_10m") or 0, 1),
            "wind_deg": c.get("wind_direction_10m"),
            "wind_dir": _wind_dir(c.get("wind_direction_10m")),
            "rain": round(c.get("precipitation") or 0, 1),
            "cloud": round(c.get("cloud_cover") or 0),
            "code": c.get("weather_code"),
            "days": days,
        }
        frappe.cache().set_value("uagri:map:weather", out, expires_in_sec=WEATHER_TTL)
    except Exception as e:
        frappe.log_error(f"weather_now: {e}", "upande_agriculture")
    return out


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def _centre(geom: dict | None = None) -> dict:
    """Map Settings when upande_scp supplies it, else the middle of the survey."""
    if frappe.db.exists("DocType", "Map Settings"):
        try:
            s = frappe.get_cached_doc("Map Settings")
            if s.get("lat") and s.get("lon"):
                return {"lat": s.lat, "lon": s.lon, "zoom": s.get("default_zoom") or 16.4}
        except Exception:
            pass
    if geom:
        pts = [pt for polys in geom.values() for poly in polys for ring in poly for pt in ring]
        if pts:
            return {"lat": sum(p[1] for p in pts) / len(pts),
                    "lon": sum(p[0] for p in pts) / len(pts), "zoom": 16.4}
    return dict(DEFAULT_CENTRE)


def _stage(weeks: float | None, proto: dict | None) -> str:
    if weeks is None:
        return "unknown"
    if not proto:
        return "producing" if weeks > 30 else "establishing"
    # A summer flower has no bending stage — it's establishing until its
    # first flush, then straight into producing.
    b1 = proto.get("weeks_to_first_bending") or proto.get("weeks_to_first_flush") or 0
    b2 = b1 + (proto.get("weeks_to_second_bending") or 0)
    life = proto.get("productive_life_weeks") or 0
    if weeks < b1:
        return "establishing"
    if weeks < b2:
        return "bending"
    if life and weeks > life * 0.85:
        return "ageing"
    return "producing"


@frappe.whitelist()
def map_payload(year: int | None = None) -> dict:
    today = getdate(frappe.utils.nowdate())
    year = int(year or today.year)
    iso_year, now_week, _ = today.isocalendar()
    wiy = iso_weeks_in_year(year)

    from upande_agriculture.controllers import _seasonal_factor_map

    geom = greenhouse_geometry()
    cycles = frappe.db.get_all(
        "Crop Cycle",
        filters={"status": ("!=", "Ended")},
        fields=list(CYCLE_FIELDS) + ["planted_area", "plants_per_sqm", "status"],
        order_by="greenhouse asc, variety asc",
    )

    ahead = [w for w in range(now_week, now_week + SURGE_WEEKS) if w <= wiy]
    behind = [w for w in range(max(1, now_week - SURGE_BASE_WEEKS), now_week)]

    houses: dict[str, dict] = {}
    for c in cycles:
        # Old-model leftovers (a cycle named after its greenhouse, no
        # variety) can't be budgeted or drawn — they'd crash the variety
        # rollup below and add empty houses.
        if not c.get("variety"):
            continue
        gh = c["greenhouse"]
        h = houses.setdefault(gh, {
            "name": gh, "label": house(gh), "plantings": [], "area": 0.0,
            "plants": 0, "week_stems": 0, "ahead_stems": 0, "base_stems": 0,
            "annual": 0,
        })
        proto = _resolve_protocol(c)
        # Same seasonal curve the spreadsheet uses, or surge would measure a
        # flat plateau against itself and always read zero.
        sf = _seasonal_factor_map(c["variety"]) or default_seasonal_factors()
        wk = build_budget_year([(c, proto)], year, sf) if proto else {}
        annual = sum(wk.values())

        age = None
        if c.get("planting_date"):
            age = round((today - getdate(c["planting_date"])).days / 7.0, 1)

        area = c.get("planted_area") or 0
        h["area"] += area
        h["plants"] += int(c.get("qty_planted") or 0)
        h["annual"] += annual
        h["week_stems"] += wk.get(now_week, 0)
        h["ahead_stems"] += sum(wk.get(w, 0) for w in ahead)
        h["base_stems"] += sum(wk.get(w, 0) for w in behind)
        h["plantings"].append({
            "cycle": c["name"],
            "variety": c["variety"],
            "area": round(area, 1),
            "plants": int(c.get("qty_planted") or 0),
            "density": c.get("plants_per_sqm"),
            "planting_date": str(c["planting_date"]) if c.get("planting_date") else None,
            "age_weeks": age,
            "stage": _stage(age, proto),
            "annual": annual,
            "week_stems": wk.get(now_week, 0),
            "ahead_stems": sum(wk.get(w, 0) for w in ahead),
            "rate": round(annual / area) if area and annual else None,
        })

    out = []
    for gh, h in houses.items():
        polys = _match_geometry(geom, gh)
        cen = _centroid(polys) if polys else None
        # Surge = the next four weeks against the recent weekly run rate. It is
        # what makes a house worth staffing, not its absolute size.
        base_rate = (h["base_stems"] / len(behind)) if behind and h["base_stems"] else 0
        ahead_rate = (h["ahead_stems"] / len(ahead)) if ahead else 0
        surge = round((ahead_rate - base_rate) / base_rate * 100) if base_rate else None
        h.update({
            "polygons": polys or [],
            "has_geometry": bool(polys),
            "lon": cen[0] if cen else None,
            "lat": cen[1] if cen else None,
            "footprint": _footprint_m2(polys) if polys else None,
            "area": round(h["area"], 1),
            "varieties": sorted({p["variety"] for p in h["plantings"]}),
            "surge": surge,
            "ahead_rate": round(ahead_rate),
            "base_rate": round(base_rate),
            "beds": frappe.db.count("Crop Cycle Bed",
                                    {"parent": ("in", [p["cycle"] for p in h["plantings"]])})
            if h["plantings"] else 0,
        })
        h["plantings"].sort(key=lambda p: -(p["area"] or 0))
        out.append(h)

    out.sort(key=lambda h: h["label"])
    # Houses with geometry but nothing planted still belong on the map — an
    # empty house is a planning fact, not an absence of data.
    planted = {h["name"] for h in out}
    for name, polys in geom.items():
        if name in planted or any(house(name).lower() == house(p).lower() for p in planted):
            continue
        cen = _centroid(polys)
        out.append({
            "name": name, "label": house(name), "plantings": [], "varieties": [],
            "area": 0, "plants": 0, "annual": 0, "week_stems": 0, "ahead_stems": 0,
            "base_stems": 0, "surge": None, "ahead_rate": 0, "base_rate": 0, "beds": 0,
            "polygons": polys, "has_geometry": True,
            "lon": cen[0] if cen else None, "lat": cen[1] if cen else None,
            "footprint": _footprint_m2(polys), "empty": True,
        })

    varieties = sorted({v for h in out for v in h["varieties"]})
    return {
        "centre": _centre(geom),
        "year": year,
        "iso_year": iso_year,
        "now_week": now_week,
        "weeks_in_year": wiy,
        "surge_weeks": SURGE_WEEKS,
        "greenhouses": out,
        "varieties": varieties,
        "totals": {
            "houses": len([h for h in out if not h.get("empty")]),
            "empty": len([h for h in out if h.get("empty")]),
            "area": round(sum(h["area"] for h in out), 1),
            "plants": sum(h["plants"] for h in out),
            "week_stems": sum(h["week_stems"] for h in out),
            "annual": sum(h["annual"] for h in out),
        },
        "weather": weather_now(),
        "site": frappe.db.get_single_value("Global Defaults", "default_company") or "Farm",
        "geometry_source": (
            "surveyed" if frappe.db.has_column("Warehouse", "custom_raw_geojson")
            and frappe.db.count("Warehouse", {"custom_raw_geojson": ("is", "set")})
            else "bundled" if _bundled_survey_enabled()
            else "none"
        ),
        # Karen Roses is three farms up to 3 km apart, so the page needs to know
        # which house belongs where and how far it has to zoom out to show them.
        "farms": _farm_summary(geom, out),
        "bounds": geometry_bounds(geom),
    }


def _farm_summary(geom: dict, houses: list) -> list:
    """One entry per farm: its houses, its centre and its own bounds.

    Fitting the whole survey makes each house tiny, so the page offers a jump
    per farm. Houses the survey cannot place sit under "Unplaced" rather than
    being dropped silently.
    """
    by_house = house_farms(geom)
    groups: dict = {}
    for h in houses:
        wh = h.get("warehouse") or h.get("name")
        farm = by_house.get(wh) or "Unplaced"
        groups.setdefault(farm, []).append(h)

    out = []
    for farm, items in sorted(groups.items()):
        sub = {k: geom[k] for k in
               (i.get("warehouse") or i.get("name") for i in items) if k in geom}
        b = geometry_bounds(sub)
        out.append({
            "farm": farm,
            "houses": len(items),
            "planted": len([i for i in items if not i.get("empty")]),
            "bounds": b,
            "centre": ({"lat": (b["south"] + b["north"]) / 2,
                        "lon": (b["west"] + b["east"]) / 2} if b else None),
        })
    return out


@frappe.whitelist()
def bed_geometry(greenhouse: str) -> dict:
    """Row lines for one house, if upande_scp's Zone data is on this site."""
    if not frappe.db.exists("DocType", "Zone"):
        return {"rows": [], "source": "none"}
    try:
        zones = frappe.db.get_all(
            "Zone",
            filters={"greenhouse": greenhouse, "raw_geojson": ("is", "set")},
            fields=["name", "bed", "zone", "raw_geojson"],
            limit=6000,
        )
    except Exception:
        return {"rows": [], "source": "none"}

    # Each Zone holds one short segment; a bed is the segments sharing a line_id,
    # ordered along the row.
    beds: dict[int, list] = {}
    for z in zones:
        try:
            gj = json.loads(z["raw_geojson"])
        except Exception:
            continue
        for f in gj.get("features") or []:
            g = f.get("geometry") or {}
            if g.get("type") != "LineString":
                continue
            props = f.get("properties") or {}
            line = props.get("line_id")
            if line is None:
                continue
            beds.setdefault(int(line), []).append(
                (props.get("zone_id") or 0, g.get("coordinates") or []))

    rows = []
    for line, segs in sorted(beds.items()):
        segs.sort(key=lambda s: s[0])
        coords = [pt for _, seg in segs for pt in seg]
        if len(coords) >= 2:
            rows.append({"bed": line, "coords": coords})
    return {"rows": rows, "source": "zones", "count": len(rows)}
