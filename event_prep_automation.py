"""
Event Prep Tracker — Asana Automation Script
=============================================
Asana Rules handle ALL section moves. This script ONLY creates subtasks.

Each phase includes ALL subtasks from previous phases for full accountability
and visibility — completed or not.

Runs daily via GitHub Actions.

Setup:    pip install requests
Usage:    ASANA_TOKEN=your_token python event_prep_automation.py
Backfill: ASANA_TOKEN=your_token python event_prep_automation.py --backfill
"""

import os
import sys
import requests
from datetime import date, timedelta

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
}

SECTION_GID_TO_KEY = {v: k for k, v in SECTIONS.items()}

# ─── SUBTASK DEFINITIONS ──────────────────────────────────────────────────────
# Each phase defines ONLY its own new subtasks.
# The script will cumulatively stack all prior phases when creating subtasks.
# (task name, days offset from THAT phase's start date)

PHASE_SUBTASKS = {
    "120days": {
        "days_out": 120,
        "tasks": [
            ("Confirm event details (teams, date, time, location)",                       0),
            ("Refresh Content — Add/Refresh DAAL records, confirm URL domain resources",  2),
            ("Schedule & Conduct 90-Day Check-In Meeting",                                7),
        ],
    },
    "60days": {
        "days_out": 60,
        "tasks": [
            ("Confirm Feature Usage (Ticketing Integration, Live Escalation, Integrations)", 0),
            ("Review Promotion & Placement",                                               3),
            ("Schedule & Conduct 60-Day Check-In Meeting",                                 7),
        ],
    },
    "30days": {
        "days_out": 30,
        "tasks": [
            ("Confirm Security Alert Point of Contacts",                                   0),
            ("Refresh Content — Ensure DAAL content scraped recently, add new URLs",       2),
            ("Push for Final Promotion & Placement",                                       3),
            ("QA Common FAQs",                                                             5),
            ("Schedule & Conduct 30-Day Check-In Meeting",                                 7),
        ],
    },
    "7days": {
        "days_out": 7,
        "tasks": [
            ("Check in with Client POC",                                                   0),
            ("Flag Support (Weekends/Holidays)",                                           1),
        ],
    },
    "during": {
        "days_out": 0,
        "tasks": [
            ("Review Traffic/Content",                                                     0),
            ("Ensure Off-Hours Support is Aware",                                          0),
        ],
    },
    "post": {
        "days_out": 0,
        "tasks": [
            ("Post-Event Deck",                                                            3),
            ("Post-Event Content Updates",                                                 7),
        ],
    },
}

# Phase order — used to determine which prior phases to include cumulatively
PHASE_ORDER = ["120days", "60days", "30days", "7days", "during", "post"]


def get_all_subtasks_for_phase(phase_key: str, event_date: date) -> list:
    """
    Returns a cumulative list of (task_name, due_date) for the given phase
    and all phases that came before it.
    """
    today      = date.today()
    all_tasks  = []
    phase_idx  = PHASE_ORDER.index(phase_key)

    for p in PHASE_ORDER[:phase_idx + 1]:
        phase_def   = PHASE_SUBTASKS[p]
        days_out    = phase_def["days_out"]
        phase_start = event_date - timedelta(days=days_out)

        for name, offset in phase_def["tasks"]:
            due = phase_start + timedelta(days=offset)
            # Don't set due dates in the past
            if due < today:
                due = today
            all_tasks.append((name, due))

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


def get_all_tasks():
    params = {
        "project":    PROJECT_GID,
        "opt_fields": "name,gid,due_on,memberships.section.gid,memberships.section.name,completed",
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


def create_subtask(parent_gid, name, due_on):
    return post_api(f"/tasks/{parent_gid}/subtasks", {
        "name":     name,
        "assignee": ASSIGNEE_GID,
        "due_on":   str(due_on),
    })


def get_current_section(task) -> str:
    try:
        return SECTION_GID_TO_KEY.get(task["memberships"][0]["section"]["gid"], "unknown")
    except (KeyError, IndexError):
        return "unknown"


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

    for task in tasks:
        name      = task.get("name", "").strip()
        gid       = task.get("gid")
        due_on    = task.get("due_on")
        completed = task.get("completed", False)

        if name in ("[ Add event here ]", "") or completed or not due_on:
            skipped += 1
            continue

        event_date      = date.fromisoformat(due_on)
        current_section = get_current_section(task)
        days_until      = (event_date - today).days

        # Only act on sections that have subtask definitions
        if current_section not in PHASE_ORDER:
            skipped += 1
            continue

        print(f"  '{name}' — {days_until} days out [{current_section}]")

        # Get full cumulative subtask list for this phase
        all_subtasks      = get_all_subtasks_for_phase(current_section, event_date)
        existing_names    = get_existing_subtask_names(gid)
        missing_subtasks  = [(n, d) for n, d in all_subtasks if n not in existing_names]

        if not missing_subtasks:
            print(f"     All subtasks already exist — skipping\n")
            skipped += 1
            continue

        print(f"     Creating {len(missing_subtasks)} missing subtasks (of {len(all_subtasks)} total)...")
        for name_st, due in missing_subtasks:
            try:
                create_subtask(gid, name_st, due)
                print(f"      + '{name_st}' (due {due})")
                subtasks_created += 1
            except Exception as e:
                print(f"      ERROR '{name_st}': {e}")
        print()

    print(f"\n{'='*60}")
    print(f"  Done! Subtasks created: {subtasks_created} | Skipped: {skipped}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    backfill = "--backfill" in sys.argv
    run(backfill=backfill)
