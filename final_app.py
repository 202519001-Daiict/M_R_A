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

# ─────────────────────────────────────────────
# 4. GEOCODER
# ─────────────────────────────────────────────
@st.cache_data(ttl=600, show_spinner="Geocoding location...")
def geocode_location(address: str):
    try:
        geolocator = Nominatim(user_agent="road_risk_navigator_v2", timeout=10)
        result = geolocator.geocode(address)
        if result:
            return result.latitude, result.longitude
        return None
    except Exception as e:
        st.warning(f"Geocoding error: {e}")
        return None

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
                       highlight_point=None,
                       show_car: bool = True) -> str:

    zones_js = json.dumps([
        {
            "id":   int(r["id"]),
            "lat":  float(r["latitude"]),
            "lng":  float(r["longitude"]),
            "area": str(r.get("area", "")),
            "loc":  str(r.get("location", "")),
            "si":   float(r.get("severity_index", 0)),
            "risk": str(r.get("risk_level", "Low")),
            "ta":   int(r.get("total_accident", 0)),
            "tf":   int(r.get("total_fatality", 0)),
        }
        for _, r in accident_df.iterrows()
    ])

    paths_js     = json.dumps([{"id": p["id"], "coords": p["coordinates"]} for p in driver_paths])
    highlight_js = json.dumps(list(highlight_point) if highlight_point else None)
    show_car_js  = "true" if show_car else "false"

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>Road Risk Map</title>
<!-- FIX: jsdelivr CDN is reliable on Streamlit Cloud (unpkg often times out) -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body, html {{ height:100%; background:#0e1117; font-family:sans-serif; }}
  #wrapper {{ display:flex; flex-direction:column; height:100vh; }}
  #map {{ flex:1; min-height:0; position:relative; }}

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
</style>
</head>
<body>
<div id="wrapper">
  <div id="map">
    <div id="mapAlert"></div>
  </div>
  <div id="alertFeed">
    <div id="alertMsg" class="safe">🟢 &nbsp; Press <b>Start</b> in the sidebar to begin car simulation…</div>
  </div>
</div>

<script>
const ZONES    = {zones_js};
const PATHS    = {paths_js};
const HLIGHT   = {highlight_js};
const SHOW_CAR = {show_car_js};
const APPROACH_R = 500;
const ENTER_R    = 120;

// ── MAP INIT ─────────────────────────────────
const map = L.map('map', {{zoomControl:true, preferCanvas:false}})
              .setView([{center_lat}, {center_lng}], 13);

// ── HELPERS ──────────────────────────────────
function siColor(si) {{
  if (si > 25) return '#d50000';
  if (si > 10) return '#ff6d00';
  return '#ffd600';
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
  const bar = document.getElementById('alertMsg');
  bar.className = cls;
  bar.innerHTML = fullMsg;
  const bubble = document.getElementById('mapAlert');
  bubble.className = 'show ' + (cls==='approaching'?'ap': cls==='entered'?'en':'lft');
  bubble.textContent = shortMsg;
  if (mapAlertTimer) clearTimeout(mapAlertTimer);
  mapAlertTimer = setTimeout(() => {{ bubble.className = ''; }}, 4000);
}}

// ── ACCIDENT ZONES ────────────────────────────
const zoneLayer = L.layerGroup().addTo(map);
ZONES.forEach(z => {{
  const col = siColor(z.si);
  L.circle([z.lat,z.lng], {{
    radius:APPROACH_R, color:col, fillColor:col,
    fillOpacity:0.10, weight:1.5, dashArray:'6 4'
  }}).addTo(map);
  L.circle([z.lat,z.lng], {{
    radius:ENTER_R, color:col, fillColor:col,
    fillOpacity:0.28, weight:2
  }}).addTo(map);
  L.marker([z.lat,z.lng], {{
    icon: L.divIcon({{
      className:'',
      html:`<div style="width:13px;height:13px;border-radius:50%;background:${{col}};
                 border:2px solid #fff;box-shadow:0 0 7px ${{col}}99"></div>`,
      iconSize:[13,13], iconAnchor:[6,6]
    }})
  }}).bindPopup(`
    <b>🚨 ${{z.area}}</b><br><small>${{z.loc}}</small>
    <hr style="margin:4px 0">
    Severity: <b>${{z.si.toFixed(1)}}</b> | Risk: <b style="color:${{col}}">${{z.risk}}</b><br>
    Accidents: <b>${{z.ta}}</b> | Fatalities: <b>${{z.tf}}</b>
  `,{{maxWidth:220}}).addTo(zoneLayer);
}});

// ── DRIVER PATHS ──────────────────────────────
const pathLayer = L.layerGroup().addTo(map);
const PATH_COLORS = ['#00e5ff','#69ff47','#ff4081','#e040fb','#ffab40'];
PATHS.forEach((p,i) => {{
  if (!p.coords || p.coords.length < 2) return;
  const col = PATH_COLORS[i % PATH_COLORS.length];
  L.polyline(p.coords, {{color:col, weight:4, opacity:0.55, dashArray:'8 5'}})
   .bindPopup(`<b>Driver Path #${{p.id}}</b> — ${{p.coords.length}} points`)
   .addTo(pathLayer);
  L.circleMarker(p.coords[p.coords.length-1], {{
    radius:7, color:'#d50000', fillColor:'#d50000', fillOpacity:1, weight:2
  }}).bindTooltip('🔴 Destination').addTo(pathLayer);
}});

// ── SEARCHED LOCATION ────────────────────────
if (HLIGHT) {{
  L.marker(HLIGHT, {{icon:L.divIcon({{className:'',
    html:`<div style="background:#00e5ff;width:18px;height:18px;border-radius:50%;
               border:3px solid #fff;box-shadow:0 0 14px #00e5ff"></div>`,
    iconSize:[18,18],iconAnchor:[9,9]}})
  }}).bindPopup('<b>📍 Searched Location</b>').addTo(map).openPopup();
  map.setView(HLIGHT, 14);
}}

// ── LAYER CONTROL ─────────────────────────────
const baseDark = L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  attribution:'© OpenStreetMap © CartoDB', subdomains:'abcd', maxZoom:19
}});
const baseStreet = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution:'© OpenStreetMap contributors', maxZoom:19
}});
const baseSatellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
  attribution:'© Esri World Imagery', maxZoom:19
}});
baseDark.addTo(map);
const baseMaps = {{
  '🌑 Dark (CartoDB)'  : baseDark,
  '🗺️ Street (OSM)'    : baseStreet,
  '🛰️ Satellite (Esri)': baseSatellite,
}};
const overlayMaps = {{
  '🚨 Accident Zones': zoneLayer,
  '🛣️ Driver Paths'  : pathLayer,
}};
L.control.layers(baseMaps, overlayMaps, {{collapsed:false, position:'topright'}}).addTo(map);

