"""Checks data/meetings.json and prompts.json against the agreed rules.

Run from the project folder:   python tools/check_data.py
Exit code 0 means every labelled record passes. Records still marked "todo" are listed, not failed.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATS = {"clear_task", "several_tasks", "missing_owner", "unclear_date",
        "non_action", "correction", "speaker_ambiguity", "no_action"}
WARNINGS = {"missing_owner", "missing_deadline", "unresolved_deadline"}
REL = re.compile(r"^in (\d{1,2}) days$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

errors = []


def err(rid, msg):
    errors.append(f"{rid}: {msg}")


def expected_due(text, meeting_date):
    if text is None:
        return None
    t = text.strip().lower()
    if ISO.match(t):
        return t
    if meeting_date is None:
        return None
    md = date.fromisoformat(meeting_date)
    if t == "today":
        return md.isoformat()
    if t == "tomorrow":
        return (md + timedelta(days=1)).isoformat()
    m = REL.match(t)
    if m and 1 <= int(m.group(1)) <= 30:
        return (md + timedelta(days=int(m.group(1)))).isoformat()
    return None


def expected_warnings(owner, text, due):
    w = []
    if owner is None:
        w.append("missing_owner")
    if text is None:
        w.append("missing_deadline")
    elif due is None:
        w.append("unresolved_deadline")
    return w


def check_action(rid, a, lines, meeting_date, final=True):
    keys = ({"task", "owner", "deadline_text", "due_date", "evidence", "warnings"} if final
            else {"task", "owner", "deadline_text", "ambiguities", "evidence"})
    if set(a) != keys:
        err(rid, f"action keys {sorted(a)} should be {sorted(keys)}")
        return
    if not a["task"] or not isinstance(a["task"], str):
        err(rid, "empty task")
    if not a["evidence"]:
        err(rid, f"'{a['task']}' has no evidence")
    joined = ""
    for e in a["evidence"]:
        m = re.fullmatch(r"L(\d{2})", e.get("line_id", ""))
        if not m or not 1 <= int(m.group(1)) <= len(lines):
            err(rid, f"bad line_id {e.get('line_id')}")
            continue
        line = lines[int(m.group(1)) - 1]
        if e.get("quote", "") not in line or not e.get("quote"):
            err(rid, f"quote not found exactly on {e['line_id']}: {e.get('quote')!r}")
        joined += " " + e.get("quote", "")
    if a["owner"] is not None and a["owner"] not in joined:
        err(rid, f"owner {a['owner']!r} is not inside the evidence quotes")
    if a["deadline_text"] is not None and a["deadline_text"].lower() not in joined.lower():
        err(rid, f"deadline {a['deadline_text']!r} is not inside the evidence quotes")
    if final:
        d = expected_due(a["deadline_text"], meeting_date)
        if a["due_date"] != d:
            err(rid, f"'{a['task']}' due_date {a['due_date']} should be {d}")
        w = expected_warnings(a["owner"], a["deadline_text"], d)
        if a["warnings"] != w:
            err(rid, f"'{a['task']}' warnings {a['warnings']} should be {w}")


def main():
    data = json.loads((ROOT / "data" / "meetings.json").read_text(encoding="utf-8"))
    recs = data["records"]
    ids = [r["id"] for r in recs]

    if len(recs) != 60:
        err("dataset", f"{len(recs)} records, need exactly 60")
    for rid, n in Counter(ids).items():
        if n > 1:
            err(rid, "duplicate id")

    splits = Counter(r["split"] for r in recs)
    for sp, want in (("example", 10), ("development", 10), ("test", 40)):
        if splits[sp] != want:
            err("dataset", f"{sp} split has {splits[sp]}, need {want}")

    test_cats = Counter(r["category"] for r in recs if r["split"] == "test")
    for c in CATS:
        if test_cats[c] != 5:
            err("dataset", f"test category {c} has {test_cats[c]}, need 5")
    for r in recs:
        if r["category"] not in CATS:
            err(r["id"], f"unknown category {r['category']}")

    fam = defaultdict(set)
    for r in recs:
        fam[r["family_id"]].add(r["split"])
    for f, sps in fam.items():
        if len(sps) > 1:
            err(f, f"family spans several splits {sorted(sps)}")

    todo = []
    for r in recs:
        if r.get("status") != "labelled":
            todo.append(r["id"])
            continue
        rid = r["id"]
        lines = [l for l in r["notes"].split("\n") if l.strip()]
        if not 3 <= len(lines) <= 25:
            err(rid, f"{len(lines)} non-empty lines, need 3 to 25")
        if len(r["notes"]) > 6000:
            err(rid, "notes longer than 6,000 characters")
        if len(r["gold_actions"]) > 8:
            err(rid, "more than 8 actions")
        if r["meeting_date"] is not None and not ISO.match(r["meeting_date"]):
            err(rid, f"meeting_date {r['meeting_date']!r} is not ISO")
        if r["category"] == "no_action" and r["gold_actions"]:
            err(rid, "no_action record has gold actions")
        for a in r["gold_actions"]:
            check_action(rid, a, lines, r["meeting_date"], final=True)

    genuine_none = sum(1 for r in recs if r["split"] == "test"
                       and r.get("status") == "labelled" and not r["gold_actions"])

    # prompts.json
    p = json.loads((ROOT / "prompts.json").read_text(encoding="utf-8"))
    for k in ("shared_rules", "few_shot_examples", "repair_rules"):
        if not p.get(k):
            err("prompts.json", f"missing {k}")
    by_id = {r["id"]: r for r in recs}
    for ex in p.get("few_shot_examples", []):
        src = by_id.get(ex["id"])
        if not src or src["split"] != "example":
            err("prompts.json", f"few-shot {ex['id']} is not from the example split")
            continue
        if ex["notes"] != src["notes"]:
            err("prompts.json", f"few-shot {ex['id']} notes differ from the dataset")
        lines = [l for l in ex["notes"].split("\n") if l.strip()]
        for a in ex["output"]["actions"]:
            check_action(ex["id"], a, lines, ex["meeting_date"], final=False)

    print(f"records {len(recs)} | labelled {len(recs) - len(todo)} | todo {len(todo)}")
    print("splits", dict(splits), "| test categories", dict(sorted(test_cats.items())))
    print(f"labelled test meetings with no actions: {genuine_none} (need at least 5 once all are labelled)")
    if todo:
        by_author = defaultdict(list)
        for rid in todo:
            by_author[by_id[rid]["provenance"]["author"]].append(rid)
        for who, rids in by_author.items():
            print(f"  still todo - {who}: {rids[0]}..{rids[-1]} ({len(rids)})")
    if errors:
        print(f"\n{len(errors)} problem(s):")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("\nAll labelled records and prompts pass.")


if __name__ == "__main__":
    main()
