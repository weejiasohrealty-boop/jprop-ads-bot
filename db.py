"""
db.py — SQLite database for JPROP Investor Dashboard
Tables: users, assignments, profits
"""
import sqlite3, hashlib, os

DB_PATH = os.environ.get("DB_PATH", "jprop.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'investor',
            created_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS assignments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            account_idx   INTEGER NOT NULL,
            campaign_id   TEXT NOT NULL,
            campaign_name TEXT NOT NULL,
            UNIQUE(user_id, campaign_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS profits (
            campaign_id    TEXT PRIMARY KEY,
            campaign_name  TEXT,
            closing_sales  INTEGER DEFAULT 0,
            nett_sales     REAL DEFAULT 0,
            commission     REAL DEFAULT 0,
            notes          TEXT DEFAULT '',
            updated_at     TEXT DEFAULT (datetime('now'))
        );
    """)
    c.commit()
    c.close()


def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def create_user(name: str, email: str, password: str, role: str = "investor") -> dict:
    c = _conn()
    try:
        c.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?,?,?,?)",
            (name, email.lower().strip(), _hash(password), role),
        )
        c.commit()
        return {"ok": True}
    except sqlite3.IntegrityError:
        return {"ok": False, "error": "Email already exists"}
    finally:
        c.close()


def verify_user(email: str, password: str) -> dict | None:
    c = _conn()
    row = c.execute(
        "SELECT * FROM users WHERE email=? AND password_hash=?",
        (email.lower().strip(), _hash(password)),
    ).fetchone()
    c.close()
    return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def list_users() -> list:
    c = _conn()
    rows = c.execute(
        "SELECT id, name, email, role, created_at FROM users ORDER BY created_at DESC"
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def delete_user(user_id: int):
    c = _conn()
    c.execute("DELETE FROM users WHERE id=?", (user_id,))
    c.commit()
    c.close()


def update_password(user_id: int, new_password: str):
    c = _conn()
    c.execute("UPDATE users SET password_hash=? WHERE id=?", (_hash(new_password), user_id))
    c.commit()
    c.close()


def assign_campaign(user_id: int, account_idx: int, campaign_id: str, campaign_name: str):
    c = _conn()
    c.execute(
        "INSERT OR IGNORE INTO assignments (user_id, account_idx, campaign_id, campaign_name) VALUES (?,?,?,?)",
        (user_id, account_idx, campaign_id, campaign_name),
    )
    c.commit()
    c.close()


def unassign_campaign(user_id: int, campaign_id: str):
    c = _conn()
    c.execute(
        "DELETE FROM assignments WHERE user_id=? AND campaign_id=?",
        (user_id, campaign_id),
    )
    c.commit()
    c.close()


def get_assignments(user_id: int) -> list:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM assignments WHERE user_id=?", (user_id,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_all_assignments() -> list:
    c = _conn()
    rows = c.execute("""
        SELECT a.*, u.name as investor_name, u.email as investor_email
        FROM assignments a JOIN users u ON a.user_id = u.id
    """).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_profit(campaign_id: str) -> dict:
    c = _conn()
    row = c.execute("SELECT * FROM profits WHERE campaign_id=?", (campaign_id,)).fetchone()
    c.close()
    return dict(row) if row else {
        "campaign_id": campaign_id, "campaign_name": "",
        "closing_sales": 0, "nett_sales": 0, "commission": 0,
        "notes": "", "updated_at": "",
    }


def set_profit(campaign_id: str, campaign_name: str,
               closing_sales: int, nett_sales: float,
               commission: float, notes: str = ""):
    c = _conn()
    c.execute("""
        INSERT INTO profits (campaign_id, campaign_name, closing_sales, nett_sales, commission, notes, updated_at)
        VALUES (?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(campaign_id) DO UPDATE SET
            campaign_name  = excluded.campaign_name,
            closing_sales  = excluded.closing_sales,
            nett_sales     = excluded.nett_sales,
            commission     = excluded.commission,
            notes          = excluded.notes,
            updated_at     = excluded.updated_at
    """, (campaign_id, campaign_name, closing_sales, nett_sales, commission, notes))
    c.commit()
    c.close()


def list_profits() -> list:
    c = _conn()
    rows = c.execute("SELECT * FROM profits ORDER BY updated_at DESC").fetchall()
    c.close()
    return [dict(r) for r in rows]
