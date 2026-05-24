#!/usr/bin/env python3
"""
JPROP Ads Assistant — Telegram Bot
Runs on Render free tier.
- Telegram: long-polling
- Health check: aiohttp web server on $PORT (keeps Render from sleeping)
"""
import os, html, logging, asyncio
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from meta_api import (
    ACCOUNTS, fetch_all_accounts, fetch_single_account,
    get_actions_value, LEAD_ACTION_TYPES,
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
PORT      = int(os.environ.get("PORT", 10000))
MAX_LEN   = 4000

DATE_LABELS = {
    "today":               "Today",
    "yesterday":           "Yesterday",
    "this_week_sun_today": "This Week",
    "this_month":          "This Month",
    "last_week_sun_sat":   "Last Week",
    "last_month":          "Last Month",
}

# ── Keyboards ──────────────────────────────────────────────────────

def main_kbd() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 All — Today",      callback_data="all:today"),
         InlineKeyboardButton("📊 All — Yesterday",  callback_data="all:yesterday")],
        [InlineKeyboardButton("📊 All — This Week",  callback_data="all:this_week_sun_today"),
         InlineKeyboardButton("📊 All — This Month", callback_data="all:this_month")],
        [InlineKeyboardButton("📊 All — Last Week",  callback_data="all:last_week_sun_sat"),
         InlineKeyboardButton("📊 All — Last Month", callback_data="all:last_month")],
        [InlineKeyboardButton("── Single Account ──", callback_data="noop")],
        [InlineKeyboardButton("🔵 Tony & WJ",   callback_data="pick:0"),
         InlineKeyboardButton("🟢 Weejia",      callback_data="pick:1")],
        [InlineKeyboardButton("🟣 Ivan",        callback_data="pick:2"),
         InlineKeyboardButton("🔶 Darren",      callback_data="pick:3")],
        [InlineKeyboardButton("🔴 Joey",        callback_data="pick:4"),
         InlineKeyboardButton("🟡 Am Prop",     callback_data="pick:5")],
        [InlineKeyboardButton("⚪ Maurice",     callback_data="pick:6"),
         InlineKeyboardButton("🌸 Janice & WJ", callback_data="pick:7")],
    ])

def acc_kbd(i: int) -> InlineKeyboardMarkup:
    s = str(i)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Today",      callback_data=f"acc:{s}:today"),
         InlineKeyboardButton("Yesterday",  callback_data=f"acc:{s}:yesterday")],
        [InlineKeyboardButton("This Week",  callback_data=f"acc:{s}:this_week_sun_today"),
         InlineKeyboardButton("This Month", callback_data=f"acc:{s}:this_month")],
        [InlineKeyboardButton("Last Week",  callback_data=f"acc:{s}:last_week_sun_sat"),
         InlineKeyboardButton("Last Month", callback_data=f"acc:{s}:last_month")],
        [InlineKeyboardButton("« Back",     callback_data="menu")],
    ])

# ── Signal helpers ─────────────────────────────────────────────────

def _h(t):     return html.escape(str(t))
def rm(v):     return f"RM{round(v)}"
def b_cpm(v):  return "🔴" if v > 150 else ("🟡" if v > 80  else "🟢")
def b_hook(v): return "🟢" if v >= 30  else ("🟡" if v >= 15 else "🔴")
def b_thru(v): return "🟢" if v >= 10  else ("🟡" if v >= 5  else "🔴")
def b_ctr(v):  return "🟢" if v >= 1.0 else ("🟡" if v >= 0.5 else "🔴")
def b_cpl(v):  return "🟢" if 0 < v <= 50 else ("🟡" if v <= 100 else "🔴")

# ── Campaign formatter (single account detail) ─────────────────────

