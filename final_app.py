def build_leaflet_map(accident_df,
                       driver_paths,
                       center_lat=19.076,
                       center_lng=72.877,
                       highlight_point=None,
                       show_car=True):
    import json

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
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
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
    <div id="alertMsg" class="safe">&#128994; &nbsp; Press <b>Start</b> in the sidebar to begin car simulation&hellip;</div>
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

// ── INTERPOLATE between two coords ───────────
// Returns a point 't' fraction (0..1) from A to B
function interpolate(a, b, t) {{
  return [a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t];
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
    <b>&#128680; ${{z.area}}</b><br><small>${{z.loc}}</small>
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
   .bindPopup(`<b>Driver Path #${{p.id}}</b> &mdash; ${{p.coords.length}} points`)
   .addTo(pathLayer);
  L.circleMarker(p.coords[p.coords.length-1], {{
    radius:7, color:'#d50000', fillColor:'#d50000', fillOpacity:1, weight:2
  }}).bindTooltip('&#128308; Destination').addTo(pathLayer);
}});

// ── SEARCHED LOCATION ────────────────────────
if (HLIGHT) {{
  L.marker(HLIGHT, {{icon:L.divIcon({{className:'',
    html:`<div style="background:#00e5ff;width:18px;height:18px;border-radius:50%;
               border:3px solid #fff;box-shadow:0 0 14px #00e5ff"></div>`,
    iconSize:[18,18],iconAnchor:[9,9]}})
  }}).bindPopup('<b>&#128205; Searched Location</b>').addTo(map).openPopup();
  map.setView(HLIGHT, 14);
}}

// ── LAYER CONTROL ─────────────────────────────
const baseDark = L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  attribution:'&copy; OpenStreetMap &copy; CartoDB', subdomains:'abcd', maxZoom:19
}});
const baseStreet = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution:'&copy; OpenStreetMap contributors', maxZoom:19
}});
const baseSatellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
  attribution:'&copy; Esri World Imagery', maxZoom:19
}});
baseDark.addTo(map);
L.control.layers(
  {{'&#127761; Dark':'baseDark','&#128506;&#65039; Street':baseStreet,'&#128752;&#65039; Satellite':baseSatellite}},
  {{'&#128680; Accident Zones':zoneLayer,'&#128739;&#65039; Driver Paths':pathLayer}},
  {{collapsed:false, position:'topright'}}
).addTo(map);

// ── LEGEND ───────────────────────────────────
const legend = L.control({{position:'bottomright'}});
legend.onAdd = () => {{
  const d = L.DomUtil.create('div','legend');
  d.innerHTML = `<h4>Legend</h4>
    <span class="dot" style="background:#d50000"></span>High Risk<br>
    <span class="dot" style="background:#ff6d00"></span>Medium Risk<br>
    <span class="dot" style="background:#ffd600"></span>Low Risk<br>
    <span class="dot" style="background:#00e5ff"></span>Driver Path<br>
    <span class="dot" style="background:#1e90ff"></span>Car Trail`;
  return d;
}};
legend.addTo(map);

