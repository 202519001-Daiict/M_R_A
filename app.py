import streamlit as st
import pandas as pd
import psycopg2
import folium
import time
from shapely import wkt
from shapely.geometry import Point
from streamlit_folium import st_folium

st.set_page_config(layout="wide")

st.title("🚦 Road Accident Risk Intelligence System")

# ---------------------------------------------------
# DATABASE CONNECTION
# ---------------------------------------------------

conn = psycopg2.connect(
    host="localhost",
    database="accident_db",
    user="postgres",
    password="yourpassword",
    port="5432"
)

# ---------------------------------------------------
# LOAD ACCIDENT DATA
# ---------------------------------------------------

accident_query = """
SELECT
id,
area,
severity_index,
risk_level,
buffer_color,
ST_AsText(geom) AS geom,
ST_AsText(buffer_geom) AS buffer_geom
FROM accident_data1
WHERE risk_level='High'
"""

accidents = pd.read_sql(accident_query, conn)

accidents["geom"] = accidents["geom"].apply(wkt.loads)
accidents["buffer_geom"] = accidents["buffer_geom"].apply(wkt.loads)

# ---------------------------------------------------
# LOAD DRIVER PATH
# ---------------------------------------------------

driver_query = """
SELECT ST_AsText(geom) AS geom
FROM driver_path
LIMIT 1
"""

driver_df = pd.read_sql(driver_query, conn)

driver_line = wkt.loads(driver_df.iloc[0]["geom"])
driver_points = list(driver_line.coords)

# ---------------------------------------------------
# SESSION STATE
# ---------------------------------------------------

if "step" not in st.session_state:
    st.session_state.step = 0

if "previous_state" not in st.session_state:
    st.session_state.previous_state = "SAFE"

# ---------------------------------------------------
# DRIVER POSITION
# ---------------------------------------------------

driver_location = driver_points[st.session_state.step]
driver_point = Point(driver_location)

# ---------------------------------------------------
# ALERT ENGINE
# ---------------------------------------------------

current_state = "SAFE"

for _, row in accidents.iterrows():

    buffer_polygon = row["buffer_geom"]

    if buffer_polygon.contains(driver_point):
        current_state = "INSIDE"
        break

    elif buffer_polygon.distance(driver_point) < 0.003:  
        current_state = "APPROACHING"

# ---------------------------------------------------
# ALERT MESSAGE LOGIC
# ---------------------------------------------------

alert_message = ""

if current_state == "APPROACHING" and st.session_state.previous_state == "SAFE":
    alert_message = "⚠️ Entering High Risk Area"

elif current_state == "INSIDE" and st.session_state.previous_state != "INSIDE":
    alert_message = "🚨 Entered Accident Prone Area"

elif current_state == "SAFE" and st.session_state.previous_state == "INSIDE":
    alert_message = "✅ Left Accident Prone Area"

st.session_state.previous_state = current_state

# ---------------------------------------------------
# ALERT DISPLAY
# ---------------------------------------------------

if "Entered" in alert_message:
    st.error(alert_message)

elif "Entering" in alert_message:
    st.warning(alert_message)

elif "Left" in alert_message:
    st.success(alert_message)

# ---------------------------------------------------
# MAP INITIALIZATION
# ---------------------------------------------------

m = folium.Map(location=[19.140,72.855], zoom_start=14)

# ---------------------------------------------------
# ACCIDENT POINTS
# ---------------------------------------------------

for _, row in accidents.iterrows():

    lat = row["geom"].y
    lon = row["geom"].x

    folium.CircleMarker(
        location=[lat,lon],
        radius=6,
        color="red",
        fill=True,
        popup=f"""
        Accident Area: {row['area']}
        Severity: {row['severity_index']}
        """
    ).add_to(m)

# ---------------------------------------------------
# BUFFER ZONES
# ---------------------------------------------------

for _, row in accidents.iterrows():

    folium.GeoJson(
        row["buffer_geom"].__geo_interface__,
        style_function=lambda x, color=row["buffer_color"]: {
            "fillColor": color,
            "color": color,
            "fillOpacity":0.35
        }
    ).add_to(m)

# ---------------------------------------------------
# DRIVER PATH LINE
# ---------------------------------------------------

path_latlon = [(y,x) for x,y in driver_points]

folium.PolyLine(
    path_latlon,
    color="blue",
    weight=4,
    opacity=0.7,
    tooltip="Driver Route"
).add_to(m)

# ---------------------------------------------------
# DRIVER MARKER
# ---------------------------------------------------

folium.Marker(
    [driver_location[1],driver_location[0]],
    icon=folium.Icon(color="blue",icon="car"),
    popup="Driver Location"
).add_to(m)

# ---------------------------------------------------
# MAP DISPLAY
# ---------------------------------------------------

st_folium(m,width=1200,height=650)

# ---------------------------------------------------
# CONTROL PANEL
# ---------------------------------------------------

st.subheader("Driver Simulation Controls")

col1,col2,col3 = st.columns(3)

if col1.button("Move Driver"):
    if st.session_state.step < len(driver_points)-1:
        st.session_state.step += 1
        st.rerun()

if col2.button("Auto Drive"):
    for i in range(st.session_state.step,len(driver_points)):
        st.session_state.step = i
        time.sleep(1)
        st.rerun()

if col3.button("Reset Simulation"):
    st.session_state.step = 0
    st.session_state.previous_state = "SAFE"
    st.rerun()
