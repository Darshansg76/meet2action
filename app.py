import csv
import io
import json
from datetime import date
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

# Shared extraction function from service.py
try:
    from service import extract_actions
except ImportError:
    # Fallback mock matching Page 6 contract until service.py is connected
    def extract_actions(
        notes: str, meeting_date: str = None, method: str = "B"
    ) -> Dict[str, Any]:
        return {
            "status": "ok",
            "actions": [
                {
                    "task": "Send the slides",
                    "owner": "Maya",
                    "deadline_text": "tomorrow",
                    "due_date": "2026-11-12",
                    "evidence": [
                        {
                            "line_id": "L01",
                            "quote": "Maya: I will send the slides tomorrow.",
                        }
                    ],
                    "warnings": [],
                }
            ],
            "message": "",
            "meta": {
                "latency_ms": 120,
                "model_calls": 1,
                "input_tokens": 42,
                "output_tokens": 18,
                "model": "mock-model",
                "raw_responses": [],
                "validation_errors": [],
            },
        }


SAMPLE_NOTES = """Maya: I will send the slides tomorrow.
Raj: Can you also review the budget draft by Friday?
Maya: Yes, I will take care of the budget review."""

INPUT_MAX_LINES = 25
INPUT_MAX_CHARS = 6000
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_cell(val: Any) -> str:
    """Escapes formula injection characters for spreadsheet safety."""
    if val is None:
        return ""
    s = str(val).strip()
    if s.startswith(FORMULA_PREFIXES):
        return "'" + s
    return s


def convert_actions_to_csv(actions: List[Dict[str, Any]]) -> str:
    """Converts action records into CSV format with 1 row per task."""
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "Task",
            "Owner",
            "Original Deadline",
            "Due Date",
            "Evidence Quotes",
            "Line IDs",
            "Warnings",
        ]
    )

    for act in actions:
        ev_list = act.get("evidence", [])
        quotes = "; ".join(e.get("quote", "") for e in ev_list if e.get("quote"))
        line_ids = ", ".join(
            e.get("line_id", "") for e in ev_list if e.get("line_id")
        )
        warnings = "; ".join(act.get("warnings", []))
        writer.writerow(
            [
                sanitize_csv_cell(act.get("task", "")),
                sanitize_csv_cell(act.get("owner", "")),
                sanitize_csv_cell(act.get("deadline_text", "")),
                sanitize_csv_cell(act.get("due_date", "")),
                sanitize_csv_cell(quotes),
                sanitize_csv_cell(line_ids),
                sanitize_csv_cell(warnings),
            ]
        )
    return output.getvalue()


# Session state initialization
if "notes_text" not in st.session_state:
    st.session_state["notes_text"] = ""
if "meeting_date_val" not in st.session_state:
    st.session_state["meeting_date_val"] = None
if "method_val" not in st.session_state:
    st.session_state["method_val"] = "C"
if "cached_response" not in st.session_state:
    st.session_state["cached_response"] = None
if "is_stale" not in st.session_state:
    st.session_state["is_stale"] = False


def mark_stale():
    st.session_state["is_stale"] = True


st.set_page_config(page_title="Meet2Action", page_icon="📋", layout="wide")
st.title("📋 Meet2Action: Action Item Extractor")
st.caption("Extract agreed tasks, supported owners, deadlines, and source quotes.")

# Load Example Button
col_load, _ = st.columns([1, 4])
with col_load:
    if st.button("Load example", use_container_width=True):
        st.session_state["notes_text"] = SAMPLE_NOTES
        st.session_state["meeting_date_val"] = date(2026, 11, 11)
        st.session_state["method_val"] = "C"
        mark_stale()
        st.rerun()

# Controls
notes_input = st.text_area(
    "Meeting Notes (up to 25 non-empty lines, max 6,000 chars):",
    value=st.session_state["notes_text"],
    height=180,
    placeholder="Paste meeting notes here...",
    on_change=mark_stale,
)
st.session_state["notes_text"] = notes_input

col_date, col_method = st.columns(2)
with col_date:
    use_date = st.checkbox(
        "Specify Meeting Date",
        value=(st.session_state["meeting_date_val"] is not None),
    )
    if use_date:
        sel_date = st.date_input(
            "Meeting Date",
            value=st.session_state["meeting_date_val"] or date(2026, 11, 11),
            on_change=mark_stale,
        )
        st.session_state["meeting_date_val"] = sel_date
    else:
        st.session_state["meeting_date_val"] = None

with col_method:
    method_options = ["A", "B", "C"]
    selected_method = st.selectbox(
        "Extraction Method",
        method_options,
        index=method_options.index(st.session_state["method_val"]),
        help="A: Instructions | B: Few-shot | C: B + Validation Repair",
        on_change=mark_stale,
    )
    st.session_state["method_val"] = selected_method