// ══════════════════════════════════════════════
// ── MOVING CAR — pure setInterval (no leaflet.motion) ──
// ══════════════════════════════════════════════
if (SHOW_CAR && PATHS.length > 0) {{

  // 1. Flatten all path coords into one continuous route
  const allCoords = [];
  PATHS.forEach(p => {{ if (p.coords) allCoords.push(...p.coords); }});
  const totalPts = allCoords.length;

  if (totalPts < 2) {{
    console.warn('Not enough waypoints for animation');
  }} else {{

    // 2. Trail polyline
    const trailLine = L.polyline([], {{color:'#1e90ff', weight:3, opacity:0.8}}).addTo(map);

    // 3. Car marker — using a simple divIcon (no library needed)
    const carIcon = L.divIcon({{
      className: '',
      html: '<div style="font-size:22px;line-height:1;text-shadow:0 0 8px #1e90ff;">&#128663;</div>',
      iconSize: [28, 28],
      iconAnchor: [14, 14]
    }});
    const carMarker = L.marker(allCoords[0], {{icon: carIcon, zIndexOffset: 1000}}).addTo(map);

    // 4. Zone alert state
    const zoneState = {{}};
    function checkZones(lat, lng) {{
      ZONES.forEach(z => {{
        const dist = haversineM(lat, lng, z.lat, z.lng);
        const prev = zoneState[z.id] || null;
        const riskLevel = z.risk ? z.risk.toUpperCase() : 'RISK';

        if (dist <= ENTER_R) {{
          if (prev !== 'entered') {{
            zoneState[z.id] = 'entered';
            addAlert(
              `Entered ${{riskLevel}} Risk Zone: ${{z.area}}`,
              `&#128680; <b>Entered ${{riskLevel}} Risk Zone</b> &mdash; ${{z.area}} | ${{z.loc}} | Severity ${{z.si.toFixed(1)}}`,
              'entered'
            );
          }}
        }} else if (dist <= APPROACH_R) {{
          if (prev === 'entered') {{
            zoneState[z.id] = null;
            addAlert(
              `Left ${{riskLevel}} Risk Zone: ${{z.area}}`,
              `&#9989; <b>Left ${{riskLevel}} Risk Zone &mdash; Safe</b> &nbsp;&rsaquo;&nbsp; ${{z.area}}`,
              'left'
            );
          }} else if (prev !== 'approaching') {{
            zoneState[z.id] = 'approaching';
            addAlert(
              `Approaching ${{riskLevel}} Risk Zone: ${{z.area}} (${{Math.round(dist)}}m)`,
              `&#9888;&#65039; <b>Approaching ${{riskLevel}} Risk Zone</b> &mdash; ${{z.area}} | ${{z.loc}} | ${{Math.round(dist)}}m ahead`,
              'approaching'
            );
          }}
        }} else {{
          if (prev === 'entered') {{
            zoneState[z.id] = null;
            addAlert(
              `Left ${{riskLevel}} Risk Zone: ${{z.area}}`,
              `&#9989; <b>Left ${{riskLevel}} Risk Zone &mdash; Safe</b> &nbsp;&rsaquo;&nbsp; ${{z.area}}`,
              'left'
            );
          }} else if (prev === 'approaching') {{
            zoneState[z.id] = null;
          }}
        }}
      }});
    }}

    // 5. Animation state
    // We move at ~60 km/h. Each tick = 100ms = 1/10 sec
    // 60 km/h = 16.67 m/s → 1.667 m per 100ms tick
    // We compute segment lengths and advance by fixed metres per tick.
    const SPEED_MPS = 16.67;   // metres per second (60 km/h)
    const TICK_MS   = 100;     // ms per frame
    const DIST_PER_TICK = SPEED_MPS * (TICK_MS / 1000);  // metres per tick

    // Precompute cumulative distances along the route
    const segLengths = [];
    let totalDist = 0;
    for (let i = 1; i < totalPts; i++) {{
      const d = haversineM(allCoords[i-1][0], allCoords[i-1][1], allCoords[i][0], allCoords[i][1]);
      segLengths.push(d);
      totalDist += d;
    }}

    let distTravelled = 0;
    const trailPoints = [allCoords[0]];

    // Find position on route at given cumulative distance
    function posAtDist(d) {{
      let cum = 0;
      for (let i = 0; i < segLengths.length; i++) {{
        if (cum + segLengths[i] >= d) {{
          const t = (d - cum) / segLengths[i];
          return interpolate(allCoords[i], allCoords[i+1], t);
        }}
        cum += segLengths[i];
      }}
      return allCoords[totalPts - 1];  // end of route
    }}

    // Zoom to start
    map.setView(allCoords[0], 14);

    // Status message
    const barEl = document.getElementById('alertMsg');
    barEl.className = 'safe';
    barEl.innerHTML = `&#128994; <b>Simulation started &mdash; ${{totalPts}} waypoints | ${"{:.1f}".format(0)} km route</b>`;

    // 6. Start ticking
    const timer = setInterval(() => {{
      distTravelled += DIST_PER_TICK;

      if (distTravelled >= totalDist) {{
        // Reached destination
        clearInterval(timer);
        carMarker.setLatLng(allCoords[totalPts-1]);
        trailLine.setLatLngs(allCoords);
        const bar = document.getElementById('alertMsg');
        bar.className = 'safe';
        bar.innerHTML = '&#127937; <b>Simulation complete &mdash; Destination reached safely!</b>';
        return;
      }}

      const pos = posAtDist(distTravelled);
      carMarker.setLatLng(pos);

      // Grow trail
      trailPoints.push(pos);
      trailLine.setLatLngs(trailPoints);

      // Auto-pan if car goes off screen
      if (!map.getBounds().contains(pos)) {{
        map.panTo(pos, {{animate:true, duration:0.4, easeLinearity:0.5}});
      }}

      // Zone alerts
      checkZones(pos[0], pos[1]);

    }}, TICK_MS);

  }} // end if totalPts >= 2
}} // end if SHOW_CAR
</script>
</body>
</html>"""
    return html
