#!/usr/bin/env python3
"""
JPROP Ads Assistant — Telegram Bot
Uses raw aiohttp to call Telegram API directly.
No python-telegram-bot dependency — works on any Python version.
"""
import os, html, logging, asyncio
from aiohttp import web, ClientSession, ClientTimeout
from meta_api import (
    ACCOUNTS, fetch_all_accounts, fetch_single_account,
    get_actions_value, LEAD_ACTION_TYPES,
)
from sheets_report import write_weekly_report, _tab_name_for_last_week

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
PORT    = int(os.environ.get("PORT", 10000))
TG      = f"https://api.telegram.org/bot{TOKEN}"
MAX_LEN = 4000

DATE_LABELS = {
    "today":               "Today",
    "yesterday":           "Yesterday",
    "this_week_sun_today": "This Week",
    "this_month":          "This Month",
    "last_week_sun_sat":   "Last Week",
    "last_month":          "Last Month",
}

# ── Keyboards (raw Telegram inline_keyboard format) ────────────────

MAIN_KBD = [
    [{"text": "📊 All — Today",      "callback_data": "all:today"},
     {"text": "📊 All — Yesterday",  "callback_data": "all:yesterday"}],
    [{"text": "📊 All — This Week",  "callback_data": "all:this_week_sun_today"},
     {"text": "📊 All — This Month", "callback_data": "all:this_month"}],
    [{"text": "📊 All — Last Week",  "callback_data": "all:last_week_sun_sat"},
     {"text": "📊 All — Last Month", "callback_data": "all:last_month"}],
    [{"text": "── Single Account ──", "callback_data": "noop"}],
    [{"text": "🔵 Tony & WJ",   "callback_data": "pick:0"},
     {"text": "🟢 Weejia",      "callback_data": "pick:1"}],
    [{"text": "🟣 Ivan",        "callback_data": "pick:2"},
     {"text": "🔶 Darren",      "callback_data": "pick:3"}],
    [{"text": "🔴 Joey",        "callback_data": "pick:4"},
     {"text": "🟡 Am Prop",     "callback_data": "pick:5"}],
    [{"text": "⚪ Maurice",     "callback_data": "pick:6"},
     {"text": "🟤 Janice & WJ", "callback_data": "pick:7"}],
    [{"text": "🟠 Bobo Yeong",  "callback_data": "pick:8"}],
    [{"text": "⛳️ Pang",        "callback_data": "pick:9"}],
    [{"text": "⛔️ Chris",        "callback_data": "pick:10"}],
    [{"text": "📋 Weekly Report → Sheets", "callback_data": "weekly"}],
]

def acc_kbd(i: int) -> list:
    s = str(i)
    return [
        [{"text": "Today",      "callback_data": f"acc:{s}:today"},
         {"text": "Yesterday",  "callback_data": f"acc:{s}:yesterday"}],
        [{"text": "This Week",  "callback_data": f"acc:{s}:this_week_sun_today"},
         {"text": "This Month", "callback_data": f"acc:{s}:this_month"}],
        [{"text": "Last Week",  "callback_data": f"acc:{s}:last_week_sun_sat"},
         {"text": "Last Month", "callback_data": f"acc:{s}:last_month"}],
        [{"text": "« Back",     "callback_data": "menu"}],
    ]

# ── Signal helpers ─────────────────────────────────────────────────

def _h(t):     return html.escape(str(t))
def rm(v):     return f"RM{round(v)}"
def b_cpm(v):  return "🔴" if v > 150 else ("🟡" if v > 80  else "🟢")
def b_hook(v): return "🟢" if v >= 30  else ("🟡" if v >= 15 else "🔴")
def b_thru(v): return "🟢" if v >= 10  else ("🟡" if v >= 5  else "🔴")
def b_ctr(v):  return "🟢" if v >= 1.0 else ("🟡" if v >= 0.5 else "🔴")
def b_cpl(v):  return "🟢" if 0 < v <= 50 else ("🟡" if v <= 100 else "🔴")

# ── Campaign formatter ─────────────────────────────────────────────

