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
  ASANA_TOKEN=your_token_here python event_prep_automation.py

GitHub Actions: see .github/workflows/event_prep.yml
"""

import os
import requests
from datetime import date, timedelta

# ─── CONFIG ───────────────────────────────────────────────────────────────────

ASANA_TOKEN  = os.environ.get("ASANA_TOKEN", "YOUR_ASANA_TOKEN_HERE")
PROJECT_GID  = "1213618936694523"
ASSIGNEE_GID = "1208237467326302"  # Tyler McCrobie

# Section GIDs (from your project)
SECTIONS = {
    "backlog":  "1213618936694542",  # 🗂️ Backlog (120+ Days Out)
    "120days":  "1213618977001098",  # 📅 120 Days Out
    "60days":   "1213618936710403",  # 📅 60 Days Out
    "30days":   "1213619113937079",  # 📅 30 Days Out
    "7days":    "1213618976986169",  # ⚠️ 7 Days Out
    "during":   "1213618973032767",  # 🟢 During Event
    "post":     "1213619113878344",  # ✅ Post-Event
}

# Section GID → key (reverse lookup)
SECTION_GID_TO_KEY = {v: k for k, v in SECTIONS.items()}

# ─── SUBTASK DEFINITIONS ──────────────────────────────────────────────────────
# Each entry: (task name, days offset from phase start date)
# Phase start date = event_date minus days_out

SUBTASKS = {
    "120days": [
        ("Confirm event details (teams, date, time, location)",              0),
        ("Refresh Content — Add/Refresh DAAL records, confirm URL domain resources", 2),
        ("Schedule & Conduct 90-Day Check-In Meeting",                       7),
    ],
    "60days": [
        ("Confirm Feature Usage (Ticketing Integration, Live Escalation, Integrations)", 0),
        ("Review Promotion & Placement",                                      3),
        ("Schedule & Conduct 60-Day Check-In Meeting",                        7),
    ],
    "30days": [
        ("Confirm Security Alert Point of Contacts",                          0),
        ("Refresh Content — Ensure DAAL content scraped recently, add new URLs", 2),
        ("Push for Final Promotion & Placement",                              3),
        ("QA Common FAQs",                                                    5),
        ("Schedule & Conduct 30-Day Check-In Meeting",                        7),
    ],
    "7days": [
        ("Check in with Client POC via Email",                                0),
        ("Flag Support for Weekend/Holiday Coverage",                         1),
        ("Review Traffic & Content",                                          0),
        ("Monitor Support — Flag Weekend/Holiday Tickets",                    1),
    ],
    "post": [
        ("Create Post-Event Deck",                                            3),
        ("Post-Event Content Updates",                                        7),
    ],
}

# Days out used to calculate subtask due dates
PHASE_DAYS_OUT = {
    "120days": 120,
    "60days":  60,
    "30days":  30,
    "7days":   7,
    "post":    0,
}

# ─── ASANA API HELPERS ────────────────────────────────────────────────────────

HEADERS = {
    "Authorization": f"Bearer {ASANA_TOKEN}",
    "Content-Type": "application/json",
}
BASE = "https://app.asana.com/api/1.0"


def get(endpoint, params=None):
    r = requests.get(f"{BASE}{endpoint}", headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def post(endpoint, data):
    r = requests.post(f"{BASE}{endpoint}", headers=HEADERS, json={"data": data})
    r.raise_for_status()
    return r.json()


def add_task_to_section(task_gid, section_gid):
    r = requests.post(
        f"{BASE}/sections/{section_gid}/addTask",
        headers=HEADERS,
        json={"data": {"task": task_gid}},
    )
    r.raise_for_status()
    return r.json()


def get_subtasks(task_gid):
    r = get(f"/tasks/{task_gid}/subtasks", params={"opt_fields": "name,completed"})
    return r.get("data", [])


def create_subtask(parent_gid, name, due_on):
    return post(f"/tasks/{parent_gid}/subtasks", {
        "name":     name,
        "assignee": ASSIGNEE_GID,
        "due_on":   str(due_on),
    })


def get_all_tasks():
    params = {
        "project":    PROJECT_GID,
        "opt_fields": "name,gid,due_on,memberships.section.gid,memberships.section.name,completed",
        "limit":      100,
    }
    tasks = []
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = get("/tasks", params=params)
        tasks.extend(resp.get("data", []))
        next_page = resp.get("next_page")
        if next_page and next_page.get("offset"):
            offset = next_page["offset"]
        else:
            break
    return tasks

# ─── PHASE LOGIC ─────────────────────────────────────────────────────────────

def get_target_section(event_date: date, today: date) -> str:
    """Return the section key an event should be in based on days until event."""
    days = (event_date - today).days
    if days > 120:    return "backlog"
    elif days > 60:   return "120days"
    elif days > 30:   return "60days"
    elif days > 21:   return "30days"
    elif days > 0:    return "7days"
    elif days >= -14: return "during"
    else:             return "post"


def get_current_section(task) -> str:
    """Return the section key the task is currently in."""
    try:
        section_gid = task["memberships"][0]["section"]["gid"]
        return SECTION_GID_TO_KEY.get(section_gid, "unknown")
    except (KeyError, IndexError):
        return "unknown"


def subtasks_already_exist(task_gid, phase_key) -> bool:
    """Check if subtasks for this phase have already been created."""
    existing = get_subtasks(task_gid)
    if not existing:
        return False
    expected_names = [name for name, _ in SUBTASKS.get(phase_key, [])]
    existing_names = [s["name"] for s in existing]
    # If any expected subtask already exists, assume phase was already processed
    return any(name in existing_names for name in expected_names)


def create_phase_subtasks(task_gid, phase_key, event_date: date):
    """Create all subtasks for the given phase."""
    subtask_defs = SUBTASKS.get(phase_key, [])
    days_out = PHASE_DAYS_OUT.get(phase_key, 0)
    phase_start = event_date - timedelta(days=days_out)

    for name, offset in subtask_defs:
        due = phase_start + timedelta(days=offset)
        # Don't set a due date in the past
        today = date.today()
        if due < today:
            due = today
        create_subtask(task_gid, name, due)
        print(f"    ✅ Created subtask: '{name}' (due {due})")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run():
    today = date.today()
    print(f"\n{'='*60}")
    print(f"  Event Prep Automation — {today}")
    print(f"{'='*60}\n")

    tasks = get_all_tasks()
    print(f"Found {len(tasks)} tasks in project\n")

    moved    = 0
    skipped  = 0
    no_date  = 0

    for task in tasks:
        name     = task.get("name", "")
        gid      = task.get("gid")
        due_on   = task.get("due_on")
        completed = task.get("completed", False)

        # Skip placeholder tasks and completed tasks
        if name.strip() in ("[ Add event here ]", "") or completed:
            skipped += 1
            continue

        if not due_on:
            print(f"  ⚠️  No due date: '{name}' — skipping")
            no_date += 1
            continue

        event_date     = date.fromisoformat(due_on)
        current_section = get_current_section(task)
        target_section  = get_target_section(event_date, today)

        days_until = (event_date - today).days
        print(f"  📅 '{name}' — {days_until} days out")
        print(f"     Current: {current_section} → Target: {target_section}")

        if current_section == target_section:
            print(f"     ✓ Already in correct section\n")
            skipped += 1
            continue

        # Move to correct section
        add_task_to_section(gid, SECTIONS[target_section])
        print(f"     🚀 Moved to {target_section}")
        moved += 1

        # Create subtasks if this phase has them and they don't exist yet
        if target_section in SUBTASKS:
            if subtasks_already_exist(gid, target_section):
                print(f"     ℹ️  Subtasks already exist for this phase — skipping")
            else:
                print(f"     📝 Creating subtasks...")
                create_phase_subtasks(gid, target_section, event_date)

        print()

    print(f"\n{'='*60}")
    print(f"  Done! Moved: {moved} | Skipped: {skipped} | No date: {no_date}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run()
