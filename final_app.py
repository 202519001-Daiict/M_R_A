"""
Road Accident Risk Navigation Dashboard
Uses Supabase PostgreSQL + Leaflet.js (moving car + smart zone alerts)

KEY ARCHITECTURE:
- @st.fragment on the map block = map iframe is NEVER re-rendered when search fires
- Search stores results in session_state only, no st.rerun() called
- Alert lag fixed: checkZones() called synchronously in same JS frame as setLatLng()
  with 5m interpolation so car never skips a boundary
- Search result card rendered as overlay INSIDE the map iframe
"""

import re
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
# 1. PAGE CONFIG
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
# 2. DATABASE
# ─────────────────────────────────────────────
def get_db_connection():
    return psycopg2.connect(
        host=st.secrets["DB_HOST"], port=int(st.secrets["DB_PORT"]),
        dbname=st.secrets["DB_NAME"], user=st.secrets["DB_USER"],
        password=st.secrets["DB_PASSWORD"], sslmode="require"
    )

@st.cache_data(ttl=300, show_spinner="Loading accident zones...")
def load_accident_data() -> pd.DataFrame:
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM accident_data1;")
        rows = cur.fetchall()
    conn.close()
    df = pd.DataFrame(rows)
    for col in ["latitude","longitude","total_accident","total_fatality","severity_index"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df.dropna(subset=["latitude","longitude"], inplace=True)
    return df

@st.cache_data(ttl=300, show_spinner="Loading driver path...")
def load_driver_path() -> list:
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM driver_path;")
        rows = cur.fetchall()
    conn.close()
    paths = []
    for row in rows:
        coords = _decode_wkb(str(row.get("geom","")))
        if coords:
            paths.append({"id": row.get("id"), "coordinates": coords})
    return paths

def _decode_wkb(hex_wkb: str) -> list:
    try:
        raw = bytes.fromhex(hex_wkb)
        endian = "<" if raw[0]==1 else ">"
        offset = 9
        n = struct.unpack_from(endian+"I", raw, offset)[0]; offset += 4
        coords = []
        for _ in range(n):
            x,y = struct.unpack_from(endian+"dd", raw, offset); offset += 16
            coords.append([y,x])
        return coords
    except Exception:
        return []

# ─────────────────────────────────────────────
# 3. GEOCODER + LOCATION RESOLVER
# ─────────────────────────────────────────────
@st.cache_data(ttl=600, show_spinner="Geocoding...")
def _nominatim(address: str):
    try:
        geo = Nominatim(user_agent="road_risk_nav_v4", timeout=10)
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
        if (ql in str(row.get("area","")).lower() or
            ql in str(row.get("location","")).lower() or
            ql in str(row.get("city","")).lower()):
            return float(row["latitude"]), float(row["longitude"]), \
                   f"{row.get('area','')} — {row.get('location','')}"
    return _nominatim(q)

# ─────────────────────────────────────────────
# 4. RISK CHECK
# ─────────────────────────────────────────────
def check_risk_at_point(lat, lon, accident_df, radius_m=1000):
    nearby = []
    for _, row in accident_df.iterrows():
        d = geodesic((lat,lon),(row["latitude"],row["longitude"])).meters
        if d <= radius_m:
            nearby.append({**row.to_dict(), "distance_m": round(d)})
    if not nearby:
        return {"level":"SAFE","zones":[],"message":"No accident zones nearby."}
    ndf    = pd.DataFrame(nearby)
    max_si = ndf["severity_index"].max()
    level  = "HIGH" if max_si>25 else ("MEDIUM" if max_si>10 else "LOW")
    return {"level":level,"zones":nearby,
            "message":f"{len(nearby)} zone(s) within {radius_m}m — max severity {max_si:.1f}"}

# ─────────────────────────────────────────────
# 5. MAP HTML BUILDER
# ─────────────────────────────────────────────
def build_leaflet_map(accident_df, driver_paths,
                      center_lat=19.076, center_lng=72.877,
                      search_zones=None, search_label="",
                      show_car=False) -> str:

    zones_js     = json.dumps([
        {"id":int(r["id"]),"lat":float(r["latitude"]),"lng":float(r["longitude"]),
         "area":str(r.get("area","")),"loc":str(r.get("location","")),"city":str(r.get("city","")),
         "si":float(r.get("severity_index",0)),"risk":str(r.get("risk_level","Low")),
         "ta":int(r.get("total_accident",0)),"tf":int(r.get("total_fatality",0))}
        for _,r in accident_df.iterrows()
    ])
    paths_js     = json.dumps([{"id":p["id"],"coords":p["coordinates"]} for p in driver_paths])
    sz_ids       = [z["id"] for z in search_zones] if search_zones else []
    sz_data      = search_zones if search_zones else []
    matched_js   = json.dumps(sz_ids)
    sz_data_js   = json.dumps(sz_data)
    label_js     = json.dumps(search_label)
    show_car_js  = "true" if show_car else "false"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body,html{{height:100%;background:#0e1117;font-family:'Segoe UI',sans-serif;}}
#wrapper{{display:flex;flex-direction:column;height:100vh;}}
#map{{flex:1;min-height:0;position:relative;overflow:hidden;}}

/* bottom alert bar */
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

/* floating bubble */
#mapAlert{{position:absolute;top:14px;left:50%;transform:translateX(-50%);z-index:1001;
           padding:7px 22px;border-radius:20px;font-size:0.82rem;font-weight:700;color:#fff;
           pointer-events:none;opacity:0;transition:opacity 0.2s;white-space:nowrap;
           box-shadow:0 2px 14px #0009;}}
#mapAlert.show{{opacity:1;}}
#mapAlert.ap{{background:rgba(0,60,100,0.93);border:1.5px solid #00e5ff;}}
#mapAlert.en{{background:rgba(120,0,0,0.95);border:1.5px solid #ff3333;}}
#mapAlert.lft{{background:rgba(0,70,20,0.93);border:1.5px solid #00c853;}}

/* search result card — floats inside the map */
#searchCard{{
  position:absolute;bottom:62px;left:12px;z-index:1002;
  background:rgba(11,14,20,0.97);border:1px solid #2c3a5a;
  border-radius:12px;padding:0;width:300px;max-height:360px;
  display:none;box-shadow:0 6px 28px #000c;overflow:hidden;
}}
#searchCard.visible{{display:flex;flex-direction:column;animation:fadeUp 0.28s ease;}}
#cardHeader{{
  padding:11px 14px 9px;
  background:rgba(30,40,65,0.98);
  border-bottom:1px solid #2c3a5a;
  display:flex;align-items:center;justify-content:space-between;
}}
#cardHeader h3{{margin:0;font-size:0.9rem;color:#e8ecf4;font-weight:700;}}
#closeBtn{{background:none;border:none;color:#666;font-size:1.1rem;cursor:pointer;
           padding:0 2px;line-height:1;}}
