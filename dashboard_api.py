#!/usr/bin/env python3
import pathlib
BASE_DIR = pathlib.Path(__file__).parent
"""
dashboard_api.py — JPROP Investor Dashboard API
FastAPI backend. Run with: uvicorn dashboard_api:app --host 0.0.0.0 --port 8000

Environment variables (add to Render):
    DASHBOARD_SECRET   — random secret for signing tokens (required)
    ADMIN_EMAIL        — WJ's admin login email
    ADMIN_PASSWORD     — WJ's admin login password
    DB_PATH            — path to SQLite file (default: jprop.db)
    TOKEN_JPROP / TOKEN_AM / TOKEN_TONY — Meta API tokens (same as bot)
"""
import os, hashlib, hmac
from datetime import datetime, timezone, date
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db import (
    init_db, create_user, verify_user, get_user, list_users, delete_user,
    update_password, assign_campaign, unassign_campaign,
    get_assignments, get_all_assignments,
    get_profit, set_profit, list_profits,
)
from meta_api import (
    ACCOUNTS, fetch_all_accounts, fetch_single_account,
    get_actions_value, LEAD_ACTION_TYPES,
)

app = FastAPI(title="JPROP Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

SECRET         = os.environ.get("DASHBOARD_SECRET", "change-me-now")
ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL", "wj@jprop.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "jprop2024")

# ── Startup ──────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    create_user("WJ Admin", ADMIN_EMAIL, ADMIN_PASSWORD, "admin")  # no-op if exists

# ── Token helpers ──────────────────────────────────────────

def _make_token(user_id: int, role: str) -> str:
    ts = int(datetime.now(timezone.utc).timestamp())
    payload = f"{user_id}:{role}:{ts}"
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"

def _decode_token(token: str) -> dict | None:
    try:
        uid, role, ts, sig = token.split(":")
        payload = f"{uid}:{role}:{ts}"
        expected = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(datetime.now(timezone.utc).timestamp()) - int(ts) > 86400 * 30:
            return None
        return {"user_id": int(uid), "role": role}
    except Exception:
        return None

def current_user(request: Request) -> dict:
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    data  = _decode_token(token)
    if not data:
        raise HTTPException(401, "Not authenticated")
    return data

