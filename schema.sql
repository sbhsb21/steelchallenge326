PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS material (
    slot TEXT PRIMARY KEY,
    stage TEXT NOT NULL CHECK(stage IN ('raw','eaf','secondary')),
    name TEXT,
    price_per_t REAL,
    density_t_m3 REAL CHECK(density_t_m3 > 0),
    composition TEXT NOT NULL DEFAULT '{}',
    source TEXT,
    details TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS recovery (
    slot TEXT REFERENCES material(slot),
    facility TEXT NOT NULL,
    element TEXT NOT NULL,
    fraction REAL NOT NULL CHECK(fraction BETWEEN 0 AND 1),
    evidence TEXT NOT NULL,
    PRIMARY KEY(slot, facility, element)
);
CREATE TABLE IF NOT EXISTS melt_sample (
    id INTEGER PRIMARY KEY,
    slot TEXT NOT NULL REFERENCES material(slot),
    element TEXT NOT NULL,
    wt_pct REAL NOT NULL CHECK(wt_pct BETWEEN 0 AND 100),
    evidence TEXT NOT NULL,
    UNIQUE(slot, element, evidence)
);
CREATE TABLE IF NOT EXISTS grade (
    name TEXT PRIMARY KEY,
    limits TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS route (
    name TEXT PRIMARY KEY,
    specification TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY,
    digest TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    raw_log TEXT NOT NULL,
    prediction TEXT,
    actual TEXT NOT NULL DEFAULT '{}',
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS event (
    run_id INTEGER REFERENCES run(id) ON DELETE CASCADE,
    line INTEGER NOT NULL,
    seconds REAL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    raw TEXT NOT NULL,
    PRIMARY KEY(run_id, line)
);
CREATE TABLE IF NOT EXISTS master_run (
    run_id INTEGER PRIMARY KEY REFERENCES run(id) ON DELETE CASCADE,
    source_key TEXT NOT NULL UNIQUE,
    workbook TEXT NOT NULL,
    result TEXT NOT NULL,
    analysis TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pdf_source (
    source TEXT PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS master_alias (
    source_key TEXT PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS pdf_review (
    source TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    transcript TEXT NOT NULL
);
