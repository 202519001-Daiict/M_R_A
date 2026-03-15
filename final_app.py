"""
Road Risk Intelligence Navigator
Uses Supabase PostgreSQL + Leaflet.js (moving car + smart zone alerts)
"""

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import json
import struct
import psycopg2
from psycopg2.extras import RealDictCursor
from geopy.geocoders import Nominatim
from geopy.distance import geodesic

# ─────────────────────────────────────────────
# 1. PAGE CONFIGURATION
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Road Risk Intelligence Navigator",
    page_icon="🚦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main { background-color: #0e1117; }
    .block-container { padding-top: 1rem; }
    .stButton > button { border-radius: 8px; font-weight: 600; transition: all 0.2s; }
    .alert-safe    { background:#1a3a1a; border-left:4px solid #00c853; padding:10px 14px; border-radius:6px; margin:6px 0; }
    .alert-warning { background:#3a2a00; border-left:4px solid #ffd600; padding:10px 14px; border-radius:6px; margin:6px 0; }
    .alert-danger  { background:#3a0000; border-left:4px solid #d50000; padding:10px 14px; border-radius:6px; margin:6px 0; }
    .footer-bar    { background:#1c1f26; border-top:1px solid #333; padding:12px 0; text-align:center;
                     color:#888; font-size:0.8rem; margin-top:2rem; border-radius:0 0 8px 8px; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# 2. SUPABASE DATA CONNECTION
# ─────────────────────────────────────────────
def get_db_connection():
    return psycopg2.connect(
        host     = st.secrets["DB_HOST"],
        port     = int(st.secrets["DB_PORT"]),
        dbname   = st.secrets["DB_NAME"],
        user     = st.secrets["DB_USER"],
        password = st.secrets["DB_PASSWORD"],
        sslmode  = "require"
    )

@st.cache_data(ttl=300, show_spinner="Loading accident zones...")
def load_accident_data() -> pd.DataFrame:
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM accident_data1;")
        rows = cur.fetchall()
    conn.close()
    df = pd.DataFrame(rows)
    df["latitude"]       = pd.to_numeric(df["latitude"],       errors="coerce")
    df["longitude"]      = pd.to_numeric(df["longitude"],      errors="coerce")
    df["total_accident"] = pd.to_numeric(df["total_accident"], errors="coerce").fillna(0)
    df["total_fatality"] = pd.to_numeric(df["total_fatality"], errors="coerce").fillna(0)
    df["severity_index"] = pd.to_numeric(df["severity_index"], errors="coerce").fillna(0)
    df.dropna(subset=["latitude", "longitude"], inplace=True)
    return df

@st.cache_data(ttl=300, show_spinner="Loading driver path...")
def load_driver_path() -> list:
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM driver_path;")
        rows = cur.fetchall()
    conn.close()
    all_paths = []
    for row in rows:
        coords = decode_wkb_linestring(str(row.get("geom", "")))
        if coords:
            start_lat, start_lng = coords[0][0],  coords[0][1]
            end_lat,   end_lng   = coords[-1][0], coords[-1][1]
            all_paths.append({
                "id":          row.get("id"),
                "created_at":  str(row.get("created_at", "")),
                "coordinates": coords,
                "start_coord": [start_lat, start_lng],
                "end_coord":   [end_lat,   end_lng],
                "start_name":  _reverse_geocode(start_lat, start_lng),
                "end_name":    _reverse_geocode(end_lat,   end_lng),
            })
    return all_paths

@st.cache_data(ttl=86400, show_spinner=False)
def _reverse_geocode(lat: float, lng: float) -> str:
    """Return a short human-readable place name for a lat/lng coordinate."""
    try:
        geo = Nominatim(user_agent="road_risk_nav_revgeo", timeout=8)
        loc = geo.reverse((lat, lng), language="en", exactly_one=True)
        if not loc:
            return f"{lat:.4f}, {lng:.4f}"
        addr = loc.raw.get("address", {})
        # Build short name: neighbourhood/suburb/road + city
        parts = []
        for key in ["road", "neighbourhood", "suburb", "quarter"]:
            if addr.get(key):
                parts.append(addr[key])
                break
        for key in ["city_district", "suburb", "city", "town", "village"]:
            if addr.get(key):
                parts.append(addr[key])
                break
        return ", ".join(parts) if parts else loc.address.split(",")[0]
    except Exception:
        return f"{lat:.4f}, {lng:.4f}"

# ─────────────────────────────────────────────
# 3. WKB GEOMETRY DECODER
# ─────────────────────────────────────────────
def decode_wkb_linestring(hex_wkb: str) -> list:
    try:
        raw = bytes.fromhex(hex_wkb)
        byte_order = raw[0]
        endian = "<" if byte_order == 1 else ">"
        offset = 9
        num_points = struct.unpack_from(endian + "I", raw, offset)[0]
        offset += 4
        coords = []
        for _ in range(num_points):
            x, y = struct.unpack_from(endian + "dd", raw, offset)
            offset += 16
            coords.append([y, x])
        return coords
    except Exception:
        return []

# ─────────────────────────────────────────────
import re

@st.cache_data(ttl=600, show_spinner="Geocoding...")
def _nominatim(address: str):
    try:
        geo = Nominatim(user_agent="road_risk_nav_v6", timeout=10)
        r   = geo.geocode(address)
        return (r.latitude, r.longitude, str(r.address[:70])) if r else None
    except Exception:
        return None

def resolve_location(query: str, accident_df: pd.DataFrame):
    q = query.strip()
    m = re.match(r'^([+-]?\d{1,3}(?:\.\d+)?)\s*[,\s]\s*([+-]?\d{1,3}(?:\.\d+)?)$', q)
    if m:
        la,ln = float(m.group(1)),float(m.group(2))
        if -90<=la<=90 and -180<=ln<=180:
            return la, ln, f"{la:.5f}, {ln:.5f}"
    ql = q.lower()
    for _, row in accident_df.iterrows():
        area = str(row.get("area","")).lower().strip()
        # Exact match: query must equal the area field exactly (case-insensitive)
        # This prevents "Andheri" from matching rows where area="Pantnagar"
        # just because location contains "Andheri Link Rd"
        if ql == area:
            return float(row["latitude"]), float(row["longitude"]), \
                   f"{row.get('area','')} — {row.get('location','')}"
    return _nominatim(q)

# ─────────────────────────────────────────────
# 5. RISK CHECK FUNCTIONS
# ─────────────────────────────────────────────
def check_risk_at_point(lat: float, lon: float, accident_df: pd.DataFrame,
                         radius_m: int = 500) -> dict:
    nearby = []
    for _, row in accident_df.iterrows():
        dist = geodesic((lat, lon), (row["latitude"], row["longitude"])).meters
        if dist <= radius_m:
            nearby.append({**row.to_dict(), "distance_m": round(dist)})
    if not nearby:
        return {"level": "SAFE", "zones": [], "message": "No accident zones nearby."}
    nearby_df = pd.DataFrame(nearby)
    max_si = nearby_df["severity_index"].max()
    level = "HIGH" if max_si > 25 else ("MEDIUM" if max_si > 10 else "LOW")
    return {
        "level": level,
        "zones": nearby,
        "message": f"{len(nearby)} accident zone(s) within {radius_m}m — max severity {max_si:.1f}"
    }

# ─────────────────────────────────────────────
# 6. LEAFLET MAP WITH MOVING CAR + SMART ALERTS
# ─────────────────────────────────────────────
def build_leaflet_map(accident_df: pd.DataFrame,
                       driver_paths: list,
                       center_lat: float = 19.076,
                       center_lng: float = 72.877,
                       search_zones: list = None,
                       search_label: str = "",
                       nav_origin_coord=None,
                       nav_dest_coord=None,
                       nav_origin_name: str = "",
                       nav_dest_name: str = "",
                       show_car: bool = False) -> str:

    zones_js    = json.dumps([
        {"id":int(r["id"]),"lat":float(r["latitude"]),"lng":float(r["longitude"]),
         "area":str(r.get("area","")),"loc":str(r.get("location","")),"city":str(r.get("city","")),
         "si":float(r.get("severity_index",0)),"risk":str(r.get("risk_level","Low")),
         "ta":int(r.get("total_accident",0)),"tf":int(r.get("total_fatality",0))}
        for _,r in accident_df.iterrows()
    ])
    paths_js        = json.dumps([{"id":p["id"],"coords":p["coordinates"]} for p in driver_paths])
    nav_origin_js   = json.dumps(list(nav_origin_coord) if nav_origin_coord else None)
    nav_dest_js     = json.dumps(list(nav_dest_coord)   if nav_dest_coord   else None)
    nav_oname_js    = json.dumps(nav_origin_name)
    nav_dname_js    = json.dumps(nav_dest_name)
    sz_ids_js   = json.dumps([z["id"]  for z in search_zones] if search_zones else [])
    sz_data_js  = json.dumps(search_zones if search_zones else [])
    sz_label_js = json.dumps(search_label)
    show_car_js = "true" if show_car else "false"

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>Road Risk Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body, html {{ height:100%; background:#0e1117; font-family:sans-serif; }}
  #wrapper {{ display:flex; flex-direction:column; height:100vh; }}
  #map {{ flex:1; min-height:0; position:relative; }}
  /* overlay sits ON TOP of leaflet, outside its event system */
  #mapOverlay {{
    position:absolute; top:0; left:0; width:100%; height:100%;
    pointer-events:none; z-index:2000;
  }}
  #mapOverlay > * {{ pointer-events:all; }}

  /* ── Single alert bar (one message at a time) ── */
  #alertFeed {{
    background:#0d1117;
    border-top:2px solid #1e90ff33;
    height:52px;
    display:flex;
    align-items:center;
    flex-shrink:0;
    padding:0 10px;
    overflow:hidden;
  }}
  #alertMsg {{
    width:100%;
    padding:8px 14px;
    border-radius:6px;
    font-size:0.82rem;
    font-weight:600;
    color:#fff;
    border-left:4px solid #00c853;
    background:#0d2a0d;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
    transition:background 0.25s, border-color 0.25s;
  }}
  #alertMsg.approaching {{ background:#0d1f2d; border-left-color:#00e5ff; }}
  #alertMsg.entered     {{ background:#2a0000; border-left-color:#d50000; }}
  #alertMsg.left        {{ background:#0d2a0d; border-left-color:#00c853; }}
  #alertMsg.safe        {{ background:#0d2a0d; border-left-color:#00c853; }}

  /* ── Floating map alert bubble ── */
  #mapAlert {{
    position:absolute;
    top:14px; left:50%; transform:translateX(-50%);
    z-index:1000;
    padding:7px 22px;
    border-radius:20px;
    font-size:0.82rem;
    font-weight:700;
    color:#fff;
    pointer-events:none;
    opacity:0;
    transition:opacity 0.3s;
    white-space:nowrap;
    box-shadow:0 2px 14px #0009;
  }}
  #mapAlert.show {{ opacity:1; }}
  #mapAlert.ap  {{ background:rgba(0,60,100,0.93); border:1.5px solid #00e5ff; }}
  #mapAlert.en  {{ background:rgba(100,0,0,0.95);  border:1.5px solid #ff3333; }}
  #mapAlert.lft {{ background:rgba(0,70,20,0.93);  border:1.5px solid #00c853; }}

  .legend {{ background:rgba(14,17,23,0.92); color:#eee; padding:10px 14px;
             border-radius:8px; font-size:0.76rem; line-height:1.8; box-shadow:0 2px 10px #0006; }}
  .legend h4 {{ margin:0 0 5px; font-size:0.82rem; border-bottom:1px solid #333; padding-bottom:3px; }}
  .dot {{ width:11px; height:11px; border-radius:50%; display:inline-block; margin-right:5px; vertical-align:middle; }}

  /* search result panel */
  #srPanel{{position:absolute;bottom:62px;left:12px;z-index:1002;width:300px;max-height:380px;
            background:rgba(10,13,20,0.97);border:1px solid #2c3a5a;border-radius:12px;
            box-shadow:0 6px 30px #000d;overflow:hidden;display:none;flex-direction:column;}}
  #srPanel.open{{display:flex;animation:fadeUp 0.25s ease;}}
  #srHead{{padding:10px 14px 8px;background:rgba(22,32,55,0.99);border-bottom:1px solid #1e2c44;
           display:flex;align-items:center;justify-content:space-between;flex-shrink:0;}}
  #srHead h3{{margin:0;font-size:0.88rem;color:#e2e8f0;font-weight:700;}}
  #srClose{{background:none;border:none;color:#556;font-size:1.1rem;cursor:pointer;padding:0 2px;}}
  #srClose:hover{{color:#ccc;}}
  #srScroll{{overflow-y:auto;max-height:310px;}}
  .sr-row{{padding:9px 14px;border-bottom:1px solid #131b2c;font-size:0.78rem;color:#cdd;}}
  .sr-row:last-child{{border-bottom:none;}}
  .sr-name{{font-size:0.85rem;font-weight:700;margin-bottom:1px;}}
  .sr-sub{{color:#7a8fa8;font-size:0.72rem;margin-bottom:4px;}}
  .sr-badge{{display:inline-block;padding:1px 8px;border-radius:8px;font-size:0.69rem;font-weight:800;margin-right:4px;}}
  .sr-stats{{font-size:0.74rem;color:#9ab;margin-top:3px;}}
  .sr-stats b{{color:#cde;}}
  @keyframes fadeUp{{from{{opacity:0;transform:translateY(8px);}}to{{opacity:1;transform:translateY(0);}}}}
  @keyframes pulse{{0%,100%{{transform:scale(1);opacity:1;}}50%{{transform:scale(1.6);opacity:0.6);}}}}

  /* ── Google Maps–style layers button ── */
  #layerBtn{{
    position:absolute;top:12px;right:12px;z-index:2001;
    width:44px;height:44px;border-radius:10px;
    background:#fff;border:none;cursor:pointer;
    box-shadow:0 2px 10px rgba(0,0,0,0.3);
    display:flex;align-items:center;justify-content:center;
    font-size:20px;transition:box-shadow 0.2s,transform 0.15s;
  }}
  #layerBtn:hover{{box-shadow:0 4px 16px rgba(0,0,0,0.4);transform:scale(1.05);}}
  #layerBtn.active{{background:#e8f0fe;box-shadow:0 4px 16px rgba(66,133,244,0.4);}}
  #layerPanel{{
    position:absolute;top:64px;right:12px;z-index:2001;
    background:#fff;border-radius:14px;
    box-shadow:0 4px 20px rgba(0,0,0,0.25);
    width:210px;padding:12px 0 8px;
    display:none;animation:fadeDown 0.2s ease;
  }}
  @keyframes fadeDown{{from{{opacity:0;transform:translateY(-8px);}}to{{opacity:1;transform:translateY(0);}}}}
  #layerPanel.open{{display:block;}}
  #layerPanel .lp-title{{
    font-size:0.68rem;font-weight:700;letter-spacing:.8px;
    color:#5f6368;text-transform:uppercase;
    padding:0 14px 8px;border-bottom:1px solid #f0f0f0;margin-bottom:4px;
  }}
  #layerPanel .lp-section{{padding:6px 14px 4px;font-size:0.7rem;color:#5f6368;font-weight:600;letter-spacing:.5px;}}
  .lp-row{{
    display:flex;align-items:center;gap:10px;padding:7px 14px;
    cursor:pointer;transition:background 0.15s;border-radius:0;
    font-size:0.82rem;color:#202124;font-weight:400;
  }}
  .lp-row:hover{{background:#f8f9fa;}}
  .lp-row.selected{{background:#e8f0fe;color:#1a73e8;font-weight:600;}}
  .lp-dot{{width:28px;height:28px;border-radius:8px;display:flex;align-items:center;
            justify-content:center;font-size:16px;flex-shrink:0;}}
  .lp-radio{{width:16px;height:16px;border-radius:50%;border:2px solid #dadce0;
              display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-left:auto;}}
  .lp-radio.on{{border-color:#1a73e8;background:#1a73e8;}}
  .lp-radio.on::after{{content:'';width:6px;height:6px;border-radius:50%;background:#fff;}}
  .lp-check{{width:16px;height:16px;border-radius:3px;border:2px solid #dadce0;
              display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-left:auto;font-size:10px;}}
  .lp-check.on{{border-color:#1a73e8;background:#1a73e8;color:#fff;}}
  .lp-divider{{height:1px;background:#f0f0f0;margin:4px 0;}}
</style>
</head>
<body>
<div id="wrapper">
  <div id="map">
    <div id="mapAlert"></div>
    <div id="srPanel">
      <div id="srHead">
        <h3 id="srTitle">📍 Search Results</h3>
        <button id="srClose" onclick="closeSR()">✕</button>
      </div>
      <div id="srScroll"><div id="srBody"></div></div>
    </div>
  </div>
  <!-- mapOverlay: sits above Leaflet, pointer-events handled separately -->
  <div id="mapOverlay">
    <button id="layerBtn" onclick="toggleLayerPanel()" title="Map layers">🗺️</button>
    <div id="layerPanel">
      <div class="lp-title">Map Type</div>
      <div id="lp-dark"      class="lp-row selected" onclick="setBase('dark')">
        <div class="lp-dot" style="background:#1a1a2e">🌑</div><span>Dark</span>
        <div class="lp-radio on" id="r-dark"></div>
      </div>
      <div id="lp-light"     class="lp-row" onclick="setBase('light')">
        <div class="lp-dot" style="background:#f0f0f0">☀️</div><span>Light</span>
        <div class="lp-radio" id="r-light"></div>
      </div>
      <div id="lp-street"    class="lp-row" onclick="setBase('street')">
        <div class="lp-dot" style="background:#e8f5e9">🗺️</div><span>Street</span>
        <div class="lp-radio" id="r-street"></div>
      </div>
      <div id="lp-satellite" class="lp-row" onclick="setBase('satellite')">
        <div class="lp-dot" style="background:#0d1b2a">🛰️</div><span>Satellite</span>
        <div class="lp-radio" id="r-satellite"></div>
      </div>
      <div id="lp-topo"      class="lp-row" onclick="setBase('topo')">
        <div class="lp-dot" style="background:#d5e8c4">🏔️</div><span>Topo</span>
        <div class="lp-radio" id="r-topo"></div>
      </div>
      <div class="lp-divider"></div>
      <div class="lp-title" style="padding-top:6px">Overlays</div>
      <div class="lp-row" id="ov-zones" onclick="toggleOverlay('zones')">
        <div class="lp-dot" style="background:#fdecea">🚨</div><span>Accident Zones</span>
        <div class="lp-check on" id="c-zones">✓</div>
      </div>
      <div class="lp-row" id="ov-paths" onclick="toggleOverlay('paths')">
        <div class="lp-dot" style="background:#e8f0fe">🛣️</div><span>Driver Paths</span>
        <div class="lp-check on" id="c-paths">✓</div>
      </div>
    </div>
  </div>
  <div id="alertFeed">
    <div id="alertMsg" class="safe">🟢 &nbsp; Press <b>Start</b> in the sidebar to begin car simulation…</div>
  </div>
</div>

<script>
const ZONES     = {zones_js};
const PATHS     = {paths_js};
const SZ_IDS    = new Set({sz_ids_js});
const SZ_DATA   = {sz_data_js};
const SZ_LABEL  = {sz_label_js};
const SHOW_CAR  = {show_car_js};
const NAV_ORIGIN = {nav_origin_js};
const NAV_DEST   = {nav_dest_js};
const NAV_ONAME  = {nav_oname_js};
const NAV_DNAME  = {nav_dname_js};
const NAV_ACTIVE = !!(NAV_ORIGIN && NAV_DEST);
const APPROACH_R = 500;
const ENTER_R    = 120;
const HAS_SEARCH = SZ_IDS.size > 0;
const LS_IDX    = 'riskmap_car_idx';
const LS_TRAIL  = 'riskmap_trail';

// ── MAP INIT ─────────────────────────────────
const map = L.map('map', {{zoomControl:true, preferCanvas:false}})
              .setView([{center_lat}, {center_lng}], 13);
// ── 5 BASE LAYERS + localStorage persistence ──
const BL = {{
  dark     : L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',   {{attribution:'&copy; OSM &copy; CartoDB',subdomains:'abcd',maxZoom:19}}),
  light    : L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png',  {{attribution:'&copy; OSM &copy; CartoDB',subdomains:'abcd',maxZoom:19}}),
  street   : L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',                {{attribution:'&copy; OpenStreetMap contributors',maxZoom:19}}),
  satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',{{attribution:'&copy; Esri',maxZoom:19}}),
  topo     : L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',                  {{attribution:'&copy; OSM &copy; OpenTopoMap',subdomains:'abc',maxZoom:17}}),
}};
BL[localStorage.getItem('riskmap_bl')||'dark'].addTo(map);

// ── NAVIGATION MARKERS ────────────────────────
// ── NAVIGATION MARKERS (Google Maps style) ────
if (NAV_ACTIVE) {{

  // ── START: Blue "My Location" dot (pulsing circle like Google Maps) ──
  L.marker(NAV_ORIGIN, {{icon: L.divIcon({{
    className:'',
    html:`<div style="position:relative;width:22px;height:22px">
      <!-- Outer pulse ring -->
      <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
           width:22px;height:22px;border-radius:50%;
           background:rgba(66,133,244,0.2);
           animation:locPulse 2s ease-out infinite;"></div>
      <!-- Mid ring -->
      <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
           width:14px;height:14px;border-radius:50%;
           background:rgba(66,133,244,0.35);"></div>
      <!-- Core blue dot -->
      <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
           width:14px;height:14px;border-radius:50%;
           background:#4285f4;border:2.5px solid #fff;
           box-shadow:0 1px 6px rgba(66,133,244,0.8);"></div>
    </div>
    <style>
      @keyframes locPulse{{
        0%{{transform:translate(-50%,-50%) scale(0.8);opacity:0.8;}}
        70%{{transform:translate(-50%,-50%) scale(2.2);opacity:0;}}
        100%{{transform:translate(-50%,-50%) scale(0.8);opacity:0;}}
      }}
    </style>`,
    iconSize:[22,22], iconAnchor:[11,11], popupAnchor:[0,-14]
  }})}})
  .bindPopup(`<div style="font-size:0.82rem">
    <b style="color:#4285f4">📍 Start</b><br>
    <span style="color:#555">${{NAV_ONAME}}</span>
  </div>`)
  .addTo(map).openPopup();

  // ── DESTINATION: Red Google Maps–style teardrop pin ──
  L.marker(NAV_DEST, {{icon: L.divIcon({{
    className:'',
    html:`<div style="position:relative;width:28px;height:40px;
               filter:drop-shadow(0 2px 6px rgba(0,0,0,0.4))">
      <svg viewBox="0 0 28 40" xmlns="http://www.w3.org/2000/svg"
           style="width:28px;height:40px;display:block">
        <!-- Pin shadow ellipse -->
        <ellipse cx="14" cy="38.5" rx="5" ry="2" fill="rgba(0,0,0,0.2)"/>
        <!-- Pin body -->
        <path d="M14 1 C7.4 1 2 6.4 2 13 C2 22 14 37 14 37 C14 37 26 22 26 13 C26 6.4 20.6 1 14 1Z"
              fill="#ea4335" stroke="#c62828" stroke-width="0.8"/>
        <!-- White inner circle -->
        <circle cx="14" cy="13" r="7" fill="white"/>
        <!-- Red center dot -->
        <circle cx="14" cy="13" r="3.5" fill="#ea4335"/>
      </svg>
    </div>`,
    iconSize:[28,40], iconAnchor:[14,40], popupAnchor:[0,-42]
  }})}})
  .bindPopup(`<div style="font-size:0.82rem">
    <b style="color:#ea4335">🏁 Destination</b><br>
    <span style="color:#555">${{NAV_DNAME}}</span>
  </div>`)
  .addTo(map);

  // Fit map to show both markers
  map.fitBounds([NAV_ORIGIN, NAV_DEST], {{padding:[60,60], maxZoom:15, animate:true}});
}}

// ── HELPERS ──────────────────────────────────
// Use actual DB risk_level field for colors — matches what the table shows
function riskColor(risk) {{
  const r = (risk||'').toLowerCase();
  if (r === 'high')   return '#d50000';
  if (r === 'medium') return '#ff6d00';
  return '#ffd600';  // low or unknown
}}
function haversineM(lat1,lng1,lat2,lng2) {{
  const R=6371000,
        dLat=(lat2-lat1)*Math.PI/180,
        dLng=(lng2-lng1)*Math.PI/180,
        a=Math.sin(dLat/2)**2+Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLng/2)**2;
  return R*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
}}
let mapAlertTimer = null;

function addAlert(shortMsg, fullMsg, cls) {{
  // ── Bottom bar: replace current message ──
  const bar = document.getElementById('alertMsg');
  bar.className = cls;
  bar.innerHTML = fullMsg;

  // ── Map floating bubble: short message only ──
  const bubble = document.getElementById('mapAlert');
  bubble.className = 'show ' + (cls==='approaching'?'ap': cls==='entered'?'en':'lft');
  bubble.textContent = shortMsg;
  if (mapAlertTimer) clearTimeout(mapAlertTimer);
  mapAlertTimer = setTimeout(() => {{
    bubble.className = '';
  }}, 4000);
}}


// ── SEARCH RESULT PANEL ───────────────────────
function openSR(){{
  document.getElementById('srTitle').textContent=SZ_LABEL||'Search Results';
  let html='';
  SZ_DATA.forEach(z=>{{
    const col=riskColor(z.risk),lv=z.risk.toUpperCase();
    const bg=lv==='HIGH'?'rgba(213,0,0,0.15)':lv==='MEDIUM'?'rgba(255,109,0,0.15)':'rgba(255,214,0,0.12)';
    html+=`<div class="sr-row">
      <div class="sr-name" style="color:${{col}}">🚨 ${{z.area}}</div>
      <div class="sr-sub">${{z.loc}}</div>
      <span class="sr-badge" style="background:${{bg}};color:${{col}};border:1px solid ${{col}}55">${{lv}} RISK</span>
      <div class="sr-stats">
        Severity: <b style="color:${{col}}">${{z.si.toFixed(1)}}</b>
        &nbsp;·&nbsp; Accidents: <b>${{z.ta}}</b>
        &nbsp;·&nbsp; Fatalities: <b>${{z.tf}}</b>
      </div>
    </div>`;
  }});
  if(!html)html='<div style="padding:12px 14px;color:#778;font-size:0.8rem">No matching zones found.</div>';
  document.getElementById('srBody').innerHTML=html;
  document.getElementById('srPanel').className='open';
}}
function closeSR(){{document.getElementById('srPanel').className='';}}

// ── ACCIDENT ZONES ────────────────────────────
const zoneLayer=L.layerGroup().addTo(map);
ZONES.forEach(z=>{{
  const col=riskColor(z.risk);
  const isMatch=!HAS_SEARCH||SZ_IDS.has(z.id);
  const dimmed=HAS_SEARCH&&!isMatch;
  const sz=isMatch&&HAS_SEARCH?16:13;
  const pulse=isMatch&&HAS_SEARCH
    ?`animation:pulse 1.2s ease-in-out infinite;box-shadow:0 0 20px ${{col}};`
    :`box-shadow:0 0 7px ${{col}}99;`;

  L.circle([z.lat,z.lng],{{radius:APPROACH_R,color:col,fillColor:col,
    fillOpacity:dimmed?0.01:0.10,weight:dimmed?0.3:1.5,opacity:dimmed?0.10:1,dashArray:'6 4'}}).addTo(map);
  L.circle([z.lat,z.lng],{{radius:ENTER_R,color:col,fillColor:col,
    fillOpacity:dimmed?0.02:0.28,weight:dimmed?0.3:2,opacity:dimmed?0.10:1}}).addTo(map);
  L.marker([z.lat,z.lng],{{icon:L.divIcon({{
    className:'',
    html:`<div style="width:${{sz}}px;height:${{sz}}px;border-radius:50%;
              background:${{col}};border:2px solid #fff;
              opacity:${{dimmed?0.07:1}};${{pulse}}"></div>`,
    iconSize:[sz,sz],iconAnchor:[sz/2,sz/2]
  }})}})
  .bindPopup(`
    <b style="color:${{col}}">🚨 ${{z.area}}</b><br>
    <small style="color:#aaa">${{z.loc}}</small>
    <hr style="margin:5px 0;border-color:#333">
    <table style="font-size:0.8rem;width:100%;border-collapse:collapse">
      <tr><td style="color:#888;padding:2px 4px">Risk Level</td><td><b style="color:${{col}}">${{z.risk}}</b></td></tr>
      <tr><td style="color:#888;padding:2px 4px">Severity</td><td><b style="color:${{col}}">${{z.si.toFixed(1)}}</b></td></tr>
      <tr><td style="color:#888;padding:2px 4px">Accidents</td><td><b>${{z.ta}}</b></td></tr>
      <tr><td style="color:#888;padding:2px 4px">Fatalities</td><td><b>${{z.tf}}</b></td></tr>
    </table>`,{{maxWidth:240}})
  .addTo(zoneLayer);
}});


// ── DRIVER PATHS ──────────────────────────────
const pathLayer = L.layerGroup().addTo(map);
const PATH_COLORS = ['#00e5ff','#69ff47','#ff4081','#e040fb','#ffab40'];
PATHS.forEach(function(p,i) {{
  if (!p.coords || p.coords.length < 2) return;
  var col = PATH_COLORS[i % PATH_COLORS.length];
  L.polyline(p.coords, {{color:col, weight:4, opacity:0.65, dashArray:'8 5'}})
   .bindPopup('<b>Driver Path #'+p.id+'</b> — '+p.coords.length+' points')
   .addTo(pathLayer);
  L.circleMarker(p.coords[0], {{radius:7,color:'#00c853',fillColor:'#00c853',fillOpacity:1,weight:2}})
   .bindTooltip('🟢 Start').addTo(pathLayer);
  L.circleMarker(p.coords[p.coords.length-1], {{radius:7,color:'#d50000',fillColor:'#d50000',fillOpacity:1,weight:2}})
   .bindTooltip('🔴 Destination').addTo(pathLayer);
}});

// ── SEARCH: zoom to matched zones + open panel ────────────────────
if(HAS_SEARCH){{
  const mz=ZONES.filter(z=>SZ_IDS.has(z.id));
  if(mz.length>0){{
    const lats=mz.map(z=>z.lat),lngs=mz.map(z=>z.lng);
    map.fitBounds(
      [[Math.min(...lats)-0.008,Math.min(...lngs)-0.008],
       [Math.max(...lats)+0.008,Math.max(...lngs)+0.008]],
      {{padding:[40,40],maxZoom:15,animate:true,duration:0.8}}
    );
    setTimeout(openSR,900);
  }}
}}

// ── CUSTOM GOOGLE MAPS–STYLE LAYER CONTROL ────
// Remove default Leaflet control — we have our own button
var _currentBase = localStorage.getItem('riskmap_bl') || 'dark';
var _zonesVisible = true;
var _pathsVisible = true;

function toggleLayerPanel() {{
  var panel = document.getElementById('layerPanel');
  var btn   = document.getElementById('layerBtn');
  var open  = panel.classList.contains('open');
  panel.classList.toggle('open', !open);
  btn.classList.toggle('active', !open);
}}

// Close panel when clicking the map (not the overlay)
map.on('click', function() {{
  document.getElementById('layerPanel').classList.remove('open');
  document.getElementById('layerBtn').classList.remove('active');
}});
// Prevent map click from firing when clicking overlay
document.getElementById('mapOverlay').addEventListener('click', function(e) {{
  e.stopPropagation();
}});

function setBase(name) {{
  // Remove current base layer
  Object.values(BL).forEach(function(l){{ if(map.hasLayer(l)) map.removeLayer(l); }});
  BL[name].addTo(map);
  _currentBase = name;
  localStorage.setItem('riskmap_bl', name);
  // Update UI
  ['dark','light','street','satellite','topo'].forEach(function(k) {{
    var row = document.getElementById('lp-'+k);
    var rad = document.getElementById('r-'+k);
    if(row) row.classList.toggle('selected', k===name);
    if(rad) {{ rad.classList.toggle('on', k===name); }}
  }});
}}

function toggleOverlay(which) {{
  if(which==='zones') {{
    _zonesVisible = !_zonesVisible;
    _zonesVisible ? map.addLayer(zoneLayer) : map.removeLayer(zoneLayer);
    var c = document.getElementById('c-zones');
    var r = document.getElementById('ov-zones');
    c.classList.toggle('on', _zonesVisible);
    c.textContent = _zonesVisible ? '✓' : '';
    r.classList.toggle('selected', _zonesVisible);
  }} else {{
    _pathsVisible = !_pathsVisible;
    _pathsVisible ? map.addLayer(pathLayer) : map.removeLayer(pathLayer);
    var c2 = document.getElementById('c-paths');
    var r2 = document.getElementById('ov-paths');
    c2.classList.toggle('on', _pathsVisible);
    c2.textContent = _pathsVisible ? '✓' : '';
    r2.classList.toggle('selected', _pathsVisible);
  }}
}}

// Set initial base layer UI state
setBase(_currentBase);

// ── LEGEND ────────────────────────────────────
const legend=L.control({{position:'bottomleft'}});
legend.onAdd=()=>{{
  const d=L.DomUtil.create('div','legend');
  d.innerHTML=`<h4>🗺 Legend</h4>
    <span class="dot" style="background:#d50000"></span>High Risk<br>
    <span class="dot" style="background:#ff6d00"></span>Medium Risk<br>
    <span class="dot" style="background:#ffd600"></span>Low Risk<br>
    <span class="dot" style="background:#00e5ff"></span>Driver Path<br>
    <span class="dot" style="background:#1e90ff"></span>🚗 Car Trail`;
  return d;
}};
legend.addTo(map);

// ── MOVING CAR SIMULATION ─────────────────────
if (SHOW_CAR && PATHS.length > 0) {{

  var makeCarIcon = function() {{
    return L.divIcon({{
      className: 'car-icon',
      html: `<div style="
        position:relative;
        width:38px;
        height:46px;
        filter:drop-shadow(0 3px 8px rgba(0,0,0,0.5));
      ">
        <!-- Location pin shape -->
        <svg viewBox="0 0 38 46" xmlns="http://www.w3.org/2000/svg" style="position:absolute;top:0;left:0;width:38px;height:46px">
          <!-- Pin shadow -->
          <ellipse cx="19" cy="44" rx="6" ry="2.5" fill="rgba(0,0,0,0.25)"/>
          <!-- Pin body gradient -->
          <defs>
            <radialGradient id="pinGrad" cx="38%" cy="32%" r="65%">
              <stop offset="0%" stop-color="#4fc3f7"/>
              <stop offset="100%" stop-color="#0277bd"/>
            </radialGradient>
          </defs>
          <!-- Pin outline for depth -->
          <path d="M19 2 C9.6 2 2 9.6 2 19 C2 29.5 19 44 19 44 C19 44 36 29.5 36 19 C36 9.6 28.4 2 19 2Z"
                fill="url(#pinGrad)" stroke="#01579b" stroke-width="1.2"/>
          <!-- White circle inside pin -->
          <circle cx="19" cy="19" r="12" fill="white" opacity="0.95"/>
        </svg>
        <!-- Car SVG centered in pin -->
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"
          style="position:absolute;top:7px;left:7px;width:24px;height:24px">
          <path d="M5 11l1.5-4.5h11L19 11" stroke="#0277bd" stroke-width="1.2" fill="none"/>
          <rect x="3" y="11" width="18" height="7" rx="2" fill="#0277bd"/>
          <rect x="5" y="12.5" width="5" height="3.5" rx="1" fill="#e3f2fd"/>
          <rect x="14" y="12.5" width="5" height="3.5" rx="1" fill="#e3f2fd"/>
          <circle cx="7.5" cy="19.5" r="1.8" fill="#01579b" stroke="#e3f2fd" stroke-width="0.8"/>
          <circle cx="16.5" cy="19.5" r="1.8" fill="#01579b" stroke="#e3f2fd" stroke-width="0.8"/>
          <rect x="9" y="11" width="6" height="0.8" fill="#0277bd"/>
        </svg>
      </div>`,
      iconSize:   [38, 46],
      iconAnchor: [19, 44],
      popupAnchor:[0, -44]
    }});
  }};

  // Build 5m-interpolated path for smooth movement + precise zone detection
  var allC = [];
  PATHS.forEach(function(p) {{ if(p.coords) allC = allC.concat(p.coords); }});
  var ic = [];
  for (var i = 0; i < allC.length-1; i++) {{
    var la1=allC[i][0], ln1=allC[i][1], la2=allC[i+1][0], ln2=allC[i+1][1];
    var R=6371000, dL=(la2-la1)*Math.PI/180, dl=(ln2-ln1)*Math.PI/180;
    var a=Math.sin(dL/2)*Math.sin(dL/2)+Math.cos(la1*Math.PI/180)*Math.cos(la2*Math.PI/180)*Math.sin(dl/2)*Math.sin(dl/2);
    var seg=R*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
    var n=Math.max(1,Math.round(seg/5));
    for (var s=0; s<n; s++) {{ var t=s/n; ic.push([la1+(la2-la1)*t, ln1+(ln2-ln1)*t]); }}
  }}
  ic.push(allC[allC.length-1]);

  var savedIdx = parseInt(localStorage.getItem(LS_IDX)||'0', 10);
  var startIdx = (savedIdx > 0 && savedIdx < ic.length) ? savedIdx : 0;

  var trailPts = [];
  try {{ var _s=JSON.parse(localStorage.getItem(LS_TRAIL)||'[]'); if(Array.isArray(_s)) trailPts=_s; }} catch(e) {{}}

  var car   = L.marker(ic[startIdx], {{icon:makeCarIcon(), zIndexOffset:1000}}).addTo(map);
  var trail = L.polyline(trailPts, {{color:'#1e90ff', weight:3, opacity:0.7}}).addTo(map);
  var zSt   = {{}};

  function checkZones(lat, lng) {{
    // PASS 1: update all zone states, collect transition events
    var alerts = [];

    ZONES.forEach(function(z) {{
      var dist = haversineM(lat,lng,z.lat,z.lng);
      var prev = zSt[z.id]||null;
      var rl   = (z.risk||'RISK').toUpperCase();

      if (dist<=ENTER_R) {{
        if (prev!=='entered') {{
          zSt[z.id]='entered';
          alerts.push({{type:'entered', rl:rl, z:z, dist:dist}});
        }}
      }} else if (dist<=APPROACH_R) {{
        if (prev==='entered') {{
          zSt[z.id]='approaching';
          alerts.push({{type:'left_inner', rl:rl, z:z, dist:dist}});
        }} else if (prev===null) {{
          zSt[z.id]='approaching';
          alerts.push({{type:'approaching', rl:rl, z:z, dist:dist}});
        }}
      }} else {{
        if (prev==='entered'||prev==='approaching') {{
          zSt[z.id]=null;
          if (prev==='entered') alerts.push({{type:'left_all', rl:rl, z:z, dist:dist}});
        }}
      }}
    }});

    // PASS 2: pick one alert to show based on priority
    // Key rule: "Left Safe" fires when car exits the INNER ring,
    // UNLESS it is simultaneously inside (entered) a DIFFERENT zone.
    // Being in the outer approach ring of the same zone is fine — show Left.
    var nowEntered = Object.keys(zSt).some(function(id){{ return zSt[id]==='entered'; }});
    var toShow = null;

    // 1. Entered — always highest priority
    var entered = alerts.filter(function(a){{ return a.type==='entered'; }});
    if (entered.length>0) {{
      entered.sort(function(a,b){{ return b.z.si-a.z.si; }});
      toShow = entered[0];
      toShow.msg  = '🚨 Entered '+toShow.rl+' Zone: '+toShow.z.area;
      toShow.full = '🚨 <b>ENTERED '+toShow.rl+' RISK ZONE</b> — '+toShow.z.area+' | '+toShow.z.loc+' | Severity '+toShow.z.si.toFixed(1);
      toShow.cls  = 'entered';
    }}

    // 2. Approaching a new zone — only if not inside any zone
    if (!toShow) {{
      var appr = alerts.filter(function(a){{ return a.type==='approaching'; }});
      if (appr.length>0 && !nowEntered) {{
        appr.sort(function(a,b){{ return a.dist-b.dist; }});
        toShow = appr[0];
        toShow.msg  = '⚠️ Approaching '+toShow.rl+' Zone: '+toShow.z.area+' ('+Math.round(toShow.dist)+'m)';
        toShow.full = '⚠️ <b>APPROACHING '+toShow.rl+' RISK ZONE</b> — '+toShow.z.area+' | '+Math.round(toShow.dist)+'m ahead';
        toShow.cls  = 'approaching';
      }}
    }}

    // 3. Left inner ring — show "Left Safe" unless inside a DIFFERENT zone
    if (!toShow) {{
      var left = alerts.filter(function(a){{ return a.type==='left_inner'||a.type==='left_all'; }});
      if (left.length>0 && !nowEntered) {{
        toShow = left[0];
        toShow.msg  = '✅ Left '+toShow.rl+' Risk Zone — Safe: '+toShow.z.area;
        toShow.full = '✅ <b>Left '+toShow.rl+' Risk Zone — Safe</b> &nbsp;›&nbsp; '+toShow.z.area;
        toShow.cls  = 'left';
      }}
    }}

    if (toShow) addAlert(toShow.msg, toShow.full, toShow.cls);
  }}


  var idx = startIdx;
  function step() {{
    if (idx >= ic.length) {{
      car.setIcon(L.divIcon({{
        className:'car-icon',
        html:`<div style="position:relative;width:38px;height:46px;filter:drop-shadow(0 3px 8px rgba(0,0,0,0.5))">
          <svg viewBox="0 0 38 46" xmlns="http://www.w3.org/2000/svg" style="position:absolute;top:0;left:0;width:38px;height:46px">
            <ellipse cx="19" cy="44" rx="6" ry="2.5" fill="rgba(0,0,0,0.25)"/>
            <defs><radialGradient id="doneGrad" cx="38%" cy="32%" r="65%"><stop offset="0%" stop-color="#a5d6a7"/><stop offset="100%" stop-color="#2e7d32"/></radialGradient></defs>
            <path d="M19 2 C9.6 2 2 9.6 2 19 C2 29.5 19 44 19 44 C19 44 36 29.5 36 19 C36 9.6 28.4 2 19 2Z" fill="url(#doneGrad)" stroke="#1b5e20" stroke-width="1.2"/>
            <circle cx="19" cy="19" r="12" fill="white" opacity="0.95"/>
            <text x="19" y="23.5" text-anchor="middle" font-size="14">🏁</text>
          </svg>
        </div>`,
        iconSize:[38,46], iconAnchor:[19,44]
      }}));
      addAlert('🏁 Done!','🏁 <b>Simulation complete — Destination reached!</b>','safe');
      localStorage.removeItem(LS_IDX); localStorage.removeItem(LS_TRAIL);
      return;
    }}
    var lat=ic[idx][0], lng=ic[idx][1];
    car.setLatLng([lat,lng]);
    trailPts.push([lat,lng]); trail.setLatLngs(trailPts);
    if (idx%10===0) {{
      localStorage.setItem(LS_IDX, idx);
      localStorage.setItem(LS_TRAIL, JSON.stringify(trailPts.slice(-200)));
    }}
    if (!map.getBounds().contains([lat,lng]))
      map.panTo([lat,lng], {{animate:true,duration:0.4,easeLinearity:0.5}});
    checkZones(lat,lng);
    idx++;
    setTimeout(step, 80);
  }}

  if (startIdx===0) {{
    map.setView(ic[0], 14);
    addAlert('🟢 Started','🟢 <b>Simulation started — '+ic.length+' steps</b>','safe');
    setTimeout(step, 500);
  }} else {{
    map.setView(ic[startIdx], 14);
    addAlert('🟢 Resumed','🟢 <b>Resumed from step '+startIdx+' / '+ic.length+'</b>','safe');
    setTimeout(step, 100);
  }}

}} else if (!SHOW_CAR) {{
  localStorage.removeItem(LS_IDX);
  localStorage.removeItem(LS_TRAIL);
}}

</script>
</body>
</html>"""
    return html


# ─────────────────────────────────────────────
# 7. ALERT DISPLAY HELPER
# ─────────────────────────────────────────────
def alert_box(level: str, text: str):
    css  = {"SAFE":"alert-safe","LOW":"alert-safe","MEDIUM":"alert-warning","HIGH":"alert-danger"}.get(level.upper(),"alert-warning")
    icon = {"SAFE":"✅","LOW":"🟡","MEDIUM":"⚠️","HIGH":"🚨"}.get(level.upper(),"ℹ️")
    st.markdown(f'<div class="{css}">{icon} <b>{level}</b> — {text}</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────
# 8. MAIN
# ─────────────────────────────────────────────
def main():
    defaults = dict(running=False, highlight_point=None, highlight_label="",
                    search_query="", search_zones=None, risk_info=None, search_error="",
                    nav_origin="", nav_dest="", nav_origin_coord=None, nav_dest_coord=None,
                    nav_error="", nav_active=False)
    for k,v in defaults.items():
        if k not in st.session_state: st.session_state[k]=v

    # Load early so sidebar stats can use actual path waypoints
    _early_paths = load_driver_path()

    with st.sidebar:
        # ── CUSTOM SIDEBAR CSS ─────────────────────
        st.markdown("""
<style>
/* Sidebar background */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0a0e1a 0%, #0d1117 100%);
    border-right: 1px solid #1a2035;
}
section[data-testid="stSidebar"] .block-container { padding: 0 !important; }

/* Hide default streamlit padding */
section[data-testid="stSidebar"] > div { padding: 0 !important; }

/* Input styling */
section[data-testid="stSidebar"] .stTextInput input {
    background: #141929 !important;
    border: 1px solid #1e2a3e !important;
    border-radius: 8px !important;
    color: #e2e8f0 !important;
    font-size: 0.85rem !important;
    padding: 8px 12px !important;
}
section[data-testid="stSidebar"] .stTextInput input:focus {
    border-color: #4285f4 !important;
    box-shadow: 0 0 0 2px rgba(66,133,244,0.2) !important;
}
section[data-testid="stSidebar"] .stTextInput label {
    color: #4a5568 !important;
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.3px !important;
}
/* Tighten gap between inputs so dots align */
section[data-testid="stSidebar"] .stTextInput {
    margin-bottom: -6px !important;
}

/* Button styling */
section[data-testid="stSidebar"] .stButton > button {
    border-radius: 20px !important;
    font-size: 0.8rem !important;
    font-weight: 600 !important;
    border: none !important;
    transition: all 0.2s !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: linear-gradient(135deg,#4285f4,#1a73e8) !important;
    color: #fff !important;
}
section[data-testid="stSidebar"] .stButton > button:not([kind="primary"]) {
    background: #1e2535 !important;
    color: #aab !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 12px #0006 !important;
}

/* Multiselect */
section[data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"] {
    background:#141929 !important; border-color:#1e2a3e !important;
}
section[data-testid="stSidebar"] .stCheckbox label { color:#aab !important; font-size:0.82rem !important; }
</style>
""", unsafe_allow_html=True)

        # ── APP HEADER ────────────────────────────
        st.markdown("""
<div style="background:linear-gradient(135deg,#1a73e8,#0d47a1);padding:20px 20px 18px;margin-bottom:0">
  <div style="display:flex;align-items:center;gap:12px">
    <div style="background:rgba(255,255,255,0.15);border-radius:12px;padding:8px;font-size:1.4rem">🚦</div>
    <div>
      <div style="color:#fff;font-size:1.05rem;font-weight:700;line-height:1.2">Road Risk Intelligence Navigator</div>
      <div style="color:rgba(255,255,255,0.65);font-size:0.7rem">Accident zone risk analysis · Mumbai</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

        # ── ROUTE NAVIGATION SECTION ──────────────
        st.markdown("""
<div style="padding:14px 16px 6px">
  <div style="font-size:0.82rem;font-weight:800;letter-spacing:0.5px;color:#4285f4;
              display:flex;align-items:center;gap:6px">
    <span style="font-size:1rem">🧭</span> ROUTE NAVIGATION
  </div>
</div>
""", unsafe_allow_html=True)

        # Marker A: blue dot label
        st.markdown("""
<div id="nav-origin-label" style="display:flex;align-items:center;gap:8px;
     margin:4px 0 2px;padding:0 4px">
  <svg width="18" height="18" viewBox="0 0 18 18">
    <circle cx="9" cy="9" r="6" fill="#4285f4"/>
    <circle cx="9" cy="9" r="6" fill="none" stroke="white" stroke-width="2"/>
    <circle cx="9" cy="9" r="9" fill="#4285f4" fill-opacity="0.18"/>
  </svg>
  <span style="font-size:0.72rem;font-weight:700;color:#6b7a8d;letter-spacing:.5px;text-transform:uppercase">Start</span>
</div>
""", unsafe_allow_html=True)

        origin_input = st.text_input("origin", key="nav_origin_input",
            value=st.session_state.nav_origin,
            placeholder="Start location",
            label_visibility="collapsed")

        # Gradient connector line
        st.markdown("""
<div style="display:flex;align-items:center;gap:8px;margin:0 0 0 4px;height:16px">
  <div style="width:18px;display:flex;justify-content:center">
    <div style="width:2px;height:16px;background:linear-gradient(to bottom,#4285f4,#ea4335);border-radius:2px"></div>
  </div>
</div>
""", unsafe_allow_html=True)

        # Marker B: red pin label
        st.markdown("""
<div id="nav-dest-label" style="display:flex;align-items:center;gap:8px;
     margin:2px 0 2px;padding:0 4px">
  <svg width="18" height="22" viewBox="0 0 18 22">
    <path d="M9 1 C5.1 1 2 4.1 2 8 C2 13.3 9 21 9 21 C9 21 16 13.3 16 8 C16 4.1 12.9 1 9 1Z"
          fill="#ea4335" stroke="#c62828" stroke-width="0.8"/>
    <circle cx="9" cy="8" r="3" fill="white"/>
  </svg>
  <span style="font-size:0.72rem;font-weight:700;color:#6b7a8d;letter-spacing:.5px;text-transform:uppercase">Destination</span>
</div>
""", unsafe_allow_html=True)

        dest_input = st.text_input("destination", key="nav_dest_input",
            value=st.session_state.nav_dest,
            placeholder="Destination",
            label_visibility="collapsed")

        nc1, nc2 = st.columns([3,2])
        with nc1: nav_btn   = st.button("🗺️ Get Directions", use_container_width=True, type="primary")
        with nc2: nav_clear = st.button("✕ Clear", use_container_width=True)

        if st.session_state.nav_active:
            st.markdown(f"""
<div style="margin:4px 0 2px;background:#0a1628;border:1px solid #1a3a6e;
            border-radius:10px;padding:10px 14px;font-size:0.78rem">
  <div style="color:#4285f4;font-weight:700;font-size:0.68rem;margin-bottom:6px;
              letter-spacing:.5px">✅ ACTIVE ROUTE</div>
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
    <svg width="14" height="14" viewBox="0 0 14 14" style="flex-shrink:0">
      <circle cx="7" cy="7" r="5.5" fill="#34a853" stroke="#fff" stroke-width="1.5"/>
      <circle cx="7" cy="7" r="2.2" fill="#fff"/>
    </svg>
    <div style="color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:0.78rem">
      {st.session_state.nav_origin}</div>
  </div>
  <div style="width:2px;height:10px;background:#1e3a5e;margin:0 0 4px 6px"></div>
  <div style="display:flex;align-items:center;gap:8px">
    <svg width="14" height="17" viewBox="0 0 14 17" style="flex-shrink:0">
      <path d="M7 1C4.24 1 2 3.24 2 6c0 3.75 5 10 5 10s5-6.25 5-10c0-2.76-2.24-5-5-5z"
            fill="#ea4335" stroke="#fff" stroke-width="1.2"/>
      <circle cx="7" cy="6" r="2" fill="#fff"/>
    </svg>
    <div style="color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:0.78rem">
      {st.session_state.nav_dest}</div>
  </div>
</div>""", unsafe_allow_html=True)

        if st.session_state.nav_error:
            st.markdown(f"""
<div style="margin:4px 0;background:#2a0a0a;border:1px solid #d5000044;border-radius:8px;
            padding:8px 12px;font-size:0.75rem;color:#ff6b6b">⚠️ {st.session_state.nav_error}
</div>""", unsafe_allow_html=True)

        # ── DIVIDER ───────────────────────────────
        st.markdown('<div style="height:1px;background:#1a2035;margin:12px 0"></div>', unsafe_allow_html=True)

        # ── SIMULATION CONTROLS ───────────────────
        st.markdown("""
<div style="padding:4px 16px 8px">
  <div style="font-size:0.82rem;font-weight:800;letter-spacing:0.5px;color:#4285f4;
              display:flex;align-items:center;gap:6px">
    <span style="font-size:1rem">🚗</span> ROUTE SIMULATION
  </div>
  <div style="font-size:0.68rem;color:#556;margin-top:2px">Replay car through historical accident data</div>
</div>
""", unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        with c1: start_btn = st.button("▶ Start", use_container_width=True, type="primary")
        with c2: stop_btn  = st.button("⏹ Stop",  use_container_width=True)

        if st.session_state.running:
            st.markdown("""
<div style="margin:6px 0 2px;background:#0a1f0a;border:1px solid #00c85344;border-radius:8px;
            padding:8px 12px;display:flex;align-items:center;gap:8px;font-size:0.78rem">
  <div style="width:8px;height:8px;border-radius:50%;background:#00c853;
              animation:blink 1s ease-in-out infinite;flex-shrink:0"></div>
  <span style="color:#00c853;font-weight:600">Simulation running</span>
</div>
<style>@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}</style>
""", unsafe_allow_html=True)
        else:
            st.markdown("""
<div style="margin:6px 0 2px;background:#111827;border:1px solid #1e2535;border-radius:8px;
            padding:8px 12px;font-size:0.78rem;color:#556;text-align:center">
  Press <b style="color:#aab">Start</b> to begin simulation
</div>""", unsafe_allow_html=True)

        # ── DIVIDER ───────────────────────────────
        st.markdown('<div style="height:1px;background:#1a2035;margin:12px 0"></div>', unsafe_allow_html=True)

        # ── ZONE SEARCH ───────────────────────────
        st.markdown("""
<div style="padding:4px 16px 8px">
  <div style="font-size:0.82rem;font-weight:800;letter-spacing:0.5px;color:#4285f4;
              display:flex;align-items:center;gap:6px">
    <span style="font-size:1rem">🔍</span> SEARCH ACCIDENT ZONES
  </div>
  <div style="font-size:0.68rem;color:#556;margin-top:2px">Find zones by area name or coordinates</div>
</div>
""", unsafe_allow_html=True)

        address_input = st.text_input("Search accident zones",
            placeholder="e.g. Deonar | Andheri | 19.076, 72.877")
        search_btn = st.button("Search & Check Risk", use_container_width=True, type="primary")
        if st.session_state.highlight_point:
            if st.button("✕ Clear Search", use_container_width=True):
                for k in ["highlight_point","search_zones","risk_info","highlight_label","search_query","search_error"]:
                    st.session_state[k] = None if k in ["highlight_point","search_zones","risk_info"] else ""

        # ── DIVIDER ───────────────────────────────
        st.markdown('<div style="height:1px;background:#1a2035;margin:12px 0"></div>', unsafe_allow_html=True)

        # ── MAP LAYERS ────────────────────────────
        st.markdown("""
<div style="padding:4px 16px 8px">
  <div style="font-size:0.82rem;font-weight:800;letter-spacing:0.5px;color:#4285f4;
              display:flex;align-items:center;gap:6px">
    <span style="font-size:1rem">🗺️</span> MAP LAYERS
  </div>
  <div style="font-size:0.68rem;color:#556;margin-top:2px">Filter zones by risk level</div>
</div>
""", unsafe_allow_html=True)

        risk_filter = st.multiselect("Risk Levels", ["High","Medium","Low"],
                                     default=["High","Medium","Low"], label_visibility="collapsed")
        show_paths = st.checkbox("🛣️ Driver Paths",   value=True)
        show_zones = st.checkbox("🚨 Accident Zones", value=True)

        # ── DIVIDER ───────────────────────────────
        st.markdown('<div style="height:1px;background:#1a2035;margin:12px 0"></div>', unsafe_allow_html=True)


        # ── LIVE STATS ────────────────────────────
        _df_tmp = load_accident_data()

        # If a route is active, filter zones that lie along the route bounding box
        # (between origin and destination, with a small buffer)
        _nav_o = st.session_state.nav_origin_coord
        _nav_d = st.session_state.nav_dest_coord
        _route_active = st.session_state.nav_active and _nav_o and _nav_d

        if _route_active:
            import math

            def _pt_seg_dist_m(plat, plng, alat, alng, blat, blng):
                """Metres from point P to segment A→B using equirectangular approx."""
                R = 6371000
                clat = math.cos(math.radians((alat+blat)/2))
                ax = math.radians(alng)*R*clat; ay = math.radians(alat)*R
                bx = math.radians(blng)*R*clat; by = math.radians(blat)*R
                px = math.radians(plng)*R*clat; py = math.radians(plat)*R
                abx,aby = bx-ax,by-ay
                ab2 = abx*abx+aby*aby
                if ab2==0: return math.hypot(px-ax,py-ay)
                t = max(0,min(1,((px-ax)*abx+(py-ay)*aby)/ab2))
                return math.hypot(px-(ax+t*abx), py-(ay+t*aby))

            def _zone_on_path(zlat, zlng, path_coords, threshold=600):
                """True if zone is within threshold metres of ANY segment of the path."""
                for i in range(len(path_coords)-1):
                    a,b = path_coords[i], path_coords[i+1]
                    if _pt_seg_dist_m(zlat,zlng,a[0],a[1],b[0],b[1]) <= threshold:
                        return True
                return False

            # Get actual driver path waypoints
            _path_coords = []
            for p in _early_paths:
                _path_coords.extend(p.get("coordinates", []))

            # Fallback: if no path coords, use straight line origin→dest
            if len(_path_coords) < 2:
                _path_coords = [_nav_o, _nav_d]

            _mask = _df_tmp.apply(
                lambda r: _zone_on_path(r["latitude"], r["longitude"], _path_coords),
                axis=1
            )
            _stats_df = _df_tmp[_mask]
            _stats_label = "ON YOUR ROUTE"
            _label_color = "#4285f4"
        else:
            _stats_df    = _df_tmp
            _stats_label = "ALL ZONES"
            _label_color = "#556"

        _high  = int((_stats_df["risk_level"]=="High").sum())
        _med   = int((_stats_df["risk_level"]=="Medium").sum())
        _low   = int((_stats_df["risk_level"]=="Low").sum())
        _total = len(_stats_df)

        _route_tag = f'<span style="font-size:0.62rem;color:{_label_color};font-weight:600;letter-spacing:.5px">{_stats_label}</span>' if _route_active else ""

        st.markdown(f"""
<div style="padding:4px 16px 6px;display:flex;align-items:center;justify-content:space-between">
  <div>
    <div style="font-size:0.82rem;font-weight:800;letter-spacing:0.5px;color:#4285f4;
                display:flex;align-items:center;gap:6px">
      <span style="font-size:1rem">📊</span> ACCIDENT ZONE STATS
    </div>
    <div style="font-size:0.68rem;color:#556;margin-top:2px">Based on recorded historical data</div>
  </div>
  {_route_tag}
</div>
<div style="margin:0 0 10px;display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:0.76rem">
  <div style="background:#1a0a0a;border:1px solid #d5000033;border-radius:8px;padding:8px 10px;text-align:center">
    <div style="color:#d50000;font-size:1.3rem;font-weight:800">{_high}</div>
    <div style="color:#778;font-size:0.67rem">High Risk</div>
  </div>
  <div style="background:#1a100a;border:1px solid #ff6d0033;border-radius:8px;padding:8px 10px;text-align:center">
    <div style="color:#ff6d00;font-size:1.3rem;font-weight:800">{_med}</div>
    <div style="color:#778;font-size:0.67rem">Medium Risk</div>
  </div>
  <div style="background:#1a1a0a;border:1px solid #ffd60033;border-radius:8px;padding:8px 10px;text-align:center">
    <div style="color:#ffd600;font-size:1.3rem;font-weight:800">{_low}</div>
    <div style="color:#778;font-size:0.67rem">Low Risk</div>
  </div>
  <div style="background:#0a0f1a;border:1px solid #4285f433;border-radius:8px;padding:8px 10px;text-align:center">
    <div style="color:#4285f4;font-size:1.3rem;font-weight:800">{_total}</div>
    <div style="color:#778;font-size:0.67rem">Total Zones</div>
  </div>
</div>
""", unsafe_allow_html=True)

        if _route_active:
            st.markdown(f"""
<div style="margin:-4px 0 10px;background:#0a0f1a;border:1px solid #1e2a3e;
            border-radius:8px;padding:7px 12px;font-size:0.72rem;
            color:#4285f4;text-align:center">
  Zones between<br>
  <b style="color:#e2e8f0">{st.session_state.nav_origin}</b>
  <span style="color:#556"> → </span>
  <b style="color:#e2e8f0">{st.session_state.nav_dest}</b>
</div>""", unsafe_allow_html=True)

        # ── FOOTER ────────────────────────────────
        st.markdown("""
<div style="padding:10px 16px;text-align:center;font-size:0.67rem;color:#334">
  Powered by Supabase · Leaflet · OSM · Historical Data
</div>
""", unsafe_allow_html=True)

    # Start/Stop — only these change show_car
    if start_btn:
        st.session_state.running = True
        st.rerun()
    if stop_btn:
        st.session_state.running = False
        st.rerun()

    # ── NAV ROUTE SET ──────────────────────────
    if nav_clear:
        st.session_state.nav_origin       = ""
        st.session_state.nav_dest         = ""
        st.session_state.nav_origin_coord = None
        st.session_state.nav_dest_coord   = None
        st.session_state.nav_error        = ""
        st.session_state.nav_active       = False
        st.rerun()

    if nav_btn and origin_input and dest_input:
        accident_df_tmp = load_accident_data()
        o = resolve_location(origin_input.strip(), accident_df_tmp)
        d = resolve_location(dest_input.strip(),   accident_df_tmp)
        if not o:
            st.session_state.nav_error = f"❌ Could not find start location: '{origin_input}'"
        elif not d:
            st.session_state.nav_error = f"❌ Could not find destination: '{dest_input}'"
        else:
            st.session_state.nav_origin       = origin_input.strip()
            st.session_state.nav_dest         = dest_input.strip()
            st.session_state.nav_origin_coord = [o[0], o[1]]
            st.session_state.nav_dest_coord   = [d[0], d[1]]
            st.session_state.nav_error        = ""
            st.session_state.nav_active       = True
            # Clear saved car position so it restarts from new origin
            st.rerun()

    accident_df  = load_accident_data()
    driver_paths = load_driver_path()

    # SEARCH — stores in session_state, car resumes via localStorage on rebuild
    if search_btn and address_input:
        result = resolve_location(address_input.strip(), accident_df)
        if result:
            lat, lon, label = result
            st.session_state.highlight_point = (lat, lon)
            st.session_state.highlight_label = label
            st.session_state.search_query    = address_input.strip()
            ql = address_input.strip().lower()
            matched = [
                {"id":int(r["id"]),"lat":float(r["latitude"]),"lng":float(r["longitude"]),
                 "area":str(r.get("area","")),"loc":str(r.get("location","")),
                 "location":str(r.get("location","")),"city":str(r.get("city","")),
                 "si":float(r.get("severity_index",0)),"severity_index":float(r.get("severity_index",0)),
                 "risk":str(r.get("risk_level","Low")),"risk_level":str(r.get("risk_level","Low")),
                 "ta":int(r.get("total_accident",0)),"total_accident":int(r.get("total_accident",0)),
                 "tf":int(r.get("total_fatality",0)),"total_fatality":int(r.get("total_fatality",0))}
                for _,r in accident_df.iterrows()
                if ql == str(r.get("area","")).lower().strip()
            ]
            st.session_state.search_zones = matched if matched else None
            st.session_state.risk_info = {
                "level": "HIGH" if any(z["risk"].lower()=="high" for z in matched) else
                         "MEDIUM" if any(z["risk"].lower()=="medium" for z in matched) else "LOW",
                "zones": matched,
                "message": f"{len(matched)} exact zone(s) found for '{address_input.strip()}'"
            } if matched else None
            st.session_state.search_error = ""
        else:
            st.session_state.highlight_point = None
            st.session_state.search_zones    = None
            st.session_state.risk_info       = None
            st.session_state.search_error    = f"Location not found for '{address_input}'."

    filtered_df   = accident_df[accident_df["risk_level"].isin(risk_filter)] if risk_filter else accident_df
    display_df    = filtered_df if show_zones else accident_df.iloc[0:0]
    display_paths = driver_paths if show_paths else []

    st.title("🚦 Road Risk Intelligence Navigator")
    k1,k2,k3,k4,k5 = st.columns(5)
    k1.metric("Total Hotspots",  len(accident_df))
    k2.metric("High Risk Zones", int((accident_df["risk_level"]=="High").sum()))
    k3.metric("Total Accidents", int(accident_df["total_accident"].sum()))
    k4.metric("Total Fatalities",int(accident_df["total_fatality"].sum()))
    k5.metric("Driver Paths",    len(driver_paths))
    st.divider()

    if st.session_state.search_error:
        st.error(st.session_state.search_error)

    if st.session_state.highlight_point and st.session_state.search_zones:
        ri = st.session_state.risk_info
        matched_zones = st.session_state.search_zones
        st.subheader("📊 Risk Assessment")
        st.caption(f"**{st.session_state.highlight_label}**")
        if ri:
            alert_box(ri["level"], ri["message"])
        # Build display df safely using .get() — works regardless of which key names exist
        near_df = pd.DataFrame([{
            "Area":           z.get("area", ""),
            "Location":       z.get("location", z.get("loc", "")),
            "Risk Level":     z.get("risk_level", z.get("risk", "")),
            "Severity Index": z.get("severity_index", z.get("si", 0)),
            "Accidents":      z.get("total_accident", z.get("ta", 0)),
            "Fatalities":     z.get("total_fatality", z.get("tf", 0)),
        } for z in matched_zones]).sort_values("Severity Index", ascending=False)
        st.dataframe(near_df, use_container_width=True, hide_index=True)

    st.subheader("🗺️ Accident Risk Map  •  🚗 Route Simulation")
    if st.session_state.nav_active:
        st.caption(f"🟢 **{st.session_state.nav_origin}** → 🔴 **{st.session_state.nav_dest}**")
    else:
        st.caption("Car simulates a route through historical accident zones. Alert bar shows zone warnings based on recorded data.")

    # Center on route midpoint if nav is active
    if st.session_state.nav_active and st.session_state.nav_origin_coord and st.session_state.nav_dest_coord:
        o, d = st.session_state.nav_origin_coord, st.session_state.nav_dest_coord
        center_lat = (o[0] + d[0]) / 2
        center_lng = (o[1] + d[1]) / 2
    else:
        center_lat = float(display_df["latitude"].mean())  if not display_df.empty else 19.076
        center_lng = float(display_df["longitude"].mean()) if not display_df.empty else 72.877

    map_html = build_leaflet_map(
        accident_df       = display_df,
        driver_paths      = display_paths,
        center_lat        = center_lat,
        center_lng        = center_lng,
        search_zones      = st.session_state.search_zones,
        search_label      = st.session_state.highlight_label,
        nav_origin_coord  = st.session_state.nav_origin_coord,
        nav_dest_coord    = st.session_state.nav_dest_coord,
        nav_origin_name   = st.session_state.nav_origin,
        nav_dest_name     = st.session_state.nav_dest,
        show_car          = st.session_state.running,
    )
    components.html(map_html, height=700, scrolling=False)

    with st.expander("📋 Accident Zone Data Table", expanded=False):
        st.dataframe(
            filtered_df[["id","city","area","location","risk_level",
                         "severity_index","total_accident","total_fatality",
                         "latitude","longitude"]].sort_values("severity_index",ascending=False),
            use_container_width=True, hide_index=True
        )

    st.markdown("""
<div class="footer-bar">
    🚦 <strong>Road Risk Intelligence Navigator</strong> &nbsp;|&nbsp;
    Data: Supabase PostgreSQL &nbsp;|&nbsp;
    Map: Leaflet.js &nbsp;|&nbsp;
    Geocoding: Nominatim (OpenStreetMap) &nbsp;|&nbsp;
    <em>For road safety awareness only.</em>
</div>""", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
