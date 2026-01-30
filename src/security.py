from __future__ import annotations
import bcrypt

def hash_password(password_clear: str, rounds: int = 12) -> str:
    salt = bcrypt.gensalt(rounds=rounds)
    hashed = bcrypt.hashpw(password_clear.encode("utf-8"), salt)
    return hashed.decode("utf-8")

def verify_password(password_clear: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password_clear.encode("utf-8"), password_hash.encode("utf-8"))
