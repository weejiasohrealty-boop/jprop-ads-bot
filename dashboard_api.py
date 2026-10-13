#!/usr/bin/env python3
import pathlib
BASE_DIR = pathlib.Path(__file__).parent
"""
dashboard_api.py — JPROP Investor Dashboard API
FastAPI backend. Run with: uvicorn dashboard_api:app --host 0.0.0.0 --port 8000
"""
import os, hashlib, hmac, requests as _req
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
    init_deals, add_deal, list_deals, update_deal, delete_deal,
    list_deal_projects, list_deal_members,
)
from meta_api import (
    ACCOUNTS, fetch_all_accounts, fetch_single_account,
    get_actions_value, get_messages_value, get_leads_value, LEAD_ACTION_TYPES, fetch_trend_data,
)

WHATSAPP_ACTION_TYPES = {
    "onsite_conversion.messaging_conversation_started_7d",
    "messaging_conversation_started_7d",
    "onsite_conversion.total_messaging_connection",
    "onsite_conversion.messaging_first_reply",
}

# ── Telegram ──────────────────────────────────────────────────────────────────
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT    = os.environ.get("TELEGRAM_CHAT_ID", "")
INT_SECRET = os.environ.get("INTERNAL_SECRET", "jprop-internal-2024")

# Benchmarks
BENCH_HOOK_PCT  = 25.0   # Hook% target
BENCH_CTR       = 1.0    # Link CTR% target
BENCH_CPL       = 50.0   # CPL target (RM)
ALERT_CPL       = 100.0  # CPL alert threshold (RM) — triggers immediate Telegram alert
BENCH_CONV      = 10.0   # Conversion rate % (leads/clicks) target

def _send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        _req.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return True
    except Exception:
        return False

def _build_report(campaigns: list) -> str:
    active = [c for c in campaigns if c.get("spend", 0) > 0]
    total_spend  = sum(c["spend"]    for c in active)
    total_leads  = sum(c["leads"]    for c in active)
    total_msgs   = sum(c["messages"] for c in active)
    avg_cpl = round(total_spend / total_leads, 2) if total_leads else 0

    today = date.today().strftime("%d %b %Y")
    lines = [f"🏢 <b>JPROP Daily Report — {today}</b>\n"]

    # Overview
    cpl_icon = "✅" if avg_cpl <= BENCH_CPL else "🔴"
    lines.append("📊 <b>Overview (This Week)</b>")
    lines.append(f"Total Spend: RM {total_spend:,.0f}")
    lines.append(f"Total Leads: {total_leads}  |  Messages: {total_msgs}")
    lines.append(f"Avg CPL: RM {avg_cpl} {cpl_icon}\n")

    # Low Hook%
    low_hook = [c for c in active if 0 < c.get("hook_pct", 0) < BENCH_HOOK_PCT]
    if low_hook:
        lines.append(f"⚠️ <b>Low Hook% (&lt;{BENCH_HOOK_PCT}%) — Check Creatives</b>")
        for c in sorted(low_hook, key=lambda x: x["hook_pct"])[:6]:
            lines.append(f"  • {c['campaign_name'][:42]} — {c['hook_pct']}%")
        lines.append("")

    # Low CTR
    low_ctr = [c for c in active if 0 < c.get("ctr", 0) < BENCH_CTR]
    if low_ctr:
        lines.append(f"⚠️ <b>Low CTR (&lt;{BENCH_CTR}%) — Weak Ad Copy</b>")
        for c in sorted(low_ctr, key=lambda x: x["ctr"])[:4]:
            lines.append(f"  • {c['campaign_name'][:42]} — {c['ctr']}%")
        lines.append("")

    # Wasted spend — high spend, zero leads
    wasted = [c for c in active if c.get("leads", 0) == 0 and c.get("messages", 0) == 0 and c.get("spend", 0) > 100]
    if wasted:
        lines.append(f"⚠️ <b>Wasted Spend — No Leads, Spent &gt;RM100</b>")
        for c in sorted(wasted, key=lambda x: -x["spend"])[:5]:
            lines.append(f"  • {c['campaign_name'][:38]} — RM {c['spend']:,.0f} spent")
        lines.append("")

    # Critical CPL (>RM100)
    critical_cpl = [c for c in active if c["leads"] > 0 and c["cpl"] > ALERT_CPL]
    if critical_cpl:
        lines.append(f"🚨 <b>CRITICAL CPL (&gt;RM{ALERT_CPL:.0f}) — Pause or Fix Now</b>")
        for c in sorted(critical_cpl, key=lambda x: -x["cpl"])[:4]:
            lines.append(f"  • {c['campaign_name'][:38]} — RM {c['cpl']}")
        lines.append("")

    # High CPL (>RM50)
    high_cpl = [c for c in active if c["leads"] > 0 and BENCH_CPL < c["cpl"] <= ALERT_CPL]
    if high_cpl:
        lines.append(f"🔴 <b>High CPL (&gt;RM{BENCH_CPL:.0f}) — Needs Optimisation</b>")
        for c in sorted(high_cpl, key=lambda x: -x["cpl"])[:4]:
            lines.append(f"  • {c['campaign_name'][:38]} — RM {c['cpl']}")
        lines.append("")

    # Good performers
    good = [c for c in active if c["leads"] > 0 and c["cpl"] <= BENCH_CPL]
    if good:
        lines.append(f"✅ <b>Good (CPL ≤ RM{BENCH_CPL:.0f})</b>")
        for c in sorted(good, key=lambda x: x["cpl"])[:5]:
            lines.append(f"  • {c['campaign_name'][:38]} — RM {c['cpl']}")
        lines.append("")

    # High CPM warning
    cpms = [c["cpm"] for c in active if c["cpm"] > 0]
    if cpms:
        avg_cpm = round(sum(cpms) / len(cpms), 2)
        spikes  = [c for c in active if c["cpm"] > avg_cpm * 1.5]
        if spikes:
            lines.append(f"📈 <b>CPM Spikes (avg RM {avg_cpm})</b>")
            for c in sorted(spikes, key=lambda x: -x["cpm"])[:3]:
                lines.append(f"  • {c['account_label']} / {c['campaign_name'][:30]} — RM {c['cpm']}")
            lines.append("")

    lines.append("📅 Sent by JPROP Auto-Report")
    return "\n".join(lines)