def fmt_campaign(c: dict, bm: dict) -> str:
    name   = _h(c.get("campaign_name", "Unknown")[:45])
    spend  = float(c.get("spend", 0))
    imp    = int(c.get("impressions", 0))
    cpm    = float(c.get("cpm", 0))
    ctr    = float(c.get("ctr", 0))
    acts   = c.get("actions") or []
    thru_a = c.get("video_thruplay_watched_actions") or []
    cid    = c.get("campaign_id", "")

    leads  = get_actions_value(acts, LEAD_ACTION_TYPES)
    hook   = get_actions_value(acts, {"video_view"})
    thru   = sum(float(a.get("value", 0)) for a in thru_a)
    hook_p = hook / imp * 100 if imp else 0
    thru_p = thru / imp * 100 if imp else 0
    cpl    = spend / leads if leads else 0
    daily  = bm.get(cid, 0)

    bud_s  = f" <i>💰{rm(daily)}/day</i>" if daily > 0 else ""
    lead_s = f"{int(leads)} leads" if leads else "<b>0 leads ⚠️</b>"
    cpl_s  = f" | CPL <b>{rm(cpl)}</b>{b_cpl(cpl)}" if leads else ""

    line  = f"  📌 <b>{name}</b>{bud_s}\n"
    line += f"     💸 {rm(spend)} | {lead_s}{cpl_s}\n"
    line += f"     CPM {rm(cpm)}{b_cpm(cpm)} | Hook {hook_p:.1f}%{b_hook(hook_p)}"
    line += f" | Thru {thru_p:.1f}%{b_thru(thru_p)} | CTR {ctr:.2f}%{b_ctr(ctr)}"
    return line

# ── Report: All accounts ───────────────────────────────────────────

async def build_all(preset: str) -> str:
    results  = await fetch_all_accounts(preset)
    label    = DATE_LABELS.get(preset, preset)
    parts    = [f"📊 <b>All Accounts — {label}</b>\n{'─'*30}"]
    alerts   = []
    gs = gl = g_budget = 0.0

    for r in results:
        emoji = r["emoji"]
        lbl   = r["label"]
        data  = r.get("data") or []
        err   = r.get("error")
        bm    = r.get("budget_map", {})

        if err:
            parts.append(f"{emoji} <b>{_h(lbl)}</b> ❌ {_h(err.get('message',''))}")
            continue
        if not data:
            parts.append(f"{emoji} <b>{_h(lbl)}</b> — 📭 No data")
            continue

        acc_budget = sum(bm.get(c.get("campaign_id", ""), 0) for c in data)
        bud_hdr    = f" | 💰 <b>{rm(acc_budget)}/day</b>" if acc_budget > 0 else ""
        lines      = [f"{emoji} <b>{_h(lbl)}</b>{bud_hdr}"]
        acc_sp = acc_ld = 0.0

        for c in data:
            sp  = float(c.get("spend", 0))
            cpm = float(c.get("cpm", 0))
            ld  = get_actions_value(c.get("actions") or [], LEAD_ACTION_TYPES)
            cpl = sp / ld if ld else 0
            acc_sp += sp; acc_ld += ld

            name  = _h(c.get("campaign_name", "Unknown")[:38])
            ld_s  = f"{int(ld)} leads | CPL {rm(cpl)}{b_cpl(cpl)}" if ld else "0 leads ⚠️"
            lines.append(f"  • <b>{name}</b>\n    {rm(sp)} | {ld_s} | CPM {rm(cpm)}{b_cpm(cpm)}")

            if sp >= 50 and ld == 0:
                alerts.append(f"⚠️ <b>{_h(lbl)}</b> — {_h(c.get('campaign_name','')[:25])} {rm(sp)} 0 leads")
            if ld > 0 and cpl > 50:
                alerts.append(f"💸 <b>{_h(lbl)}</b> — {_h(c.get('campaign_name','')[:25])} CPL {rm(cpl)}{b_cpl(cpl)}")

        gs += acc_sp; gl += acc_ld; g_budget += acc_budget
        acpl   = acc_sp / acc_ld if acc_ld else 0
        acpl_s = f"{int(acc_ld)} leads | CPL <b>{rm(acpl)}</b>{b_cpl(acpl)}" if acc_ld else "0 leads ⚠️"
        lines.append(f"  ↳ Spent <b>{rm(acc_sp)}</b> | {acpl_s}")
        parts.append("\n".join(lines))

    gcpl  = gs / gl if gl else 0
    bud_t = f"Budget <b>{rm(g_budget)}/day</b> | " if g_budget > 0 else ""
    cpl_t = f" | Avg CPL <b>{rm(gcpl)}</b>{b_cpl(gcpl)}" if gl else ""
    parts += [f"{'─'*30}",
              f"📦 <b>GRAND TOTAL</b>\n{bud_t}Spent <b>{rm(gs)}</b> | <b>{int(gl)} leads</b>{cpl_t}"]
    if alerts:
        parts.append(f"🚨 <b>Alerts ({len(alerts)})</b>\n" + "\n".join(alerts))
    return "\n\n".join(parts)

