#!/usr/bin/env python3
"""
bot.py — JPROP Telegram Report Bot
/start  → all active accounts summary (spend, leads, CPL)
Click account → campaign breakdown
/alert  → CPL > RM50 warnings
"""
import os, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from meta_api import (
    ACCOUNTS, fetch_all_accounts,
    get_leads_value, get_messages_value,
    LEAD_ACTION_TYPES, WHATSAPP_ACTION_TYPES,
)

TOKEN         = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CPL_THRESHOLD = 50.0
PRESET        = "this_week"


# ── Helpers ───────────────────────────────────────────────────────────────────

def rm(v):
    return f"RM {v:,.0f}"

def cpl_icon(v):
    if v == 0:   return "⚪"
    if v <= 50:  return "✅"
    if v <= 100: return "🟡"
    return "🔴"


async def fetch_accounts(preset=PRESET):
    """Fetch all accounts and return parsed summary + campaigns."""
    results = await fetch_all_accounts(preset)
    accounts = []
    for r in results:
        if r.get("error"):
            continue
        budget_map = r.get("budget_map", {})
        campaigns = []
        for c in r.get("data", []):
            sp = float(c.get("spend", 0))
            if sp == 0:
                continue
            actions = c.get("actions", [])
            ld  = int(get_leads_value(actions, LEAD_ACTION_TYPES))
            msg = int(get_messages_value(actions, WHATSAPP_ACTION_TYPES))
            # Prefer messaging when messages > leads
            is_lead = ld > 0 and ld >= msg
            is_msg  = msg > 0 and not is_lead
            cpl = round(sp / ld, 2) if is_lead and ld else 0
            cost_msg = round(sp / msg, 2) if is_msg and msg else 0
            cid = c.get("campaign_id", "")
            campaigns.append({
                "name":     c.get("campaign_name", ""),
                "id":       cid,
                "spend":    round(sp, 2),
                "budget":   round(budget_map.get(cid, 0), 0),
                "leads":    ld,
                "messages": msg,
                "cpl":      cpl,
                "cost_msg": cost_msg,
                "is_lead":  is_lead,
                "is_msg":   is_msg,
                "status":   c.get("effective_status", "UNKNOWN"),
            })
        if not campaigns:
            continue
        active = [c for c in campaigns if c["status"] == "ACTIVE"]
        total_spend  = sum(c["spend"]  for c in campaigns)
        total_leads  = sum(c["leads"]  for c in campaigns)
        total_budget = sum(c["budget"] for c in active)
        avg_cpl = round(total_spend / total_leads, 0) if total_leads else 0
        accounts.append({
            "label":     r["label"],
            "emoji":     r["emoji"],
            "spend":     total_spend,
            "leads":     total_leads,
            "budget":    total_budget,
            "avg_cpl":   avg_cpl,
            "campaigns": campaigns,
        })
    return accounts


# ── Account summary ───────────────────────────────────────────────────────────

async def show_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    loading = await msg.reply_text("⏳ Fetching this week's data…")

    accounts = await fetch_accounts()
    ctx.user_data["accounts"] = accounts

    if not accounts:
        await loading.edit_text("No active campaigns found.")
        return

    total_spend = sum(a["spend"] for a in accounts)
    total_leads = sum(a["leads"] for a in accounts)
    overall_cpl = round(total_spend / total_leads, 0) if total_leads else 0

    lines = [
        "🏢 <b>JPROP — This Week</b>",
        f"Total Spend: <b>{rm(total_spend)}</b>  |  Leads: <b>{total_leads}</b>  |  CPL: <b>{rm(overall_cpl)}</b> {cpl_icon(overall_cpl)}",
        "",
    ]
    buttons = []
    for i, acc in enumerate(accounts):
        icon = cpl_icon(acc["avg_cpl"])
        lines.append(
            f"{acc['emoji']} <b>{acc['label']}</b>  "
            f"Spent {rm(acc['spend'])}  |  {acc['leads']} leads  |  "
            f"CPL {rm(acc['avg_cpl'])} {icon}"
        )
        buttons.append([InlineKeyboardButton(
            f"{acc['emoji']} {acc['label']}", callback_data=f"acc:{i}"
        )])

    buttons.append([InlineKeyboardButton("🚨 CPL Alert (>RM50)", callback_data="alert")])

    await loading.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ── Campaign detail ───────────────────────────────────────────────────────────

