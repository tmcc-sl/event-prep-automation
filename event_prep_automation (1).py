"""
Event Prep Tracker — Asana Automation Script
=============================================
Runs daily (via GitHub Actions or cron) to:
1. Check every event in the project against today's date
2. Move it to the correct section based on days until event
3. Create the appropriate prep subtasks when an event moves sections

Setup:
  pip install requests

Usage:
  # Normal daily run:
  ASANA_TOKEN=your_token python event_prep_automation.py

  # One-time backfill (creates subtasks for events already in correct sections):
  ASANA_TOKEN=your_token python event_prep_automation.py --backfill

GitHub Actions: see .github/workflows/event_prep.yml
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

SUBTASKS = {
    "120days": [
        ("Confirm event details (teams, date, time, location)",                       0),
        ("Refresh Content — Add/Refresh DAAL records, confirm URL domain resources",  2),
        ("Schedule & Conduct 90-Day Check-In Meeting",                                7),
    ],
    "60days": [
        ("Confirm Feature Usage (Ticketing Integration, Live Escalation, Integrations)", 0),
        ("Review Promotion & Placement",                                               3),
        ("Schedule & Conduct 60-Day Check-In Meeting",                                 7),
    ],
    "30days": [
        ("Confirm Security Alert Point of Contacts",                                   0),
        ("Refresh Content — Ensure DAAL content scraped recently, add new URLs",       2),
        ("Push for Final Promotion & Placement",                                       3),
        ("QA Common FAQs",                                                             5),
        ("Schedule & Conduct 30-Day Check-In Meeting",                                 7),
    ],
    "7days": [
        ("Check in with Client POC via Email",                                         0),
        ("Flag Support for Weekend/Holiday Coverage",                                  1),
        ("Review Traffic & Content",                                                   0),
        ("Monitor Support — Flag Weekend/Holiday Tickets",                             1),
    ],
    "post": [
        ("Create Post-Event Deck",                                                     3),
        ("Post-Event Content Updates",                                                 7),
    ],
}

PHASE_DAYS_OUT = {
    "120days": 120,
    "60days":  60,
    "30days":  30,
    "7days":   7,
    "post":    0,
}

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


def get_subtasks(task_gid):
    try:
        resp = get(f"/tasks/{task_gid}/subtasks", params={"opt_fields": "name,completed"})
        return resp.get("data", [])
    except Exception:
        return []


def create_subtask(parent_gid, name, due_on):
    today = date.today()
    effective_due = due_on if due_on >= today else today
    return post_api(f"/tasks/{parent_gid}/subtasks", {
        "name":     name,
        "assignee": ASSIGNEE_GID,
        "due_on":   str(effective_due),
    })


def move_to_section(task_gid, section_gid):
    return post_api(f"/sections/{section_gid}/addTask", {"task": task_gid})


# ─── LOGIC ────────────────────────────────────────────────────────────────────

def get_target_section(event_date: date, today: date) -> str:
    days = (event_date - today).days
    if days > 120:    return "backlog"
    elif days > 60:   return "120days"
    elif days > 30:   return "60days"
    elif days > 21:   return "30days"
    elif days > 0:    return "7days"
    elif days >= -14: return "during"
    else:             return "post"


def get_current_section(task) -> str:
    try:
        return SECTION_GID_TO_KEY.get(task["memberships"][0]["section"]["gid"], "unknown")
    except (KeyError, IndexError):
        return "unknown"


def subtasks_already_exist(task_gid, phase_key) -> bool:
    existing       = get_subtasks(task_gid)
    expected       = {name for name, _ in SUBTASKS.get(phase_key, [])}
    existing_names = {s["name"] for s in existing}
    return bool(expected & existing_names)


def create_phase_subtasks(task_gid, phase_key, event_date: date):
    days_out    = PHASE_DAYS_OUT.get(phase_key, 0)
    phase_start = event_date - timedelta(days=days_out)
    created     = 0
    for name, offset in SUBTASKS.get(phase_key, []):
        due = phase_start + timedelta(days=offset)
        try:
            create_subtask(task_gid, name, due)
            print(f"      + '{name}' (due {due})")
            created += 1
        except Exception as e:
            print(f"      ERROR creating '{name}': {e}")
    return created


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run(backfill=False):
    today = date.today()
    mode  = "BACKFILL" if backfill else "DAILY RUN"
    print(f"\n{'='*60}")
    print(f"  Event Prep Automation — {today} [{mode}]")
    print(f"{'='*60}\n")

    tasks            = get_all_tasks()
    moved            = 0
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
        target_section  = get_target_section(event_date, today)
        days_until      = (event_date - today).days

        print(f"  '{name}' — {days_until} days out")

        if backfill:
            # Backfill mode: don't move, just create missing subtasks in current section
            if current_section in SUBTASKS:
                if subtasks_already_exist(gid, current_section):
                    print(f"     Subtasks already exist — skipping\n")
                    skipped += 1
                else:
                    print(f"     Creating subtasks for {current_section}...")
                    n = create_phase_subtasks(gid, current_section, event_date)
                    subtasks_created += n
                    print()
            else:
                print(f"     No subtasks defined for '{current_section}' — skipping\n")
                skipped += 1
        else:
            # Normal mode: move if needed, then create subtasks
            if current_section != target_section:
                try:
                    move_to_section(gid, SECTIONS[target_section])
                    print(f"     Moved: {current_section} -> {target_section}")
                    moved += 1
                except Exception as e:
                    print(f"     ERROR moving task: {e}\n")
                    continue

                if target_section in SUBTASKS:
                    if subtasks_already_exist(gid, target_section):
                        print(f"     Subtasks already exist — skipping")
                    else:
                        print(f"     Creating subtasks...")
                        n = create_phase_subtasks(gid, target_section, event_date)
                        subtasks_created += n
            else:
                print(f"     Already in correct section ({current_section})")
                skipped += 1

            print()

    print(f"\n{'='*60}")
    print(f"  Done! Moved: {moved} | Subtasks created: {subtasks_created} | Skipped: {skipped}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    backfill = "--backfill" in sys.argv
    run(backfill=backfill)