# ── Report: Single account ─────────────────────────────────────────

async def build_single(idx: int, preset: str) -> str:
    r     = await fetch_single_account(idx, preset)
    label = DATE_LABELS.get(preset, preset)
    emoji = r["emoji"]
    lbl   = r["label"]
    data  = r.get("data") or []
    err   = r.get("error")
    bm    = r.get("budget_map", {})
    hdr   = f"📊 <b>{_h(lbl)} — {label}</b>\n{'─'*30}"

    if err:      return f"{hdr}\n❌ {_h(err.get('message',''))}"
    if not data: return f"{hdr}\n📭 No active campaigns"

    parts  = [f"{hdr}\n{emoji} <b>{_h(lbl)}</b>"]
    tot_sp = tot_ld = tot_bud = 0.0
    alerts = []

    for c in data:
        sp  = float(c.get("spend", 0))
        ld  = get_actions_value(c.get("actions") or [], LEAD_ACTION_TYPES)
        cpl = sp / ld if ld else 0
        bud = bm.get(c.get("campaign_id", ""), 0)
        tot_sp += sp; tot_ld += ld; tot_bud += bud
        parts.append(fmt_campaign(c, bm))
        if sp >= 50 and ld == 0:
            alerts.append(f"⚠️ {_h(c.get('campaign_name','')[:30])} {rm(sp)} — 0 leads")
        if ld > 0 and cpl > 50:
            alerts.append(f"💸 CPL {rm(cpl)}{b_cpl(cpl)} — {_h(c.get('campaign_name','')[:30])}")

    tcpl  = tot_sp / tot_ld if tot_ld else 0
    tls   = f"🎯 {int(tot_ld)} leads | CPL <b>{rm(tcpl)}</b>{b_cpl(tcpl)}" if tot_ld else "<b>0 leads ⚠️</b>"
    bud_l = f" | Budget <b>{rm(tot_bud)}/day</b>" if tot_bud > 0 else ""
    parts.append(f"💰 <b>Spent: {rm(tot_sp)}{bud_l} | {tls}</b>")
    if alerts:
        parts.append("🚨 <b>Alerts:</b>\n" + "\n".join(alerts))
    return "\n\n".join(parts)

# ── Telegram API helpers ───────────────────────────────────────────

async def tg(session: ClientSession, method: str, payload: dict) -> dict:
    try:
        async with session.post(
            f"{TG}/{method}", json=payload,
            timeout=ClientTimeout(total=10)
        ) as r:
            return await r.json()
    except Exception as e:
        log.error(f"tg/{method} error: {e}")
        return {}

async def send(session: ClientSession, chat_id: int, text: str, kbd: list = None) -> None:
    kw = {"chat_id": chat_id, "parse_mode": "HTML"}
    if kbd:
        kw["reply_markup"] = {"inline_keyboard": kbd}

    if len(text) <= MAX_LEN:
        await tg(session, "sendMessage", {**kw, "text": text})
        return

    paras, cur, chunks = text.split("\n\n"), "", []
    for p in paras:
        if len(cur) + len(p) + 2 > MAX_LEN:
            chunks.append(cur.strip()); cur = p
        else:
            cur += ("\n\n" if cur else "") + p
    if cur:
        chunks.append(cur.strip())

    for i, chunk in enumerate(chunks):
        payload = {**kw, "text": chunk}
        if i < len(chunks) - 1:
            payload.pop("reply_markup", None)
        await tg(session, "sendMessage", payload)

# ── Update handler ─────────────────────────────────────────────────

