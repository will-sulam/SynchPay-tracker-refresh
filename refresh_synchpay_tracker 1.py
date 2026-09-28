#!/usr/bin/env python3
"""
refresh_synchpay_tracker.py

Refreshes the SynchPay Master Client Communication Tracker (Google Sheet) from
Attio CRM data. Written to run OUTSIDE the Cowork session, wherever you have:

  1. A Google service account (or OAuth client) with edit access to the sheet,
     and the Sheets API enabled on that Google Cloud project.
  2. An Attio API token. For full accuracy (see note 2 below) it needs read
     scopes for: Notes, Meetings, and — if you want email-derived touches too
     — Emails (Workspace settings -> Developers -> [integration] -> Scopes).

-------------------------------------------------------------------------------
SETUP
-------------------------------------------------------------------------------
    pip install google-auth google-api-python-client requests

Environment variables (or edit the CONFIG block below):
    GOOGLE_SERVICE_ACCOUNT_JSON   Path to a service-account JSON key file.
                                  The service account's email must be shared
                                  as an Editor on the spreadsheet.
    ATTIO_API_KEY                 Attio API token.

Usage:
    python refresh_synchpay_tracker.py --dry-run     # preview only, no writes
    python refresh_synchpay_tracker.py                # apply the refresh
    python refresh_synchpay_tracker.py --diff-only    # print status changes,
                                                        # write nothing

-------------------------------------------------------------------------------
IMPORTANT — VERIFY BEFORE YOU RUN THIS FOR REAL
-------------------------------------------------------------------------------
1. Target sheet + column layout. Points at "tracker_updated"
   (1BGJxRssifEtUV-opja1bzLv4bK6Y0HiaARVpVjo4cZs), whose "Deals" tab has the
   full 18-column layout (A-R):
       A Deal | B Stage | C Company | D Lead Contact | E Contact Email |
       F Phone / Cell | G PMS / Software | H Days Quiet | I Last Touch |
       J Last Activity Date | K Status | L Lead Temp (Hot/Med/Cold) |
       M Deal Summary (from calls/CRM) | N Last Discussion Topic |
       O Pricing Details (from calls/CRM) | P Recordings Mined |
       Q Suggested Next Action | R Deal Type
   Re-run with --dry-run first if this ever changes.

2. Where "real call/CRM activity" actually lives in this workspace (found by
   testing directly against the live API, not assumed):
     - Deal records themselves have ZERO notes attached. The rich,
       transcript-derived call summaries (Granola-sourced) live as NOTES on
       the PEOPLE records linked to a deal (and occasionally the company).
     - Attio also has a /v2/meetings endpoint — calendar events (Zoom/
       Calendly/etc.), each with participant emails and sometimes
       "linked_records". These carry a reliable date/time even when no note
       was taken, so they're used here as a secondary recency signal (and a
       last-resort summary source: title + description) — but they are NOT
       transcripts, so a meeting-only touch produces a much thinner "Deal
       Summary" than a note-backed one.
     - Attio also exposes an /v2/emails endpoint, but it requires the
       "Emails" read scope on the API key. That scope was NOT available on
       the key used to develop/test this script, so fetch_all_emails() is
       written defensively: it tries once, and if it gets a 403 it disables
       email lookups for the rest of the run and prints a one-line warning
       instead of failing. If your key has that scope, this script will use
       it automatically; if you want it to actually contribute data, grant
       the scope and re-run --dry-run to confirm it's being picked up (watch
       for the "emails scope not available" warning — if you don't see it,
       it worked).
   This script pulls all three sources ONCE up front (not per-deal — that
   was the first draft's mistake and made it both slow and wrong), builds
   in-memory indexes, then looks each deal up against those indexes. The
   single most recent item across all three sources (by timestamp) always
   determines Days Quiet / Last Touch / Status. What determines the Deal
   Summary TEXT is note 2a below.

2a. Multi-call narrative summaries (requires ANTHROPIC_API_KEY). Checking
    the actual note counts per deal while building this: 50 of the 73 deals
    have 2+ notes attached to their linked people/company — some have far
    more (one had 105, because that contact/consultant is shared across
    multiple deals, which is itself worth knowing: a note count that high is
    probably NOT all specific to that one deal, and the summary should be
    read with that in mind). Pasting only the single newest note, as the
    previous version of this script did, throws away that history and reads
    nothing like the original tracker's "intro call covered X, a later call
    pushed on Y" narrative style. So: when a deal has MIN_NOTES_TO_SYNTHESIZE
    (2) or more notes, this script sends the most recent
    MAX_NOTES_PER_SYNTHESIS (12) of them — each truncated to
    MAX_CHARS_PER_NOTE (1500) characters, oldest-to-newest so the model sees
    the actual progression — plus a short list of any other recent meeting
    titles/dates, to Claude (ANTHROPIC_MODEL, default "claude-sonnet-5") and
    asks for a synthesized narrative summary and a one-line "last discussion
    topic," returned as JSON. With exactly 0 or 1 notes there's nothing to
    synthesize, so it skips the API call entirely and uses that single note
    (or the newest meeting) verbatim, same as before — this keeps cost/
    latency down and only spends an API call where it actually adds value.
    If ANTHROPIC_API_KEY is unset, this whole feature is skipped and every
    deal falls back to the old most-recent-item-verbatim behavior, with one
    warning printed up front so it's obvious which mode a given run used.
    IMPORTANT — THIS PATH IS UNTESTED against a live model call as of this
    version: it was built and code-reviewed in an environment without a
    usable Anthropic API key to test against, and the two rounds of dry-run
    testing that caught the /notes-vs-people bug and the future-dated-
    meeting bug happened before this feature existed. Run --dry-run with a
    real ANTHROPIC_API_KEY and actually read the resulting Deal Summary text
    for a handful of multi-note deals (the note-count leaders are the best
    stress test: CareCloud & FoxPT, Manhattan Dental Spa, Dentirate
    Partnership) before trusting it.

2b. Plain-language requirement. Every synthesized summary is explicitly
    written for a high-school reading level: short sentences, one idea per
    sentence, no business/finance jargon (plain "money"/"fee"/"cost" instead
    of "processing volume"/"surcharge"/"reconciliation"/"PMS integration"),
    acronyms spelled out, 2-4 sentences, but still specific — real names,
    dollar amounts, and dates from the notes are kept, "simple" is about the
    words and sentence length, not about being vague. Because of this,
    MIN_NOTES_TO_SYNTHESIZE is set to 1, not 2: even a SINGLE note gets run
    through the model now, since the point isn't just stitching multiple
    calls together, it's also translating jargon-heavy raw notes into plain
    language. Meetings-only deals (no notes at all, just a calendar/Zoom/
    Calendly entry) are routed through the same pass too, since that
    boilerplate (dial-in numbers, "Powered by Calendly.com" footers, etc.)
    needs simplifying just as much. This makes the LLM call fire for
    virtually every deal that has ANY activity on record, not just the ~50
    multi-note ones — expect it to run close to 70 times per refresh rather
    than 50, with a correspondingly longer runtime and higher API cost when
    ANTHROPIC_API_KEY is set. Same untested-against-a-live-key caveat as 2a
    applies to the plain-language wording itself — read the actual output
    before trusting that a high schooler would really follow it.

3. Status thresholds. The legend on the Summary tab reads:
       OVERDUE              - no touch in a while, needs outreach today
       REACH OUT NOW         - inside 5-day check-in window
       ACTIVE                 - touched recently, on track
       NO ACTIVITY ON RECORD  - no calls/notes found at all
       CLOSED (Won/Lost)      - no enrichment run
   The exact day thresholds for OVERDUE vs. REACH OUT NOW vs. ACTIVE were not
   documented anywhere retrievable, so STATUS_THRESHOLDS below is a
   best-effort default — tune it, or replace compute_status() with your real
   business rule. Sanity-check rows with --dry-run against your own judgment.

4. Phone/Cell and Lead Temp are never written automatically (see
   UNTOUCHED_UNLESS_NEW_DATA) — task instructions were to leave them as-is
   unless real data was found, and this script has no verified source for
   either yet.

5. Pricing Details protection. PROTECTED_PRICING lists the entries the task
   said never to touch unless new statement data justifies it: Dr. Volchonok
   / AV Periodontics, Clinton Street Dental, Dental World (verified/green —
   never overwritten, full stop) and Westchester Oral Surgery (keep the
   needs-audit/orange flag unless the new note text itself mentions a
   "statement"). classify_pricing_confidence() color-codes any *new* pricing
   text for non-protected rows — it's a keyword heuristic, not a certified
   classifier; review its output.

6. This script does an authenticated, direct write. Always run --dry-run
   first, read the console output, and only then re-run without --dry-run.

7. Rate limiting / retries. All Attio calls go through request_with_retry(),
   which backs off and retries on 429/502/503/504 — the live API returned a
   transient 502 mid-pagination during development, so this isn't optional.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# =============================================================================
# CONFIG - edit these to match your environment / sheet layout
# =============================================================================

SPREADSHEET_ID = "1BGJxRssifEtUV-opja1bzLv4bK6Y0HiaARVpVjo4cZs"  # "tracker_updated"
DEALS_SHEET_NAME = "Deals"
SUMMARY_SHEET_NAME = "Summary"

GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
ATTIO_API_KEY = os.environ.get("ATTIO_API_KEY", "")
ATTIO_API_BASE = "https://api.attio.com/v2"

# Optional: synthesizes a multi-call narrative "Deal Summary" instead of just
# pasting the single most recent note verbatim (see note 2a below). Leave
# ANTHROPIC_API_KEY unset to skip this and fall back to the old
# most-recent-item-verbatim behavior.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_BASE = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
ANTHROPIC_VERSION = "2023-06-01"
MIN_NOTES_TO_SYNTHESIZE = 1   # any note at all gets run through the LLM — see note 2b (plain-language pass)
MAX_NOTES_PER_SYNTHESIS = 12  # most-recent N notes fed to the model, to bound cost/latency
MAX_CHARS_PER_NOTE = 1500     # truncate any single very long note before it goes in the prompt
MAX_MEETINGS_LISTED = 8       # supplementary "other touchpoints" list, titles/dates only

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

COLUMNS = {
    "deal": 0,
    "stage": 1,
    "company": 2,
    "lead_contact": 3,
    "contact_email": 4,
    "phone_cell": 5,
    "pms_software": 6,
    "days_quiet": 7,
    "last_touch": 8,
    "last_activity_date": 9,
    "status": 10,
    "lead_temp": 11,
    "deal_summary": 12,
    "last_discussion_topic": 13,
    "pricing_details": 14,
    "recordings_mined": 15,
    "suggested_next_action": 16,
    "deal_type": 17,
}
NUM_COLS = len(COLUMNS)

WRITABLE_FIELDS = {
    "days_quiet",
    "last_touch",
    "last_activity_date",
    "status",
    "deal_summary",
    "last_discussion_topic",
    "recordings_mined",
    "suggested_next_action",
}

UNTOUCHED_UNLESS_NEW_DATA = {
    "phone_cell": COLUMNS["phone_cell"],
    "lead_temp": COLUMNS["lead_temp"],
}

PRICING_DETAILS_COL = COLUMNS["pricing_details"]

PROTECTED_PRICING = {
    "verified": {
        "dr. volchonok", "volchonok", "av periodontics",
        "clinton street dental",
        "dental world",
    },
    "needs_audit_keep": {
        "westchester oral surgery",
    },
}

STATUS_COLORS = {
    "OVERDUE":               {"bg": (0.957, 0.800, 0.800), "fg": (0.600, 0.000, 0.000)},  # red
    "REACH OUT NOW":         {"bg": (1.000, 0.949, 0.800), "fg": (0.600, 0.400, 0.000)},  # yellow
    "ACTIVE":                {"bg": (0.851, 0.918, 0.827), "fg": (0.000, 0.400, 0.000)},  # green
    "NO ACTIVITY ON RECORD": {"bg": (0.898, 0.898, 0.898), "fg": (0.400, 0.400, 0.400)},  # gray
    "Closed":                {"bg": (0.851, 0.878, 0.918), "fg": (0.200, 0.250, 0.400)},  # blue-gray
}

PRICING_CONFIDENCE_COLORS = {
    "verified": {"bg": (0.851, 0.918, 0.827)},       # green
    "raw_statement": {"bg": (1.000, 0.878, 0.702)},  # orange
    "call_mention": {"bg": (1.000, 0.949, 0.800)},   # yellow
}

STATUS_THRESHOLDS = {
    "reach_out_now_days": 5,
    "active_days": 10,
}

REQUEST_PAUSE_SECONDS = 0.15
MAX_RETRIES = 5
NOTES_PAGE_LIMIT = 50
MEETINGS_PAGE_LIMIT = 50
EMAILS_PAGE_LIMIT = 50
SAFETY_MAX_PAGES = 500  # hard stop so a pagination bug can't loop forever


# =============================================================================
# Data classes
# =============================================================================

@dataclass
class DealRow:
    row_index: int
    rep: str
    deal_name: str
    company: str
    current: dict = field(default_factory=dict)
    refreshed: dict = field(default_factory=dict)

    def is_protected_verified(self) -> bool:
        haystack = f"{self.deal_name} {self.company}".lower()
        return any(term in haystack for term in PROTECTED_PRICING["verified"])

    def is_protected_needs_audit(self) -> bool:
        haystack = f"{self.deal_name} {self.company}".lower()
        return any(term in haystack for term in PROTECTED_PRICING["needs_audit_keep"])

    def contact_emails(self) -> list:
        raw = self.current.get("contact_email", "") or ""
        return [e.strip().lower() for e in re.split(r"[;,]", raw) if e.strip() and "@" in e]


# =============================================================================
# HTTP helper with retries (the live Attio API returned a transient 502
# mid-pagination during development — don't skip this)
# =============================================================================

def request_with_retry(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    delay = 1.0
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.request(method, url, timeout=30, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(delay)
            delay *= 2
            continue
        if resp.status_code in (429, 502, 503, 504):
            time.sleep(delay)
            delay *= 2
            continue
        return resp
    if last_exc:
        raise last_exc
    return resp  # last response, even if it was a retryable status


# =============================================================================
# Google Sheets helpers
# =============================================================================

def get_sheets_service():
    if not os.path.exists(GOOGLE_SERVICE_ACCOUNT_JSON):
        sys.exit(
            f"ERROR: service account file not found at "
            f"{GOOGLE_SERVICE_ACCOUNT_JSON!r}. Set GOOGLE_SERVICE_ACCOUNT_JSON "
            f"or edit the CONFIG block."
        )
    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_SERVICE_ACCOUNT_JSON, scopes=SHEETS_SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def get_sheet_id(service, sheet_name: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for sheet in meta["sheets"]:
        props = sheet["properties"]
        if props["title"] == sheet_name:
            return props["sheetId"]
    sys.exit(f"ERROR: sheet tab {sheet_name!r} not found in spreadsheet.")


def read_deals_tab(service) -> list:
    last_col_letter = _col_letter(NUM_COLS - 1)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SPREADSHEET_ID,
             range=f"'{DEALS_SHEET_NAME}'!A1:{last_col_letter}1000")
        .execute()
    )
    return result.get("values", [])


def parse_deal_rows(grid: list) -> list:
    rows = []
    current_rep = None
    in_data_block = False

    for i, raw in enumerate(grid):
        row_num = i + 1
        first_cell = raw[0].strip() if raw else ""

        if not first_cell:
            in_data_block = False
            continue

        if re.search(r"\(\d+ deals\)\s*$", first_cell):
            current_rep = re.sub(r"\s*\(\d+ deals\)\s*$", "", first_cell).strip()
            in_data_block = False
            continue

        if first_cell == "Deal":
            in_data_block = True
            continue

        if first_cell.startswith("Deals —") or first_cell.startswith("SynchPay"):
            continue

        if in_data_block and current_rep:
            padded = raw + [""] * (NUM_COLS - len(raw))
            current = {name: padded[idx] for name, idx in COLUMNS.items()}
            rows.append(
                DealRow(
                    row_index=row_num,
                    rep=current_rep,
                    deal_name=current["deal"],
                    company=current["company"],
                    current=current,
                )
            )

    return rows


def build_value_update_requests(deal_rows: list) -> list:
    requests_batch = []
    for d in deal_rows:
        if not d.refreshed:
            continue

        for field_name in WRITABLE_FIELDS:
            if field_name not in d.refreshed:
                continue
            col_letter = _col_letter(COLUMNS[field_name])
            requests_batch.append(
                {
                    "range": f"'{DEALS_SHEET_NAME}'!{col_letter}{d.row_index}",
                    "values": [[d.refreshed[field_name]]],
                }
            )

        if "pricing_details" in d.refreshed:
            if d.is_protected_verified():
                print(f"  [skip] {d.deal_name}: VERIFIED pricing is protected, not overwriting.")
            elif d.is_protected_needs_audit() and not d.refreshed.get("pricing_details_is_new_statement"):
                print(f"  [skip] {d.deal_name}: needs-audit flag protected, not clearing without new statement data.")
            else:
                col_letter = _col_letter(PRICING_DETAILS_COL)
                requests_batch.append(
                    {
                        "range": f"'{DEALS_SHEET_NAME}'!{col_letter}{d.row_index}",
                        "values": [[d.refreshed["pricing_details"]]],
                    }
                )

    return requests_batch


def _col_letter(idx: int) -> str:
    letters = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def build_color_requests(deal_rows: list, sheet_id: int) -> list:
    requests_batch = []
    status_col = COLUMNS["status"]
    pricing_col = COLUMNS["pricing_details"]

    for d in deal_rows:
        new_status = d.refreshed.get("status")
        if new_status and new_status != d.current.get("status"):
            bucket = new_status.split(" (")[0].strip()
            colors = STATUS_COLORS.get(bucket)
            if colors:
                requests_batch.append(_repeat_cell_color_request(
                    sheet_id, d.row_index, status_col, colors["bg"], colors["fg"]
                ))

        if "pricing_details" in d.refreshed and not d.is_protected_verified() \
                and not (d.is_protected_needs_audit() and not d.refreshed.get("pricing_details_is_new_statement")):
            confidence = classify_pricing_confidence(d.refreshed["pricing_details"])
            colors = PRICING_CONFIDENCE_COLORS.get(confidence)
            if colors:
                requests_batch.append(_repeat_cell_color_request(
                    sheet_id, d.row_index, pricing_col, colors["bg"], None
                ))

    return requests_batch


def _repeat_cell_color_request(sheet_id, row_index, col_idx, bg_rgb, fg_rgb):
    cell_format = {"backgroundColor": {"red": bg_rgb[0], "green": bg_rgb[1], "blue": bg_rgb[2]}}
    fields = "userEnteredFormat.backgroundColor"
    if fg_rgb:
        cell_format["textFormat"] = {"foregroundColor": {"red": fg_rgb[0], "green": fg_rgb[1], "blue": fg_rgb[2]}}
        fields += ",userEnteredFormat.textFormat.foregroundColor"
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_index - 1,
                "endRowIndex": row_index,
                "startColumnIndex": col_idx,
                "endColumnIndex": col_idx + 1,
            },
            "cell": {"userEnteredFormat": cell_format},
            "fields": fields,
        }
    }


def classify_pricing_confidence(text: str) -> str:
    t = (text or "").lower()
    if any(kw in t for kw in ("processor statement", "verified", "audited", "synchpay model")):
        return "verified"
    if any(kw in t for kw in ("statement on file", "raw statement")):
        return "raw_statement"
    return "call_mention"


def apply_updates(service, deal_rows: list, sheet_id: int, dry_run: bool):
    value_updates = build_value_update_requests(deal_rows)
    color_updates = build_color_requests(deal_rows, sheet_id)

    print(f"Prepared {len(value_updates)} cell value updates and "
          f"{len(color_updates)} cell-color updates.")

    if dry_run:
        print("--dry-run set: no writes performed.")
        return

    if value_updates:
        body = {"valueInputOption": "USER_ENTERED", "data": value_updates}
        service.spreadsheets().values().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()
        time.sleep(REQUEST_PAUSE_SECONDS)

    if color_updates:
        service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": color_updates}).execute()

    print("Sheet updated.")


# =============================================================================
# Attio: bulk fetch + indexing (fetched ONCE, not per-deal)
# =============================================================================

def attio_session() -> requests.Session:
    if not ATTIO_API_KEY:
        sys.exit("ERROR: ATTIO_API_KEY is not set.")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {ATTIO_API_KEY}", "Content-Type": "application/json"})
    return s


def fetch_all_deals(session: requests.Session) -> list:
    deals = []
    offset = 0
    limit = 50
    for _ in range(SAFETY_MAX_PAGES):
        resp = request_with_retry(session, "POST", f"{ATTIO_API_BASE}/objects/deals/records/query",
                                   json={"limit": limit, "offset": offset})
        resp.raise_for_status()
        page = resp.json().get("data", [])
        deals.extend(page)
        if len(page) < limit:
            break
        offset += limit
        time.sleep(REQUEST_PAUSE_SECONDS)
    return deals


def fetch_all_notes(session: requests.Session) -> dict:
    """Returns {(parent_object, parent_record_id): [note, ...]} for every note
    in the workspace, fetched once via offset pagination (max page size 50).
    This is where the actual call-mined content lives (see note 2 at top)."""
    index = {}
    offset = 0
    total = 0
    for page_num in range(SAFETY_MAX_PAGES):
        resp = request_with_retry(session, "GET", f"{ATTIO_API_BASE}/notes",
                                   params={"limit": NOTES_PAGE_LIMIT, "offset": offset})
        if resp.status_code != 200:
            print(f"  WARNING: /notes page at offset {offset} returned {resp.status_code}; stopping early.")
            break
        data = resp.json().get("data", [])
        for note in data:
            key = (note.get("parent_object"), note.get("parent_record_id"))
            index.setdefault(key, []).append(note)
        total += len(data)
        if len(data) < NOTES_PAGE_LIMIT:
            break
        offset += NOTES_PAGE_LIMIT
        time.sleep(REQUEST_PAUSE_SECONDS)
    print(f"Fetched {total} notes from Attio.")
    return index


def fetch_all_meetings(session: requests.Session) -> dict:
    """Returns {"by_record": {(object_slug, record_id): [meeting,...]},
                "by_email": {email_lower: [meeting,...]}}
    fetched once via cursor pagination. Meetings are calendar events, not
    transcripts — used as a recency signal and last-resort summary text."""
    by_record, by_email = {}, {}
    cursor = None
    total = 0
    for page_num in range(SAFETY_MAX_PAGES):
        params = {"limit": MEETINGS_PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        resp = request_with_retry(session, "GET", f"{ATTIO_API_BASE}/meetings", params=params)
        if resp.status_code != 200:
            print(f"  WARNING: /meetings page {page_num} returned {resp.status_code}; stopping early.")
            break
        body = resp.json()
        data = body.get("data", [])
        for mtg in data:
            for link in mtg.get("linked_records", []) or []:
                key = (link.get("object_slug"), link.get("record_id"))
                by_record.setdefault(key, []).append(mtg)
            for p in mtg.get("participants", []) or []:
                email = (p.get("email_address") or "").strip().lower()
                if email:
                    by_email.setdefault(email, []).append(mtg)
        total += len(data)
        cursor = body.get("pagination", {}).get("next_cursor")
        if not cursor or not data:
            break
        time.sleep(REQUEST_PAUSE_SECONDS)
    print(f"Fetched {total} meetings from Attio.")
    return {"by_record": by_record, "by_email": by_email}


def fetch_all_emails(session: requests.Session) -> dict:
    """Best-effort. Returns {"by_email": {email_lower: [email,...]}}, or an
    empty index (with a printed warning) if the API key doesn't have the
    Emails read scope — this was the case on the key used to build this
    script, so this path is untested against real data. If your key has the
    scope, watch the console: if you don't see the scope warning below, it
    worked and emails are contributing to the refresh."""
    by_email = {}
    resp = request_with_retry(session, "GET", f"{ATTIO_API_BASE}/emails", params={"limit": EMAILS_PAGE_LIMIT})
    if resp.status_code == 403:
        print("  NOTE: this Attio API key doesn't have the Emails read scope — "
              "skipping email-derived touches. Grant 'Read access to Emails' "
              "on the key in Attio (Workspace settings -> Developers) for "
              "full accuracy, then re-run.")
        return {"by_email": by_email}
    if resp.status_code != 200:
        print(f"  WARNING: /emails returned {resp.status_code}; skipping email-derived touches.")
        return {"by_email": by_email}

    cursor = None
    total = 0
    first_page = resp.json()
    pages_data = [first_page]
    cursor = first_page.get("pagination", {}).get("next_cursor")
    for page_num in range(SAFETY_MAX_PAGES - 1):
        if not cursor:
            break
        r = request_with_retry(session, "GET", f"{ATTIO_API_BASE}/emails",
                                params={"limit": EMAILS_PAGE_LIMIT, "cursor": cursor})
        if r.status_code != 200:
            break
        body = r.json()
        pages_data.append(body)
        cursor = body.get("pagination", {}).get("next_cursor")
        time.sleep(REQUEST_PAUSE_SECONDS)

    for body in pages_data:
        for email in body.get("data", []):
            total += 1
            participants = (email.get("to", []) or []) + (email.get("from", []) or []) + (email.get("cc", []) or [])
            for p in participants:
                addr = (p.get("email_address") or p if isinstance(p, str) else p.get("email_address", "")) or ""
                addr = addr.strip().lower() if isinstance(addr, str) else ""
                if addr:
                    by_email.setdefault(addr, []).append(email)
    print(f"Fetched {total} emails from Attio.")
    return {"by_email": by_email}


def get_related_parents(attio_deal: dict) -> list:
    """(object, record_id) pairs worth checking for a given deal: its
    associated people, consultant(s), associated company, and the deal
    record itself — in that priority order (richest notes were found on
    people records; the deal record itself had none in testing, but it's a
    free dict lookup now so there's no cost to still checking it)."""
    values = attio_deal.get("values", {}) or {}
    parents = []

    def _extract(entries):
        out = []
        if not entries:
            return out
        if isinstance(entries, dict):
            entries = [entries]
        for e in entries:
            if not isinstance(e, dict):
                continue
            obj = e.get("target_object") or e.get("object_slug") or e.get("object")
            rid = e.get("target_record_id") or e.get("record_id")
            if obj and rid:
                out.append((obj, rid))
        return out

    parents.extend(_extract(values.get("associated_people")))
    parents.extend(_extract(values.get("consultant")))
    parents.extend(_extract(values.get("associated_company")))
    deal_id = attio_deal.get("id", {}).get("record_id")
    if deal_id:
        parents.append(("deals", deal_id))

    seen = set()
    deduped = []
    for obj, rid in parents:
        key = (obj, rid)
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


# =============================================================================
# Refresh computation
# =============================================================================

def _parse_dt(raw: str):
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def gather_activity_candidates(d: DealRow, attio_deal: dict, note_index: dict,
                                meeting_index: dict, email_index: dict) -> list:
    """Returns a list of (datetime, source_type, text) candidates, newest
    first, pooling every note/meeting/email touch found across every record
    linked to this deal.

    BUG FOUND IN TESTING: "XPO Transitions" matched a meeting dated 2028 —
    two years in the future (a future-scheduled or mis-dated calendar entry
    picked up via a shared contact email). That produced a negative
    days-quiet and a bogus REACH OUT NOW. Any candidate timestamp after "now"
    is therefore discarded below — a deal can't have been touched in the
    future, so a future-dated item is either a scheduled-but-not-yet-happened
    meeting (not evidence of a completed touch) or bad calendar data."""
    now = dt.datetime.now(dt.timezone.utc)
    candidates = []
    seen_ids = set()

    for obj, rid in get_related_parents(attio_deal):
        for note in note_index.get((obj, rid), []):
            note_id = note.get("id", {}).get("note_id")
            if note_id in seen_ids:
                continue
            seen_ids.add(note_id)
            created = _parse_dt(note.get("created_at"))
            if not created or created > now:
                continue
            text = note.get("content_plaintext") or note.get("content_markdown") or ""
            title = note.get("title") or ""
            candidates.append((created, "note", f"{title}\n{text}".strip() if title else text))

        for mtg in meeting_index.get("by_record", {}).get((obj, rid), []):
            mtg_id = mtg.get("id", {}).get("meeting_id")
            if mtg_id in seen_ids:
                continue
            seen_ids.add(mtg_id)
            created = _parse_dt((mtg.get("start") or {}).get("datetime"))
            if not created or created > now:
                continue
            title = mtg.get("title") or "Meeting"
            desc = (mtg.get("description") or "").strip()
            text = f"Meeting: {title}" + (f" — {desc[:300]}" if desc else "")
            candidates.append((created, "meeting", text))

    for email_addr in d.contact_emails():
        for mtg in meeting_index.get("by_email", {}).get(email_addr, []):
            mtg_id = mtg.get("id", {}).get("meeting_id")
            if mtg_id in seen_ids:
                continue
            seen_ids.add(mtg_id)
            created = _parse_dt((mtg.get("start") or {}).get("datetime"))
            if not created or created > now:
                continue
            title = mtg.get("title") or "Meeting"
            desc = (mtg.get("description") or "").strip()
            text = f"Meeting: {title}" + (f" — {desc[:300]}" if desc else "")
            candidates.append((created, "meeting", text))

        for eml in email_index.get("by_email", {}).get(email_addr, []):
            eml_id = eml.get("id", {}).get("email_id") if isinstance(eml.get("id"), dict) else eml.get("id")
            if eml_id in seen_ids:
                continue
            seen_ids.add(eml_id)
            created = _parse_dt(eml.get("sent_at") or eml.get("created_at"))
            if not created or created > now:
                continue
            subject = eml.get("subject") or "Email"
            snippet = (eml.get("snippet") or eml.get("summary") or "").strip()
            text = f"Email: {subject}" + (f" — {snippet[:300]}" if snippet else "")
            candidates.append((created, "email", text))

    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates


_ANTHROPIC_WARNING_PRINTED = False


def synthesize_multi_call_summary(deal_name: str, company: str, notes: list, meetings: list):
    """
    notes: list of (datetime, text) tuples, any order — internally sorted
           oldest-first so the model sees the real progression.
    meetings: list of (datetime, title) tuples, supplementary context only.
    Returns {"summary": str, "last_discussion_topic": str} on success, or
    None on any failure (missing key, HTTP error, unparseable response) —
    callers must fall back to the non-LLM path when this returns None.
    """
    global _ANTHROPIC_WARNING_PRINTED
    if not ANTHROPIC_API_KEY:
        if not _ANTHROPIC_WARNING_PRINTED:
            print("  NOTE: ANTHROPIC_API_KEY is not set — multi-call narrative summaries are "
                  "disabled. Every deal's Deal Summary will fall back to the single most recent "
                  "note/meeting, verbatim, same as the previous version of this script.")
            _ANTHROPIC_WARNING_PRINTED = True
        return None

    notes_sorted = sorted(notes, key=lambda n: n[0])[-MAX_NOTES_PER_SYNTHESIS:]
    notes_block = "\n\n".join(
        f"[Call/note {i+1} — {ts.date().isoformat()}]\n{text[:MAX_CHARS_PER_NOTE]}"
        for i, (ts, text) in enumerate(notes_sorted)
    )
    meetings_sorted = sorted(meetings, key=lambda m: m[0], reverse=True)[:MAX_MEETINGS_LISTED]
    meetings_block = "\n".join(f"- {ts.date().isoformat()}: {title}" for ts, title in meetings_sorted)

    prompt = f"""You are maintaining a sales CRM tracker. Below are call/meeting notes for one deal,
oldest to newest. Write a "Deal Summary" covering who's involved, what's been discussed across the
calls (in order if there's a progression — e.g. "First call covered X. Later, Y came up."),
any pushback or concerns raised, and where things stand now. Be specific and factual — use real
names and numbers from the notes, don't editorialize or invent anything not in the notes.

WRITING LEVEL — this is the most important instruction: write it so a high school student with no
business or finance background could read it and fully understand it on the first pass.
    - Short sentences. One idea per sentence.
    - Plain, everyday words. No jargon, and no business/finance buzzwords — say "money," "fee," or
      "cost" instead of terms like "processing volume," "surcharge," "reconciliation," or "PMS
      integration." If a technical term from the notes is essential to keep (a product name, a
      software name), keep the name but explain in a few plain words what it does or why it matters.
    - No acronyms without spelling out what they mean in plain language the first time.
    - 2-4 sentences total. Say only what matters — don't pad it to sound more sophisticated.
    - Still be specific: keep real names, dollar amounts, and dates from the notes. "Simple" means
      easy words and short sentences, not vague or missing the actual facts.

Also give a short "last_discussion_topic" (under 12 words, same plain-language rule) describing
just the most recent call's main subject.

Deal: {deal_name}
Company: {company or "(not set)"}

--- Notes (chronological) ---
{notes_block or "(no notes)"}

--- Other recent meetings on file (titles/dates only, no transcript) ---
{meetings_block or "(none)"}

Respond with ONLY a JSON object, no other text, in exactly this shape:
{{"summary": "...", "last_discussion_topic": "..."}}"""

    session = requests.Session()
    session.headers.update({
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    })
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}],
    }

    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(ANTHROPIC_API_BASE, json=body, timeout=60)
        except requests.RequestException as exc:
            print(f"  WARNING: Anthropic API request failed for {deal_name!r} ({exc}); "
                  f"falling back to verbatim summary for this deal.")
            return None
        if resp.status_code in (429, 529, 502, 503, 504):
            time.sleep(delay)
            delay *= 2
            continue
        break
    else:
        print(f"  WARNING: Anthropic API kept rate-limiting/erroring for {deal_name!r}; "
              f"falling back to verbatim summary for this deal.")
        return None

    if resp.status_code != 200:
        print(f"  WARNING: Anthropic API returned {resp.status_code} for {deal_name!r} "
              f"({resp.text[:200]!r}); falling back to verbatim summary for this deal.")
        return None

    try:
        content = resp.json()["content"][0]["text"]
        match = re.search(r"\{.*\}", content, re.DOTALL)
        parsed = json.loads(match.group(0) if match else content)
        return {
            "summary": parsed["summary"].strip(),
            "last_discussion_topic": parsed.get("last_discussion_topic", "").strip(),
        }
    except (KeyError, ValueError, IndexError, AttributeError) as exc:
        print(f"  WARNING: couldn't parse Anthropic response for {deal_name!r} ({exc}); "
              f"falling back to verbatim summary for this deal.")
        return None