def admin_only(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Admin only")
    return user

# ── Auth ─────────────────────────────────────────────────

class LoginReq(BaseModel):
    email: str
    password: str

@app.post("/api/login")
def login(req: LoginReq):
    user = verify_user(req.email, req.password)
    if not user:
        raise HTTPException(401, "Invalid email or password")
    return {
        "token": _make_token(user["id"], user["role"]),
        "role":  user["role"],
        "name":  user["name"],
    }

# ── Admin: investors ─────────────────────────────────────────

class CreateInvestorReq(BaseModel):
    name: str
    email: str
    password: str

@app.post("/api/admin/investors")
def create_investor(req: CreateInvestorReq, _=Depends(admin_only)):
    result = create_user(req.name, req.email, req.password, "investor")
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    return {"ok": True}

@app.get("/api/admin/investors")
def get_investors(_=Depends(admin_only)):
    return list_users()

@app.delete("/api/admin/investors/{user_id}")
def del_investor(user_id: int, _=Depends(admin_only)):
    delete_user(user_id)
    return {"ok": True}

class ResetPwReq(BaseModel):
    new_password: str

@app.post("/api/admin/investors/{user_id}/reset-password")
def reset_pw(user_id: int, req: ResetPwReq, _=Depends(admin_only)):
    update_password(user_id, req.new_password)
    return {"ok": True}

# ── Admin: campaigns (live from Meta) ─────────────────────

@app.get("/api/admin/campaigns")
async def all_campaigns(preset: str = "last_week_sun_sat", since: str = "", until: str = "", _=Depends(admin_only)):
    """All campaigns across all accounts — for overview and assignment UI."""
    if preset == "this_year":
        today = date.today()
        since = f"{today.year}-01-01"
        until = today.strftime("%Y-%m-%d")
    results = await fetch_all_accounts(preset)
    out = []
    for i, r in enumerate(results):
        for c in (r.get("data") or []):
            cid  = c.get("campaign_id", "")
            sp   = float(c.get("spend", 0))
            ld   = get_actions_value(c.get("actions") or [], LEAD_ACTION_TYPES)
            imp  = int(c.get("impressions", 0))
            hook = get_actions_value(c.get("actions") or [], {"video_view"})
            hook_pct = round(hook / imp * 100, 1) if imp else 0
            out.append({
                "account_idx":      i,
                "account_label":    r["label"],
                "campaign_id":      cid,
                "campaign_name":    c.get("campaign_name", ""),
                "spend":            round(sp, 2),
                "leads":            int(ld),
                "cpl":              round(sp / ld, 2) if ld else 0,
                "cpm":              round(float(c.get("cpm", 0)), 2),
                "ctr":              round(float(c.get("inline_link_click_ctr") or c.get("link_ctr") or 0), 2),
                "impressions":      imp,
                "hook_pct":         hook_pct,
                "daily_budget":     r.get("budget_map", {}).get(cid, 0),
                "effective_status": c.get("effective_status", "ACTIVE"),
                "updated_time":     c.get("updated_time", ""),
            })
    return out

# ── Admin: assignments ─────────────────────────────────────────

class AssignReq(BaseModel):
    user_id:       int
    account_idx:   int
    campaign_id:   str
    campaign_name: str

@app.post("/api/admin/assign")
def do_assign(req: AssignReq, _=Depends(admin_only)):
    assign_campaign(req.user_id, req.account_idx, req.campaign_id, req.campaign_name)
    return {"ok": True}

@app.delete("/api/admin/assign")
def do_unassign(user_id: int, campaign_id: str, _=Depends(admin_only)):
    unassign_campaign(user_id, campaign_id)
    return {"ok": True}

@app.get("/api/admin/assignments/{user_id}")
def investor_assignments(user_id: int, _=Depends(admin_only)):
    return get_assignments(user_id)

# ── Admin: profit entry ─────────────────────────────────────────

class ProfitReq(BaseModel):
    campaign_id:   str
    campaign_name: str
    closing_sales: int   = 0
    nett_sales:    float = 0
    commission:    float = 0
    notes:         str   = ""

@app.post("/api/admin/profit")
def save_profit(req: ProfitReq, _=Depends(admin_only)):
    set_profit(req.campaign_id, req.campaign_name,
               req.closing_sales, req.nett_sales,
               req.commission, req.notes)
    return {"ok": True}

@app.get("/api/admin/profits")
def all_profits(_=Depends(admin_only)):
    return list_profits()

# ── Investor: dashboard ─────────────────────────────────────────

@app.get("/api/dashboard")
async def investor_dashboard(preset: str = "last_week_sun_sat", user=Depends(current_user)):
    assignments = get_assignments(user["user_id"])
    if not assignments:
        return {"campaigns": [], "totals": {"spend": 0, "leads": 0, "cpl": 0, "closing_sales": 0, "nett_sales": 0, "commission": 0, "roi": 0}}

    by_acc: dict[int, set] = {}
    for a in assignments:
        by_acc.setdefault(a["account_idx"], set()).add(a["campaign_id"])

    campaigns = []
    for acc_idx, cids in by_acc.items():
        r = await fetch_single_account(acc_idx, preset)
        for c in (r.get("data") or []):
            if c.get("campaign_id") not in cids:
                continue
            sp  = float(c.get("spend", 0))
            ld  = get_actions_value(c.get("actions") or [], LEAD_ACTION_TYPES)
            cpl = round(sp / ld, 2) if ld else 0
            p   = get_profit(c.get("campaign_id", ""))
            closing_sales = p.get("closing_sales", 0)
            nett_sales    = p.get("nett_sales", 0)
            commission    = p.get("commission", 0)
            roi           = round(commission / sp, 2) if sp and commission else 0
            campaigns.append({
                "campaign_id":   c.get("campaign_id"),
                "campaign_name": c.get("campaign_name"),
                "account":       r["label"],
                "spend":         round(sp, 2),
                "leads":         int(ld),
                "cpl":           cpl,
                "cpm":           round(float(c.get("cpm", 0)), 2),
                "ctr":           round(float(c.get("inline_link_click_ctr") or c.get("link_ctr") or 0), 2),
                "closing_sales": closing_sales,
                "nett_sales":    nett_sales,
                "commission":    commission,
                "roi":           roi,
                "notes":         p.get("notes", ""),
                "updated_at":    p.get("updated_at", ""),
            })

    totals = {
        "spend":         round(sum(c["spend"]         for c in campaigns), 2),
        "leads":         sum(c["leads"]         for c in campaigns),
        "closing_sales": sum(c["closing_sales"] for c in campaigns),
        "nett_sales":    round(sum(c["nett_sales"]    for c in campaigns), 2),
        "commission":    round(sum(c["commission"]    for c in campaigns), 2),
    }
    totals["cpl"] = round(totals["spend"] / totals["leads"], 2) if totals["leads"] else 0
    totals["roi"] = round(totals["commission"] / totals["spend"], 2) if totals["spend"] and totals["commission"] else 0
    return {"campaigns": campaigns, "totals": totals}

# ── Serve HTML files ───────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    return FileResponse(BASE_DIR / "dashboard.html")

@app.get("/admin", response_class=HTMLResponse)
def serve_admin():
    return FileResponse(BASE_DIR / "admin.html")

@app.get("/weekly-report", response_class=HTMLResponse)
def serve_weekly_report():
    return FileResponse(BASE_DIR / "weekly-report.html")
