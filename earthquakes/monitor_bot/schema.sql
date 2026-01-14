-- Earthquake Monitor Bot - Database Schema
-- Version: 1.0.0
-- For local development and testing

-- Drop existing tables (careful!)
-- DROP TABLE IF EXISTS market_reactions CASCADE;
-- DROP TABLE IF EXISTS source_reports CASCADE;
-- DROP TABLE IF EXISTS earthquake_events CASCADE;

-- Main table: deduplicated earthquake events
CREATE TABLE IF NOT EXISTS earthquake_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Best available data (updated as more sources report)
    best_magnitude DECIMAL(3,1) NOT NULL,
    best_magnitude_type VARCHAR(10),
    latitude DECIMAL(8,5) NOT NULL,
    longitude DECIMAL(8,5) NOT NULL,
    depth_km DECIMAL(6,2),
    location_name TEXT,

    -- Timestamps
    event_time TIMESTAMPTZ NOT NULL,
    first_detected_at TIMESTAMPTZ NOT NULL,
    usgs_published_at TIMESTAMPTZ,

    -- Source-specific IDs
    usgs_id VARCHAR(50) UNIQUE,
    jma_id VARCHAR(50),
    emsc_id VARCHAR(50),
    gfz_id VARCHAR(50),
    geonet_id VARCHAR(50),

    -- Metadata
    source_count INTEGER DEFAULT 1,
    is_significant BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for earthquake_events
CREATE INDEX IF NOT EXISTS idx_events_time ON earthquake_events(event_time DESC);
CREATE INDEX IF NOT EXISTS idx_events_magnitude ON earthquake_events(best_magnitude DESC);
CREATE INDEX IF NOT EXISTS idx_events_significant ON earthquake_events(is_significant) WHERE is_significant = TRUE;
CREATE INDEX IF NOT EXISTS idx_events_usgs ON earthquake_events(usgs_id) WHERE usgs_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_events_first_detected ON earthquake_events(first_detected_at DESC);

-- Table: raw source reports
CREATE TABLE IF NOT EXISTS source_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID REFERENCES earthquake_events(event_id) ON DELETE CASCADE,

    -- Source identification
    source VARCHAR(20) NOT NULL,
    source_event_id VARCHAR(100),

    -- Reported values
    magnitude DECIMAL(3,1) NOT NULL,
    magnitude_type VARCHAR(10),
    latitude DECIMAL(8,5),
    longitude DECIMAL(8,5),
    depth_km DECIMAL(6,2),
    location_name TEXT,

    -- Timestamps
    event_time TIMESTAMPTZ NOT NULL,
    reported_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL,

    -- Raw data for debugging
    raw_data JSONB,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(source, source_event_id)
);

-- Indexes for source_reports
CREATE INDEX IF NOT EXISTS idx_reports_event ON source_reports(event_id);
CREATE INDEX IF NOT EXISTS idx_reports_source ON source_reports(source);
CREATE INDEX IF NOT EXISTS idx_reports_received ON source_reports(received_at DESC);

-- Table: market reactions (for future use)
CREATE TABLE IF NOT EXISTS market_reactions (
    reaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID REFERENCES earthquake_events(event_id) ON DELETE CASCADE,

    -- Market info
    market_slug VARCHAR(200) NOT NULL,
    outcome VARCHAR(50) NOT NULL,
    token_id VARCHAR(100),

    -- Prices
    price_at_detection DECIMAL(5,4),
    price_at_usgs DECIMAL(5,4),
    price_1h_after DECIMAL(5,4),
    price_final DECIMAL(5,4),

    -- Timing
    detected_at TIMESTAMPTZ NOT NULL,
    usgs_published_at TIMESTAMPTZ,

    -- Analysis
    edge_minutes DECIMAL(6,2),
    price_move_pct DECIMAL(5,2),

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for market_reactions
CREATE INDEX IF NOT EXISTS idx_reactions_event ON market_reactions(event_id);
CREATE INDEX IF NOT EXISTS idx_reactions_market ON market_reactions(market_slug);

-- View: extended history
CREATE OR REPLACE VIEW extended_history AS
SELECT
    COALESCE(e.usgs_id, e.event_id::text) as id,
    e.event_time as time,
    e.best_magnitude as magnitude,
    e.best_magnitude_type as mag_type,
    e.latitude,
    e.longitude,
    e.depth_km,
    e.location_name as place,
    e.usgs_id IS NOT NULL as in_usgs,
    e.source_count,
    e.first_detected_at,
    e.usgs_published_at,
    CASE
        WHEN e.usgs_published_at IS NOT NULL
        THEN EXTRACT(EPOCH FROM (e.usgs_published_at - e.first_detected_at))/60
        ELSE NULL
    END as detection_advantage_minutes
FROM earthquake_events e
WHERE e.best_magnitude >= 6.0
ORDER BY e.event_time DESC;

-- View: source performance statistics
CREATE OR REPLACE VIEW source_performance AS
SELECT
    source,
    COUNT(*) as total_reports,
    AVG(EXTRACT(EPOCH FROM (received_at - event_time))) as avg_delay_seconds,
    MIN(EXTRACT(EPOCH FROM (received_at - event_time))) as min_delay_seconds,
    MAX(EXTRACT(EPOCH FROM (received_at - event_time))) as max_delay_seconds
FROM source_reports
GROUP BY source;

-- Grant permissions (adjust as needed)
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO postgres;

-- Success message
SELECT 'Database schema created successfully!' as status;