app = FastAPI(title="JPROP Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

SECRET         = os.environ.get("DASHBOARD_SECRET", "change-me-now")
ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL", "wj@jprop.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "jprop2024")

@app.on_event("startup")
def startup():
    init_db()
    init_deals()
    create_user("WJ Admin", ADMIN_EMAIL, ADMIN_PASSWORD, "admin")

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

@app.get("/api/admin/campaigns")
async def all_campaigns(preset: str = "last_week_sun_sat", since: str = "", until: str = "", _=Depends(admin_only)):
    if preset == "this_year":
        today = date.today()
        since = f"{today.year}-01-01"
        until = today.strftime("%Y-%m-%d")
    results = await fetch_all_accounts(preset, since, until)
    out = []
    for i, r in enumerate(results):
        for c in (r.get("data") or []):
            cid  = c.get("campaign_id", "")
            sp   = float(c.get("spend", 0))
            ld   = get_leads_value(c.get("actions") or [], LEAD_ACTION_TYPES)
            msg  = get_messages_value(c.get("actions") or [], WHATSAPP_ACTION_TYPES)
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
                "messages":         int(msg),
                "cpl":              round(sp / ld, 2) if ld else 0,
                "cpm":              round(float(c.get("cpm", 0)), 2),
                "ctr":              round(float(c.get("inline_link_click_ctr") or c.get("link_ctr") or 0), 2),
                "frequency":        round(float(c.get("frequency", 0)), 2),
                "impressions":      imp,
                "hook_pct":         hook_pct,
                "daily_budget":     r.get("budget_map", {}).get(cid, 0),
                "effective_status": c.get("effective_status", "ACTIVE"),
                "updated_time":     c.get("updated_time", ""),
            })
    return out

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

@app.get("/api/admin/trend-debug")
async def trend_debug(_=Depends(admin_only)):
    """Debug endpoint — returns raw Meta API response so we can see what's happening."""
    import requests, json
    from datetime import date, timedelta
    import meta_api as _ma
    # Get BASE_URL from meta_api module directly
    base_url = getattr(_ma, "BASE_URL", "https://graph.facebook.com/v19.0")
    until = date.today()
    since = until - timedelta(days=7)
    out = []
    for acc in ACCOUNTS:
        token  = acc.get("token") or acc.get("access_token") or acc.get("TOKEN") or ""
        acc_id = acc.get("id")    or acc.get("account_id")   or ""
        label  = acc.get("label", acc_id)
        params = {
            "fields":         "date_start,spend,impressions,cpm",
            "time_increment": 1,
            "level":          "campaign",
            "time_range":     json.dumps({"since": since.strftime("%Y-%m-%d"), "until": until.strftime("%Y-%m-%d")}),
            "access_token":   token,
        }
        resp = requests.get(f"{base_url}/{acc_id}/insights", params=params, timeout=30)
        out.append({
            "label":         label,
            "acc_id":        acc_id,
            "token_prefix":  (token[:15] + "...") if token else "EMPTY",
            "http_status":   resp.status_code,
            "meta_response": resp.json(),
        })
    return out

