"""
sheets_report.py — Write weekly Meta ad data to Google Sheets (account level).

One row per account. Tab per week: "25 May - 31 May 2026"

Columns:
  A: Account        B: Spend (RM)     C: Daily Budget (RM)
  D: Leads          E: Cost/Lead      F: Appt (manual)
  G: Appt %         H: Cost/Appt

E, G, H are formulas — auto-calculate.
F (Appt) is filled manually by WJ in Google Sheets (highlighted yellow).

Env vars required:
  GOOGLE_CREDS_B64   — base64-encoded service account JSON
  REPORT_SHEET_ID    — Google Sheet ID from the URL
  REPORT_SHEET_NAME  — spreadsheet name fallback (default: JPROP Weekly Reports)
"""
import os, base64, json, asyncio, logging
from datetime import datetime, timezone, timedelta
import gspread
from gspread_formatting import (
    format_cell_range, CellFormat, TextFormat, Color,
    set_frozen, set_column_width,
)
from google.oauth2.service_account import Credentials

log = logging.getLogger(__name__)

SCOPES     = ["https://www.googleapis.com/auth/spreadsheets",
               "https://www.googleapis.com/auth/drive"]
SHEET_NAME = os.environ.get("REPORT_SHEET_NAME", "JPROP Weekly Reports")

HEADERS = [
    "Account",
    "Spend (RM)", "Daily Budget (RM)", "Leads",
    "Cost/Lead", "Appt", "Appt %", "Cost/Appt",
]

# Column letters
SP  = "B"   # Spend
BUD = "C"   # Daily Budget
LD  = "D"   # Leads
CPL = "E"   # Cost/Lead  (formula)
APT = "F"   # Appt       (manual, yellow)
APR = "G"   # Appt %     (formula)
CAP = "H"   # Cost/Appt  (formula)
LAST_COL = "H"


def _creds():
    b64 = os.environ.get("GOOGLE_CREDS_B64", "").strip()
    if not b64:
        raise RuntimeError("GOOGLE_CREDS_B64 env var is missing or empty.")
    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        raise RuntimeError(f"GOOGLE_CREDS_B64 is not valid base64: {e}")
    try:
        info = json.loads(raw)
    except Exception as e:
        raise RuntimeError(
            f"GOOGLE_CREDS_B64 decoded but is not valid JSON: {e}. "
            f"Make sure you ran: base64 -i creds.json | tr -d '\\n' | pbcopy"
        )
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def _open_workbook():
    gc = gspread.authorize(_creds())
    sheet_id = os.environ.get("REPORT_SHEET_ID", "")
    if sheet_id:
        return gc.open_by_key(sheet_id)
    try:
        return gc.open(SHEET_NAME)
    except gspread.SpreadsheetNotFound:
        raise RuntimeError(
            "Spreadsheet not found. Set REPORT_SHEET_ID env var with your Google Sheet ID. "
            "Create the sheet manually, share it with your service account email, "
            "then copy the ID from the URL."
        )


def _tab_name_for_last_week() -> str:
    """Returns e.g. '18 May - 24 May 2026' for the last Sun-Sat week."""
    today = datetime.now(timezone.utc).date()
    days_since_sat = (today.weekday() + 2) % 7
    last_sat = today - timedelta(days=days_since_sat)
    last_sun = last_sat - timedelta(days=6)
    return f"{last_sun.strftime('%-d %b')} - {last_sat.strftime('%-d %b %Y')}"


def _formula_row(row: int) -> list:
    """Formulas for Cost/Lead, Appt%, Cost/Appt."""
    return [
        f"=IF({LD}{row}>0, ROUND({SP}{row}/{LD}{row},2), 0)",         # Cost/Lead
        "",                                                              # Appt — manual
        f"=IF({LD}{row}>0, ROUND({APT}{row}/{LD}{row}*100,2), 0)",    # Appt %
        f"=IF({APT}{row}>0, ROUND({SP}{row}/{APT}{row},2), 0)",       # Cost/Appt
    ]


