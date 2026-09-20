"""
db.py — PostgreSQL database for JPROP Investor Dashboard
Tables: users, assignments, profits
"""
import hashlib, os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _conn():
    c = psycopg2.connect(DATABASE_URL)
    return c


def init_db():
    c = _conn()
    cur = c.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            name          TEXT NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'investor',
            created_at    TEXT DEFAULT (NOW()::TEXT)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            id            SERIAL PRIMARY KEY,
            user_id       INTEGER NOT NULL,
            account_idx   INTEGER NOT NULL,
            campaign_id   TEXT NOT NULL,
            campaign_name TEXT NOT NULL,
            UNIQUE(user_id, campaign_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS profits (
            campaign_id    TEXT PRIMARY KEY,
            campaign_name  TEXT,
            closing_sales  INTEGER DEFAULT 0,
            nett_sales     REAL DEFAULT 0,
            commission     REAL DEFAULT 0,
            notes          TEXT DEFAULT '',
            updated_at     TEXT DEFAULT (NOW()::TEXT)
        )
    """)
    c.commit()
    cur.close()
    c.close()


def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def create_user(name: str, email: str, password: str, role: str = "investor") -> dict:
    c = _conn()
    cur = c.cursor()
    try:
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (%s,%s,%s,%s)",
            (name, email.lower().strip(), _hash(password), role),
        )
        c.commit()
        return {"ok": True}
    except psycopg2.IntegrityError:
        c.rollback()
        return {"ok": False, "error": "Email already exists"}
    finally:
        cur.close()
        c.close()


def verify_user(email: str, password: str) -> dict | None:
    c = _conn()
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM users WHERE email=%s AND password_hash=%s",
        (email.lower().strip(), _hash(password)),
    )
    row = cur.fetchone()
    cur.close()
    c.close()
    return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    c = _conn()
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    c.close()
    return dict(row) if row else None


def list_users() -> list:
    c = _conn()
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, name, email, role, created_at FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    c.close()
    return [dict(r) for r in rows]


def delete_user(user_id: int):
    c = _conn()
    cur = c.cursor()
    cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    c.commit()
    cur.close()
    c.close()


def update_password(user_id: int, new_password: str):
    c = _conn()
    cur = c.cursor()
    cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (_hash(new_password), user_id))
    c.commit()
    cur.close()
    c.close()


def assign_campaign(user_id: int, account_idx: int, campaign_id: str, campaign_name: str):
    c = _conn()
    cur = c.cursor()
    cur.execute(
        "INSERT INTO assignments (user_id, account_idx, campaign_id, campaign_name) VALUES (%s,%s,%s,%s) ON CONFLICT (user_id, campaign_id) DO NOTHING",
        (user_id, account_idx, campaign_id, campaign_name),
    )
    c.commit()
    cur.close()
    c.close()


def unassign_campaign(user_id: int, campaign_id: str):
    c = _conn()
    cur = c.cursor()
    cur.execute(
        "DELETE FROM assignments WHERE user_id=%s AND campaign_id=%s",
        (user_id, campaign_id),
    )
    c.commit()
    cur.close()
    c.close()


def get_assignments(user_id: int) -> list:
    c = _conn()
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM assignments WHERE user_id=%s", (user_id,))
    rows = cur.fetchall()
    cur.close()
    c.close()
    return [dict(r) for r in rows]


def get_all_assignments() -> list:
    c = _conn()
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT a.*, u.name as investor_name, u.email as investor_email
        FROM assignments a JOIN users u ON a.user_id = u.id
    """)
    rows = cur.fetchall()
    cur.close()
    c.close()
    return [dict(r) for r in rows]


def get_profit(campaign_id: str) -> dict:
    c = _conn()
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM profits WHERE campaign_id=%s", (campaign_id,))
    row = cur.fetchone()
    cur.close()
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
    cur = c.cursor()
    cur.execute("""
        INSERT INTO profits (campaign_id, campaign_name, closing_sales, nett_sales, commission, notes, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,NOW()::TEXT)
        ON CONFLICT(campaign_id) DO UPDATE SET
            campaign_name  = EXCLUDED.campaign_name,
            closing_sales  = EXCLUDED.closing_sales,
            nett_sales     = EXCLUDED.nett_sales,
            commission     = EXCLUDED.commission,
            notes          = EXCLUDED.notes,
            updated_at     = NOW()::TEXT
    """, (campaign_id, campaign_name, closing_sales, nett_sales, commission, notes))
    c.commit()
    cur.close()
    c.close()


def list_profits() -> list:
    c = _conn()
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM profits ORDER BY updated_at DESC")
    rows = cur.fetchall()
    cur.close()
    c.close()
    return [dict(r) for r in rows]
