from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd

from src.config import Settings
from src.security import hash_password

SAMPLE_DIR = Path("data/sample")

def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

def seed_demo_if_empty(conn: sqlite3.Connection, settings: Settings) -> None:
    # Seed uniquement si base vide en users (signal simple)
    if _table_count(conn, "users") > 0:
        return
    seed_from_csv(conn, settings=settings, sample_dir=SAMPLE_DIR)

def reset_demo(conn: sqlite3.Connection, settings: Settings, sample_dir: Path = SAMPLE_DIR) -> None:
    # Drop “soft” (V1) : truncate tables en respectant FK
    conn.execute("DELETE FROM time_entries;")
    conn.execute("DELETE FROM mission_assignments;")
    conn.execute("DELETE FROM mission_leads;")
    conn.execute("DELETE FROM missions;")
    conn.execute("DELETE FROM clients;")
    conn.execute("DELETE FROM capacity_overrides;")
    conn.execute("DELETE FROM users;")
    seed_from_csv(conn, settings=settings, sample_dir=sample_dir)

def seed_from_csv(conn: sqlite3.Connection, settings: Settings, sample_dir: Path) -> None:
    enc = settings.csv_encoding
    sample_dir.mkdir(parents=True, exist_ok=True)

    # ---- Users
    users = pd.read_csv(sample_dir / "users.csv", encoding=enc, sep=None, engine="python")
    users.columns = [c.strip().lstrip("\ufeff") for c in users.columns]  # trim + BOM-safe

    for _, r in users.iterrows():
        pwd_clear = str(r.get("password_clear", "")).strip() or settings.demo_admin_password
        pwd_hash = hash_password(pwd_clear, rounds=settings.bcrypt_rounds)
        conn.execute(
            """INSERT INTO users(username, full_name, email, role, is_active, password_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (r["username"], r["full_name"], r.get("email", None), r["role"], int(r.get("is_active", 1)), pwd_hash),
        )

    # ---- Clients
    clients = pd.read_csv(sample_dir / "clients.csv", encoding=enc, sep=None, engine="python")
    clients.columns = [c.strip().lstrip("\ufeff") for c in clients.columns]

    for _, r in clients.iterrows():
        conn.execute(
            "INSERT INTO clients(name, is_active) VALUES (?, ?)",
            (r["name"], int(r.get("is_active", 1))),
        )

    # ---- Missions
    missions = pd.read_csv(sample_dir / "missions.csv", encoding=enc, sep=None, engine="python")
    missions.columns = [c.strip().lstrip("\ufeff") for c in missions.columns]

    for _, r in missions.iterrows():
        client_id = conn.execute("SELECT id FROM clients WHERE name=?", (r["client_name"],)).fetchone()["id"]
        conn.execute(
            """INSERT INTO missions(client_id, code, name, status, start_date, end_date,
                                   sold_days, sold_amount_eur, daily_cost_eur, is_active, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                client_id, r["code"], r["name"], r.get("status", "ongoing"),
                r["start_date"], (r.get("end_date", None) if pd.notna(r.get("end_date", None)) else None),
                float(r.get("sold_days", 0)), float(r.get("sold_amount_eur", 0)), float(r.get("daily_cost_eur", 0)),
                int(r.get("is_active", 1)), (r.get("notes", None) if pd.notna(r.get("notes", None)) else None)
            ),
        )

    # ---- Leads
    leads = pd.read_csv(sample_dir / "mission_leads.csv", encoding=enc, sep=None, engine="python")
    leads.columns = [c.strip().lstrip("\ufeff") for c in leads.columns]

    for _, r in leads.iterrows():
        mid = conn.execute("SELECT id FROM missions WHERE code=?", (r["mission_code"],)).fetchone()["id"]
        uid = conn.execute("SELECT id FROM users WHERE username=?", (r["lead_username"],)).fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO mission_leads(mission_id, user_id) VALUES (?, ?)", (mid, uid))

    # ---- Assignments
    assign = pd.read_csv(sample_dir / "mission_assignments.csv", encoding=enc, sep=None, engine="python")
    assign.columns = [c.strip().lstrip("\ufeff") for c in assign.columns]

    for _, r in assign.iterrows():
        mid = conn.execute("SELECT id FROM missions WHERE code=?", (r["mission_code"],)).fetchone()["id"]
        uid = conn.execute("SELECT id FROM users WHERE username=?", (r["username"],)).fetchone()["id"]
        conn.execute(
            """INSERT INTO mission_assignments(mission_id, user_id, start_date, end_date, allocation_pct)
               VALUES (?, ?, ?, ?, ?)""",
            (mid, uid, r["start_date"], (r.get("end_date", None) if pd.notna(r.get("end_date", None)) else None), int(r.get("allocation_pct", 100))),
        )

    # ---- Time entries
    te = pd.read_csv(sample_dir / "time_entries.csv", encoding=enc, sep=None, engine="python")
    te.columns = [c.strip().lstrip("\ufeff") for c in te.columns]

    for _, r in te.iterrows():
        uid = conn.execute("SELECT id FROM users WHERE username=?", (r["username"],)).fetchone()["id"]
        mission_id = None
        if pd.notna(r.get("mission_code", None)) and str(r.get("mission_code", "")).strip():
            mission_id = conn.execute("SELECT id FROM missions WHERE code=?", (r["mission_code"],)).fetchone()["id"]

        conn.execute(
            """INSERT OR IGNORE INTO time_entries(entry_date, user_id, mission_id, category, hours, description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (r["entry_date"], uid, mission_id, r["category"], int(r["hours"]), r.get("description", None)),
        )

    # ---- Capacity overrides
    cap_path = sample_dir / "capacity_overrides.csv"
    if cap_path.exists():
        caps = pd.read_csv(cap_path, encoding=enc)
        for _, r in caps.iterrows():
            uid = conn.execute("SELECT id FROM users WHERE username=?", (r["username"],)).fetchone()["id"]
            conn.execute(
                """INSERT OR IGNORE INTO capacity_overrides(user_id, cap_date, capacity_h, reason)
                   VALUES (?, ?, ?, ?)""",
                (uid, r["cap_date"], int(r["capacity_h"]), r.get("reason", None)),
            )
