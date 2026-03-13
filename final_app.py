"""
Road Accident Risk Navigation Dashboard
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
    page_title="Road Risk Navigator",
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
            all_paths.append({
                "id": row.get("id"),
                "created_at": str(row.get("created_at", "")),
                "coordinates": coords
            })
    return all_paths

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

import re

# ─────────────────────────────────────────────
# 4. GEOCODER + SMART RESOLVER
# ─────────────────────────────────────────────
@st.cache_data(ttl=600, show_spinner="Geocoding...")
def _nominatim(address: str):
    try:
        geo = Nominatim(user_agent="road_risk_nav_v5", timeout=10)
        r   = geo.geocode(address)
        return (r.latitude, r.longitude, str(r.address[:70])) if r else None
    except Exception:
        return None

def resolve_location(query: str, accident_df: pd.DataFrame):
    q = query.strip()
    m = re.match(r'^([+-]?\d{1,3}(?:\.\d+)?)\s*[,\s]\s*([+-]?\d{1,3}(?:\.\d+)?)$', q)
    if m:
        la, ln = float(m.group(1)), float(m.group(2))
        if -90 <= la <= 90 and -180 <= ln <= 180:
            return la, ln, f"{la:.5f}, {ln:.5f}"
    ql = q.lower()
    for _, row in accident_df.iterrows():
        if (ql in str(row.get("area","")).lower() or
            ql in str(row.get("location","")).lower() or
            ql in str(row.get("city","")).lower()):
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
                       search_zones: list = None,   # matched zone dicts [{id,area,loc,si,risk,ta,tf,lat,lng}]
                       search_label: str = "",
                       show_car: bool = True) -> str:

    zones_js    = json.dumps([
        {"id":int(r["id"]),"lat":float(r["latitude"]),"lng":float(r["longitude"]),
         "area":str(r.get("area","")),"loc":str(r.get("location","")),"city":str(r.get("city","")),
         "si":float(r.get("severity_index",0)),"risk":str(r.get("risk_level","Low")),
         "ta":int(r.get("total_accident",0)),"tf":int(r.get("total_fatality",0))}
        for _,r in accident_df.iterrows()
    ])
    paths_js    = json.dumps([{"id":p["id"],"coords":p["coordinates"]} for p in driver_paths])
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
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body,html{{height:100%;background:#0e1117;font-family:'Segoe UI',sans-serif;}}
  #wrapper{{display:flex;flex-direction:column;height:100vh;}}
  #map{{flex:1;min-height:0;position:relative;overflow:hidden;}}

  /* ── alert bar ── */
  #alertFeed{{background:#0d1117;border-top:2px solid #1e90ff33;height:52px;
              display:flex;align-items:center;flex-shrink:0;padding:0 10px;overflow:hidden;}}
  #alertMsg{{width:100%;padding:8px 14px;border-radius:6px;font-size:0.82rem;font-weight:600;
             color:#fff;border-left:4px solid #00c853;background:#0d2a0d;
             white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
             transition:background 0.2s,border-color 0.2s;}}
  #alertMsg.approaching{{background:#0d1f2d;border-left-color:#00e5ff;}}
  #alertMsg.entered    {{background:#2a0000;border-left-color:#d50000;}}
  #alertMsg.left       {{background:#0d2a0d;border-left-color:#00c853;}}
  #alertMsg.safe       {{background:#0d2a0d;border-left-color:#00c853;}}

  /* ── floating bubble ── */
  #mapAlert{{position:absolute;top:14px;left:50%;transform:translateX(-50%);z-index:1001;
             padding:7px 22px;border-radius:20px;font-size:0.82rem;font-weight:700;color:#fff;
             pointer-events:none;opacity:0;transition:opacity 0.2s;white-space:nowrap;
             box-shadow:0 2px 14px #0009;}}
  #mapAlert.show{{opacity:1;}}
  #mapAlert.ap{{background:rgba(0,60,100,0.93);border:1.5px solid #00e5ff;}}
  #mapAlert.en{{background:rgba(120,0,0,0.95);border:1.5px solid #ff3333;}}
  #mapAlert.lft{{background:rgba(0,70,20,0.93);border:1.5px solid #00c853;}}

  /* ── SEARCH RESULT PANEL — floats inside map, bottom-left ── */
  #srPanel{{
    position:absolute;bottom:62px;left:12px;z-index:1002;
    width:300px;max-height:380px;
    background:rgba(10,13,20,0.97);
    border:1px solid #2c3a5a;border-radius:12px;
    box-shadow:0 6px 30px #000d;overflow:hidden;
    display:none;flex-direction:column;
  }}
  #srPanel.open{{display:flex;animation:fadeUp 0.25s ease;}}
  #srHead{{
    padding:10px 14px 8px;background:rgba(22,32,55,0.99);
    border-bottom:1px solid #1e2c44;
    display:flex;align-items:center;justify-content:space-between;flex-shrink:0;
  }}
  #srHead h3{{margin:0;font-size:0.88rem;color:#e2e8f0;font-weight:700;}}
  #srClose{{background:none;border:none;color:#556;font-size:1.1rem;cursor:pointer;padding:0 2px;}}
  #srClose:hover{{color:#ccc;}}
  #srScroll{{overflow-y:auto;max-height:310px;}}
  .sr-row{{padding:9px 14px;border-bottom:1px solid #131b2c;font-size:0.78rem;}}
  .sr-row:last-child{{border-bottom:none;}}
  .sr-name{{font-size:0.85rem;font-weight:700;margin-bottom:1px;}}
  .sr-sub{{color:#7a8fa8;font-size:0.72rem;margin-bottom:4px;}}
  .sr-badge{{display:inline-block;padding:1px 8px;border-radius:8px;font-size:0.69rem;
              font-weight:800;letter-spacing:.3px;margin-right:4px;}}
  .sr-stats{{font-size:0.74rem;color:#9ab;margin-top:3px;}}
  .sr-stats b{{color:#cde;}}

  .legend{{background:rgba(14,17,23,0.93);color:#eee;padding:10px 14px;border-radius:8px;
           font-size:0.76rem;line-height:1.8;box-shadow:0 2px 10px #0006;}}
  .legend h4{{margin:0 0 5px;font-size:0.82rem;border-bottom:1px solid #333;padding-bottom:3px;}}
  .dot{{width:11px;height:11px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:middle;}}
  @keyframes fadeUp{{from{{opacity:0;transform:translateY(8px);}}to{{opacity:1;transform:translateY(0);}}}}
  @keyframes pulse{{0%,100%{{transform:scale(1);opacity:1;}}50%{{transform:scale(1.6);opacity:0.6;}}}}
</style>
</head>
<body>
<div id="wrapper">
  <div id="map">
    <div id="mapAlert"></div>
    <!-- Search result panel — lives INSIDE iframe, never triggers Streamlit rerender -->
    <div id="srPanel">
      <div id="srHead">
        <h3 id="srTitle">📍 Search Results</h3>
        <button id="srClose" onclick="closeSR()">✕</button>
      </div>
      <div id="srScroll"><div id="srBody"></div></div>
    </div>
  </div>
  <div id="alertFeed">
    <div id="alertMsg" class="safe">🟢 &nbsp; Press <b>Start</b> in the sidebar to begin car simulation…</div>
  </div>
</div>

<script>
const ZONES     = {zones_js};
const PATHS     = {paths_js};
const SZ_IDS    = new Set({sz_ids_js});   // IDs of zones matching the search
const SZ_DATA   = {sz_data_js};           // full zone objects for the panel
const SZ_LABEL  = {sz_label_js};
const SHOW_CAR  = {show_car_js};
const APPROACH_R = 500;
const ENTER_R    = 120;
const HAS_SEARCH = SZ_IDS.size > 0;

// ── MAP ─────────────────────────────────────
const map = L.map('map',{{zoomControl:true,preferCanvas:false}})
              .setView([{center_lat},{center_lng}],13);

// ── 5 BASE LAYERS + localStorage ─────────────
const BL = {{
  dark     : L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',   {{attribution:'&copy; OSM &copy; CartoDB',subdomains:'abcd',maxZoom:19}}),
  light    : L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png',  {{attribution:'&copy; OSM &copy; CartoDB',subdomains:'abcd',maxZoom:19}}),
  street   : L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',                {{attribution:'&copy; OpenStreetMap contributors',maxZoom:19}}),
  satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',{{attribution:'&copy; Esri',maxZoom:19}}),
  topo     : L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',                  {{attribution:'&copy; OSM &copy; OpenTopoMap',subdomains:'abc',maxZoom:17}}),
}};
BL[localStorage.getItem('riskmap_bl')||'dark'].addTo(map);

// ── HELPERS ──────────────────────────────────
const siColor = si => si>25?'#d50000':si>10?'#ff6d00':'#ffd600';
const siLabel = si => si>25?'HIGH':si>10?'MEDIUM':'LOW';
function hav(la1,ln1,la2,ln2){{
  const R=6371000,dL=(la2-la1)*Math.PI/180,dl=(ln2-ln1)*Math.PI/180,
        a=Math.sin(dL/2)**2+Math.cos(la1*Math.PI/180)*Math.cos(la2*Math.PI/180)*Math.sin(dl/2)**2;
  return R*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
}}

// ── ALERT — synchronous DOM write, zero lag ──
let _bt=null;
function addAlert(short,full,cls){{
  const bar=document.getElementById('alertMsg');
  bar.className=cls; bar.innerHTML=full;
  const bub=document.getElementById('mapAlert');
  bub.className='show '+(cls==='approaching'?'ap':cls==='entered'?'en':'lft');
  bub.textContent=short;
  if(_bt)clearTimeout(_bt);
  _bt=setTimeout(()=>bub.className='',4000);
}}

// ── SEARCH RESULT PANEL ───────────────────────
function openSR(){{
  document.getElementById('srTitle').textContent = SZ_LABEL || 'Search Results';
  let html='';
  SZ_DATA.forEach(z=>{{
    const col=siColor(z.si), lv=siLabel(z.si);
    const bg = z.si>25?'rgba(213,0,0,0.15)':z.si>10?'rgba(255,109,0,0.15)':'rgba(255,214,0,0.12)';
    html += `<div class="sr-row">
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
  if(!html) html='<div style="padding:12px 14px;color:#778;font-size:0.8rem">No matching zones found in database.</div>';
  document.getElementById('srBody').innerHTML=html;
  document.getElementById('srPanel').className='open';
}}
function closeSR(){{document.getElementById('srPanel').className='';}}

// ── ACCIDENT ZONES ────────────────────────────
const zoneLayer=L.layerGroup().addTo(map);
ZONES.forEach(z=>{{
  const col=siColor(z.si);
  const isMatch = !HAS_SEARCH || SZ_IDS.has(z.id);
  const dimmed  = HAS_SEARCH && !isMatch;
  const sz      = isMatch && HAS_SEARCH ? 16 : 13;
  const pulse   = isMatch && HAS_SEARCH
    ? `animation:pulse 1.2s ease-in-out infinite;box-shadow:0 0 20px ${{col}};`
    : `box-shadow:0 0 7px ${{col}}99;`;

  // outer approach ring
  L.circle([z.lat,z.lng],{{
    radius:APPROACH_R,color:col,fillColor:col,
    fillOpacity:dimmed?0.01:0.10,weight:dimmed?0.3:1.5,
    opacity:dimmed?0.10:1,dashArray:'6 4'
  }}).addTo(map);
  // inner entered ring
  L.circle([z.lat,z.lng],{{
    radius:ENTER_R,color:col,fillColor:col,
    fillOpacity:dimmed?0.02:0.28,weight:dimmed?0.3:2,
    opacity:dimmed?0.10:1
  }}).addTo(map);
  // dot marker
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
      <tr><td style="color:#888;padding:2px 4px">Severity</td><td><b style="color:${{col}}">${{z.si.toFixed(1)}}</b></td></tr>
      <tr><td style="color:#888;padding:2px 4px">Risk</td><td><b style="color:${{col}}">${{z.risk}}</b></td></tr>
      <tr><td style="color:#888;padding:2px 4px">Accidents</td><td><b>${{z.ta}}</b></td></tr>
      <tr><td style="color:#888;padding:2px 4px">Fatalities</td><td><b>${{z.tf}}</b></td></tr>
    </table>
  `,{{maxWidth:240}})
  .addTo(zoneLayer);
}});

// ── ZOOM + PANEL on search ────────────────────
if(HAS_SEARCH){{
  const mz = ZONES.filter(z=>SZ_IDS.has(z.id));
  if(mz.length>0){{
    const lats=mz.map(z=>z.lat), lngs=mz.map(z=>z.lng);
    map.fitBounds(
      [[Math.min(...lats)-0.008,Math.min(...lngs)-0.008],
       [Math.max(...lats)+0.008,Math.max(...lngs)+0.008]],
      {{padding:[40,40],maxZoom:15,animate:true,duration:0.8}}
    );
    setTimeout(openSR, 900);
  }}
}}

// ── DRIVER PATHS ─────────────────────────────
const pathLayer=L.layerGroup().addTo(map);
['#00e5ff','#69ff47','#ff4081','#e040fb','#ffab40'].forEach((col,i)=>{{
  const p=PATHS[i]; if(!p||!p.coords||p.coords.length<2)return;
  L.polyline(p.coords,{{color:col,weight:4,opacity:0.55,dashArray:'8 5'}})
   .bindPopup(`<b>Driver Path #${{p.id}}</b>`).addTo(pathLayer);
  L.circleMarker(p.coords[p.coords.length-1],{{
    radius:7,color:'#d50000',fillColor:'#d50000',fillOpacity:1,weight:2
  }}).bindTooltip('🔴 Destination').addTo(pathLayer);
}});

// ── LAYER CONTROL ─────────────────────────────
L.control.layers(
  {{'🌑 Dark':BL.dark,'☀️ Light':BL.light,'🗺️ Street':BL.street,'🛰️ Satellite':BL.satellite,'🏔️ Topo':BL.topo}},
  {{'🚨 Accident Zones':zoneLayer,'🛣️ Driver Paths':pathLayer}},
  {{collapsed:false,position:'topright'}}
).addTo(map);
map.on('baselayerchange',e=>{{
  const k={{'🌑 Dark':'dark','☀️ Light':'light','🗺️ Street':'street','🛰️ Satellite':'satellite','🏔️ Topo':'topo'}}[e.name];
  if(k)localStorage.setItem('riskmap_bl',k);
}});

// ── LEGEND ────────────────────────────────────
const legend=L.control({{position:'bottomright'}});
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

// ── CAR SIMULATION ────────────────────────────
// SHOW_CAR is baked in at build time.
// The map is wrapped in @st.fragment in main() — search never triggers
// a fragment re-run so the iframe is never rebuilt and the car keeps running.
if(SHOW_CAR&&PATHS.length>0){{
  const makeCarIcon=()=>L.divIcon({{
    className:'car-icon',
    html:`<span style="font-size:22px;line-height:1;display:block;filter:drop-shadow(0 0 6px #1e90ff);">🚗</span>`,
    iconSize:[24,24],iconAnchor:[12,12],popupAnchor:[0,-14]
  }});
  const car=L.marker(PATHS[0].coords[0],{{icon:makeCarIcon(),zIndexOffset:1000}}).addTo(map);
  const trailPts=[];
  const trail=L.polyline([],{{color:'#1e90ff',weight:3,opacity:0.7}}).addTo(map);
  const zSt={{}};

  // checkZones is synchronous — called in same JS call-stack frame as setLatLng.
  // 5m interpolation ensures car never jumps over a boundary.
  function checkZones(lat,lng){{
    ZONES.forEach(z=>{{
      const d=hav(lat,lng,z.lat,z.lng), prev=zSt[z.id]||null, rl=(z.risk||'RISK').toUpperCase();
      if(d<=ENTER_R){{
        if(prev!=='entered'){{
          zSt[z.id]='entered';
          addAlert(`🚨 Entered ${{rl}} Zone: ${{z.area}}`,
            `🚨 <b>ENTERED ${{rl}} RISK ZONE</b> — ${{z.area}} | ${{z.loc}} | Severity ${{z.si.toFixed(1)}}`,'entered');
        }}
      }}else if(d<=APPROACH_R){{
        if(prev==='entered'){{
          zSt[z.id]='approaching';
          addAlert(`✅ Left ${{rl}} Zone — Safe: ${{z.area}}`,
            `✅ <b>Left ${{rl}} Risk Zone — Safe</b> &nbsp;›&nbsp; ${{z.area}}`,'left');
        }}else if(prev===null){{
          zSt[z.id]='approaching';
          addAlert(`⚠️ Approaching ${{rl}} Zone: ${{z.area}} (${{Math.round(d)}}m)`,
            `⚠️ <b>APPROACHING ${{rl}} RISK ZONE</b> — ${{z.area}} | ${{Math.round(d)}}m ahead`,'approaching');
        }}
      }}else{{
        if(prev==='entered'){{ zSt[z.id]=null; addAlert(`✅ Left ${{rl}} Zone — Safe: ${{z.area}}`,`✅ <b>Left ${{rl}} Risk Zone — Safe</b> &nbsp;›&nbsp; ${{z.area}}`,'left'); }}
        else if(prev==='approaching'){{ zSt[z.id]=null; }}
      }}
    }});
  }}

  // 5m interpolated path
  const allC=[]; PATHS.forEach(p=>{{if(p.coords)allC.push(...p.coords);}});
  const ic=[];
  for(let i=0;i<allC.length-1;i++){{
    const[la1,ln1]=allC[i],[la2,ln2]=allC[i+1];
    const n=Math.max(1,Math.round(hav(la1,ln1,la2,ln2)/5));
    for(let s=0;s<n;s++){{const t=s/n;ic.push([la1+(la2-la1)*t,ln1+(ln2-ln1)*t]);}}
  }}
  ic.push(allC[allC.length-1]);

  let idx=0;
  function step(){{
    if(idx>=ic.length){{
      car.setIcon(L.divIcon({{className:'car-icon',html:`<span style="font-size:22px;line-height:1;display:block;">🏁</span>`,iconSize:[24,24],iconAnchor:[12,12]}}));
      addAlert('🏁 Done!','🏁 <b>Simulation complete — Destination reached!</b>','safe'); return;
    }}
    const[lat,lng]=ic[idx];
    car.setLatLng([lat,lng]);
    trailPts.push([lat,lng]); trail.setLatLngs(trailPts);
    if(!map.getBounds().contains([lat,lng]))
      map.panTo([lat,lng],{{animate:true,duration:0.4,easeLinearity:0.5}});
    checkZones(lat,lng);   // synchronous — zero lag between car move and alert
    idx++;
    setTimeout(step,80);
  }}

  map.setView(ic[0],14);
  addAlert('🟢 Started',`🟢 <b>Simulation started — ${{ic.length}} steps</b>`,'safe');
  setTimeout(step,500);
}}
</script>
</body>
</html>"""
    return html


