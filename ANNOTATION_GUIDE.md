# Meet2Action — Annotation Guide

Owner: Dhanush (PES1UG24AM360). Use this when writing and labelling your 15 snippets.
After editing `data/meetings.json`, run `python tools/check_data.py` — it must print
"All labelled records and prompts pass."

## 1. Your IDs and what each one must be

Each member writes 15 snippets: 10 test (one per category plus two extras) and 5 example or
development cases. The ID, split and category are already filled in `data/meetings.json` —
write notes that fit the category on that record.

| Member | IDs | Example pool | Development | Extra test categories |
|---|---|---|---|---|
| Dhanraj | M001–M015 | M001 clear_task, M002 missing_owner | M003 non_action, M004 correction, M005 speaker_ambiguity | clear_task, several_tasks |
| Vismaya | M016–M030 | M016 unclear_date, M017 non_action, M018 speaker_ambiguity | M019 clear_task, M020 several_tasks | missing_owner, unclear_date |
| Dhanush | M031–M045 | M031 several_tasks, M032 correction, M033 no_action | M034 missing_owner, M035 unclear_date | non_action, correction |
| Darshan | M046–M060 | M046 clear_task, M047 unclear_date | M048 no_action, M049 speaker_ambiguity, M050 several_tasks | speaker_ambiguity, no_action |

Totals: 10 example, 10 development, 40 test, and exactly 5 test meetings per category.
The 5 `no_action` test meetings are the genuine no-action cases.

**Independent reviews** (label these yourself first, then compare with the author):
Dhanraj reviews M016–M018 · Vismaya reviews M031–M033 · Dhanush reviews M046–M048 · Darshan reviews M001–M003.

## 2. The eight categories

| Category | What the snippet must contain |
|---|---|
| clear_task | One or two plain commitments with a named owner |
| several_tasks | Three to eight actions from different people |
| missing_owner | An agreed task with no person responsible ("has to be", "we need to") |
| unclear_date | Deadlines like "next week", "soon", "end of month" that cannot become a date |
| non_action | Suggestions and finished work mixed with at most one or two real actions |
| correction | An owner or deadline that is changed later in the meeting |
| speaker_ambiguity | A line with no speaker, or "someone"/"we" as the doer |
| no_action | Zero actions — only updates, finished work and suggestions |

## 3. Writing a snippet

- 3 to 25 non-empty lines, under 6,000 characters, 0 to 8 actions.
- Format each line as `Name: what they said`. Leave one label off only for speaker_ambiguity.
- Use a different set of names in each snippet, so few-shot examples cannot leak names into tests.
- Set `meeting_date` to an ISO date such as `"2026-11-14"`, or `null` to test a missing date.
- Set `"status": "labelled"` and fill `provenance.created` when you are done.

## 4. What counts as an action

**Is an action**
- A commitment: "I will", "I'll", "I can send it by…"
- An accepted request: "Arjun, can you collect the fees?" / "Yes, I'll collect them."
- An agreed need with no owner: "has to", "must", "needs to", "we need to"
- A "will" with an unclear doer: "Someone will merge the files."

**Is NOT an action**
- Suggestions: "could", "might", "maybe", "it might be nice", "let's think about it"
- Finished work: "already", "finished", "was fixed", anything in the past tense
- Status updates and greetings
- A commitment later cancelled or replaced — keep only the final version

## 5. Gold label format

Gold labels use the **final** format. (Few-shot examples in `prompts.json` use the model's
format instead — never mix the two.)

```json
{
  "task": "Send the vendor contract to legal",
  "owner": "Sana",
  "deadline_text": "2026-11-14",
  "due_date": "2026-11-14",
  "evidence": [
    {"line_id": "L03", "quote": "Leela: Actually, correction, Sana will send the contract instead."},
    {"line_id": "L04", "quote": "Sana: Sure, I can send it by 2026-11-14."}
  ],
  "warnings": []
}
```

| Field | Rule |
|---|---|
| task | Short instruction starting with a verb |
| owner | Name exactly as written; `null` if nobody is clearly responsible |
| deadline_text | Time phrase copied from the notes; drop a leading "by", "on" or "for"; keep "before", "next", "end of", "in"; `null` if none |
| due_date | See section 6 |
| evidence | `line_id` counts **non-empty lines** from `L01`. `quote` is the line copied exactly, speaker name included, without the line ID |
| warnings | See section 6, in this order: `missing_owner`, `missing_deadline`, `unresolved_deadline` |

The owner name and the deadline phrase must both appear inside your quotes.
For corrections, quote the correcting line and any line that confirms the final value.

## 6. Dates and warnings

| deadline_text | due_date |
|---|---|
| ISO date, e.g. `2026-11-20` | the same date (even without a meeting date) |
| `today` | meeting date |
| `tomorrow` | meeting date + 1 |
| `in N days`, N = 1 to 30 | meeting date + N (write "days" — avoid "in 1 day") |
| anything else ("Friday", "next week", "soon") | `null` |
| a relative phrase when `meeting_date` is `null` | `null` |

- owner is `null` → `missing_owner`
- deadline_text is `null` → `missing_deadline`
- deadline_text present but due_date `null` → `unresolved_deadline`

The checker recomputes every `due_date` and `warnings` list from these rules and flags mismatches.

## 7. Few-shot examples

The B prompt uses three example-pool cases written by Dhanush:
M031 (several tasks, one ownerless), M032 (correction changes the owner) and
M033 (finished work and a suggestion, so no actions). Do not reuse their names or wording in
development or test snippets.

## 8. Settling disagreements

Reviewers label their three IDs independently, then compare with the author. Record the outcome
in each record's `review` block (`"agreed": true/false`, plus a short note). Dhanush resolves
anything still disputed using sections 4–6, and the guide is updated if a rule was unclear.
