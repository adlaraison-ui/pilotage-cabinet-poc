from src.config import load_settings

def test_settings_load():
    s = load_settings()
    assert s.db_path