# ─────────────────────────────────────────────
# 7. ALERT BOX HELPER
# ─────────────────────────────────────────────
def alert_box(level: str, text: str):
    css  = {"SAFE":"alert-safe","LOW":"alert-safe","MEDIUM":"alert-warning","HIGH":"alert-danger"}.get(level.upper(),"alert-warning")
    icon = {"SAFE":"✅","LOW":"🟡","MEDIUM":"⚠️","HIGH":"🚨"}.get(level.upper(),"ℹ️")
    st.markdown(f'<div class="{css}">{icon} <b>{level}</b> — {text}</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────
# 8. MAP FRAGMENT — isolated so search never rebuilds the iframe
# ─────────────────────────────────────────────
@st.fragment
def render_map(display_df, display_paths, center_lat, center_lng,
               search_zones, search_label, show_car):
    map_html = build_leaflet_map(
        accident_df  = display_df,
        driver_paths = display_paths,
        center_lat   = center_lat,
        center_lng   = center_lng,
        search_zones = search_zones,
        search_label = search_label,
        show_car     = show_car,
    )
    components.html(map_html, height=700, scrolling=False)


# ─────────────────────────────────────────────
# 9. MAIN
# ─────────────────────────────────────────────
def main():
    defaults = dict(running=False, highlight_point=None, highlight_label="",
                    search_query="", search_zones=None, risk_info=None, search_error="")
    for k,v in defaults.items():
        if k not in st.session_state: st.session_state[k]=v

    with st.sidebar:
        st.image("https://img.icons8.com/color/96/traffic-jam.png", width=56)
        st.title("Road Risk Navigator")
        st.caption("Powered by Supabase + Leaflet")
        st.divider()

        st.subheader("🎮 Session Controls")
        c1,c2 = st.columns(2)
        with c1: start_btn = st.button("▶ Start", use_container_width=True, type="primary")
        with c2: stop_btn  = st.button("⏹ Stop",  use_container_width=True)
        st.success("🟢 Car simulation active.") if st.session_state.running else st.info("⏸ Press Start.")
        st.divider()

        st.subheader("📍 Location Search")
        address_input = st.text_input("Search location",
            placeholder="e.g. Deonar | Andheri, Mumbai | 19.076, 72.877")
        search_btn = st.button("🔍 Find & Check Risk", use_container_width=True)
        if st.session_state.highlight_point:
            if st.button("✖ Clear Search", use_container_width=True):
                for k in ["highlight_point","search_zones","risk_info","highlight_label","search_query","search_error"]:
                    st.session_state[k] = None if k in ["highlight_point","search_zones","risk_info"] else ""
        st.divider()

        st.subheader("🔧 Filters & Settings")
        risk_filter = st.multiselect("Risk Level", ["High","Medium","Low"], default=["High","Medium","Low"])
        show_paths  = st.checkbox("Show Driver Paths", value=True)
        show_zones  = st.checkbox("Show Accident Zones", value=True)

    # ONLY Start/Stop call st.rerun() — intentionally rebuilds the map fragment
    if start_btn: st.session_state.running = True;  st.rerun()
    if stop_btn:  st.session_state.running = False; st.rerun()

    accident_df  = load_accident_data()
    driver_paths = load_driver_path()

    # SEARCH — NO st.rerun() → @st.fragment stays frozen → car keeps running
    if search_btn and address_input:
        result = resolve_location(address_input.strip(), accident_df)
        if result:
            lat, lon, label = result
            st.session_state.highlight_point = (lat, lon)
            st.session_state.highlight_label = label
            st.session_state.search_query    = address_input.strip()
            st.session_state.risk_info       = check_risk_at_point(lat, lon, accident_df, 1500)
            ql = address_input.strip().lower()
            matched = [
                {"id":int(r["id"]),"lat":float(r["latitude"]),"lng":float(r["longitude"]),
                 "area":str(r.get("area","")),"loc":str(r.get("location","")),
                 "si":float(r.get("severity_index",0)),"risk":str(r.get("risk_level","Low")),
                 "ta":int(r.get("total_accident",0)),"tf":int(r.get("total_fatality",0))}
                for _,r in accident_df.iterrows()
                if ql in str(r.get("area","")).lower()
                or ql in str(r.get("location","")).lower()
                or ql in str(r.get("city","")).lower()
            ]
            st.session_state.search_zones = matched if matched else None
            st.session_state.search_error = ""
        else:
            st.session_state.highlight_point = None
            st.session_state.search_zones    = None
            st.session_state.risk_info       = None
            st.session_state.search_error    = f"Location not found for '{address_input}'."

    filtered_df   = accident_df[accident_df["risk_level"].isin(risk_filter)] if risk_filter else accident_df
    display_df    = filtered_df if show_zones else accident_df.iloc[0:0]
    display_paths = driver_paths if show_paths else []

    st.title("🚦 Road Accident Risk Dashboard")
    k1,k2,k3,k4,k5 = st.columns(5)
    k1.metric("Total Hotspots",  len(accident_df))
    k2.metric("High Risk Zones", int((accident_df["risk_level"]=="High").sum()))
    k3.metric("Total Accidents", int(accident_df["total_accident"].sum()))
    k4.metric("Total Fatalities",int(accident_df["total_fatality"].sum()))
    k5.metric("Driver Paths",    len(driver_paths))
    st.divider()

    if st.session_state.search_error:
        st.error(st.session_state.search_error)

    # Risk panel below KPIs
    if st.session_state.highlight_point and st.session_state.risk_info:
        ri = st.session_state.risk_info
        st.subheader("📊 Risk Assessment")
        st.caption(f"**{st.session_state.highlight_label}**")
        alert_box(ri["level"], ri["message"])
        if ri["zones"]:
            near_df = pd.DataFrame(ri["zones"])[
                ["area","location","risk_level","severity_index","total_accident","total_fatality","distance_m"]
            ].sort_values("distance_m")
            st.dataframe(near_df, use_container_width=True, hide_index=True)

    st.subheader("🗺️ Interactive Risk Map  •  🚗 Live Car Simulation")
    st.caption("Car moves along the driver path. Alert feed at bottom shows real-time zone alerts.")

    center_lat = float(display_df["latitude"].mean())  if not display_df.empty else 19.076
    center_lng = float(display_df["longitude"].mean()) if not display_df.empty else 72.877

    # @st.fragment — only re-renders when Start/Stop fires st.rerun()
    render_map(
        display_df   = display_df,
        display_paths= display_paths,
        center_lat   = center_lat,
        center_lng   = center_lng,
        search_zones = st.session_state.search_zones,
        search_label = st.session_state.highlight_label,
        show_car     = st.session_state.running,
    )

    with st.expander("📋 Accident Zone Data Table", expanded=False):
        st.dataframe(
            filtered_df[["id","city","area","location","risk_level",
                         "severity_index","total_accident","total_fatality",
                         "latitude","longitude"]].sort_values("severity_index",ascending=False),
            use_container_width=True, hide_index=True
        )

    st.markdown("""
<div class="footer-bar">
    🚦 <strong>Road Risk Navigator</strong> &nbsp;|&nbsp;
    Data: Supabase PostgreSQL &nbsp;|&nbsp;
    Map: Leaflet.js &nbsp;|&nbsp;
    Geocoding: Nominatim (OpenStreetMap) &nbsp;|&nbsp;
    <em>For road safety awareness only.</em>
</div>""", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
