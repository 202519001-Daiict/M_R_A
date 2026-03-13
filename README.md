\# 🚦 Road Accident Risk Intelligence System



This project demonstrates a \*\*Geospatial Risk Monitoring Dashboard\*\* that detects when a driver enters accident-prone zones.



The system uses \*\*PostgreSQL + PostGIS\*\* for spatial analysis and \*\*Streamlit\*\* for interactive visualization.



---



\## 📌 Project Objective



To build a system that:



• Stores accident hotspot data  

• Calculates accident severity levels  

• Generates spatial buffer zones around dangerous areas  

• Tracks a driver's route  

• Detects when the driver enters risk zones  

• Displays real-time alerts on a map dashboard  



---



\## 🗺 System Architecture



PostgreSQL + PostGIS  

↓  

Spatial Risk Analysis  

↓  

Streamlit Dashboard  

↓  

OpenStreetMap Visualization  

↓  

Real-time Driver Alerts



---



\## ⚙️ Technologies Used



• PostgreSQL  

• PostGIS  

• Streamlit  

• Python  

• Folium  

• GeoPandas  

• Shapely  

• OpenStreetMap



---



\## 📊 Key Features



\### Accident Risk Analysis

\- Calculates accident \*\*severity index\*\*

\- Classifies areas into \*\*High / Medium / Low risk\*\*



\### Buffer-Based Risk Zones

\- Generates \*\*200m spatial buffer zones\*\*

\- Highlights dangerous locations



\### Driver Path Simulation

\- Uses a real \*\*LineString route\*\*

\- Simulates vehicle movement along the path



\### Intelligent Alert System



The dashboard shows alerts when the driver:



⚠️ Approaches a danger zone  

🚨 Enters an accident-prone area  

✅ Leaves the danger zone  



---



\## 🗂 Database Structure



Main Tables:



• accident\_data1  

• driver\_path  



Views:



• high\_risk\_accidents  

• driver\_alerts  

• driver\_risk\_summary  



---



\## ▶️ Running the Dashboard



Install dependencies:



pip install -r requirements.txt



Run the application:



streamlit run app.py



The dashboard will open in your browser.



---



\## 🧠 Example Dashboard Workflow



1️⃣ Load accident hotspots  

2️⃣ Display risk buffers  

3️⃣ Show driver route  

4️⃣ Simulate driver movement  

5️⃣ Trigger alerts when entering danger zones  



---



\## 📍 Future Improvements



• Real-time GPS driver tracking  

• Traffic data integration  

• Accident heatmaps  

• Machine learning risk prediction  

• Mobile alert notifications  



---



\## 👨‍💻 Author



Geospatial Risk Intelligence Project  

