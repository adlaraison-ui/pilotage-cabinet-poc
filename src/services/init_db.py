from __future__ import annotations
import sqlite3
from src.config import Settings

DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('ADMIN','BOARD','LEAD','CONSULTANT')),
  full_name     TEXT NOT NULL,
  email         TEXT,
  is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  created_at    TEXT NOT NULL DEFAULT (date('now'))
);

CREATE TABLE IF NOT EXISTS clients (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL UNIQUE,
  is_active  INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);

CREATE TABLE IF NOT EXISTS missions (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id           INTEGER NOT NULL,
  code               TEXT NOT NULL UNIQUE,
  name               TEXT NOT NULL,
  status             TEXT NOT NULL DEFAULT 'ongoing'
                      CHECK (status IN ('pipeline','ongoing','paused','done','cancelled')),
  start_date         TEXT NOT NULL,
  end_date           TEXT,
  sold_days          REAL NOT NULL DEFAULT 0,
  sold_amount_eur    REAL NOT NULL DEFAULT 0,
  daily_cost_eur     REAL NOT NULL DEFAULT 0,
  notes              TEXT,
  is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE TABLE IF NOT EXISTS mission_leads (
  mission_id INTEGER NOT NULL,
  user_id    INTEGER NOT NULL,
  PRIMARY KEY (mission_id, user_id),
  FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mission_assignments (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id      INTEGER NOT NULL,
  user_id         INTEGER NOT NULL,
  start_date      TEXT NOT NULL,
  end_date        TEXT,
  allocation_pct  INTEGER NOT NULL DEFAULT 100 CHECK (allocation_pct BETWEEN 0 AND 100),
  FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS time_entries (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_date    TEXT NOT NULL,
  user_id       INTEGER NOT NULL,
  mission_id    INTEGER,
  category      TEXT NOT NULL CHECK (category IN ('billable','non_billable_client','internal')),
  hours         INTEGER NOT NULL CHECK (hours IN (1,4,8)),
  description   TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_time_entry_unique_day
ON time_entries(entry_date, user_id, COALESCE(mission_id, -1), category);

CREATE INDEX IF NOT EXISTS ix_time_entries_user_date ON time_entries(user_id, entry_date);
CREATE INDEX IF NOT EXISTS ix_time_entries_mission_date ON time_entries(mission_id, entry_date);

CREATE TABLE IF NOT EXISTS capacity_overrides (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER NOT NULL,
  cap_date     TEXT NOT NULL,
  capacity_h   INTEGER NOT NULL CHECK (capacity_h BETWEEN 0 AND 24),
  reason       TEXT,
  UNIQUE (user_id, cap_date),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- =========================
-- Simulation Board (devis + suivi)
-- =========================

CREATE TABLE IF NOT EXISTS simulations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id INTEGER NULL,                 -- NULL = devis (avant-vente)
  client_name TEXT NOT NULL,
  project_name TEXT NOT NULL,
  sector TEXT,
  start_date TEXT,
  end_date TEXT,

  author_user_id INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','validated','archived')),

  notes TEXT,

  FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE SET NULL,
  FOREIGN KEY (author_user_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_simulations_mission ON simulations(mission_id);
CREATE INDEX IF NOT EXISTS ix_simulations_status ON simulations(status);

CREATE TABLE IF NOT EXISTS simulation_internal_resources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  simulation_id INTEGER NOT NULL,

  resource_name TEXT,                       -- nom ou rôle
  grade TEXT,

  std_rate_per_hour REAL DEFAULT 0,          -- taux standard (vente)
  std_cost_per_hour REAL DEFAULT 0,          -- coût standard (interne)

  planned_days REAL DEFAULT 0,
  hours_per_day REAL NOT NULL DEFAULT 8,

  billable_ratio REAL NOT NULL DEFAULT 1.0,  -- 1.0 = 100% facturable
  non_billable_hours REAL NOT NULL DEFAULT 0,

  FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_sim_int_sim ON simulation_internal_resources(simulation_id);

CREATE TABLE IF NOT EXISTS simulation_external_resources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  simulation_id INTEGER NOT NULL,

  provider_name TEXT,
  role TEXT,

  buy_rate_per_day REAL DEFAULT 0,
  sell_rate_per_day REAL DEFAULT 0,

  planned_days REAL DEFAULT 0,
  hours_per_day REAL NOT NULL DEFAULT 8,

  FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_sim_ext_sim ON simulation_external_resources(simulation_id);

CREATE TABLE IF NOT EXISTS simulation_costs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  simulation_id INTEGER NOT NULL,

  cost_type TEXT NOT NULL CHECK (cost_type IN ('fees','expenses','non_billable','other')),
  label TEXT,
  cost_amount REAL NOT NULL DEFAULT 0,
  refactured_amount REAL NOT NULL DEFAULT 0,

  FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_sim_costs_sim ON simulation_costs(simulation_id);

-- Views KPI
CREATE VIEW IF NOT EXISTS kpi_mission_hours AS
SELECT
  m.id              AS mission_id,
  m.code            AS mission_code,
  m.name            AS mission_name,
  c.name            AS client_name,
  m.status          AS status,
  m.start_date      AS start_date,
  m.end_date        AS end_date,
  m.sold_days       AS sold_days,
  ROUND(m.sold_days * 8.0, 2) AS sold_hours,
  COALESCE(SUM(te.hours), 0)  AS consumed_hours,
  ROUND((COALESCE(SUM(te.hours), 0) / NULLIF(m.sold_days * 8.0, 0)) * 100.0, 1) AS consumed_pct
FROM missions m
JOIN clients c ON c.id = m.client_id
LEFT JOIN time_entries te
  ON te.mission_id = m.id
 AND te.category IN ('billable','non_billable_client')
WHERE m.is_active = 1
GROUP BY m.id;

CREATE VIEW IF NOT EXISTS kpi_mission_variance AS
SELECT
  mission_id,
  mission_code,
  mission_name,
  client_name,
  sold_hours,
  consumed_hours,
  (consumed_hours - sold_hours) AS variance_hours
FROM kpi_mission_hours;

CREATE VIEW IF NOT EXISTS kpi_alert_missions_risk AS
SELECT
  mission_id,
  mission_code,
  mission_name,
  client_name,
  sold_hours,
  consumed_hours,
  variance_hours,
  CASE
    WHEN sold_hours = 0 THEN 'no_sold_load'
    WHEN consumed_hours > sold_hours THEN 'overrun'
    WHEN consumed_hours >= sold_hours * 0.9 THEN 'near_limit'
    ELSE 'ok'
  END AS risk_level
FROM kpi_mission_variance
WHERE (sold_hours = 0) OR (consumed_hours >= sold_hours * 0.9);

CREATE VIEW IF NOT EXISTS kpi_user_load_daily AS
SELECT
  te.entry_date AS day,
  u.id          AS user_id,
  u.full_name   AS user_name,
  SUM(te.hours) AS logged_hours
FROM time_entries te
JOIN users u ON u.id = te.user_id
WHERE u.is_active = 1
GROUP BY te.entry_date, u.id;

CREATE VIEW IF NOT EXISTS kpi_time_by_category_daily AS
SELECT
  entry_date AS day,
  category,
  SUM(hours) AS hours
FROM time_entries
GROUP BY entry_date, category;

CREATE VIEW IF NOT EXISTS kpi_finance_mission AS
SELECT
  m.id AS mission_id,
  m.code AS mission_code,
  m.name AS mission_name,
  c.name AS client_name,
  m.sold_amount_eur AS sold_amount_eur,
  m.daily_cost_eur  AS daily_cost_eur,
  COALESCE(SUM(te.hours), 0) AS consumed_hours,
  ROUND((COALESCE(SUM(te.hours), 0) / 8.0) * m.daily_cost_eur, 2) AS cost_eur,
  ROUND(m.sold_amount_eur - ((COALESCE(SUM(te.hours), 0) / 8.0) * m.daily_cost_eur), 2) AS margin_eur
FROM missions m
JOIN clients c ON c.id = m.client_id
LEFT JOIN time_entries te
  ON te.mission_id = m.id
 AND te.category IN ('billable','non_billable_client')
WHERE m.is_active = 1
GROUP BY m.id;

-- =========================
-- KPI Simulation (Board)
-- =========================

CREATE VIEW IF NOT EXISTS kpi_simulation_summary AS
WITH
internal AS (
  SELECT
    simulation_id,
    SUM(planned_days * hours_per_day) AS planned_hours,
    SUM(planned_days * hours_per_day * billable_ratio) AS billable_hours,
    SUM(planned_days * hours_per_day * std_rate_per_hour * billable_ratio) AS revenue_std,
    SUM(planned_days * hours_per_day * std_cost_per_hour) AS cost_internal
  FROM simulation_internal_resources
  GROUP BY simulation_id
),
external AS (
  SELECT
    simulation_id,
    SUM(planned_days * sell_rate_per_day) AS revenue_external,
    SUM(planned_days * buy_rate_per_day)  AS cost_external
  FROM simulation_external_resources
  GROUP BY simulation_id
),
costs AS (
  SELECT
    simulation_id,
    SUM(cost_amount) AS cost_other,
    SUM(refactured_amount) AS revenue_other
  FROM simulation_costs
  GROUP BY simulation_id
)
SELECT
  s.id AS simulation_id,
  s.mission_id,
  s.client_name,
  s.project_name,
  s.sector,
  s.start_date,
  s.end_date,
  s.status,
  s.created_at,

  COALESCE(i.planned_hours, 0) AS planned_hours,
  COALESCE(i.billable_hours, 0) AS billable_hours,

  -- CA = production std interne (billable) + vente externe + refacturations
  (COALESCE(i.revenue_std, 0) + COALESCE(e.revenue_external, 0) + COALESCE(c.revenue_other, 0)) AS revenue_total,

  -- Coûts = interne + externe + autres
  (COALESCE(i.cost_internal, 0) + COALESCE(e.cost_external, 0) + COALESCE(c.cost_other, 0)) AS cost_total,

  -- Marge
  ((COALESCE(i.revenue_std, 0) + COALESCE(e.revenue_external, 0) + COALESCE(c.revenue_other, 0))
   - (COALESCE(i.cost_internal, 0) + COALESCE(e.cost_external, 0) + COALESCE(c.cost_other, 0))) AS margin_total,

  CASE
    WHEN (COALESCE(i.revenue_std, 0) + COALESCE(e.revenue_external, 0) + COALESCE(c.revenue_other, 0)) = 0 THEN NULL
    ELSE ROUND(
      (
        ((COALESCE(i.revenue_std, 0) + COALESCE(e.revenue_external, 0) + COALESCE(c.revenue_other, 0))
         - (COALESCE(i.cost_internal, 0) + COALESCE(e.cost_external, 0) + COALESCE(c.cost_other, 0)))
        /
        (COALESCE(i.revenue_std, 0) + COALESCE(e.revenue_external, 0) + COALESCE(c.revenue_other, 0))
      ) * 100.0
    , 1)
  END AS margin_pct

FROM simulations s
LEFT JOIN internal i ON i.simulation_id = s.id
LEFT JOIN external e ON e.simulation_id = s.id
LEFT JOIN costs c ON c.simulation_id = s.id;

"""

DEFAULT_SETTINGS = {
    "time.day_hours": "8",
    "ui.default_view": "week",
}

def ensure_schema(conn: sqlite3.Connection, settings: Settings) -> None:
    conn.executescript(DDL)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO app_settings(key, value) VALUES (?, ?)",
            (k, v),
        )
