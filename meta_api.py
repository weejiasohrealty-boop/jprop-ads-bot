"""
meta_api.py — async Meta Graph API helpers
8 ad accounts. Tokens loaded from environment variables.
Fetches insights + campaign budgets (CBO) + adset budgets (ABO) in parallel.
"""
import os
import asyncio
import aiohttp

GRAPH_API = "https://graph.facebook.com/v19.0"

INSIGHT_FIELDS = ",".join([
    "campaign_id",
    "campaign_name",
    "spend",
    "impressions",
    "cpm",
    "ctr",
    "actions",
    "video_thruplay_watched_actions",
])

LEAD_ACTION_TYPES = {"lead", "offsite_conversion.lead"}
HOOK_ACTION_TYPES = {"video_view"}

ACCOUNTS = [
    {"label": "Tony & WJ",              "emoji": "🔵", "id": "act_1751988812183106", "token_key": "TOKEN_TONY"},
    {"label": "JPROP / Weejia",         "emoji": "🟢", "id": "act_893342706197387",  "token_key": "TOKEN_JPROP"},
    {"label": "Ivan Lee",               "emoji": "🟣", "id": "act_1549466440123865", "token_key": "TOKEN_JPROP"},
    {"label": "Darren",                 "emoji": "🔶", "id": "act_401868168645921",  "token_key": "TOKEN_JPROP"},
    {"label": "Joey Chaw",              "emoji": "🔴", "id": "act_789254227570709",  "token_key": "TOKEN_JPROP"},
    {"label": "Am Properties",          "emoji": "🟡", "id": "act_6220476828060673", "token_key": "TOKEN_AM"},
    {"label": "Maurice / Starproperty", "emoji": "⚪", "id": "act_983330607631571",  "token_key": "TOKEN_MAURICE"},
    {"label": "Janice & WJ",            "emoji": "🟤", "id": "act_820487997381734",  "token_key": "TOKEN_JPROP"},
    {"label": "Bobo",  "emoji": "🥎", "id": "act_1616708788537498", "token_key": "TOKEN_BOBO"},
]


def get_token(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"Missing env var: {key}")
    return val


def get_actions_value(actions: list, types: set) -> float:
    return sum(float(a.get("value", 0)) for a in (actions or []) if a.get("action_type") in types)


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
        "fields": "id,daily_budget,lifetime_budget",
        "access_token": token, "limit": 500,
    })
    adsets_task = _get(session, f"{GRAPH_API}/{acc_id}/adsets", {
        "fields": "id,campaign_id,daily_budget",
        "access_token": token, "limit": 500,
    })

    ins, camps, adsets = await asyncio.gather(insights_task, campaigns_task, adsets_task)

    budget_map = parse_budget_map(camps.get("data", []), adsets.get("data", []))

    return {
        "label":      account["label"],
        "emoji":      account["emoji"],
        "data":       ins.get("data", []),
        "budget_map": budget_map,
        "error":      ins.get("error"),
    }


async def fetch_all_accounts(preset: str) -> list:
    """Fetch all 8 accounts fully in parallel."""
    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_account(session, acc, preset) for acc in ACCOUNTS]
        return await asyncio.gather(*tasks)


async def fetch_single_account(idx: int, preset: str) -> dict:
    async with aiohttp.ClientSession() as session:
        return await _fetch_account(session, ACCOUNTS[idx], preset)
