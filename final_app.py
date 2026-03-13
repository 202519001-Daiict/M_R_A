-- =====================================================
-- 1. ENABLE POSTGIS
-- =====================================================
CREATE EXTENSION IF NOT EXISTS postgis;

-- =====================================================
-- 2. BASE TABLE
-- =====================================================
CREATE TABLE IF NOT EXISTS accident_data1 (
    id INTEGER PRIMARY KEY,
    city TEXT,
    area TEXT,
    location TEXT,
    Accident_2021 INTEGER,
    Accident_2022 INTEGER,
    Accident_2023 INTEGER,
    Total_Accident INTEGER,
    Total_fatality integer,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION
);

-- =====================================================
-- 3. REMOVE INVALID COORDINATES
-- =====================================================
DELETE FROM accident_data1
WHERE latitude IS NULL
   OR longitude IS NULL
   OR latitude = 0
   OR longitude = 0;

-- =====================================================
-- 4. GEOMETRY COLUMN
-- =====================================================
ALTER TABLE accident_data1
ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326);

UPDATE accident_data1
SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326);

-- =====================================================
-- 4. SEVERITY 
-- =====================================================
ALTER TABLE accident_data1
ADD COLUMN IF NOT EXISTS severity_index DECIMAL(6,2);

UPDATE accident_data1
SET severity_index =
    CASE
        WHEN Total_Accident IS NULL OR Total_Accident = 0 THEN 0
        ELSE ROUND((Total_fatality::DECIMAL / Total_Accident) * 100, 2)
    END;

-- =====================================================
-- 5. RISK LEVEL USING PERCENTILES
-- =====================================================
ALTER TABLE accident_data1
ADD COLUMN IF NOT EXISTS risk_level TEXT;

WITH severity_stats AS (
    SELECT
        percentile_cont(0.75) WITHIN GROUP (ORDER BY severity_index) AS high_threshold,
        percentile_cont(0.50) WITHIN GROUP (ORDER BY severity_index) AS medium_threshold
    FROM accident_data1
)
UPDATE accident_data1 a
SET risk_level =
    CASE
        WHEN a.severity_index >= s.high_threshold THEN 'High'
        WHEN a.severity_index >= s.medium_threshold THEN 'Medium'
        ELSE 'Low'
    END
FROM severity_stats s;

-- =====================================================
-- 6. HIGH-RISK BUFFER ZONES (200 FIXED BUFFER)
-- =====================================================
ALTER TABLE accident_data1
ADD COLUMN IF NOT EXISTS buffer_geom geometry(Polygon, 4326);

UPDATE accident_data1
SET buffer_geom = ST_Buffer(geom::geography, 200)::geometry;

CREATE INDEX IF NOT EXISTS idx_buffer_geom
ON accident_data1
USING GIST (buffer_geom);

-- =====================================================
-- 7. LEAFLET VISUALIZATION SUPPORT
-- =====================================================

-- Color coding for map styling
ALTER TABLE accident_data1
ADD COLUMN IF NOT EXISTS buffer_color TEXT;

UPDATE accident_data1
SET buffer_color = CASE
    WHEN risk_level = 'High' THEN '#FF0000'
    WHEN risk_level = 'Medium' THEN '#FFA500'
    ELSE '#FFFF00'
END;


-- View for GeoJSON export (Leaflet ready)
CREATE OR REPLACE VIEW high_risk_accidents AS
SELECT
    id,
    city,
    area,
    location,
    severity_index,
    risk_level,
    buffer_color,
    geom,
    buffer_geom
FROM accident_data1
WHERE risk_level = 'High';

-- =====================================================
-- 8. DRIVER PATH (ANALYTICS ENGINE)
-- =====================================================
CREATE TABLE IF NOT EXISTS driver_path (
    id SERIAL PRIMARY KEY,
    geom geometry(LineString, 4326),
    created_at TIMESTAMP DEFAULT NOW()
);

TRUNCATE TABLE driver_path;

-- Example Driver Route (edit coordinates if needed)
INSERT INTO driver_path (geom)
VALUES (
    ST_SetSRID(
        ST_MakeLine(ARRAY[
            ST_MakePoint(72.856300,19.145503),
            ST_MakePoint(72.855339,19.141947),
            ST_MakePoint(72.854955,19.140313),
            ST_MakePoint(72.855051,19.137911),
            ST_MakePoint(72.855243,19.135797),
            ST_MakePoint(72.855147,19.133586),
            ST_MakePoint(72.855532,19.130607),
            ST_MakePoint(72.855243,19.128781),
            ST_MakePoint(72.855532,19.128589),
            ST_MakePoint(72.855628,19.126475)
        ]),
    4326)
);

CREATE INDEX IF NOT EXISTS idx_driver_geom
ON driver_path
USING GIST (geom);

-- =====================================================
-- 9. DRIVER ALERT SYSTEM (SPATIAL INTELLIGENCE)
-- =====================================================
CREATE OR REPLACE VIEW driver_alerts AS
SELECT
    d.id AS driver_id,
    z.id AS accident_id,
    z.severity_index,
    ROUND(
        ST_Distance(d.geom::geography, z.geom::geography)::NUMERIC,
        2
    ) AS distance_meters,
    CASE
        WHEN ST_Intersects(d.geom, z.buffer_geom)
            THEN 'INSIDE DANGER ZONE'
        WHEN ST_DWithin(d.geom::geography, z.geom::geography, 400)
            THEN 'APPROACHING DANGER ZONE'
        ELSE 'SAFE'
    END AS alert_status,
    CASE
        WHEN z.severity_index >= 20 THEN 'CRITICAL'
        WHEN z.severity_index >= 10 THEN 'HIGH'
        ELSE 'MODERATE'
    END AS alert_level
FROM driver_path d
JOIN accident_data1 z
ON ST_DWithin(d.geom::geography, z.geom::geography, 400)
WHERE z.risk_level = 'High';

-- =====================================================
-- 10. DRIVER RISK SUMMARY (EXPOSURE METRICS)
-- =====================================================
CREATE OR REPLACE VIEW  driver_risk_summary AS
SELECT
    d.id AS driver_id,
    COUNT(z.id) AS high_risk_zones_nearby,
    ROUND(SUM(z.severity_index), 2) AS total_risk_exposure,
    ROUND(AVG(z.severity_index), 2) AS avg_zone_severity
FROM driver_path d
JOIN accident_data1 z
ON ST_DWithin(d.geom::geography, z.geom::geography, 400)
WHERE z.risk_level = 'High'
GROUP BY d.id;

-- =====================================================
-- FINAL CHECK
-- =====================================================
SELECT * FROM driver_alerts;
