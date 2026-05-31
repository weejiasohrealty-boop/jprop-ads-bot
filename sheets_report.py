"""
sheets_report.py — Write weekly Meta ad data to Google Sheets.

Sheet: "JPROP Weekly Reports"  (created automatically)
Tab per week: "25 May - 31 May 2026"

Columns:
  A: Account        B: Campaign        C: Spend (RM)    D: Daily Budget (RM)
  E: Leads          F: Cost/Lead       G: Appt          H: Appt %
  I: Cost/Appt

F, H, I are formulas — auto-calculate.
G (Appt) is filled manually by WJ in Google Sheets.

Env vars required:
  GOOGLE_CREDS_B64   — base64-encoded service account JSON
  REPORT_SHEET_NAME  — spreadsheet name (default: JPROP Weekly Reports)
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
    "Account", "Campaign",
    "Spend (RM)", "Daily Budget (RM)", "Leads",
    "Cost/Lead", "Appt", "Appt %", "Cost/Appt",
]

# Column letters (A=1)
C = {h: chr(64 + i + 1) for i, h in enumerate(HEADERS)}
# C["Spend (RM)"] = "C", C["Leads"] = "E", etc.
SP  = "C"   # Spend
BUD = "D"   # Daily Budget
LD  = "E"   # Leads
CPL = "F"   # Cost/Lead  (formula)
APT = "G"   # Appt       (manual)
APR = "H"   # Appt %     (formula)
CAP = "I"   # Cost/Appt  (formula)


def _creds():
    b64 = os.environ.get("GOOGLE_CREDS_B64", "")
    if not b64:
        raise RuntimeError("Missing GOOGLE_CREDS_B64")
    return Credentials.from_service_account_info(
        json.loads(base64.b64decode(b64)), scopes=SCOPES)


def _open_workbook():
    gc = gspread.authorize(_creds())
    try:
        return gc.open(SHEET_NAME)
    except gspread.SpreadsheetNotFound:
        sh = gc.create(SHEET_NAME)
        log.info(f"Created spreadsheet: {SHEET_NAME}")
        return sh


def _tab_name_for_last_week() -> str:
    """Returns e.g. '18 May - 24 May 2026' for the last Sun-Sat week."""
    today = datetime.now(timezone.utc).date()
    # Most recent Saturday
    days_since_sat = (today.weekday() + 2) % 7  # Mon=0 → sat offset
    last_sat = today - timedelta(days=days_since_sat)
    last_sun = last_sat - timedelta(days=6)
    return f"{last_sun.strftime('%-d %b')} - {last_sat.strftime('%-d %b %Y')}"


def _tab_name_for_this_week() -> str:
    today = datetime.now(timezone.utc).date()
    days_since_sun = (today.weekday() + 1) % 7
    this_sun = today - timedelta(days=days_since_sun)
    return f"Week of {this_sun.strftime('%-d %b %Y')}"


def _formula_row(row: int) -> list:
    """Return formula strings for CPL, Appt%, Cost/Appt for a data row."""
    return [
        f"=IF({LD}{row}>0, ROUND({SP}{row}/{LD}{row},2), 0)",   # Cost/Lead
        "",                                                        # Appt — manual
        f"=IF({LD}{row}>0, ROUND({APT}{row}/{LD}{row}*100,2), 0)",  # Appt%
        f"=IF({APT}{row}>0, ROUND({SP}{row}/{APT}{row},2), 0)",  # Cost/Appt
    ]


def _formula_total(first: int, last: int) -> list:
    """Formulas for the grand total row."""
    rng = f"{first}:{last}"
    return [
        f"=ROUND(SUM({SP}{rng}),2)",
        f"=ROUND(SUM({BUD}{rng}),2)",
        f"=SUM({LD}{rng})",
        f"=IF(SUM({LD}{rng})>0, ROUND(SUM({SP}{rng})/SUM({LD}{rng}),2), 0)",
        f"=SUM({APT}{rng})",
        f"=IF(SUM({LD}{rng})>0, ROUND(SUM({APT}{rng})/SUM({LD}{rng})*100,2), 0)",
        f"=IF(SUM({APT}{rng})>0, ROUND(SUM({SP}{rng})/SUM({APT}{rng}),2), 0)",
    ]


def _write_sync(results: list, tab_name: str) -> str:
    """
    Write results to a new (or existing) tab. Returns the spreadsheet URL.
    results: list of dicts from fetch_all_accounts()
    """
    sh = _open_workbook()

    # Get or create the worksheet tab
    try:
        ws = sh.worksheet(tab_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(tab_name, rows=200, cols=len(HEADERS))

    all_rows = [HEADERS]
    data_start = 2  # row index of first data row (1-based, row 1 = header)

    for r in results:
        label   = r.get("label", "")
        emoji   = r.get("emoji", "")
        data    = r.get("data") or []
        bm      = r.get("budget_map", {})
        err     = r.get("error")

        if err or not data:
            continue

        for c in data:
            spend  = round(float(c.get("spend", 0)), 2)
            cid    = c.get("campaign_id", "")
            budget = round(bm.get(cid, 0), 2)
            leads  = int(sum(
                float(a.get("value", 0))
                for a in (c.get("actions") or [])
                if a.get("action_type") in {"lead", "offsite_conversion.lead"}
            ))
            camp_name = c.get("campaign_name", "Unknown")
            row_num   = data_start + len(all_rows) - 1  # -1 for header

            row = [
                f"{emoji} {label}",
                camp_name,
                spend,
                budget,
                leads,
            ] + _formula_row(row_num)
            all_rows.append(row)

    if len(all_rows) <= 1:
        ws.append_row(["No data for this period"])
        return sh.url

    # Total row
    first_data = data_start
    last_data  = data_start + len(all_rows) - 2  # -1 header, -1 for 0-index
    total_row  = ["TOTAL", ""] + _formula_total(first_data, last_data)
    all_rows.append(total_row)

    ws.update(all_rows, value_input_option="USER_ENTERED")

    # ── Formatting ────────────────────────────────────────────────
    n_rows = len(all_rows)

    # Header: dark blue background, white bold text
    try:
        from gspread_formatting import (
            format_cell_range, CellFormat, TextFormat, Color,
            set_frozen, set_column_width, borders
        )
        hdr_fmt = CellFormat(
            backgroundColor=Color(0.12, 0.31, 0.49),
            textFormat=TextFormat(bold=True, foregroundColor=Color(1, 1, 1), fontSize=10),
        )
        format_cell_range(ws, f"A1:I1", hdr_fmt)

        # Total row: light blue background, bold
        tot_fmt = CellFormat(
            backgroundColor=Color(0.84, 0.90, 0.95),
            textFormat=TextFormat(bold=True, fontSize=10),
        )
        format_cell_range(ws, f"A{n_rows}:I{n_rows}", tot_fmt)

        # Appt column: yellow highlight so user knows to fill it
        apt_fmt = CellFormat(
            backgroundColor=Color(1.0, 0.97, 0.80),
        )
        format_cell_range(ws, f"G2:G{n_rows-1}", apt_fmt)

        # Freeze header row
        set_frozen(ws, rows=1)

        # Column widths
        set_column_width(ws, "A", 160)
        set_column_width(ws, "B", 260)
        for col in ["C", "D", "E", "F", "G", "H", "I"]:
            set_column_width(ws, col, 110)

    except Exception as e:
        log.warning(f"Formatting skipped: {e}")

    log.info(f"Weekly report written to tab '{tab_name}' ({n_rows} rows)")
    return sh.url


async def write_weekly_report(results: list, tab_name: str = None) -> str:
    """
    Async wrapper. Returns Google Sheets URL.
    tab_name: optional custom tab name; defaults to last Sun-Sat week.
    """
    if not tab_name:
        tab_name = _tab_name_for_last_week()
    return await asyncio.to_thread(_write_sync, results, tab_name)