@app.get("/api/admin/trend")
async def campaign_trend(days: int = 30, _=Depends(admin_only)):
    """Returns raw per-account per-day rows. Frontend aggregates/filters."""
    try:
        results = await fetch_trend_data(days)
    except Exception as e:
        raise HTTPException(500, f"fetch_trend_data failed: {e}")

    # Collect any Meta API errors so the frontend can display them
    api_errors = []
    for r in results:
        if r.get("error"):
            api_errors.append(f"{r.get('label','?')}: {r['error'].get('message', r['error'])}")

    if api_errors:
        raise HTTPException(502, "Meta API error — " + " | ".join(api_errors))

    out = []
    for r in results:
        label = r.get("label", "")
        for d in (r.get("data") or []):
            try:
                dk = d.get("date_start", "")
                if not dk:
                    continue
                sp  = float(d.get("spend", 0))
                ld  = get_leads_value(d.get("actions") or [], LEAD_ACTION_TYPES)
                msg = get_messages_value(d.get("actions") or [], WHATSAPP_ACTION_TYPES)
                out.append({
                    "date":          dk,
                    "account_label": label,
                    "spend":         round(sp, 2),
                    "leads":         int(ld),
                    "messages":      int(msg),
                    "cpm":       round(float(d["cpm"]), 2) if d.get("cpm") else 0,
                    "ctr":       round(float(d["inline_link_click_ctr"]), 2) if d.get("inline_link_click_ctr") else 0,
                    "frequency": round(float(d["frequency"]), 2) if d.get("frequency") else 0,
                })
            except Exception:
                continue
    out.sort(key=lambda x: (x["date"], x["account_label"]))
    return out

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
            ld  = get_leads_value(c.get("actions") or [], LEAD_ACTION_TYPES)
            msg = get_messages_value(c.get("actions") or [], WHATSAPP_ACTION_TYPES)
            cpl = round(sp / ld, 2) if ld else 0
            p   = get_profit(c.get("campaign_id", ""))
            closing_sales = p.get("closing_sales", 0)
            nett_sales    = p.get("nett_sales", 0)
            commission    = p.get("commission", 0)
            roi           = round(commission / sp, 2) if sp and commission else 0
            imp           = int(c.get("impressions", 0))
            hook          = get_actions_value(c.get("actions") or [], {"video_view"})
            campaigns.append({
                "campaign_id":   c.get("campaign_id"),
                "campaign_name": c.get("campaign_name"),
                "account":       r["label"],
                "spend":         round(sp, 2),
                "leads":         int(ld),
                "messages":      int(msg),
                "cpl":           cpl,
                "cpm":           round(float(c.get("cpm", 0)), 2),
                "ctr":           round(float(c.get("inline_link_click_ctr") or c.get("link_ctr") or 0), 2),
                "frequency":     round(float(c.get("frequency", 0)), 2),
                "impressions":   imp,
                "hook_rate":     round(hook / imp * 100, 1) if imp else 0,
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

async def _collect_campaigns(preset: str = "this_week_sun_today") -> list:
    results = await fetch_all_accounts(preset)
    out = []
    for r in results:
        for c in (r.get("data") or []):
            sp  = float(c.get("spend", 0))
            ld  = get_leads_value(c.get("actions") or [], LEAD_ACTION_TYPES)
            msg = get_messages_value(c.get("actions") or [], WHATSAPP_ACTION_TYPES)
            imp = int(c.get("impressions", 0))
            hook = get_actions_value(c.get("actions") or [], {"video_view"})
            out.append({
                "campaign_name": c.get("campaign_name", ""),
                "account_label": r["label"],
                "spend": round(sp, 2),
                "leads": int(ld),
                "messages": int(msg),
                "cpl": round(sp / ld, 2) if ld else 0,
                "cpm": round(float(c.get("cpm", 0)), 2),
                "ctr": round(float(c.get("inline_link_click_ctr") or 0), 2),
                "hook_pct": round(hook / imp * 100, 1) if imp else 0,
            })
    return out

@app.get("/api/internal/daily-report")
async def send_daily_report_cron(secret: str = ""):
    """Called by scheduler — no JWT needed, uses INTERNAL_SECRET instead."""
    if secret != INT_SECRET:
        raise HTTPException(403, "Invalid secret")
    campaigns = await _collect_campaigns()
    text = _build_report(campaigns)
    sent = _send_telegram(text)
    return {"ok": sent, "campaigns": len(campaigns), "preview": text}

@app.post("/api/admin/test-telegram")
async def test_telegram(_=Depends(admin_only)):
    """Manual test — send report now from admin dashboard."""
    campaigns = await _collect_campaigns()
    text = _build_report(campaigns)
    sent = _send_telegram(text)
    return {"ok": sent, "preview": text}

@app.post("/api/admin/cpl-alert")
async def send_cpl_alert(_=Depends(admin_only)):
    """Send immediate Telegram alert for campaigns with CPL > RM100 or wasted spend > RM100."""
    campaigns = await _collect_campaigns()
    active = [c for c in campaigns if c.get("spend", 0) > 0]

    critical = [c for c in active if c.get("leads", 0) > 0 and c.get("cpl", 0) > ALERT_CPL]
    wasted   = [c for c in active if c.get("leads", 0) == 0 and c.get("messages", 0) == 0 and c.get("spend", 0) > 100]

    if not critical and not wasted:
        return {"ok": False, "reason": "No campaigns with CPL > RM100 or wasted spend found"}

    from datetime import date
    today = date.today().strftime("%d %b %Y")
    lines = [f"🚨 <b>JPROP CPL Alert — {today}</b>\n"]

    if critical:
        lines.append(f"🔴 <b>CPL &gt; RM{ALERT_CPL:.0f} — Action Required</b>")
        for c in sorted(critical, key=lambda x: -x["cpl"])[:6]:
            lines.append(f"  • {c['campaign_name'][:38]} — RM {c['cpl']} CPL ({c['leads']} leads)")
        lines.append("")

    if wasted:
        lines.append(f"⚠️ <b>Wasted Spend — No Leads, Spent &gt;RM100</b>")
        for c in sorted(wasted, key=lambda x: -x["spend"])[:5]:
            lines.append(f"  • {c['campaign_name'][:38]} — RM {c['spend']:,.0f} spent, 0 leads")
        lines.append("")

    lines.append("👆 Check admin.jprop.my for details")
    text = "\n".join(lines)
    sent = _send_telegram(text)
    return {"ok": sent, "critical": len(critical), "wasted": len(wasted), "preview": text}

# ── Deals (Sales Tracker) API ────────────────────────────────────────────────

class DealIn(BaseModel):
    deal_date:   str
    project:     str
    member_name: str
    sales_value: float = 0
    portion_pct: float = 0
    portion_rm:  float = 0
    remark:      str   = ""

@app.get("/api/admin/deals")
def api_list_deals(
    date_from:   str = None,
    date_until:  str = None,
    project:     str = None,
    member_name: str = None,
    _=Depends(admin_only),
):
    deals    = list_deals(date_from, date_until, project, member_name)
    projects = list_deal_projects()
    members  = list_deal_members()
    total_sales = sum(d["sales_value"] for d in deals)
    total_rm    = sum(d["portion_rm"]  for d in deals)
    return {
        "deals": deals,
        "projects": projects,
        "members": members,
        "totals": {"deals": len(deals), "sales_value": total_sales, "portion_rm": total_rm},
    }

@app.post("/api/admin/deals")
def api_add_deal(req: DealIn, _=Depends(admin_only)):
    deal = add_deal(
        req.deal_date, req.project, req.member_name,
        req.sales_value, req.portion_pct, req.portion_rm, req.remark,
    )
    return {"ok": True, "deal": deal}

@app.put("/api/admin/deals/{deal_id}")
def api_update_deal(deal_id: int, req: DealIn, _=Depends(admin_only)):
    deal = update_deal(
        deal_id, req.deal_date, req.project, req.member_name,
        req.sales_value, req.portion_pct, req.portion_rm, req.remark,
    )
    if not deal:
        raise HTTPException(404, "Deal not found")
    return {"ok": True, "deal": deal}

@app.delete("/api/admin/deals/{deal_id}")
def api_delete_deal(deal_id: int, _=Depends(admin_only)):
    delete_deal(deal_id)
    return {"ok": True}

# ── Static pages ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    return FileResponse(BASE_DIR / "dashboard.html")

@app.get("/admin", response_class=HTMLResponse)
def serve_admin():
    return FileResponse(BASE_DIR / "admin.html")

@app.get("/sales", response_class=HTMLResponse)
def serve_sales():
    return FileResponse(BASE_DIR / "sales.html")

@app.get("/weekly-report", response_class=HTMLResponse)
def serve_weekly_report():
    return FileResponse(BASE_DIR / "weekly-report.html")
