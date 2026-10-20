PRAGMA foreign_keys = ON;
CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  mtime TEXT,
  archive_modified_at TEXT,
  kind TEXT NOT NULL,
  parser TEXT,
  priority INTEGER NOT NULL DEFAULT 100,
  status TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  error TEXT
);
CREATE TABLE source_tables (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  headers_json TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE records (
  id TEXT PRIMARY KEY,
  table_id TEXT NOT NULL REFERENCES source_tables(id),
  row_number INTEGER NOT NULL,
  kind TEXT NOT NULL,
  series TEXT,
  model TEXT,
  raw_json TEXT NOT NULL,
  values_json TEXT NOT NULL,
  value_types_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  UNIQUE(table_id, row_number)
);
CREATE TABLE models (
  record_id TEXT PRIMARY KEY REFERENCES records(id),
  series TEXT NOT NULL,
  model TEXT NOT NULL,
  rated_power_kw REAL,
  poles INTEGER,
  voltage_v REAL,
  frequency_hz REAL,
  connection TEXT,
  material_name TEXT,
  calculation_date TEXT,
  source_priority INTEGER NOT NULL,
  is_primary INTEGER NOT NULL,
  normalized_json TEXT NOT NULL
);
CREATE TABLE materials (
  id TEXT PRIMARY KEY,
  table_id TEXT NOT NULL UNIQUE REFERENCES source_tables(id),
  name TEXT NOT NULL,
  source_priority INTEGER NOT NULL,
  is_primary INTEGER NOT NULL,
  frequency_hz REAL,
  frequency_status TEXT NOT NULL,
  loss_unit_source TEXT,
  loss_unit_status TEXT NOT NULL,
  bh_point_count INTEGER NOT NULL DEFAULT 0,
  loss_point_count INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE curve_points (
  id TEXT PRIMARY KEY,
  material_id TEXT NOT NULL REFERENCES materials(id),
  record_id TEXT NOT NULL REFERENCES records(id),
  curve_type TEXT NOT NULL CHECK (curve_type IN ('bh','loss')),
  point_index REAL,
  b_raw REAL NOT NULL,
  y_raw REAL NOT NULL,
  b_unit_source TEXT NOT NULL,
  y_unit_source TEXT NOT NULL,
  b_tesla REAL NOT NULL,
  h_a_per_m REAL,
  loss_value_source REAL,
  unit_status TEXT NOT NULL,
  UNIQUE(material_id, record_id, curve_type)
);
CREATE TABLE issues (
  id INTEGER PRIMARY KEY,
  severity TEXT NOT NULL,
  code TEXT NOT NULL,
  source_id TEXT REFERENCES sources(id),
  table_id TEXT REFERENCES source_tables(id),
  record_id TEXT REFERENCES records(id),
  detail_json TEXT NOT NULL
);
CREATE INDEX ix_records_hash ON records(content_sha256);
CREATE INDEX ix_records_kind ON records(kind);
CREATE INDEX ix_models_lookup ON models(series,model);
CREATE INDEX ix_models_primary ON models(is_primary,series,rated_power_kw,poles);
CREATE INDEX ix_material_name ON materials(name,is_primary);
CREATE INDEX ix_curve_points ON curve_points(material_id,curve_type,b_tesla);
CREATE VIEW model_catalog AS
SELECT m.*,r.table_id,r.row_number,r.raw_json,r.content_sha256,
       t.name AS source_table,s.path AS source_file,s.sha256 AS source_sha256
FROM models m JOIN records r ON r.id=m.record_id
JOIN source_tables t ON t.id=r.table_id JOIN sources s ON s.id=t.source_id;
CREATE VIEW material_catalog AS
SELECT m.*,t.name AS source_table,s.path AS source_file,s.sha256 AS source_sha256
FROM materials m JOIN source_tables t ON t.id=m.table_id
JOIN sources s ON s.id=t.source_id;
