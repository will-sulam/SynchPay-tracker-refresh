# SynchPay Tracker Refresh

Refreshes the SynchPay Master Client Communication Tracker (Google Sheet) from Attio CRM
data (notes, meetings, and, optionally, emails), and can optionally rewrite each deal's
"Deal Summary" as a plain-language narrative using Claude.

## Setup

```
pip install -r requirements.txt
```

Environment variables:

| Variable | Required | Purpose |
|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Yes | Path to a service-account JSON key file (see below). |
| `ATTIO_API_KEY` | Yes | Attio API token. Needs read access to Notes and Meetings; Emails is optional (the script degrades gracefully without it). |
| `ANTHROPIC_API_KEY` | No | If set, Deal Summaries are synthesized across all of a deal's notes/meetings in plain language. If unset, the script falls back to using the single most recent note/meeting verbatim. |
| `ANTHROPIC_MODEL` | No | Defaults to `claude-sonnet-5`. |

### Service account scope (the "block everything else" requirement)

The service account should be able to do exactly one thing: read/write the Sheets API on
this one spreadsheet. Two things enforce that, and both are already how this is set up —
there is no separate "restrict this service account to Sheets only" IAM role to hunt down:

1. **The spreadsheet is shared directly with the service account's email**
   (`client_email` in the JSON key, e.g. `sheets-logger@<project>.iam.gserviceaccount.com`)
   as an Editor. A service account with no files shared to it can authenticate but has
   nothing to act on — sharing is the actual access boundary, not a project-level IAM
   grant.
2. **The script only ever requests the `https://www.googleapis.com/auth/spreadsheets`
   OAuth scope** (see `SHEETS_SCOPES` in `refresh_synchpay_tracker.py`) — it never asks
   for Drive, Gmail, or any other scope, so even if this key leaked, it can't be used
   for anything beyond Sheets, and only against files explicitly shared with it.

Extra hardening, if you want belt-and-suspenders: in the GCP project's **APIs & Services**
page, disable every API except **Google Sheets API** for this project, so the key
physically cannot call anything else even if scope/sharing were misconfigured later.

**Never commit the service-account JSON key file** — `.gitignore` here already excludes
it, `secrets/`, `.env`, and common key-file naming patterns. Treat the private key as a
secret: if it's ever pasted somewhere insecure (chat, a ticket, a public gist), rotate it
immediately in the GCP Console (IAM & Admin -> Service Accounts -> Keys).

## Usage

```
python refresh_synchpay_tracker.py --dry-run     # compute everything, write nothing
python refresh_synchpay_tracker.py --diff-only   # print status changes, write nothing
python refresh_synchpay_tracker.py               # apply the refresh for real
```

**Always run `--dry-run` first** and read the console output before running for real —
see the large module docstring at the top of `refresh_synchpay_tracker.py` for the full
list of caveats (status thresholds, protected pricing entries, what's still unverified).

## What it does

1. Reads the target spreadsheet's `Deals` tab (column layout documented in the script).
2. Pulls every deal, note, and meeting from Attio once (not per-deal), and — if
   `ANTHROPIC_API_KEY` is set — sends each deal's notes/meetings to Claude to produce a
   plain-language, high-school-reading-level summary.
3. Recomputes Days Quiet / Last Touch / Status per deal and writes back only the
   refreshable columns — Deal name, Stage, Company, Lead Contact, Contact Email,
   PMS/Software, Deal Type, Phone/Cell, and Lead Temp are never touched.
4. Protects specific "verified" Pricing Details entries from being overwritten (see
   `PROTECTED_PRICING` in the script), and preserves the Westchester Oral Surgery
   needs-audit flag unless new statement data justifies changing it.
5. Recolors only the cells whose value actually changed (Status, Pricing Details),
   leaving all other formatting untouched.

## Known limitations (read before trusting a real run)

- Status day-thresholds (`STATUS_THRESHOLDS`) are best-effort defaults, not a documented
  business rule — tune them to match the team's real cadence.
- The Emails data source requires an Attio key with the Emails read scope; without it,
  the script prints a warning and skips email-derived touches.
- The Claude-synthesized summary path has been tested with mocked responses (see the
  module docstring, note 2a) but not yet against a wide sample of real output — spot
  check a handful of high-note-count deals after your first real `--dry-run` with a real
  `ANTHROPIC_API_KEY`.