def compute_status(days_quiet, stage: str) -> str:
    stage_lower = (stage or "").lower()
    if stage_lower.startswith("won") or stage_lower == "lost":
        return "Closed"
    if days_quiet is None:
        return "NO ACTIVITY ON RECORD"
    if days_quiet <= STATUS_THRESHOLDS["reach_out_now_days"]:
        return "REACH OUT NOW"
    if days_quiet <= STATUS_THRESHOLDS["active_days"]:
        return "ACTIVE"
    return f"OVERDUE ({days_quiet}d)"


def refresh_deal_row(d: DealRow, attio_deal: dict, note_index: dict,
                      meeting_index: dict, email_index: dict):
    stage = attio_deal.get("values", {}).get("stage", [{}])[0].get("status", {}).get("title", "")
    candidates = gather_activity_candidates(d, attio_deal, note_index, meeting_index, email_index)

    days_quiet = None
    last_touch_date = ""
    if candidates:
        newest_dt, _, _ = candidates[0]
        days_quiet = (dt.datetime.now(dt.timezone.utc) - newest_dt).days
        last_touch_date = newest_dt.date().isoformat()

    status = compute_status(days_quiet, stage)

    d.refreshed["days_quiet"] = str(days_quiet) if days_quiet is not None else "—"
    d.refreshed["last_touch"] = last_touch_date or "—"
    d.refreshed["last_activity_date"] = last_touch_date or "No record"
    d.refreshed["status"] = status

    if candidates:
        all_notes = [(ts, text) for ts, kind, text in candidates if kind == "note"]
        all_meetings = [(ts, text) for ts, kind, text in candidates if kind == "meeting"]

        summary_text = None
        topic_text = None

        if len(all_notes) >= MIN_NOTES_TO_SYNTHESIZE or all_meetings:
            # Runs the plain-language pass whenever there's at least one note,
            # OR (meetings-only deals) at least one meeting — calendar/Zoom/
            # Calendly text is often full of dial-in numbers and scheduling
            # boilerplate that's just as much in need of simplifying as a
            # multi-call note dump. See note 2b in the module docstring.
            synthesized = synthesize_multi_call_summary(d.deal_name, d.company, all_notes, all_meetings)
            if synthesized:
                summary_text = synthesized["summary"]
                topic_text = synthesized["last_discussion_topic"] or None

        if summary_text is None:
            # Nothing to synthesize, or the LLM call failed/was skipped
            # (no ANTHROPIC_API_KEY): fall back to the single richest recent
            # item, verbatim — same as the pre-LLM version of this script.
            newest_dt = candidates[0][0]
            window = [c for c in candidates if (newest_dt - c[0]).days <= 2]
            best_note = next((c for c in window if c[1] == "note"), None)
            chosen = best_note or window[0]
            _, _, summary_text = chosen
            summary_text = summary_text[:600]

        d.refreshed["deal_summary"] = summary_text[:600]
        if topic_text:
            d.refreshed["last_discussion_topic"] = topic_text
        d.refreshed["suggested_next_action"] = (
            "Overdue — call or email today" if status.startswith("OVERDUE")
            else "Inside check-in window — reach out" if status == "REACH OUT NOW"
            else "On track — no action needed yet" if status == "ACTIVE"
            else "No activity on record — verify deal is real / reach out cold" if status == "NO ACTIVITY ON RECORD"
            else "Closed — no action needed"
        )
        d.refreshed["recordings_mined"] = str(len(all_notes))

        if re.search(r"\b(rate|pricing|fee|surcharge|%|statement|volume)\b", summary_text, re.IGNORECASE):
            d.refreshed["pricing_details"] = summary_text[:600]
            d.refreshed["pricing_details_is_new_statement"] = bool(
                re.search(r"\bstatement\b", summary_text, re.IGNORECASE)
            )
    # No candidates found at all -> leave deal_summary / suggested_next_action
    # / pricing_details / recordings_mined out of d.refreshed so the existing
    # text is preserved rather than blanked out.


