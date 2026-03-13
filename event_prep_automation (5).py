"""
Event Prep Tracker — Asana Automation Script
=============================================
Asana Rules handle ALL section moves. This script:
1. Creates subtasks cumulatively for events that have moved sections
2. Generates an events.ics file synced to Google Calendar via GitHub Pages

Runs daily via GitHub Actions.

Setup:    pip install requests
Usage:    ASANA_TOKEN=your_token python event_prep_automation.py
Backfill: ASANA_TOKEN=your_token python event_prep_automation.py --backfill
"""

import os
import sys
import uuid
import requests
from datetime import date, timedelta, datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────────────────────────

ASANA_TOKEN  = os.environ.get("ASANA_TOKEN", "YOUR_ASANA_TOKEN_HERE")
PROJECT_GID  = "1213618936694523"
ASSIGNEE_GID = "1208237467326302"  # Tyler McCrobie

SECTIONS = {
    "backlog":  "1213618936694542",  # Backlog (120+ Days Out)
    "120days":  "1213618977001098",  # 120 Days Out
    "60days":   "1213618936710403",  # 60 Days Out
    "30days":   "1213619113937079",  # 30 Days Out
    "7days":    "1213618976986169",  # 7 Days Out
    "during":   "1213618973032767",  # During Event
    "post":     "1213619113878344",  # Post-Event
    "archived": None,                # Archived Events (GID added below)
}

SECTION_GID_TO_KEY = {v: k for k, v in SECTIONS.items() if v}

EVENT_END_DATE_FIELD_GID = "1213619113970229"
ARCHIVED_SECTION_NAME    = "Archived Events"

# ─── SUBTASK DEFINITIONS ──────────────────────────────────────────────────────

# Assignee types:
#   "csm"  = assigned to whoever owns the parent event task
#   "tsm"  = always assigned to Tyler McCrobie (ASSIGNEE_GID)

PHASE_SUBTASKS = {
    "120days": {
        "days_out": 120,
        "tasks": [
            ("Confirm event details (teams, date, time, location)",                       0, "csm"),
            ("Refresh Content — Add/Refresh DAAL records, confirm URL domain resources",  2, "tsm"),
            ("Schedule & Conduct 90-Day Check-In Meeting",                                7, "csm"),
        ],
    },
    "60days": {
        "days_out": 60,
        "tasks": [
            ("Confirm Feature Usage (Ticketing Integration, Live Escalation, Integrations)", 0, "csm"),
            ("Review Promotion & Placement",                                               3, "csm"),
            ("Schedule & Conduct 60-Day Check-In Meeting",                                 7, "csm"),
        ],
    },
    "30days": {
        "days_out": 30,
        "tasks": [
            ("Confirm Security Alert Point of Contacts",                                   0, "csm"),
            ("Refresh Content — Ensure DAAL content scraped recently, add new URLs",       2, "tsm"),
            ("Push for Final Promotion & Placement",                                       3, "csm"),
            ("QA Common FAQs",                                                             5, "csm"),
            ("Schedule & Conduct 30-Day Check-In Meeting",                                 7, "csm"),
        ],
    },
    "7days": {
        "days_out": 7,
        "tasks": [
            ("Check in with Client POC",                                                   0, "csm"),
            ("Flag Support (Weekends/Holidays)",                                           1, "tsm"),
        ],
    },
    "during": {
        "days_out": 0,
        "tasks": [
            ("Review Traffic/Content",                                                     0, "tsm"),
            ("Ensure Off-Hours Support is Aware",                                          0, "tsm"),
        ],
    },
    "post": {
        "days_out": 0,
        "tasks": [
            ("Post-Event Deck",                                                            3, "tsm"),
            ("Post-Event Content Updates",                                                 7, "tsm"),
        ],
    },
}

PHASE_ORDER = ["120days", "60days", "30days", "7days", "during", "post"]


def get_all_subtasks_for_phase(phase_key: str, event_date: date) -> list:
    """Returns list of (name, due_date, assignee_type) for phase and all prior phases."""
    today     = date.today()
    all_tasks = []
    phase_idx = PHASE_ORDER.index(phase_key)
    for p in PHASE_ORDER[:phase_idx + 1]:
        phase_def   = PHASE_SUBTASKS[p]
        days_out    = phase_def["days_out"]
        phase_start = event_date - timedelta(days=days_out)
        for name, offset, assignee_type in phase_def["tasks"]:
            due = phase_start + timedelta(days=offset)
            if due < today:
                due = today
            all_tasks.append((name, due, assignee_type))
    return all_tasks


