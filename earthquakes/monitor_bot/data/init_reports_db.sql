-- Инициализация БД для логирования source reports
-- Запуск: psql -h 62.112.10.73 -U postgres -f init_reports_db.sql

-- CREATE DATABASE earthquake_reports;  -- уже создана
-- \c earthquake_reports

CREATE TABLE IF NOT EXISTS source_reports (
    id SERIAL PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source VARCHAR(10) NOT NULL,
    source_event_id VARCHAR(100) NOT NULL,
    magnitude FLOAT NOT NULL,
    magnitude_type VARCHAR(10),
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    depth_km FLOAT,
    location_name VARCHAR(200),
    event_time TIMESTAMPTZ NOT NULL,
    reported_at TIMESTAMPTZ,
    matched_event_id UUID,
    is_new_event BOOLEAN NOT NULL DEFAULT FALSE,
    usgs_confirmed BOOLEAN,
    usgs_magnitude FLOAT,
    usgs_event_time TIMESTAMPTZ,
    usgs_latitude FLOAT,
    usgs_longitude FLOAT,
    usgs_depth_km FLOAT,
    usgs_confirmed_at TIMESTAMPTZ,
    UNIQUE (source, source_event_id)
);

CREATE INDEX IF NOT EXISTS idx_sr_event_time ON source_reports(event_time);
CREATE INDEX IF NOT EXISTS idx_sr_source ON source_reports(source);
CREATE INDEX IF NOT EXISTS idx_sr_matched_event ON source_reports(matched_event_id);
CREATE INDEX IF NOT EXISTS idx_sr_received ON source_reports(received_at);

CREATE TABLE IF NOT EXISTS events (
    event_id UUID PRIMARY KEY,
    event_time TIMESTAMPTZ NOT NULL,
    best_magnitude FLOAT NOT NULL,
    best_magnitude_type VARCHAR(10),
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    depth_km FLOAT,
    location_name VARCHAR(200),
    first_detected_at TIMESTAMPTZ NOT NULL,
    first_source VARCHAR(10) NOT NULL,
    source_count INT DEFAULT 1,
    usgs_id VARCHAR(50),
    usgs_magnitude FLOAT,
    usgs_published_at TIMESTAMPTZ,
    usgs_event_time TIMESTAMPTZ,
    usgs_latitude FLOAT,
    usgs_longitude FLOAT,
    usgs_depth_km FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time);
CREATE INDEX IF NOT EXISTS idx_events_usgs ON events(usgs_id);

-- Полезные запросы для калибровки:

-- Процент подтверждения USGS по источникам
-- SELECT source,
--        COUNT(*) as total,
--        SUM(CASE WHEN usgs_confirmed THEN 1 ELSE 0 END) as confirmed,
--        ROUND(100.0 * SUM(CASE WHEN usgs_confirmed THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
-- FROM source_reports
-- WHERE is_new_event = true
-- GROUP BY source;

-- Разница магнитуд по источникам
-- SELECT source,
--        ROUND(AVG(magnitude - usgs_magnitude)::numeric, 2) as avg_mag_diff,
--        ROUND(STDDEV(magnitude - usgs_magnitude)::numeric, 2) as std_mag_diff,
--        COUNT(*) as n
-- FROM source_reports
-- WHERE usgs_magnitude IS NOT NULL
-- GROUP BY source;

-- Задержка обнаружения: кто быстрее
-- SELECT source,
--        ROUND(AVG(EXTRACT(EPOCH FROM (received_at - event_time))/60)::numeric, 1) as avg_delay_min,
--        MIN(EXTRACT(EPOCH FROM (received_at - event_time))/60)::numeric as min_delay_min
-- FROM source_reports
-- GROUP BY source ORDER BY avg_delay_min;
