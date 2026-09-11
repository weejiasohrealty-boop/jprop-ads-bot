"""
meta_api.py — async Meta Graph API helpers
8 ad accounts. Tokens loaded from environment variables.
Fetches insights + campaign budgets (CBO) + adset budgets (ABO) in parallel.
"""
import os
import asyncio
import aiohttp

GRAPH_API = "https://graph.facebook.com/v19.0"

# Campaign-level fields — included inline_link_click_ctr for Link CTR
INSIGHT_FIELDS = ",".join([
    "campaign_id",
    "campaign_name",
    "spend",
    "impressions",
    "cpm",
    "inline_link_click_ctr",
    "frequency",          # ← 加这行
    "actions",
    "video_thruplay_watched_actions",
])

# Adset-level fields — used for weekly Google Sheets report
ADSET_INSIGHT_FIELDS = ",".join([
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "spend",
    "impressions",
    "cpm",
    "inline_link_click_ctr",
    “frequency",
    "actions",
    "video_thruplay_watched_actions",
])

LEAD_ACTION_TYPES = {"lead", "offsite_conversion.lead"}
HOOK_ACTION_TYPES = {"video_view"}
LINK_CLICK_ACTION_TYPES = {"inline_link_click", "link_click"}

ACCOUNTS = [
    {"label": "Tony & WJ",              "emoji": "🔵", "id": "act_1751988812183106", "token_key": "TOKEN_JPROP"},
    {"label": "JPROP / Weejia",         "emoji": "🟢", "id": "act_893342706197387",  "token_key": "TOKEN_JPROP"},
    {"label": "Ivan Lee",               "emoji": "🟣", "id": "act_1549466440123865", "token_key": "TOKEN_JPROP"},
    {"label": "Darren",                 "emoji": "🔶", "id": "act_401868168645921",  "token_key": "TOKEN_JPROP"},
    {"label": "Joey Chaw",              "emoji": "🔴", "id": "act_789254227570709",  "token_key": "TOKEN_JPROP"},
    {"label": "Am Properties",          "emoji": "🟡", "id": "act_6220476828060673", "token_key": "TOKEN_JPROP"},
    {"label": "Janice",                 "emoji": "⚪", "id": "act_820487997381734",  "token_key": "TOKEN_JPROP"},
    {"label": "Pang",                   "emoji": "🟤", "id": "act_266937751083201",  "token_key": "TOKEN_JPROP"},
    {"label": "Chris",                  "emoji": "🟠", "id": "act_296535961110870",  "token_key": "TOKEN_JPROP"},
    {"label": "Jet",                    "emoji": "⛔️", "id": "act_955584277458058",  "token_key": "TOKEN_JPROP"},
    {"label": "Cheng",                  "emoji": "🥎", "id": "act_1000278069552748", "token_key": "TOKEN_JPROP"},
    {"label": "Hannah",                 "emoji": "🔥", "id": "act_1970417923796227", "token_key": "TOKEN_JPROP"},
    {"label": "Jayden",                 "emoji": "🌟", "id": "act_870160215193259",  "token_key": "TOKEN_JPROP"},
    {"label": "KJ",                     "emoji": "🤖", "id": "act_866285942301565",  "token_key": "TOKEN_JPROP"},
    {"label": "Lisa",                     "emoji": "🏔️", "id": "act_1472295653590786",  "token_key": "TOKEN_JPROP"}
]


def get_token(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"Missing env var: {key}")
    return val


def get_actions_value(actions: list, types: set) -> float:
    return sum(float(a.get("value", 0)) for a in (actions or []) if a.get("action_type") in types)


def extract_link_ctr(row: dict) -> float:
    """
    Extracts Link Click CTR (Outbound Link CTR).
    Uses 'inline_link_click_ctr' if provided by Meta, 
    otherwise calculates (link_clicks / impressions) * 100.
    """
    if "inline_link_click_ctr" in row and row["inline_link_click_ctr"] is not None:
        return round(float(row["inline_link_click_ctr"]), 2)
    
    impressions = float(row.get("impressions", 0))
    if impressions > 0:
        link_clicks = get_actions_value(row.get("actions", []), LINK_CLICK_ACTION_TYPES)
        return round((link_clicks / impressions) * 100, 2)
        
    return 0.0


def parse_budget_map(camp_data: list, adset_data: list) -> dict:
    """Returns {campaign_id: daily_budget_RM}. Handles CBO and ABO."""
    m = {}
    for c in camp_data:
        m[c["id"]] = {
            "daily":   float(c.get("daily_budget", 0)) / 100,
            "abo_sum": 0.0,
        }
    for s in adset_data:
        cid = s.get("campaign_id")
        if not cid:
            continue
        if cid not in m:
            m[cid] = {"daily": 0.0, "abo_sum": 0.0}
        m[cid]["abo_sum"] += float(s.get("daily_budget", 0)) / 100

    return {cid: (v["daily"] if v["daily"] > 0 else v["abo_sum"]) for cid, v in m.items()}


def parse_adset_budget_map(camp_data: list, adset_data: list) -> dict:
    """
    Returns {adset_id: daily_budget_RM} for adset-level weekly report.
    ABO adsets: use their own daily_budget.
    CBO adsets: use the campaign daily_budget (shared across all adsets).
    """
    camp_budgets = {c["id"]: float(c.get("daily_budget", 0)) / 100 for c in camp_data}
    result = {}
    for s in adset_data:
        adset_bud = float(s.get("daily_budget", 0)) / 100
        if adset_bud > 0:
            result[s["id"]] = adset_bud          # ABO — adset has its own budget
        else:
            cid = s.get("campaign_id", "")
            result[s["id"]] = camp_budgets.get(cid, 0)  # CBO — inherit campaign budget
    return result