// ── LEGEND ───────────────────────────────────
const legend = L.control({{position:'bottomright'}});
legend.onAdd = () => {{
  const d = L.DomUtil.create('div','legend');
  d.innerHTML = `<h4>🗺 Legend</h4>
    <span class="dot" style="background:#d50000"></span>High Risk<br>
    <span class="dot" style="background:#ff6d00"></span>Medium Risk<br>
    <span class="dot" style="background:#ffd600"></span>Low Risk<br>
    <span class="dot" style="background:#00e5ff"></span>Driver Path<br>
    <span class="dot" style="background:#1e90ff"></span>🚗 Car Trail`;
  return d;
}};
legend.addTo(map);

// ══════════════════════════════════════════════
// ── MOVING CAR — pure setInterval (no leaflet.motion) ──
// ══════════════════════════════════════════════
if (SHOW_CAR && PATHS.length > 0) {{

  // Flatten all path coords into one continuous route
  const allCoords = [];
  PATHS.forEach(p => {{ if (p.coords) allCoords.push(...p.coords); }});
  const totalPts = allCoords.length;

  if (totalPts >= 2) {{

    // Trail polyline
    const trailLine = L.polyline([], {{color:'#1e90ff', weight:3, opacity:0.8}}).addTo(map);

    // Car marker — plain divIcon, zero external dependencies
    const carMarker = L.marker(allCoords[0], {{
      icon: L.divIcon({{
        className: '',
        html: '<div style="font-size:22px;line-height:1;text-shadow:0 0 8px #1e90ff;">🚗</div>',
        iconSize: [28, 28],
        iconAnchor: [14, 14]
      }}),
      zIndexOffset: 1000
    }}).addTo(map);

    // Zone alert state tracker
    const zoneState = {{}};
    function checkZones(lat, lng) {{
      ZONES.forEach(z => {{
        const dist      = haversineM(lat, lng, z.lat, z.lng);
        const prev      = zoneState[z.id] || null;
        const riskLevel = z.risk ? z.risk.toUpperCase() : 'RISK';

        if (dist <= ENTER_R) {{
          if (prev !== 'entered') {{
            zoneState[z.id] = 'entered';
            addAlert(
              `🚨 Entered ${{riskLevel}} Risk Zone: ${{z.area}}`,
              `🚨 <b>Entered ${{riskLevel}} Risk Zone</b> — ${{z.area}} | ${{z.loc}} | Severity ${{z.si.toFixed(1)}}`,
              'entered'
            );
          }}
        }} else if (dist <= APPROACH_R) {{
          if (prev === 'entered') {{
            zoneState[z.id] = null;
            addAlert(
              `✅ Left ${{riskLevel}} Risk Zone — Safe: ${{z.area}}`,
              `✅ <b>Left ${{riskLevel}} Risk Zone — Safe</b> › ${{z.area}}`,
              'left'
            );
          }} else if (prev !== 'approaching') {{
            zoneState[z.id] = 'approaching';
            addAlert(
              `⚠️ Approaching ${{riskLevel}} Risk Zone: ${{z.area}} (${{Math.round(dist)}}m)`,
              `⚠️ <b>Approaching ${{riskLevel}} Risk Zone</b> — ${{z.area}} | ${{z.loc}} | ${{Math.round(dist)}}m ahead`,
              'approaching'
            );
          }}
        }} else {{
          if (prev === 'entered') {{
            zoneState[z.id] = null;
            addAlert(
              `✅ Left ${{riskLevel}} Risk Zone — Safe: ${{z.area}}`,
              `✅ <b>Left ${{riskLevel}} Risk Zone — Safe</b> › ${{z.area}}`,
              'left'
            );
          }} else if (prev === 'approaching') {{
            zoneState[z.id] = null;
          }}
        }}
      }});
    }}

    // Precompute cumulative segment distances along the full route
    const segLengths = [];
    let totalDist = 0;
    for (let i = 1; i < totalPts; i++) {{
      const d = haversineM(
        allCoords[i-1][0], allCoords[i-1][1],
        allCoords[i][0],   allCoords[i][1]
      );
      segLengths.push(d);
      totalDist += d;
    }}

    // Return [lat, lng] at cumulative distance d along the route
    function posAtDist(d) {{
      let cum = 0;
      for (let i = 0; i < segLengths.length; i++) {{
        if (cum + segLengths[i] >= d) {{
          const t = (d - cum) / segLengths[i];
          const a = allCoords[i], b = allCoords[i+1];
          return [a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t];
        }}
        cum += segLengths[i];
      }}
      return allCoords[totalPts - 1];
    }}

    // Animation config: 60 km/h = 16.67 m/s, tick every 100ms
    const SPEED_MPS     = 16.67;
    const TICK_MS       = 100;
    const DIST_PER_TICK = SPEED_MPS * (TICK_MS / 1000);
    let distTravelled   = 0;
    const trailPoints   = [allCoords[0]];

    map.setView(allCoords[0], 14);
    addAlert(
      '🟢 Simulation started',
      `🟢 <b>Simulation started — ${{totalPts}} waypoints loaded</b>`,
      'safe'
    );

    const timer = setInterval(() => {{
      distTravelled += DIST_PER_TICK;

      if (distTravelled >= totalDist) {{
        clearInterval(timer);
        carMarker.setLatLng(allCoords[totalPts - 1]);
        trailLine.setLatLngs(allCoords);
        addAlert(
          '🏁 Destination reached!',
          '🏁 <b>Simulation complete — Destination reached safely!</b>',
          'safe'
        );
        return;
      }}

      const pos = posAtDist(distTravelled);
      carMarker.setLatLng(pos);

      trailPoints.push(pos);
      trailLine.setLatLngs(trailPoints);

      if (!map.getBounds().contains(pos)) {{
        map.panTo(pos, {{animate:true, duration:0.4, easeLinearity:0.5}});
      }}

      checkZones(pos[0], pos[1]);

    }}, TICK_MS);

  }} // end totalPts >= 2
}} // end SHOW_CAR
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
# 8. MAIN APPLICATION LOGIC
# ─────────────────────────────────────────────
def main():
    # ── Session state init — must be FIRST, before any widget ──
    if "running" not in st.session_state:
        st.session_state.running = False

    # ── Sidebar ──
    with st.sidebar:
        st.image("https://img.icons8.com/color/96/traffic-jam.png", width=56)
        st.title("Road Risk Navigator")
        st.caption("Powered by Supabase + Leaflet")
        st.divider()

        st.subheader("🎮 Session Controls")
        col1, col2 = st.columns(2)
        with col1:
            start_btn = st.button("▶ Start", use_container_width=True, type="primary")
        with col2:
            stop_btn  = st.button("⏹ Stop",  use_container_width=True)

        if st.session_state.running:
            st.success("🟢 Car simulation active on map.")
        else:
            st.info("⏸ Press Start to begin.")

        st.divider()

        st.subheader("📍 Location Search")
        address_input = st.text_input("Search address / place", placeholder="e.g. Andheri, Mumbai")
        search_btn    = st.button("🔍 Find & Check Risk", use_container_width=True)

        st.divider()

        st.subheader("🔧 Filters & Settings")
        risk_filter = st.multiselect("Risk Level", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])
        show_paths  = st.checkbox("Show Driver Paths", value=True)
        show_zones  = st.checkbox("Show Accident Zones", value=True)

    # ── Button logic OUTSIDE sidebar ──
    if start_btn:
        st.session_state.running = True
        st.rerun()
    if stop_btn:
        st.session_state.running = False
        st.rerun()

    # ── Load Data ──
    accident_df  = load_accident_data()
    driver_paths = load_driver_path()

    filtered_df   = accident_df[accident_df["risk_level"].isin(risk_filter)] if risk_filter else accident_df
    display_df    = filtered_df if show_zones else accident_df.iloc[0:0]
    display_paths = driver_paths if show_paths else []

    # ── Header KPIs ──
    st.title("🚦 Road Accident Risk Dashboard")
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Hotspots",   len(accident_df))
    k2.metric("High Risk Zones",  int((accident_df["risk_level"] == "High").sum()))
    k3.metric("Total Accidents",  int(accident_df["total_accident"].sum()))
    k4.metric("Total Fatalities", int(accident_df["total_fatality"].sum()))
    k5.metric("Driver Paths",     len(driver_paths))
    st.divider()

    # ── Geocode + Risk Check ──
    highlight_point = None
    if search_btn and address_input:
        coords = geocode_location(address_input)
        if coords:
            highlight_point = coords
            risk_info = check_risk_at_point(coords[0], coords[1], accident_df, 500)
            st.subheader("📊 Risk Assessment")
            alert_box(risk_info["level"], risk_info["message"])
            if risk_info["zones"]:
                near_df = pd.DataFrame(risk_info["zones"])[
                    ["area","location","risk_level","severity_index",
                     "total_accident","total_fatality","distance_m"]
                ].sort_values("distance_m")
                st.dataframe(near_df, use_container_width=True, hide_index=True)
        else:
            st.error("Location not found. Try a more specific address.")

    # ── Map ──
    st.subheader("🗺️ Interactive Risk Map  •  🚗 Live Car Simulation")
    st.caption("The 🚗 car moves along the driver path. The alert feed at the bottom of the map shows real-time zone alerts.")

    center_lat = float(display_df["latitude"].mean())  if not display_df.empty else 19.076
    center_lng = float(display_df["longitude"].mean()) if not display_df.empty else 72.877

    map_html = build_leaflet_map(
        accident_df     = display_df,
        driver_paths    = display_paths,
        center_lat      = center_lat,
        center_lng      = center_lng,
        highlight_point = highlight_point,
        show_car        = st.session_state.running,
    )
    components.html(map_html, height=700, scrolling=False)

    # ── Data Table ──
    with st.expander("📋 Accident Zone Data Table", expanded=False):
        st.dataframe(
            filtered_df[["id","city","area","location","risk_level",
                          "severity_index","total_accident","total_fatality",
                          "latitude","longitude"]].sort_values("severity_index", ascending=False),
            use_container_width=True, hide_index=True
        )

    # ── Footer ──
    st.markdown("""
<div class="footer-bar">
    🚦 <strong>Road Risk Navigator</strong> &nbsp;|&nbsp;
    Data: Supabase PostgreSQL &nbsp;|&nbsp;
    Map: Leaflet.js + CartoDB Dark &nbsp;|&nbsp;
    Geocoding: Nominatim (OpenStreetMap) &nbsp;|&nbsp;
    <em>For road safety awareness only — not a substitute for official traffic guidance.</em>
</div>
""", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