# Verification & Execution
non_empty_lines = [
    line for line in notes_input.splitlines() if line.strip() != ""
]
num_lines = len(non_empty_lines)
char_count = len(notes_input)

extract_btn = st.button("Extract actions", type="primary")

if extract_btn:
    if not notes_input.strip():
        st.error("Please enter meeting notes before extracting.")
    elif num_lines > INPUT_MAX_LINES:
        st.error(
            f"Input exceeds limit: {num_lines}/{INPUT_MAX_LINES} non-empty lines."
        )
    elif char_count > INPUT_MAX_CHARS:
        st.error(
            f"Input exceeds character limit: {char_count}/{INPUT_MAX_CHARS} characters."
        )
    else:
        iso_date_str = (
            st.session_state["meeting_date_val"].isoformat()
            if st.session_state["meeting_date_val"]
            else None
        )
        with st.spinner("Extracting action items..."):
            resp = extract_actions(
                notes=notes_input,
                meeting_date=iso_date_str,
                method=selected_method,
            )
            st.session_state["cached_response"] = resp
            st.session_state["is_stale"] = False

if st.session_state["is_stale"] and st.session_state["cached_response"]:
    st.warning("⚠️ Input modified. Run 'Extract actions' again before downloading.")

# Display Results
current_resp = st.session_state.get("cached_response")

if current_resp:
    st.write("---")
    status = current_resp.get("status", "unknown")
    actions = current_resp.get("actions", [])
    message = current_resp.get("message", "")
    meta = current_resp.get("meta", {})

    if status == "api_error":
        st.error(f"**API Failure**: {message or 'Could not complete extraction.'}")
        st.info("Your input notes are preserved above.")
    elif status == "review_required":
        st.warning(f"**Review Required**: {message or 'Extraction output failed validation.'}")
        val_errors = meta.get("validation_errors", [])
        if val_errors:
            st.markdown("**Validation Errors:**")
            for err in val_errors:
                st.write(f"- {err}")
    elif status == "ok":
        if not actions:
            st.info("ℹ️ **No action items detected.**")
        else:
            st.subheader(f"Extracted Actions ({len(actions)})")
            table_data = []
            for a in actions:
                warn_text = (
                    ", ".join(a.get("warnings", []))
                    if a.get("warnings")
                    else "None"
                )
                table_data.append(
                    {
                        "Task": a.get("task", ""),
                        "Owner": a.get("owner") or "Unassigned",
                        "Original Deadline": a.get("deadline_text") or "Unspecified",
                        "Due Date": a.get("due_date") or "Unresolved",
                        "Warnings": warn_text,
                    }
                )
            st.dataframe(pd.DataFrame(table_data), use_container_width=True)

            st.markdown("#### Evidence & Quotations")
            for idx, a in enumerate(actions, start=1):
                with st.expander(f"Action #{idx}: {a.get('task', 'Untitled')}", expanded=True):
                    ev_items = a.get("evidence", [])
                    if ev_items:
                        for ev in ev_items:
                            line_id = ev.get("line_id", "N/A")
                            quote = ev.get("quote", "")
                            st.markdown(f"- **[{line_id}]** *\"{quote}\"*")
                    else:
                        st.write("*(No direct quote evidence provided)*")

        # Diagnostics Expander
        with st.expander("Diagnostics & Execution Meta"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Status", status)
            c2.metric("Latency", f"{meta.get('latency_ms', 0)} ms")
            c3.metric("Model Calls", meta.get("model_calls", 1))
            tin = meta.get("input_tokens")
            tout = meta.get("output_tokens")
            c4.metric(
                "Tokens (In/Out)",
                f"{tin if tin is not None else 'n/a'} / {tout if tout is not None else 'n/a'}",
            )
            st.caption(f"Model: `{meta.get('model', 'N/A')}`")

        # Export Results
        st.write("---")
        st.subheader("Export Results")
        if st.session_state["is_stale"]:
            st.info("Downloads are disabled until a fresh extraction is run.")
        else:
            c_json, c_csv = st.columns(2)
            with c_json:
                st.download_button(
                    label="📥 Download JSON",
                    data=json.dumps(current_resp, indent=2),
                    file_name="meeting_actions.json",
                    mime="application/json",
                    use_container_width=True,
                )
            with c_csv:
                st.download_button(
                    label="📥 Download CSV",
                    data=convert_actions_to_csv(actions),
                    file_name="meeting_actions.csv",
                    mime="text/csv",
                    use_container_width=True,
                )