# ─── ASANA API ────────────────────────────────────────────────────────────────

BASE    = "https://app.asana.com/api/1.0"
HEADERS = {
    "Authorization": f"Bearer {ASANA_TOKEN}",
    "Content-Type":  "application/json",
}


def get(endpoint, params=None):
    r = requests.get(f"{BASE}{endpoint}", headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def post_api(endpoint, data):
    r = requests.post(f"{BASE}{endpoint}", headers=HEADERS, json={"data": data})
    r.raise_for_status()
    return r.json()


def put_api(endpoint, data):
    r = requests.put(f"{BASE}{endpoint}", headers=HEADERS, json={"data": data})
    r.raise_for_status()
    return r.json()


def get_all_tasks():
    params = {
        "project":    PROJECT_GID,
        "opt_fields": "name,gid,due_on,completed,assignee,memberships.section.gid,memberships.section.name,custom_fields",
        "limit":      100,
    }
    tasks, offset = [], None
    while True:
        if offset:
            params["offset"] = offset
        resp = get("/tasks", params=params)
        tasks.extend(resp.get("data", []))
        nxt = resp.get("next_page")
        if nxt and nxt.get("offset"):
            offset = nxt["offset"]
        else:
            break
    return tasks


def get_existing_subtask_names(task_gid) -> set:
    try:
        resp = get(f"/tasks/{task_gid}/subtasks", params={"opt_fields": "name"})
        return {s["name"] for s in resp.get("data", [])}
    except Exception:
        return set()


def get_task_assignee(task) -> str:
    """Returns the assignee GID of a task, falling back to Tyler if unassigned."""
    try:
        assignee = task.get("assignee")
        if assignee and assignee.get("gid"):
            return assignee["gid"]
    except Exception:
        pass
    return ASSIGNEE_GID  # fallback to Tyler


def create_subtask(parent_gid, name, due_on, assignee_gid):
    return post_api(f"/tasks/{parent_gid}/subtasks", {
        "name":     name,
        "assignee": assignee_gid,
        "due_on":   str(due_on),
    })


def rename_task(task_gid, new_name):
    put_api(f"/tasks/{task_gid}", {"name": new_name})


def get_current_section(task):
    try:
        s = task["memberships"][0]["section"]
        return s.get("name", ""), SECTION_GID_TO_KEY.get(s.get("gid", ""), "unknown")
    except (KeyError, IndexError):
        return "", "unknown"


def get_end_date(task):
    for cf in task.get("custom_fields", []):
        if cf.get("gid") == EVENT_END_DATE_FIELD_GID:
            dv = cf.get("date_value")
            if dv and dv.get("date"):
                return dv["date"]
    return None


# ─── ICS GENERATION ───────────────────────────────────────────────────────────

def format_ics_date(d: str) -> str:
    """Format date string YYYY-MM-DD to YYYYMMDD for ICS."""
    return d.replace("-", "")


def generate_ics(events: list) -> str:
    """
    Generate ICS calendar content from a list of event dicts:
    [{"name": ..., "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "uid": ...}]
    """
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Satisfi Labs//Event Prep Tracker//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Client Event Calendar",
        "X-WR-TIMEZONE:America/New_York",
    ]
    for evt in events:
        # For all-day events, end date in ICS must be day AFTER last day
        end_dt = date.fromisoformat(evt["end"]) + timedelta(days=1)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{evt['uid']}@satisfilabs.eventtracker",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{format_ics_date(evt['start'])}",
            f"DTEND;VALUE=DATE:{end_dt.strftime('%Y%m%d')}",
            f"SUMMARY:{evt['name']}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run(backfill=False):
    today = date.today()
    mode  = "BACKFILL" if backfill else "DAILY RUN"
    print(f"\n{'='*60}")
    print(f"  Event Prep Automation — {today} [{mode}]")
    print(f"{'='*60}\n")

    tasks            = get_all_tasks()
    subtasks_created = 0
    skipped          = 0
    renamed          = 0
    deleted          = 0
    calendar_events  = []

    # ── Clean up rogue top-level tasks created by Asana Rules ──
    # Asana Rules can't create true subtasks — they land as top-level project tasks.
    # Delete any top-level task whose name matches a known subtask name.
    known_subtask_names = {st for phase in PHASE_SUBTASKS.values() for st, _, _a in phase["tasks"]}
    for task in tasks:
        tname = task.get("name", "").strip()
        tgid  = task.get("gid")
        if tname in known_subtask_names:
            try:
                requests.delete(f"{BASE}/tasks/{tgid}", headers=HEADERS)
                print(f"  Deleted rogue task: '{tname}'")
                deleted += 1
            except Exception as e:
                print(f"  ERROR deleting '{tname}': {e}")
    # Re-fetch tasks after cleanup
    tasks = get_all_tasks()

    for task in tasks:
        name      = task.get("name", "").strip()
        gid       = task.get("gid")
        due_on    = task.get("due_on")
        completed = task.get("completed", False)

        # Skip placeholder tasks, subtasks (they have no section membership), and known subtask names
        known_subtask_names = {st for phase in PHASE_SUBTASKS.values() for st, _, _a in phase["tasks"]}
        if name in ("[ Add event here ]", "") or not due_on or name in known_subtask_names:
            skipped += 1
            continue

        # Skip if task has no section (it's a subtask floating at top level)
        if not task.get("memberships"):
            skipped += 1
            continue

        section_name, section_key = get_current_section(task)
        end_date = get_end_date(task) or due_on  # fall back to start if no end date

        # ── Calendar sync (all non-archived, non-completed events) ──
        if not completed and section_name != ARCHIVED_SECTION_NAME:
            calendar_events.append({
                "name":  name,
                "start": due_on,
                "end":   end_date,
                "uid":   gid,
            })

        # ── Archived rename ──
        if section_name == ARCHIVED_SECTION_NAME:
            year = due_on[:4]
            suffix = f" — {year} (Archived)"
            if suffix not in name:
                new_name = name + suffix
                try:
                    rename_task(gid, new_name)
                    print(f"  Renamed: '{name}' → '{new_name}'")
                    renamed += 1
                except Exception as e:
                    print(f"  ERROR renaming '{name}': {e}")
            skipped += 1
            continue

        if completed:
            skipped += 1
            continue

        # ── Subtask creation ──
        if section_key not in PHASE_ORDER:
            skipped += 1
            continue

        event_date    = date.fromisoformat(due_on)
        days_until    = (event_date - today).days
        print(f"  '{name}' — {days_until} days out [{section_key}]")

        all_subtasks     = get_all_subtasks_for_phase(section_key, event_date)
        existing_names   = get_existing_subtask_names(gid)
        missing_subtasks = [(n, d, a) for n, d, a in all_subtasks if n not in existing_names]
        csm_gid          = get_task_assignee(task)

        if not missing_subtasks:
            print(f"     All subtasks exist — skipping\n")
            skipped += 1
        else:
            print(f"     Creating {len(missing_subtasks)} subtasks...")
            for st_name, due, assignee_type in missing_subtasks:
                assignee_gid = csm_gid if assignee_type == "csm" else ASSIGNEE_GID
                try:
                    create_subtask(gid, st_name, due, assignee_gid)
                    label = "CSM" if assignee_type == "csm" else "TSM"
                    print(f"      + '{st_name}' (due {due}) [{label}]")
                    subtasks_created += 1
                except Exception as e:
                    print(f"      ERROR '{st_name}': {e}")
            print()

    # ── Write ICS file ──
    ics_content = generate_ics(calendar_events)
    ics_path    = "events.ics"
    with open(ics_path, "w", encoding="utf-8") as f:
        f.write(ics_content)
    print(f"\n  Calendar: wrote {len(calendar_events)} events to {ics_path}")

    print(f"\n{'='*60}")
    print(f"  Done! Subtasks: {subtasks_created} | Renamed: {renamed} | Deleted rogue: {deleted} | Skipped: {skipped}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    backfill = "--backfill" in sys.argv
    run(backfill=backfill)