def match_attio_deal(d: DealRow, attio_deals: list):
    name_lower = d.deal_name.strip().lower()
    for rec in attio_deals:
        rec_name = rec.get("values", {}).get("name", [{}])[0].get("value", "")
        if rec_name.strip().lower() == name_lower:
            return rec
    return None


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                         help="Compute everything but do not write to the sheet.")
    parser.add_argument("--diff-only", action="store_true",
                         help="Print rows whose status would change, then exit without writing.")
    args = parser.parse_args()

    sheets = get_sheets_service()
    sheet_id = get_sheet_id(sheets, DEALS_SHEET_NAME)
    grid = read_deals_tab(sheets)
    deal_rows = parse_deal_rows(grid)
    print(f"Read {len(deal_rows)} deal rows from the '{DEALS_SHEET_NAME}' tab of spreadsheet {SPREADSHEET_ID}.")

    session = attio_session()
    attio_deals = fetch_all_deals(session)
    print(f"Fetched {len(attio_deals)} deal records from Attio.")

    note_index = fetch_all_notes(session)
    meeting_index = fetch_all_meetings(session)
    email_index = fetch_all_emails(session)

    unmatched = []
    for d in deal_rows:
        rec = match_attio_deal(d, attio_deals)
        if rec is None:
            unmatched.append(d.deal_name)
            continue
        refresh_deal_row(d, rec, note_index, meeting_index, email_index)

    if unmatched:
        print(f"\nWARNING: {len(unmatched)} sheet rows had no matching Attio deal by name and were left untouched:")
        for name in unmatched:
            print(f"  - {name}")

    changed = [d for d in deal_rows if d.refreshed.get("status") not in (None, d.current.get("status"))]
    print(f"\n{len(changed)} rows have a status change:")
    for d in changed:
        print(f"  [{d.rep}] {d.deal_name}: {d.current.get('status')!r} -> {d.refreshed.get('status')!r}")

    protected_skipped = [d for d in deal_rows if "pricing_details" in d.refreshed and d.is_protected_verified()]
    if protected_skipped:
        print(f"\n{len(protected_skipped)} rows had new pricing text detected but were PROTECTED (verified) and left untouched:")
        for d in protected_skipped:
            print(f"  - {d.deal_name}")

    if args.diff_only:
        return

    apply_updates(sheets, deal_rows, sheet_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