#closeBtn:hover{{color:#ccc;}}
#cardScroll{{overflow-y:auto;max-height:290px;padding:8px 0;}}
.zone-row{{padding:8px 14px;border-bottom:1px solid #1a2235;font-size:0.78rem;color:#cdd;}}
.zone-row:last-child{{border-bottom:none;}}
.zone-name{{font-weight:700;font-size:0.84rem;margin-bottom:2px;}}
.zone-meta{{color:#8899aa;font-size:0.73rem;margin-bottom:3px;}}
.badge{{display:inline-block;padding:1px 7px;border-radius:8px;font-size:0.7rem;font-weight:800;margin-right:4px;}}
.stat{{color:#aab;font-size:0.75rem;}}
.stat b{{color:#dde;}}

/* legend */
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
    <!-- Search result card lives INSIDE the map so it never causes Streamlit to rerender -->
    <div id="searchCard">
      <div id="cardHeader">
        <h3 id="cardTitle">📍 Search Results</h3>
        <button id="closeBtn" onclick="closeCard()">✕</button>
      </div>
      <div id="cardScroll"><div id="cardBody"></div></div>
    </div>
  </div>
  <div id="alertFeed">
    <div id="alertMsg" class="safe">🟢 &nbsp; Press <b>Start</b> in the sidebar to begin car simulation…</div>
  </div>
</div>

<script>
const ZONES      = {zones_js};
const PATHS      = {paths_js};
const MATCHED    = new Set({matched_js});
const SDATA      = {sz_data_js};
const SLBL       = {label_js};
const SHOW_CAR   = {show_car_js};
const APPROACH_R = 500;
const ENTER_R    = 120;
const HAS_SEARCH = MATCHED.size > 0;

// MAP
const map = L.map('map',{{zoomControl:true,preferCanvas:false}})
              .setView([{center_lat},{center_lng}],13);

// 5 BASE LAYERS
const BL = {{
  dark     : L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',   {{attribution:'&copy; OpenStreetMap &copy; CartoDB',subdomains:'abcd',maxZoom:19}}),
  light    : L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png',  {{attribution:'&copy; OpenStreetMap &copy; CartoDB',subdomains:'abcd',maxZoom:19}}),
  street   : L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',                {{attribution:'&copy; OpenStreetMap contributors',maxZoom:19}}),
  satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',{{attribution:'&copy; Esri',maxZoom:19}}),
  topo     : L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',                  {{attribution:'&copy; OpenStreetMap &copy; OpenTopoMap',subdomains:'abc',maxZoom:17}}),
}};
BL[localStorage.getItem('riskmap_bl')||'dark'].addTo(map);

// HELPERS
const siColor=si=>si>25?'#d50000':si>10?'#ff6d00':'#ffd600';
const siLabel=si=>si>25?'HIGH':si>10?'MEDIUM':'LOW';
function haversineM(la1,ln1,la2,ln2){{
  const R=6371000,dL=(la2-la1)*Math.PI/180,dl=(ln2-ln1)*Math.PI/180,
        a=Math.sin(dL/2)**2+Math.cos(la1*Math.PI/180)*Math.cos(la2*Math.PI/180)*Math.sin(dl/2)**2;
  return R*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
}}

// ALERT — pure synchronous DOM write, zero lag
let _bTimer=null;
function addAlert(short,full,cls){{
  const bar=document.getElementById('alertMsg');
  bar.className=cls; bar.innerHTML=full;          // synchronous, paints with next frame
  const bub=document.getElementById('mapAlert');
  bub.className='show '+(cls==='approaching'?'ap':cls==='entered'?'en':'lft');
  bub.textContent=short;
  if(_bTimer)clearTimeout(_bTimer);
  _bTimer=setTimeout(()=>{{bub.className='';}},4000);
}}

// SEARCH CARD (inside iframe — never triggers Streamlit rerender)
function showSearchCard(){{
  if(!HAS_SEARCH)return;
  document.getElementById('cardTitle').textContent=SLBL||'Search Results';
  let html='';
  SDATA.forEach(z=>{{
    const col=siColor(z.si), lv=siLabel(z.si);
    const bgAlpha=z.si>25?'#d5000022':z.si>10?'#ff6d0022':'#ffd60022';
    html+=`<div class="zone-row">
      <div class="zone-name" style="color:${{col}}">🚨 ${{z.area}}</div>
      <div class="zone-meta">${{z.loc}}</div>
      <span class="badge" style="background:${{bgAlpha}};color:${{col}};border:1px solid ${{col}}44">${{lv}} RISK</span>
      <div class="stat">
        Severity: <b style="color:${{col}}">${{z.si.toFixed(1)}}</b> &nbsp;·&nbsp;
        Accidents: <b>${{z.ta}}</b> &nbsp;·&nbsp;
        Fatalities: <b>${{z.tf}}</b>
      </div>
    </div>`;
  }});
  if(!html) html='<div style="padding:10px 14px;color:#778">No matching zones in database.</div>';
  document.getElementById('cardBody').innerHTML=html;
  document.getElementById('searchCard').className='visible';
}}
function closeCard(){{document.getElementById('searchCard').className='';}}

// ACCIDENT ZONES
const zoneLayer=L.layerGroup().addTo(map);
ZONES.forEach(z=>{{
  const col=siColor(z.si);
  const isMatch=!HAS_SEARCH||MATCHED.has(z.id);
  const dimmed=HAS_SEARCH&&!isMatch;
  const fOuter=dimmed?0.01:0.10, fInner=dimmed?0.02:0.28;
  const wt=dimmed?0.3:1.5, op=dimmed?0.1:1;
  const sz=isMatch&&HAS_SEARCH?16:13, anc=sz/2;
  const pulse=isMatch&&HAS_SEARCH
    ?`animation:pulse 1.2s ease-in-out infinite;box-shadow:0 0 18px ${{col}};`
    :`box-shadow:0 0 7px ${{col}}99;`;

  L.circle([z.lat,z.lng],{{radius:APPROACH_R,color:col,fillColor:col,fillOpacity:fOuter,weight:wt,opacity:op,dashArray:'6 4'}}).addTo(map);
  L.circle([z.lat,z.lng],{{radius:ENTER_R,color:col,fillColor:col,fillOpacity:fInner,weight:isMatch?2:0.3,opacity:op}}).addTo(map);
  L.marker([z.lat,z.lng],{{icon:L.divIcon({{
    className:'',
    html:`<div style="width:${{sz}}px;height:${{sz}}px;border-radius:50%;background:${{col}};
              border:2px solid #fff;opacity:${{dimmed?0.08:1}};${{pulse}}"></div>`,
    iconSize:[sz,sz],iconAnchor:[anc,anc]
  }})}})
  .bindPopup(`
    <b style="color:${{col}}">🚨 ${{z.area}}</b><br>
    <small style="color:#aaa">${{z.loc}}</small>
    <hr style="margin:5px 0;border-color:#333">
    <table style="font-size:0.8rem;width:100%;border-collapse:collapse">
      <tr><td style="color:#888;padding:1px 4px">Severity</td><td><b style="color:${{col}}">${{z.si.toFixed(1)}}</b></td></tr>
      <tr><td style="color:#888;padding:1px 4px">Risk</td><td><b style="color:${{col}}">${{z.risk}}</b></td></tr>
      <tr><td style="color:#888;padding:1px 4px">Accidents</td><td><b>${{z.ta}}</b></td></tr>
      <tr><td style="color:#888;padding:1px 4px">Fatalities</td><td><b>${{z.tf}}</b></td></tr>
    </table>
  `,{{maxWidth:240}})
  .addTo(zoneLayer);
}});

// Zoom to matched zones
if(HAS_SEARCH){{
  const mz=ZONES.filter(z=>MATCHED.has(z.id));
  if(mz.length>0){{
    const lats=mz.map(z=>z.lat), lngs=mz.map(z=>z.lng);
    map.fitBounds(
      [[Math.min(...lats)-0.008,Math.min(...lngs)-0.008],
       [Math.max(...lats)+0.008,Math.max(...lngs)+0.008]],
      {{padding:[40,40],maxZoom:15,animate:true,duration:0.8}}
    );
    setTimeout(showSearchCard,900);
  }}
}}

// DRIVER PATHS
const pathLayer=L.layerGroup().addTo(map);
['#00e5ff','#69ff47','#ff4081','#e040fb','#ffab40'].forEach((col,i)=>{{
  const p=PATHS[i]; if(!p||!p.coords||p.coords.length<2)return;
  L.polyline(p.coords,{{color:col,weight:4,opacity:0.55,dashArray:'8 5'}})
   .bindPopup(`<b>Driver Path #${{p.id}}</b>`).addTo(pathLayer);
  L.circleMarker(p.coords[p.coords.length-1],{{
    radius:7,color:'#d50000',fillColor:'#d50000',fillOpacity:1,weight:2
  }}).bindTooltip('🔴 Destination').addTo(pathLayer);
}});

// LAYER CONTROL
L.control.layers(
  {{'🌑 Dark':BL.dark,'☀️ Light':BL.light,'🗺️ Street':BL.street,'🛰️ Satellite':BL.satellite,'🏔️ Topo':BL.topo}},
  {{'🚨 Accident Zones':zoneLayer,'🛣️ Driver Paths':pathLayer}},
  {{collapsed:false,position:'topright'}}
).addTo(map);
map.on('baselayerchange',e=>{{
  const m={{'🌑 Dark':'dark','☀️ Light':'light','🗺️ Street':'street','🛰️ Satellite':'satellite','🏔️ Topo':'topo'}};
  const k=m[e.name];if(k)localStorage.setItem('riskmap_bl',k);
}});

// LEGEND
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

// ── CAR SIMULATION ────────────────────────────────────────────────────────────
// SHOW_CAR is baked in at Python render time.
// Because this entire map lives in @st.fragment, search never re-renders the iframe.
// The car animation therefore runs uninterrupted when user searches.
//
// ALERT TIMING: checkZones() is called SYNCHRONOUSLY immediately after
// carMarker.setLatLng(). No setTimeout, no rAF delay between move and alert.
// 5m interpolation ensures the car never jumps past a zone boundary.
if(SHOW_CAR&&PATHS.length>0){{

  const makeCarIcon=()=>L.divIcon({{
    className:'car-icon',
    html:`<span style="font-size:22px;line-height:1;display:block;filter:drop-shadow(0 0 6px #1e90ff);">🚗</span>`,
    iconSize:[24,24],iconAnchor:[12,12],popupAnchor:[0,-14]
  }});

  const carMarker=L.marker(PATHS[0].coords[0],{{icon:makeCarIcon(),zIndexOffset:1000}}).addTo(map);
  const trailPts=[];
  const trailLine=L.polyline([],{{color:'#1e90ff',weight:3,opacity:0.7}}).addTo(map);
  const zSt={{}};  // per-zone state: null | 'approaching' | 'entered'

  function checkZones(lat,lng){{
    ZONES.forEach(z=>{{
      const dist=haversineM(lat,lng,z.lat,z.lng);
      const prev=zSt[z.id]||null;
      const rl=(z.risk||'RISK').toUpperCase();
      if(dist<=ENTER_R){{
        if(prev!=='entered'){{
          zSt[z.id]='entered';
          addAlert(
            `🚨 Entered ${{rl}} Zone: ${{z.area}}`,
            `🚨 <b>ENTERED ${{rl}} RISK ZONE</b> — ${{z.area}} | ${{z.loc}} | Sev.${{z.si.toFixed(1)}}`,
            'entered'
          );
        }}
      }}else if(dist<=APPROACH_R){{
        if(prev==='entered'){{
          zSt[z.id]='approaching';
          addAlert(
            `✅ Left ${{rl}} Zone — Safe: ${{z.area}}`,
            `✅ <b>Left ${{rl}} Risk Zone — Safe</b> &nbsp;›&nbsp; ${{z.area}}`,
            'left'
          );
        }}else if(prev===null){{
          zSt[z.id]='approaching';
          addAlert(
            `⚠️ Approaching ${{rl}} Zone: ${{z.area}} (${{Math.round(dist)}}m)`,
            `⚠️ <b>APPROACHING ${{rl}} RISK ZONE</b> — ${{z.area}} | ${{Math.round(dist)}}m ahead`,
            'approaching'
          );
        }}
      }}else{{
        if(prev==='entered'){{
          zSt[z.id]=null;
          addAlert(
            `✅ Left ${{rl}} Zone — Safe: ${{z.area}}`,
            `✅ <b>Left ${{rl}} Risk Zone — Safe</b> &nbsp;›&nbsp; ${{z.area}}`,
            'left'
          );
        }}else if(prev==='approaching'){{
          zSt[z.id]=null;
        }}
      }}
    }});
  }}

  // Build 5m-interpolated path for precise boundary detection
  const STEP_M=5, TICK_MS=80;
  const allC=[];
  PATHS.forEach(p=>{{if(p.coords)allC.push(...p.coords);}});
  const ic=[];
  for(let i=0;i<allC.length-1;i++){{
    const[la1,ln1]=allC[i],[la2,ln2]=allC[i+1];
    const n=Math.max(1,Math.round(haversineM(la1,ln1,la2,ln2)/STEP_M));
    for(let s=0;s<n;s++){{
      const t=s/n;
      ic.push([la1+(la2-la1)*t, ln1+(ln2-ln1)*t]);
    }}
  }}
  ic.push(allC[allC.length-1]);

  let idx=0;
  function step(){{
    if(idx>=ic.length){{
      carMarker.setIcon(L.divIcon({{className:'car-icon',
        html:`<span style="font-size:22px;line-height:1;display:block;">🏁</span>`,
        iconSize:[24,24],iconAnchor:[12,12]}}));
      addAlert('🏁 Done!','🏁 <b>Simulation complete — Destination reached!</b>','safe');
      return;
    }}
    const[lat,lng]=ic[idx];
    carMarker.setLatLng([lat,lng]);      // 1. move car icon
    trailPts.push([lat,lng]);
    trailLine.setLatLngs(trailPts);      // 2. grow trail
    if(!map.getBounds().contains([lat,lng]))
      map.panTo([lat,lng],{{animate:true,duration:0.4,easeLinearity:0.5}});  // 3. pan
    checkZones(lat,lng);                 // 4. SYNCHRONOUS alert check — same call stack
    idx++;
    setTimeout(step,TICK_MS);
  }}

  map.setView(ic[0],14);
  addAlert('🟢 Simulation started',`🟢 <b>Simulation started — ${{ic.length}} steps</b>`,'safe');
  setTimeout(step,500);
}}
</script>
</body>
</html>"""


# ─────────────────────────────────────────────
# 6. ALERT BOX
# ─────────────────────────────────────────────
def alert_box(level, text):
    css  = {"SAFE":"alert-safe","LOW":"alert-safe","MEDIUM":"alert-warning","HIGH":"alert-danger"}.get(level.upper(),"alert-warning")
    icon = {"SAFE":"✅","LOW":"🟡","MEDIUM":"⚠️","HIGH":"🚨"}.get(level.upper(),"ℹ️")
    st.markdown(f'<div class="{css}">{icon} <b>{level}</b> — {text}</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────
# 7. MAP FRAGMENT
# The @st.fragment decorator is the architectural key.
# Streamlit only re-executes this fragment when something INSIDE it changes
# via st.rerun(). Since search never calls st.rerun(), this fragment is
# frozen during search — the iframe is NOT rebuilt, the car keeps running.
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
# 8. MAIN
# ─────────────────────────────────────────────
def main():
    defaults = dict(running=False, highlight_point=None, highlight_label="",
                    search_query="", search_zones=None, risk_info=None, search_error="")
    for k,v in defaults.items():
        if k not in st.session_state: st.session_state[k]=v

    # SIDEBAR
    with st.sidebar:
        st.image("https://img.icons8.com/color/96/traffic-jam.png", width=56)
        st.title("Road Risk Navigator")
        st.caption("Powered by Supabase + Leaflet")
        st.divider()

        st.subheader("🎮 Session Controls")
        c1,c2 = st.columns(2)
        with c1: start_btn = st.button("▶ Start", use_container_width=True, type="primary")
        with c2: stop_btn  = st.button("⏹ Stop",  use_container_width=True)
        if st.session_state.running:
            st.success("🟢 Car simulation active.")
        else:
            st.info("⏸ Press Start to begin.")
        st.divider()

        st.subheader("📍 Location Search")
        address_input = st.text_input("Search location",
            placeholder="e.g. Deonar | Andheri, Mumbai | 19.076, 72.877")
        search_btn = st.button("🔍 Find & Check Risk", use_container_width=True)
        if st.session_state.highlight_point:
            if st.button("✖ Clear Search", use_container_width=True):
                st.session_state.highlight_point = None
                st.session_state.search_zones    = None
                st.session_state.risk_info       = None
                st.session_state.highlight_label = ""
                st.session_state.search_query    = ""
                st.session_state.search_error    = ""
        st.divider()

        st.subheader("🔧 Filters")
        risk_filter = st.multiselect("Risk Level", ["High","Medium","Low"], default=["High","Medium","Low"])
        show_paths  = st.checkbox("Show Driver Paths", value=True)
        show_zones  = st.checkbox("Show Accident Zones", value=True)

    # Start/Stop — ONLY actions that call st.rerun() (intentionally rebuilds map)
    if start_btn:
        st.session_state.running = True
        st.rerun()
    if stop_btn:
        st.session_state.running = False
        st.rerun()

    accident_df  = load_accident_data()
    driver_paths = load_driver_path()

    # SEARCH — NO st.rerun(). session_state updated, fragment stays frozen.
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
                {"id":int(r["id"]),"area":str(r.get("area","")),"loc":str(r.get("location","")),
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

    # KPIs
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

    if st.session_state.highlight_point and st.session_state.risk_info:
        ri = st.session_state.risk_info
        st.subheader("📊 Risk Assessment")
        st.caption(f"**{st.session_state.highlight_label}**")
        alert_box(ri["level"], ri["message"])
        if ri["zones"]:
            near_df = pd.DataFrame(ri["zones"])[
                ["area","location","risk_level","severity_index",
                 "total_accident","total_fatality","distance_m"]
            ].sort_values("distance_m")
            st.dataframe(near_df, use_container_width=True, hide_index=True)

    st.subheader("🗺️ Interactive Risk Map  •  🚗 Live Car Simulation")
    st.caption("Car moves along the driver path. Alert bar at bottom shows real-time zone alerts.")

    center_lat = float(display_df["latitude"].mean())  if not display_df.empty else 19.076
    center_lng = float(display_df["longitude"].mean()) if not display_df.empty else 72.877

    # THE MAP FRAGMENT — frozen during search, only rebuilt on Start/Stop
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
  Data: Supabase PostgreSQL &nbsp;|&nbsp; Map: Leaflet.js &nbsp;|&nbsp;
  Geocoding: Nominatim &nbsp;|&nbsp;
  <em>For road safety awareness only.</em>
</div>""", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
