from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

@dataclass(frozen=True)
class Settings:
    env: str
    db_path: str
    settings_yaml: str
    day_hours: int
    default_view: str
    bcrypt_rounds: int
    csv_encoding: str
    demo_admin_username: str
    demo_admin_password: str

def load_settings() -> Settings:
    load_dotenv(override=False)

    env = os.getenv("APP_ENV", "local")
    db_path = os.getenv("APP_DB_PATH", "data/app.db")
    settings_yaml = os.getenv("APP_SETTINGS_YAML", "configs/settings.example.yaml")

    demo_admin_username = os.getenv("APP_DEMO_ADMIN_USERNAME", "admin")
    demo_admin_password = os.getenv("APP_DEMO_ADMIN_PASSWORD", "admin123")

    p = Path(settings_yaml)
    cfg = {}
    if p.exists():
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    day_hours = int(cfg.get("time", {}).get("day_hours", 8))
    default_view = str(cfg.get("ui", {}).get("default_view", "week"))
    bcrypt_rounds = int(cfg.get("security", {}).get("bcrypt_rounds", 12))
    csv_encoding = str(cfg.get("import", {}).get("csv_encoding", "utf-8"))

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        env=env,
        db_path=db_path,
        settings_yaml=settings_yaml,
        day_hours=day_hours,
        default_view=default_view,
        bcrypt_rounds=bcrypt_rounds,
        csv_encoding=csv_encoding,
        demo_admin_username=demo_admin_username,
        demo_admin_password=demo_admin_password,
    )
