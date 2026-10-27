"""SQLite storage: users, their contacts, and share links."""
import os
import secrets
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "dialify.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    default_cc TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT DEFAULT '',
    phone TEXT NOT NULL,
    tags TEXT DEFAULT '',
    status TEXT DEFAULT 'new',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, phone)
);
CREATE TABLE IF NOT EXISTS shares (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db() -> None:
    con = get_db()
    con.executescript(SCHEMA)
    try:  # migration for databases created before the name field existed
        con.execute("ALTER TABLE users ADD COLUMN name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()


# ---- users ----

def create_user(email: str, password_hash: str, name: str = "") -> int | None:
    con = get_db()
    try:
        cur = con.execute(
            "INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)",
            (email.lower().strip(), password_hash, name.strip()),
        )
        con.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        con.close()


def get_user_by_email(email: str):
    con = get_db()
    row = con.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
    con.close()
    return row


def get_user(user_id: int):
    con = get_db()
    row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    con.close()
    return row


def set_default_cc(user_id: int, cc: str) -> None:
    con = get_db()
    con.execute("UPDATE users SET default_cc = ? WHERE id = ?", (cc.strip(), user_id))
    con.commit()
    con.close()


# ---- contacts ----

def list_contacts(user_id: int) -> list[dict]:
    con = get_db()
    rows = con.execute(
        "SELECT id, name, phone, tags, status FROM contacts WHERE user_id = ? ORDER BY name, id",
        (user_id,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def save_contacts(user_id: int, contacts: list[dict], replace: bool) -> int:
    """Insert contacts, skipping phones the user already has. Returns inserted count."""
    con = get_db()
    if replace:
        con.execute("DELETE FROM contacts WHERE user_id = ?", (user_id,))
    inserted = 0
    for c in contacts:
        try:
            con.execute(
                "INSERT INTO contacts (user_id, name, phone) VALUES (?, ?, ?)",
                (user_id, (c.get("name") or "").strip(), c["phone"]),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass  # duplicate phone for this user
    con.commit()
    con.close()
    return inserted


def update_contact(user_id: int, contact_id: int, fields: dict) -> None:
    allowed = {k: v for k, v in fields.items() if k in ("name", "tags", "status")}
    if not allowed:
        return
    sets = ", ".join(f"{k} = ?" for k in allowed)
    con = get_db()
    con.execute(
        f"UPDATE contacts SET {sets} WHERE id = ? AND user_id = ?",
        (*allowed.values(), contact_id, user_id),
    )
    con.commit()
    con.close()


def delete_contact(user_id: int, contact_id: int) -> None:
    con = get_db()
    con.execute("DELETE FROM contacts WHERE id = ? AND user_id = ?", (contact_id, user_id))
    con.commit()
    con.close()


# ---- share links ----

def create_share(user_id: int) -> str:
    con = get_db()
    row = con.execute("SELECT token FROM shares WHERE user_id = ?", (user_id,)).fetchone()
    if row:
        con.close()
        return row["token"]
    token = secrets.token_urlsafe(16)
    con.execute("INSERT INTO shares (token, user_id) VALUES (?, ?)", (token, user_id))
    con.commit()
    con.close()
    return token


def revoke_share(user_id: int) -> None:
    con = get_db()
    con.execute("DELETE FROM shares WHERE user_id = ?", (user_id,))
    con.commit()
    con.close()


def get_share_owner(token: str):
    con = get_db()
    row = con.execute("SELECT user_id FROM shares WHERE token = ?", (token,)).fetchone()
    con.close()
    return row["user_id"] if row else None