async def handle(update: dict) -> None:
    async with ClientSession() as session:
        # Text message
        if "message" in update:
            msg  = update["message"]
            text = msg.get("text", "").strip()
            cid  = msg["chat"]["id"]
            if text.startswith("/start") or text.startswith("/help"):
                await send(session, cid,
                           "👋 <b>JPROP Ads Assistant</b>\n\nTap a button to get your report:",
                           MAIN_KBD)
            return

        # Button press
        if "callback_query" not in update:
            return

        q   = update["callback_query"]
        cid = q["message"]["chat"]["id"]
        d   = q["data"]
        await tg(session, "answerCallbackQuery", {"callback_query_id": q["id"]})

        if d == "noop":
            return

        if d == "menu":
            await send(session, cid,
                       "👋 <b>JPROP Ads Assistant</b>\n\nTap a button to get your report:",
                       MAIN_KBD)
            return

        if d == "weekly":
            await build_weekly_report(session, cid)

        elif d.startswith("all:"):
            preset = d.split(":", 1)[1]
            await send(session, cid, f"⏳ Fetching {DATE_LABELS.get(preset, preset)}…")
            await send(session, cid, await build_all(preset), MAIN_KBD)

        elif d.startswith("pick:"):
            idx = int(d.split(":", 1)[1])
            a   = ACCOUNTS[idx]
            await send(session, cid,
                       f"{a['emoji']} <b>{_h(a['label'])}</b> — select period:",
                       acc_kbd(idx))

        elif d.startswith("acc:"):
            _, idx_s, preset = d.split(":")
            idx = int(idx_s)
            await send(session, cid,
                       f"⏳ Fetching {ACCOUNTS[idx]['label']} — {DATE_LABELS.get(preset, preset)}…")
            await send(session, cid, await build_single(idx, preset), acc_kbd(idx))

# ── Weekly report ─────────────────────────────────────────────────

async def build_weekly_report(session: ClientSession, cid: int) -> None:
    tab = _tab_name_for_last_week()
    await send(session, cid,
               f"⏳ Fetching last week data ({tab})…\nThis takes ~30 seconds.")
    try:
        results = await fetch_all_accounts("last_week_sun_sat")
        url     = await write_weekly_report(results, tab)
    except Exception as e:
        await send(session, cid,
                   f"❌ Error: {_h(str(e))}\n\n"
                   f"Check that GOOGLE_CREDS_B64 is set in Render.",
                   MAIN_KBD)
        return

    # Quick summary in Telegram
    total_sp = total_ld = 0.0
    for r in results:
        for c in (r.get("data") or []):
            total_sp += float(c.get("spend", 0))
            total_ld += sum(
                float(a.get("value", 0))
                for a in (c.get("actions") or [])
                if a.get("action_type") in {"lead", "offsite_conversion.lead"}
            )
    cpl = round(total_sp / total_ld, 2) if total_ld else 0

    await send(session, cid,
               f"✅ <b>Weekly Report — {tab}</b>\n"
               f"{'─'*30}\n"
               f"💸 Total Spend: <b>RM{round(total_sp, 2)}</b>\n"
               f"🎯 Total Leads: <b>{int(total_ld)}</b>\n"
               f"📊 Avg CPL: <b>RM{cpl}</b>\n\n"
               f"📋 <b>Open your sheet and fill in the Appt column (column G):</b>\n"
               f"{url}\n\n"
               f"Appt%, Cost/Appt will calculate automatically.",
               MAIN_KBD)

# ── Long-polling loop ──────────────────────────────────────────────

async def poll():
    offset = 0
    log.info("Bot polling started")
    async with ClientSession() as session:
        # Clear pending updates on start
        async with session.get(f"{TG}/getUpdates",
                               params={"offset": -1, "limit": 1},
                               timeout=ClientTimeout(total=10)) as r:
            data = await r.json()
            if data.get("result"):
                offset = data["result"][-1]["update_id"] + 1

    while True:
        try:
            async with ClientSession() as session:
                async with session.get(
                    f"{TG}/getUpdates",
                    params={"timeout": 30, "offset": offset, "limit": 10},
                    timeout=ClientTimeout(total=40)
                ) as r:
                    data = await r.json()

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                asyncio.create_task(handle(upd))

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Poll error: {e}")
            await asyncio.sleep(5)

# ── Health check + entry point ─────────────────────────────────────

async def health(_): return web.Response(text="OK")

async def run():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info(f"Health check on port {PORT}")
    await poll()

if __name__ == "__main__":
    asyncio.run(run())