async def show_campaigns(query, acc):
    lines = [f"{acc['emoji']} <b>{acc['label']} — Campaigns</b>\n"]

    for c in sorted(acc["campaigns"], key=lambda x: -x["spend"]):
        status = "🟢" if c["status"] == "ACTIVE" else "⏸"
        budget = f"{rm(c['budget'])}/day" if c["budget"] else "—"

        if c["is_lead"]:
            result = f"{c['leads']} leads | CPL {rm(c['cpl'])} {cpl_icon(c['cpl'])}"
        elif c["is_msg"]:
            result = f"{c['messages']} msg | Cost/msg {rm(c['cost_msg'])} {cpl_icon(c['cost_msg'])}"
        else:
            wasted_flag = " ⚠️ no results" if c["spend"] > 100 else ""
            result = f"No leads/msg{wasted_flag}"

        lines.append(
            f"{status} <b>{c['name'][:45]}</b>\n"
            f"   Budget {budget}  |  Spent {rm(c['spend'])}\n"
            f"   {result}\n"
        )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("← Back to Accounts", callback_data="back")
    ]])
    await query.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=keyboard
    )


# ── CPL Alert ─────────────────────────────────────────────────────────────────

async def show_alert(query_or_msg, ctx):
    is_query = hasattr(query_or_msg, "message")
    reply_to = query_or_msg.message if is_query else query_or_msg

    accounts = ctx.user_data.get("accounts")
    if not accounts:
        loading = await reply_to.reply_text("⏳ Fetching data…")
        accounts = await fetch_accounts()
        ctx.user_data["accounts"] = accounts
        await loading.delete()

    lines = ["🚨 <b>CPL Alert — Over RM50</b>\n"]
    found = False

    for acc in accounts:
        problems = []
        for c in acc["campaigns"]:
            if c["is_lead"] and c["cpl"] > CPL_THRESHOLD:
                problems.append(f"  🔴 {c['name'][:42]}\n     CPL {rm(c['cpl'])} ({c['leads']} leads, spent {rm(c['spend'])})")
            elif c["is_msg"] and c["cost_msg"] > CPL_THRESHOLD:
                problems.append(f"  🟡 {c['name'][:42]}\n     Cost/msg {rm(c['cost_msg'])} ({c['messages']} msg, spent {rm(c['spend'])})")
            elif not c["is_lead"] and not c["is_msg"] and c["spend"] > 100:
                problems.append(f"  ⚠️ {c['name'][:42]}\n     {rm(c['spend'])} spent, 0 leads/msg")
        if problems:
            found = True
            lines.append(f"{acc['emoji']} <b>{acc['label']}</b>")
            lines.extend(problems)
            lines.append("")

    if not found:
        lines.append("✅ All campaigns under RM50 — looking good!")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("← Back to Accounts", callback_data="back")
    ]])
    await reply_to.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=keyboard
    )


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show_summary(update, ctx)

async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show_alert(update.message, ctx)

async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back":
        await show_summary(update, ctx)

    elif query.data == "alert":
        await show_alert(query, ctx)

    elif query.data.startswith("acc:"):
        idx = int(query.data.split(":")[1])
        accounts = ctx.user_data.get("accounts", [])
        if idx < len(accounts):
            await show_campaigns(query, accounts[idx])
        else:
            await query.message.reply_text("Data expired — send /start to refresh.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var not set")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "report"], cmd_start))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CallbackQueryHandler(on_button))
    print("JPROP Bot running…")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
