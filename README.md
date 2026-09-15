# Mumbai Spatial Safety Engine

**Geospatial Road Accident Analysis and Driver Alert System**

A geospatial analytics platform that transforms historical road accident
data into actionable spatial intelligence using PostgreSQL/PostGIS,
Python, Supabase, Streamlit, and Metabase.

------------------------------------------------------------------------

## Why this Project?

Road accident datasets are generally available as static records and
reports, making them difficult to use for real-time decision-making.
This project demonstrates how historical accident data can be converted
into an interactive GIS-based application that helps visualize
accident-prone areas, assess road risk, and simulate driver movement.

The project showcases practical implementation of geospatial analytics,
spatial databases, backend integration, interactive dashboards, and
business intelligence in a single end-to-end system.

------------------------------------------------------------------------

## Project Overview

The system uses accident data from Mumbai (2021--2023) to:

-   Identify accident hotspots
-   Calculate a Severity Index
-   Classify locations into High, Medium, and Low risk
-   Generate 200 m spatial risk buffers
-   Simulate driver movement
-   Detect intersections with risk zones
-   Generate real-time driver alerts
-   Visualize insights through interactive dashboards

------------------------------------------------------------------------

## System Architecture

``` text
Historical Accident Data
          │
          ▼
PostgreSQL + PostGIS (pgAdmin)
Spatial Database & GIS Queries
          │
          ▼
Supabase
Backend & Database API
          │
          ▼
Python Processing Layer
GeoPandas • Shapely • Geopy
          │
          ▼
Streamlit Application
Leaflet + OpenStreetMap
          │
          ▼
Driver Simulation
          │
          ▼
Risk Alerts
```

------------------------------------------------------------------------

## System Workflow

``` text
Accident Records
      │
      ▼
Data Cleaning
      │
      ▼
Severity Index Calculation
      │
      ▼
Risk Classification
      │
      ▼
200 m Buffer Generation
      │
      ▼
Driver Route Simulation
      │
      ▼
Spatial Intersection Analysis
      │
      ▼
Real-Time Alert Generation
```

------------------------------------------------------------------------

## Technology Stack

| Category | Technologies |
|----------|--------------|
| Database | PostgreSQL, PostGIS, pgAdmin |
| Backend | Supabase |
| Programming | Python |
| Geospatial Libraries | GeoPandas, Shapely, Geopy |
| Frontend | Streamlit |
| Mapping | Leaflet, OpenStreetMap |
| Business Intelligence | Metabase |
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## Key Features

-   Accident hotspot visualization
-   Severity Index calculation
-   Risk classification
-   200 m spatial buffer generation
-   Driver route simulation
-   Real-time proximity-based alerts
-   Interactive GIS maps
-   Analytics dashboard with filters and KPIs

------------------------------------------------------------------------

## End-to-End Data Flow

``` text
Historical Accident Data
        │
        ▼
PostgreSQL + PostGIS
        │
        ▼
Supabase API
        │
        ▼
Python Spatial Processing
        │
        ▼
Streamlit Frontend
        │
        ▼
Leaflet Visualization
        │
        ▼
Driver Simulation
        │
        ▼
Risk Alerts
```

------------------------------------------------------------------------

## Dashboard

**Application Link**

Paste your Streamlit application link here.

**Analytics Dashboard**

Paste your dashboard link here.

**Note**

The application and dashboard depend on a live PostgreSQL/PostGIS
database connected through Supabase. The shared links will work only
when the backend database and application services are running.

------------------------------------------------------------------------

## Repository Structure

``` text
Mumbai-Spatial-Safety-Engine/
│
├── app/
├── database/
├── dashboard/
├── data/
├── assets/
├── requirements.txt
└── README.md
```

Update the structure above according to the repository.

------------------------------------------------------------------------

## Project Preview

Add screenshots or GIFs for:

-   Interactive map
-   Driver simulation
-   Alert system
-   Analytics dashboard

------------------------------------------------------------------------

## Team Members

-   Dhruv S. Soni
-   Mehul B. Chaudhary
-   Yash D. Daslaniya
-   Maharshi K. Patel

------------------------------------------------------------------------

## Skills Demonstrated

-   Geospatial Analysis (GIS)
-   PostgreSQL & PostGIS
-   Spatial SQL
-   Database Design
-   Backend Development
-   Supabase Integration
-   Python Programming
-   GeoPandas
-   Shapely
-   Streamlit Development
-   Interactive Mapping
-   Business Intelligence (Metabase)
-   Data Engineering

------------------------------------------------------------------------

## Future Improvements

-   Live GPS integration
-   Real-time traffic data
-   Weather-aware risk analysis
-   Machine learning-based accident prediction
-   Mobile application support
