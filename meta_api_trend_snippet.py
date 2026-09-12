# ─────────────────────────────────────────────────────────────────
# REPLACE the fetch_trend_data function in meta_api.py with this.
# (Delete the old version first, then paste this at the bottom.)
# ─────────────────────────────────────────────────────────────────

async def fetch_trend_data(days: int = 30):
    """Fetch day-by-day campaign insights for the Trends tab."""
    import requests, json
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

    results = []
    for acc in ACCOUNTS:
        token  = acc.get("token") or acc.get("access_token") or acc.get("TOKEN") or ""
        acc_id = acc.get("id")    or acc.get("account_id")   or ""
        label  = acc.get("label", acc_id)

        params = {
            "fields":         TREND_FIELDS,
            "time_increment": 1,
            "level":          "campaign",
            # Meta requires time_range as a JSON string — not separate since/until params
            "time_range":     json.dumps({
                                  "since": since.strftime("%Y-%m-%d"),
                                  "until": until.strftime("%Y-%m-%d"),
                              }),
            "access_token":   token,
        }

        resp    = requests.get(f"{BASE_URL}/{acc_id}/insights", params=params, timeout=60)
        payload = resp.json()
        data    = payload.get("data", [])
        error   = payload.get("error")

        results.append({"label": label, "data": data, "error": error})

    return results
