"""Gemini-backed extraction of supported action items from meeting notes."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, ValidationError
from dotenv import load_dotenv


_DEFAULT_MODEL = "gemini-3.8-flash"
_METHODS = {"A", "B", "C"}
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RELATIVE_DAYS_PATTERN = re.compile(r"^in (\d{1,2}) days$", re.IGNORECASE)
_LINE_ID_PATTERN = re.compile(r"^L(\d{2})$")
_ENV_PATH = Path(__file__).resolve().with_name(".env")
_PROMPTS_PATH = Path(__file__).resolve().with_name("prompts.json")
_LOGGER = logging.getLogger(__name__)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Evidence(_StrictModel):
    line_id: str
    quote: str


class _DraftAction(_StrictModel):
    task: str
    owner: str | None
    deadline_text: str | None
    ambiguities: list[str]
    evidence: list[_Evidence]


class _DraftResponse(_StrictModel):
    actions: list[_DraftAction]


_GEMINI_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "actions": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "task": types.Schema(type=types.Type.STRING),
                    "owner": types.Schema(type=types.Type.STRING, nullable=True),
                    "deadline_text": types.Schema(
                        type=types.Type.STRING, nullable=True
                    ),
                    "ambiguities": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING),
                    ),
                    "evidence": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "line_id": types.Schema(type=types.Type.STRING),
                                "quote": types.Schema(type=types.Type.STRING),
                            },
                            required=["line_id", "quote"],
                        ),
                    ),
                },
                required=[
                    "task",
                    "owner",
                    "deadline_text",
                    "ambiguities",
                    "evidence",
                ],
            ),
        ),
    },
    required=["actions"],
)


def _load_configuration() -> tuple[str, str]:
    load_dotenv(dotenv_path=_ENV_PATH)
    api_key = os.environ.get("GEMINI_API_KEY", "")
    model = os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    return api_key, model


def _create_client(api_key: str) -> genai.Client:
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=60000),
    )


def _generate_content(client: genai.Client, model: str, prompt: str) -> Any:
    return client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_GEMINI_RESPONSE_SCHEMA,
        ),
    )


def _metadata(
    *,
    started: float,
    model: str,
    model_calls: int,
    input_tokens: int | None,
    output_tokens: int | None,
    raw_responses: list[str],
    validation_errors: list[str],
) -> dict[str, Any]:
    return {
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "model_calls": model_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": model,
        "raw_responses": raw_responses,
        "validation_errors": validation_errors,
    }


def _response(
    *,
    status: str,
    actions: list[dict[str, Any]],
    message: str,
    started: float,
    model: str,
    model_calls: int = 0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    raw_responses: list[str] | None = None,
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "actions": actions,
        "message": message,
        "meta": _metadata(
            started=started,
            model=model,
            model_calls=model_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_responses=raw_responses or [],
            validation_errors=validation_errors or [],
        ),
    }


def _valid_iso_date(value: str) -> bool:
    if not _DATE_PATTERN.fullmatch(value):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _validate_input(
    notes: Any, meeting_date: Any, method: Any
) -> tuple[list[str], list[str], date | None]:
    errors: list[str] = []
    if not isinstance(notes, str) or not notes.strip():
        errors.append("notes must be a non-empty string.")
        lines: list[str] = []
    else:
        lines = [line for line in notes.splitlines() if line.strip()]
        if len(lines) > 25:
            errors.append("notes must contain no more than 25 non-empty lines.")
        if len(notes) > 6000:
            errors.append("notes must not exceed 6000 characters.")

    parsed_meeting_date: date | None = None
    if meeting_date is not None:
        if not isinstance(meeting_date, str) or not _valid_iso_date(meeting_date):
            errors.append("meeting_date must be null or a valid ISO calendar date (YYYY-MM-DD).")
        else:
            parsed_meeting_date = date.fromisoformat(meeting_date)

    if not isinstance(method, str) or method not in _METHODS:
        errors.append("method must be one of A, B, or C.")
    return errors, lines, parsed_meeting_date


def _numbered_lines(lines: list[str]) -> str:
    return "\n".join(f"L{index:02d} {line}" for index, line in enumerate(lines, start=1))


def _load_prompts() -> dict[str, Any]:
    with _PROMPTS_PATH.open(encoding="utf-8") as stream:
        prompts = json.load(stream)
    if not isinstance(prompts, dict):
        raise ValueError("prompts.json must contain a JSON object.")
    if not isinstance(prompts.get("shared_rules"), str) or not prompts["shared_rules"].strip():
        raise ValueError("prompts.json is missing shared_rules.")
    examples = prompts.get("few_shot_examples")
    if not isinstance(examples, list):
        raise ValueError("prompts.json is missing few_shot_examples.")
    if not isinstance(prompts.get("repair_rules"), str) or not prompts["repair_rules"].strip():
        raise ValueError("prompts.json is missing repair_rules.")
    return prompts


def _prompt_example(example: Any) -> dict[str, Any]:
    if not isinstance(example, dict):
        raise ValueError("Each few-shot example must be a JSON object.")
    if not isinstance(example.get("notes"), str):
        raise ValueError("Each few-shot example must include notes.")
    prepared = dict(example)
    prepared["notes"] = _numbered_lines(
        [line for line in example["notes"].splitlines() if line.strip()]
    )
    return prepared


def _build_prompt(
    prompts: dict[str, Any],
    lines: list[str],
    meeting_date: str | None,
    method: str,
) -> str:
    sections = [prompts["shared_rules"]]
    if method in {"B", "C"}:
        examples = [_prompt_example(example) for example in prompts["few_shot_examples"]]
        sections.extend(
            ["Few-shot examples:", json.dumps(examples, ensure_ascii=False, indent=2)]
        )
    date_context = meeting_date if meeting_date is not None else "not provided"
    sections.extend(
        [
            f"Meeting date (context only; do not normalize deadlines): {date_context}",
            "Meeting notes:",
            _numbered_lines(lines),
        ]
    )
    return "\n\n".join(sections)


def _safe_raw_text(value: Any, api_key: str) -> str:
    text = value if isinstance(value, str) else ""
    return text.replace(api_key, "[REDACTED]") if api_key else text


def _safe_errors(errors: list[str], api_key: str) -> list[str]:
    if not api_key:
        return errors
    return [error.replace(api_key, "[REDACTED]") for error in errors]


def _usage_value(usage: Any, attribute: str) -> int | None:
    try:
        value = getattr(usage, attribute, None) if usage is not None else None
    except Exception:
        return None
    return value if type(value) is int and value >= 0 else None


def _provider_text(response: Any) -> str:
    try:
        text = getattr(response, "text", None)
    except Exception:
        return ""
    return text if isinstance(text, str) else ""


def _provider_usage(response: Any) -> tuple[int | None, int | None]:
    try:
        usage = getattr(response, "usage_metadata", None)
    except Exception:
        return None, None
    return (
        _usage_value(usage, "prompt_token_count"),
        _usage_value(usage, "candidates_token_count"),
    )


def _pydantic_errors(exc: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    ]


def _due_date(deadline_text: str | None, meeting_date: date | None) -> str | None:
    if deadline_text is None:
        return None
    text = deadline_text.strip()
    if _DATE_PATTERN.fullmatch(text):
        return text if _valid_iso_date(text) else None
    if meeting_date is None:
        return None
    normalized = text.casefold()
    if normalized == "today":
        return meeting_date.isoformat()
    if normalized == "tomorrow":
        try:
            return (meeting_date + timedelta(days=1)).isoformat()
        except OverflowError:
            return None
    match = _RELATIVE_DAYS_PATTERN.fullmatch(text)
    if match:
        days = int(match.group(1))
        if 1 <= days <= 30:
            try:
                return (meeting_date + timedelta(days=days)).isoformat()
            except OverflowError:
                return None
    return None


def _warnings(
    owner: str | None, deadline_text: str | None, due_date: str | None
) -> list[str]:
    result: list[str] = []
    if owner is None:
        result.append("missing_owner")
    if deadline_text is None:
        result.append("missing_deadline")
    elif due_date is None:
        result.append("unresolved_deadline")
    return result


def _action_errors(
    action: _DraftAction,
    lines_by_id: dict[str, str],
    api_key: str,
) -> list[str]:
    errors: list[str] = []
    if not action.task.strip():
        errors.append("task must be a non-empty instruction.")
    if action.owner is not None and not action.owner.strip():
        errors.append("owner must be a non-empty string or null.")
    if action.deadline_text is not None:
        if not action.deadline_text.strip():
            errors.append("deadline_text must be a non-empty string or null.")
        elif re.match(r"^(by|on|for)\s+", action.deadline_text, re.IGNORECASE):
            errors.append("deadline_text must omit a leading by, on, or for.")
    if any(not ambiguity.strip() for ambiguity in action.ambiguities):
        errors.append("ambiguities must contain only non-empty strings.")
    if not action.evidence:
        errors.append("evidence must contain at least one source line.")

    evidence_quotes: list[str] = []
    for evidence in action.evidence:
        match = _LINE_ID_PATTERN.fullmatch(evidence.line_id)
        if match is None or evidence.line_id not in lines_by_id:
            errors.append("evidence line_id must identify a valid non-empty source line.")
            continue
        if evidence.quote != lines_by_id[evidence.line_id]:
            errors.append(
                f"evidence quote for {evidence.line_id} must match the complete source line."
            )
        if not evidence.quote:
            errors.append(f"evidence quote for {evidence.line_id} must not be empty.")
        evidence_quotes.append(evidence.quote)

    joined_quotes = "\n".join(evidence_quotes)
    if action.owner is not None and action.owner not in joined_quotes:
        errors.append("owner is not supported by its evidence quotes.")
    if (
        action.deadline_text is not None
        and action.deadline_text.casefold() not in joined_quotes.casefold()
    ):
        errors.append("deadline_text is not supported by its evidence quotes.")

    if api_key and api_key in json.dumps(action.model_dump(), ensure_ascii=False):
        errors.append("action contains sensitive credential material.")
    return errors


def _validate_model_text(
    raw_text: str,
    lines: list[str],
    api_key: str,
) -> tuple[dict[int, _DraftAction], dict[int, list[str]], list[str], int | None]:
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return {}, {}, ["response must be a JSON object."], None

    global_errors: list[str] = []
    if not isinstance(data, dict) or set(data) != {"actions"}:
        return {}, {}, ["response must contain exactly one top-level key: actions."], None
    if not isinstance(data["actions"], list):
        return {}, {}, ["actions must be a list."], None

    raw_actions = data["actions"]
    if len(raw_actions) > 8:
        global_errors.append("response must contain no more than eight actions.")
    source_lines = {
        f"L{index:02d}": line for index, line in enumerate(lines, start=1)
    }
    valid: dict[int, _DraftAction] = {}
    action_errors: dict[int, list[str]] = {}
    for index, item in enumerate(raw_actions):
        try:
            action = _DraftAction.model_validate(item, strict=True)
        except ValidationError as exc:
            action_errors[index] = _pydantic_errors(exc)
            continue
        errors = _action_errors(action, source_lines, api_key)
        if errors:
            action_errors[index] = errors
        else:
            valid[index] = action
    return valid, action_errors, global_errors, len(raw_actions)


def _errors_for_repair(
    action_errors: dict[int, list[str]], global_errors: list[str], api_key: str
) -> list[str]:
    errors = list(global_errors)
    for index, details in sorted(action_errors.items()):
        errors.extend(f"Action {index}: {detail}" for detail in details)
    return _safe_errors(errors, api_key)


def _repair_prompt(
    prompts: dict[str, Any],
    original_prompt: str,
    raw_text: str,
    errors: list[str],
) -> str:
    return "\n\n".join(
        [
            original_prompt,
            prompts["repair_rules"],
            "Automatic validation errors:",
            json.dumps(errors, ensure_ascii=False, indent=2),
            "Previous JSON response:",
            raw_text,
            "Keep the actions array in the same order. Preserve every action not named "
            "in the validation errors exactly; change only reported errors.",
        ]
    )


def _final_action(action: _DraftAction, meeting_date: date | None) -> dict[str, Any]:
    due = _due_date(action.deadline_text, meeting_date)
    return {
        "task": action.task.strip(),
        "owner": action.owner,
        "deadline_text": action.deadline_text,
        "due_date": due,
        "evidence": [item.model_dump() for item in action.evidence],
        "warnings": _warnings(action.owner, action.deadline_text, due),
    }


def _set_token_totals(
    usage_counts: list[tuple[int | None, int | None]],
) -> tuple[int | None, int | None]:
    input_values = [item[0] for item in usage_counts]
    output_values = [item[1] for item in usage_counts]
    input_total = sum(value for value in input_values if value is not None)
    output_total = sum(value for value in output_values if value is not None)
    return (
        input_total if all(value is not None for value in input_values) else None,
        output_total if all(value is not None for value in output_values) else None,
    )


def extract_actions(
    notes: str, meeting_date: str | None = None, method: str = "B"
) -> dict[str, Any]:
    """Extract evidence-backed action items and return the shared app/evaluator contract."""
    started = time.perf_counter()
    api_key, model = _load_configuration()
    input_errors, lines, parsed_meeting_date = _validate_input(notes, meeting_date, method)
    if input_errors:
        return _response(
            status="review_required",
            actions=[],
            message="Please correct the meeting notes, date, or extraction method.",
            started=started,
            model=model,
            validation_errors=input_errors,
        )

    if not api_key:
        return _response(
            status="api_error",
            actions=[],
            message="Gemini is not configured. Set GEMINI_API_KEY.",
            started=started,
            model=model,
            validation_errors=["GEMINI_API_KEY is not configured."],
        )
    if api_key in notes:
        return _response(
            status="review_required",
            actions=[],
            message="Remove configured credential material from the meeting notes before processing.",
            started=started,
            model=model,
            validation_errors=["notes contain configured credential material."],
        )
    if api_key in model:
        return _response(
            status="api_error",
            actions=[],
            message="Gemini model configuration is invalid.",
            started=started,
            model=_DEFAULT_MODEL,
            validation_errors=["GEMINI_MODEL must not contain credential material."],
        )

    try:
        prompts = _load_prompts()
        prompt = _build_prompt(prompts, lines, meeting_date, method)
        if api_key in prompt:
            raise ValueError("Prompt configuration contains credential material.")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _response(
            status="api_error",
            actions=[],
            message="The extraction service could not load its prompt configuration.",
            started=started,
            model=model,
            validation_errors=[f"Prompt configuration error: {type(exc).__name__}."],
        )

    try:
        client = _create_client(api_key)
    except Exception as exc:
        return _response(
            status="api_error",
            actions=[],
            message="Gemini could not be initialized. Check the service configuration.",
            started=started,
            model=model,
            validation_errors=[f"Gemini initialization failed ({type(exc).__name__})."],
        )

    model_calls = 0
    raw_responses: list[str] = []
    usage_counts: list[tuple[int | None, int | None]] = []
    validation_errors: list[str] = []
    try:
        model_calls += 1
        try:
            response = _generate_content(client, model, prompt)
        except Exception as exc:
            return _response(
                status="api_error",
                actions=[],
                message="Gemini could not complete the extraction. Please try again.",
                started=started,
                model=model,
                model_calls=model_calls,
                raw_responses=raw_responses,
                validation_errors=[f"Gemini request failed ({type(exc).__name__})."],
            )

        raw_text = _provider_text(response)
        raw_responses.append(_safe_raw_text(raw_text, api_key))
        usage_counts.append(_provider_usage(response))
        valid_actions, action_errors, global_errors, original_count = _validate_model_text(
            raw_text, lines, api_key
        )
        validation_errors.extend(_errors_for_repair(action_errors, global_errors, api_key))

        if action_errors or global_errors:
            if method != "C":
                return _response(
                    status="review_required",
                    actions=[],
                    message="The model response did not pass evidence and structure checks.",
                    started=started,
                    model=model,
                    model_calls=model_calls,
                    raw_responses=raw_responses,
                    validation_errors=validation_errors,
                    input_tokens=_set_token_totals(usage_counts)[0],
                    output_tokens=_set_token_totals(usage_counts)[1],
                )

            repair_prompt = _repair_prompt(
                prompts,
                prompt,
                _safe_raw_text(raw_text, api_key),
                _errors_for_repair(action_errors, global_errors, api_key),
            )
            model_calls += 1
            try:
                repair_response = _generate_content(client, model, repair_prompt)
            except Exception:
                usage_counts.append((None, None))
                return _response(
                    status="review_required",
                    actions=[],
                    message="The model response failed validation and repair did not complete.",
                    started=started,
                    model=model,
                    model_calls=model_calls,
                    raw_responses=raw_responses,
                    validation_errors=validation_errors
                    + ["C repair request failed; no actions were accepted."],
                    input_tokens=_set_token_totals(usage_counts)[0],
                    output_tokens=_set_token_totals(usage_counts)[1],
                )

            repaired_text = _provider_text(repair_response)
            raw_responses.append(_safe_raw_text(repaired_text, api_key))
            usage_counts.append(_provider_usage(repair_response))
            repaired_valid, repaired_errors, repaired_global, repaired_count = (
                _validate_model_text(repaired_text, lines, api_key)
            )

            if original_count is None:
                final_drafts = repaired_valid
            else:
                if original_count > 8 and (
                    len(valid_actions) > 8 or any(index >= 8 for index in valid_actions)
                ):
                    repaired_global.append(
                        "repair cannot preserve every originally valid action within the eight-action limit."
                    )
                required_count = min(max(valid_actions, default=-1) + 1, 8)
                if repaired_count is None or repaired_count < required_count:
                    repaired_global.append(
                        "repair removed actions needed to preserve the original valid actions."
                    )
                if repaired_count is not None and repaired_count > original_count:
                    repaired_global.append("repair added actions not present in the original response.")
                final_drafts = dict(repaired_valid)
                for index, action in valid_actions.items():
                    if index < 8:
                        final_drafts[index] = action

            unresolved_repair_errors = {
                index: errors
                for index, errors in repaired_errors.items()
                if index not in valid_actions
            }
            if unresolved_repair_errors or repaired_global:
                validation_errors.extend(
                    _errors_for_repair(unresolved_repair_errors, repaired_global, api_key)
                )
                return _response(
                    status="review_required",
                    actions=[],
                    message="The model response remained invalid after one repair attempt.",
                    started=started,
                    model=model,
                    model_calls=model_calls,
                    raw_responses=raw_responses,
                    validation_errors=validation_errors,
                    input_tokens=_set_token_totals(usage_counts)[0],
                    output_tokens=_set_token_totals(usage_counts)[1],
                )

            final_validation_errors: list[str] = []
            if len(final_drafts) > 8:
                final_validation_errors.append("repaired response contains more than eight actions.")
            if original_count is not None:
                for index in valid_actions:
                    if index < 8 and index not in final_drafts:
                        final_validation_errors.append(
                            f"repair did not preserve valid original action {index}."
                        )
            if final_validation_errors:
                validation_errors.extend(final_validation_errors)
                return _response(
                    status="review_required",
                    actions=[],
                    message="The model response remained invalid after one repair attempt.",
                    started=started,
                    model=model,
                    model_calls=model_calls,
                    raw_responses=raw_responses,
                    validation_errors=validation_errors,
                    input_tokens=_set_token_totals(usage_counts)[0],
                    output_tokens=_set_token_totals(usage_counts)[1],
                )
            valid_actions = final_drafts

        final_actions = [
            _final_action(valid_actions[index], parsed_meeting_date)
            for index in sorted(valid_actions)
        ]
        input_tokens, output_tokens = _set_token_totals(usage_counts)
        return _response(
            status="ok",
            actions=final_actions,
            message="",
            started=started,
            model=model,
            model_calls=model_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_responses=raw_responses,
            validation_errors=validation_errors,
        )
    finally:
        try:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            _LOGGER.warning("Gemini client cleanup failed (%s).", type(exc).__name__)
