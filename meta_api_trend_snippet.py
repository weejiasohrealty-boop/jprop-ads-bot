# ─────────────────────────────────────────────────────────────────
# REPLACE the fetch_trend_data function at the bottom of meta_api.py
# with this version. Delete the old one first.
# ─────────────────────────────────────────────────────────────────

async def fetch_trend_data(days: int = 30):
    """Fetch day-by-day campaign insights for the Trends tab."""
    import json
    from datetime import date, timedelta

    until = date.today()
    since = until - timedelta(days=days)

    TREND_FIELDS = ",".join([
        "date_start",
        "campaign_id",
        "campaign_name",
        "spend",
        "impressions",
        "cpm",
        "inline_link_click_ctr",
        "frequency",
        "actions",
    ])

    async def _fetch_one(session, acc):
        try:
            token = get_token(acc["token_key"])   # ← correct: uses token_key + env var
        except RuntimeError as e:
            return {"label": acc["label"], "data": [], "error": {"message": str(e)}}

        acc_id = acc["id"]
        params = {
            "fields":         TREND_FIELDS,
            "time_increment": 1,
            "level":          "campaign",
            "time_range":     json.dumps({           # ← Meta requires JSON string for date range
                "since": since.strftime("%Y-%m-%d"),
                "until": until.strftime("%Y-%m-%d"),
            }),
            "access_token":   token,
            "limit":          500,
        }
        result = await _get(session, f"{GRAPH_API}/{acc_id}/insights", params)  # ← correct: GRAPH_API
        return {
            "label": acc["label"],
            "data":  result.get("data", []),
            "error": result.get("error"),
        }

    async with aiohttp.ClientSession() as session:   # ← correct: uses aiohttp like the rest of the file
        tasks = [_fetch_one(session, acc) for acc in ACCOUNTS]
        return list(await asyncio.gather(*tasks))