async def _get(session: aiohttp.ClientSession, url: str, params: dict) -> dict:
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=25)) as r:
            return await r.json()
    except asyncio.TimeoutError:
        return {"error": {"message": "Timed out"}}
    except Exception as e:
        return {"error": {"message": str(e)}}


async def _fetch_account(session: aiohttp.ClientSession, account: dict, preset: str) -> dict:
    try:
        token = get_token(account["token_key"])
    except RuntimeError as e:
        return {"label": account["label"], "emoji": account["emoji"], "data": [], "budget_map": {}, "error": {"message": str(e)}}

    acc_id = account["id"]

    # 3 parallel requests: insights + campaign budgets + adset budgets
    insights_task = _get(session, f"{GRAPH_API}/{acc_id}/insights", {
        "fields": INSIGHT_FIELDS, "level": "campaign",
        "date_preset": preset, "access_token": token, "limit": 100,
    })
    campaigns_task = _get(session, f"{GRAPH_API}/{acc_id}/campaigns", {
        "fields": "id,daily_budget,lifetime_budget,effective_status,updated_time",
        "access_token": token, "limit": 500,
    })
    adsets_task = _get(session, f"{GRAPH_API}/{acc_id}/adsets", {
        "fields": "id,campaign_id,daily_budget",
        "access_token": token, "limit": 500,
    })

    ins, camps, adsets = await asyncio.gather(insights_task, campaigns_task, adsets_task)

    camp_data = camps.get("data", [])
    camp_attrs = {c["id"]: c for c in camp_data}

    # Merge status, updated_time, link_ctr, and inline_link_click_ctr into each insight row
    insight_rows = ins.get("data", [])
    for row in insight_rows:
        cid = row.get("campaign_id")
        if cid and cid in camp_attrs:
            row["effective_status"] = camp_attrs[cid].get("effective_status", "UNKNOWN")
            row["updated_time"]     = camp_attrs[cid].get("updated_time", "")
        
        # Explicitly attach link_ctr float field for UI / Dashboard consumption
        calculated_ctr = extract_link_ctr(row)
        row["link_ctr"] = calculated_ctr
        if "inline_link_click_ctr" not in row or row["inline_link_click_ctr"] is None:
            row["inline_link_click_ctr"] = calculated_ctr

    budget_map = parse_budget_map(camp_data, adsets.get("data", []))

    return {
        "label":      account["label"],
        "emoji":      account["emoji"],
        "data":       insight_rows,
        "budget_map": budget_map,
        "error":      ins.get("error"),
    }


async def _fetch_account_adset(session: aiohttp.ClientSession, account: dict, preset: str) -> dict:
    """Fetch adset-level insights — used for weekly Google Sheets report."""
    try:
        token = get_token(account["token_key"])
    except RuntimeError as e:
        return {"label": account["label"], "emoji": account["emoji"], "data": [], "adset_budget_map": {}, "error": {"message": str(e)}}

    acc_id = account["id"]

    insights_task = _get(session, f"{GRAPH_API}/{acc_id}/insights", {
        "fields": ADSET_INSIGHT_FIELDS, "level": "adset",
        "date_preset": preset, "access_token": token, "limit": 500,
        "filtering": '[{"field":"spend","operator":"GREATER_THAN","value":"0"}]',
    })
    campaigns_task = _get(session, f"{GRAPH_API}/{acc_id}/campaigns", {
        "fields": "id,daily_budget",
        "access_token": token, "limit": 500,
    })
    adsets_task = _get(session, f"{GRAPH_API}/{acc_id}/adsets", {
        "fields": "id,campaign_id,daily_budget",
        "access_token": token, "limit": 500,
    })

    ins, camps, adsets = await asyncio.gather(insights_task, campaigns_task, adsets_task)

    adset_rows = ins.get("data", [])
    for row in adset_rows:
        calculated_ctr = extract_link_ctr(row)
        row["link_ctr"] = calculated_ctr
        if "inline_link_click_ctr" not in row or row["inline_link_click_ctr"] is None:
            row["inline_link_click_ctr"] = calculated_ctr

    adset_budget_map = parse_adset_budget_map(camps.get("data", []), adsets.get("data", []))

    return {
        "label":            account["label"],
        "emoji":            account["emoji"],
        "data":             adset_rows,
        "adset_budget_map": adset_budget_map,
        "error":            ins.get("error"),
    }


async def fetch_all_accounts(preset: str) -> list:
    """Fetch all accounts at campaign level (for Telegram reports / Dashboard)."""
    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_account(session, acc, preset) for acc in ACCOUNTS]
        return await asyncio.gather(*tasks)


async def fetch_all_accounts_adset(preset: str) -> list:
    """Fetch all accounts at adset level (for weekly Google Sheets report)."""
    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_account_adset(session, acc, preset) for acc in ACCOUNTS]
        return await asyncio.gather(*tasks)


async def fetch_single_account(idx: int, preset: str) -> dict:
    async with aiohttp.ClientSession() as session:
        return await _fetch_account(session, ACCOUNTS[idx], preset)