def _formula_total(first: int, last: int) -> list:
    """Formulas for the grand total row. Uses col:row:col:row to avoid range bleed."""
    sp  = f"{SP}{first}:{SP}{last}"
    bud = f"{BUD}{first}:{BUD}{last}"
    ld  = f"{LD}{first}:{LD}{last}"
    apt = f"{APT}{first}:{APT}{last}"
    return [
        f"=ROUND(SUM({sp}),2)",
        f"=ROUND(SUM({bud}),2)",
        f"=SUM({ld})",
        f"=IF(SUM({ld})>0, ROUND(SUM({sp})/SUM({ld}),2), 0)",
        f"=SUM({apt})",
        f"=IF(SUM({ld})>0, ROUND(SUM({apt})/SUM({ld})*100,2), 0)",
        f"=IF(SUM({apt})>0, ROUND(SUM({sp})/SUM({apt}),2), 0)",
    ]


def _write_sync(results: list, tab_name: str) -> str:
    """
    Write account-level summary to a new (or existing) tab.
    results: list of dicts from fetch_all_accounts()
    Returns the spreadsheet URL.
    """
    sh = _open_workbook()

    try:
        ws = sh.worksheet(tab_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(tab_name, rows=100, cols=len(HEADERS))

    all_rows = [HEADERS]
    data_start = 2  # row 1 = header

    for r in results:
        label  = r.get("label", "")
        emoji  = r.get("emoji", "")
        data   = r.get("data") or []
        bm     = r.get("budget_map", {})
        err    = r.get("error")

        if err or not data:
            continue

        # Aggregate spend, budget, leads across all campaigns for this account
        total_spend  = round(sum(float(c.get("spend", 0)) for c in data), 2)
        total_budget = round(sum(bm.get(c.get("campaign_id", ""), 0) for c in data), 2)
        total_leads  = int(sum(
            sum(
                float(a.get("value", 0))
                for a in (c.get("actions") or [])
                if a.get("action_type") in {"lead", "offsite_conversion.lead"}
            )
            for c in data
        ))

        if total_spend == 0:
            continue  # skip accounts with no spend this week

        row_num = data_start + len(all_rows) - 1
        row = [
            f"{emoji} {label}",
            total_spend,
            total_budget,
            total_leads,
        ] + _formula_row(row_num)
        all_rows.append(row)

    if len(all_rows) <= 1:
        ws.append_row(["No data for this period"])
        return sh.url

    # Grand total row
    first_data = data_start
    last_data  = data_start + len(all_rows) - 2
    total_row  = ["TOTAL"] + _formula_total(first_data, last_data)
    all_rows.append(total_row)

    ws.update(all_rows, value_input_option="USER_ENTERED")

    # ── Formatting ─────────────────────────────────────────────────
    n_rows = len(all_rows)

    try:
        # Header: dark blue, white bold
        hdr_fmt = CellFormat(
            backgroundColor=Color(0.12, 0.31, 0.49),
            textFormat=TextFormat(bold=True, foregroundColor=Color(1, 1, 1), fontSize=10),
        )
        format_cell_range(ws, f"A1:{LAST_COL}1", hdr_fmt)

        # Total row: light blue bold
        tot_fmt = CellFormat(
            backgroundColor=Color(0.84, 0.90, 0.95),
            textFormat=TextFormat(bold=True, fontSize=10),
        )
        format_cell_range(ws, f"A{n_rows}:{LAST_COL}{n_rows}", tot_fmt)

        # Appt column (F): yellow — user fills this in
        apt_fmt = CellFormat(backgroundColor=Color(1.0, 0.97, 0.80))
        format_cell_range(ws, f"F2:F{n_rows-1}", apt_fmt)

        # Freeze header row
        set_frozen(ws, rows=1)

        # Column widths
        set_column_width(ws, "A", 200)   # Account
        for col in ["B", "C", "D", "E", "F", "G", "H"]:
            set_column_width(ws, col, 130)

    except Exception as e:
        log.warning(f"Formatting skipped: {e}")

    log.info(f"Weekly report written to tab '{tab_name}' ({n_rows-2} accounts)")
    return sh.url


async def write_weekly_report(results: list, tab_name: str = None) -> str:
    """
    Async wrapper. Returns Google Sheets URL.
    results: from fetch_all_accounts()
    tab_name: optional; defaults to last Sun-Sat week label.
    """
    if not tab_name:
        tab_name = _tab_name_for_last_week()
    return await asyncio.to_thread(_write_sync, results, tab_name)