def fmt_campaign(c: dict, budget_map: dict) -> str:
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
    daily  = budget_map.get(cid, 0)

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
            parts.append(f"{emoji} <b>{_h(lbl)}</b> ❌ {_h(err.get('message', ''))}")
            continue
        if not data:
            parts.append(f"{emoji} <b>{_h(lbl)}</b> — 📭 No data")
            continue

        # Account budget in header
        acc_budget = sum(bm.get(c.get("campaign_id", ""), 0) for c in data)
        bud_hdr    = f" | 💰 <b>{rm(acc_budget)}/day</b>" if acc_budget > 0 else ""
        lines      = [f"{emoji} <b>{_h(lbl)}</b>{bud_hdr}"]
        acc_sp = acc_ld = 0.0

        for c in data:
            sp  = float(c.get("spend", 0))
            cpm = float(c.get("cpm", 0))
            ld  = get_actions_value(c.get("actions") or [], LEAD_ACTION_TYPES)
            cpl = sp / ld if ld else 0
            acc_sp += sp
            acc_ld += ld

            name  = _h(c.get("campaign_name", "Unknown")[:38])
            ld_s  = f"{int(ld)} leads | CPL {rm(cpl)}{b_cpl(cpl)}" if ld else "0 leads ⚠️"
            lines.append(f"  • <b>{name}</b>\n    {rm(sp)} | {ld_s} | CPM {rm(cpm)}{b_cpm(cpm)}")

            if sp >= 50 and ld == 0:
                alerts.append(f"⚠️ <b>{_h(lbl)}</b> — {_h(c.get('campaign_name','')[:25])} {rm(sp)} 0 leads")
            if ld > 0 and cpl > 50:
                alerts.append(f"💸 <b>{_h(lbl)}</b> — {_h(c.get('campaign_name','')[:25])} CPL {rm(cpl)}{b_cpl(cpl)}")

        gs += acc_sp
        gl += acc_ld
        g_budget += acc_budget

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
    r      = await fetch_single_account(idx, preset)
    label  = DATE_LABELS.get(preset, preset)
    emoji  = r["emoji"]
    lbl    = r["label"]
    data   = r.get("data") or []
    err    = r.get("error")
    bm     = r.get("budget_map", {})
    hdr    = f"📊 <b>{_h(lbl)} — {label}</b>\n{'─'*30}"

    if err:      return f"{hdr}\n❌ {_h(err.get('message', ''))}"
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

    tcpl   = tot_sp / tot_ld if tot_ld else 0
    tls    = f"🎯 {int(tot_ld)} leads | CPL <b>{rm(tcpl)}</b>{b_cpl(tcpl)}" if tot_ld else "<b>0 leads ⚠️</b>"
    bud_l  = f" | Budget <b>{rm(tot_bud)}/day</b>" if tot_bud > 0 else ""
    parts.append(f"💰 <b>Spent: {rm(tot_sp)}{bud_l} | {tls}</b>")
    if alerts:
        parts.append("🚨 <b>Alerts:</b>\n" + "\n".join(alerts))
    return "\n\n".join(parts)

# ── Send helper ────────────────────────────────────────────────────

async def safe_send(update: Update, text: str, kbd=None) -> None:
    kw   = dict(parse_mode="HTML", reply_markup=kbd)
    send = (update.callback_query.message.reply_text
            if update.callback_query else update.message.reply_text)
    if len(text) <= MAX_LEN:
        await send(text, **kw)
        return
    paras, cur = text.split("\n\n"), ""
    for p in paras:
        if len(cur) + len(p) + 2 > MAX_LEN:
            await send(cur.strip(), **kw)
            cur = p
            kw  = dict(parse_mode="HTML")
        else:
            cur += ("\n\n" if cur else "") + p
    if cur:
        await send(cur.strip(), **kw)

# ── Telegram handlers ──────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>JPROP Ads Assistant</b>\n\nTap a button to get your report:",
        parse_mode="HTML", reply_markup=main_kbd(),
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q, d = update.callback_query, update.callback_query.data
    await q.answer()

    if d == "noop":
        return

    if d == "menu":
        await q.message.reply_text(
            "👋 <b>JPROP Ads Assistant</b>\n\nTap a button to get your report:",
            parse_mode="HTML", reply_markup=main_kbd(),
        )
        return

    if d.startswith("all:"):
        preset = d.split(":", 1)[1]
        await q.message.reply_text(f"⏳ Fetching {DATE_LABELS.get(preset, preset)}…")
        await safe_send(update, await build_all(preset), main_kbd())

    elif d.startswith("pick:"):
        idx = int(d.split(":", 1)[1])
        a   = ACCOUNTS[idx]
        await q.message.reply_text(
            f"{a['emoji']} <b>{_h(a['label'])}</b> — select period:",
            parse_mode="HTML", reply_markup=acc_kbd(idx),
        )

    elif d.startswith("acc:"):
        _, idx_s, preset = d.split(":")
        idx = int(idx_s)
        await q.message.reply_text(
            f"⏳ Fetching {ACCOUNTS[idx]['label']} — {DATE_LABELS.get(preset, preset)}…"
        )
        await safe_send(update, await build_single(idx, preset), acc_kbd(idx))

# ── Health check server (keeps Render free tier alive) ────────────

async def health(_request):
    return web.Response(text="OK")

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info(f"Health check running on port {PORT}")

# ── Entry point ────────────────────────────────────────────────────

async def run():
    # Start health check server
    await start_health_server()

    # Start Telegram bot
    bot = Application.builder().token(BOT_TOKEN).build()
    bot.add_handler(CommandHandler("start", cmd_start))
    bot.add_handler(CommandHandler("help",  cmd_start))
    bot.add_handler(CallbackQueryHandler(on_button))

    async with bot:
        await bot.start()
        await bot.updater.start_polling(drop_pending_updates=True)
        log.info("Bot polling started")
        await asyncio.Event().wait()   # run forever

if __name__ == "__main__":
    asyncio.run(run())
