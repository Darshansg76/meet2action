"""Run the shared engine, prepare human task matches, and score saved outcomes.

Uses only the standard library. Importing this module never imports service.py
or makes a model call. See README.md for the three CLI subcommands.
"""
import argparse
import hashlib
import importlib
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
METHODS = ("A", "B", "C")
STATUSES = {"ok", "review_required", "api_error"}
ACTION_KEYS = {"task", "owner", "deadline_text", "due_date", "evidence", "warnings"}
META_KEYS = {"latency_ms", "model_calls", "input_tokens", "output_tokens", "model",
             "raw_responses", "validation_errors"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    """Replace a JSON file atomically, so an interrupted write stays readable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False, suffix=".tmp") as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def select_records(dataset, split, ids):
    records = dataset["records"]
    all_ids = [record["id"] for record in records]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("Dataset contains duplicate meeting IDs.")
    requested = set(ids or [])
    if ids and len(ids) != len(requested):
        raise ValueError("--ids contains duplicate meeting IDs.")
    selected = [r for r in records if r["split"] == split
                and (not requested or r["id"] in requested)]
    missing = requested - {r["id"] for r in selected}
    if missing:
        raise ValueError(f"IDs are absent from split {split}: {', '.join(sorted(missing))}")
    if not selected:
        raise ValueError(f"No records selected from split {split}.")
    pending = [r["id"] for r in selected if r.get("status") != "labelled"]
    if pending:
        raise ValueError("Finish labelling these selected records first: " + ", ".join(pending))
    for record in selected:
        lines = [line for line in record["notes"].splitlines() if line.strip()]
        if not 3 <= len(lines) <= 25 or len(record["notes"]) > 6000:
            raise ValueError(f"{record['id']}: notes violate the agreed input limits.")
    return selected


def parse_settings(items):
    """Record declared settings; service.py remains responsible for applying them."""
    settings = {}
    for item in items:
        key, separator, value = item.partition("=")
        if not separator or key not in {"temperature", "top_p", "seed", "max_output_tokens"}:
            raise ValueError("--setting accepts temperature, top_p, seed or max_output_tokens as NAME=VALUE.")
        if key in settings:
            raise ValueError(f"Duplicate setting: {key}")
        settings[key] = json.loads(value)
        if isinstance(settings[key], bool) or not isinstance(settings[key], (int, float)):
            raise ValueError(f"Setting {key} must be a JSON number.")
        if not math.isfinite(settings[key]):
            raise ValueError(f"Setting {key} must be finite.")
    return settings


def nonnegative_number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


def nonnegative_integer(value):
    return type(value) is int and value >= 0


def validate_result(result, method):
    """Check the public result shape without substituting for human task matching."""
    errors = []
    if not isinstance(result, dict):
        return ["Engine must return a JSON-compatible dictionary."]
    for key in ("status", "actions", "message", "meta"):
        if key not in result:
            errors.append(f"Missing response key: {key}")
    if not isinstance(result.get("status"), str) or result["status"] not in STATUSES:
        errors.append("Unknown response status.")
    if not isinstance(result.get("message"), str):
        errors.append("message must be a string.")
    actions = result.get("actions")
    if not isinstance(actions, list):
        errors.append("actions must be a list.")
        actions = []
    if len(actions) > 8:
        errors.append("More than eight actions returned.")
    if result.get("status") != "ok" and actions:
        errors.append("Non-ok responses must have an empty actions list.")
    for index, action in enumerate(actions):
        prefix = f"Action {index}: "
        if not isinstance(action, dict) or set(action) != ACTION_KEYS:
            errors.append(prefix + "incorrect final action fields.")
            continue
        if not isinstance(action["task"], str) or not action["task"].strip():
            errors.append(prefix + "task must be a non-empty string.")
        for key in ("owner", "deadline_text", "due_date"):
            value = action[key]
            if value is not None and (not isinstance(value, str) or not value.strip()):
                errors.append(prefix + key + " must be a non-empty string or null.")
        due = action["due_date"]
        if isinstance(due, str):
            try:
                if date.fromisoformat(due).isoformat() != due:
                    raise ValueError
            except ValueError:
                errors.append(prefix + "due_date must be an ISO calendar date.")
        evidence = action["evidence"]
        if not isinstance(evidence, list) or not evidence:
            errors.append(prefix + "evidence must be a non-empty list.")
        else:
            for item in evidence:
                if (not isinstance(item, dict) or set(item) != {"line_id", "quote"}
                        or not isinstance(item.get("line_id"), str)
                        or not isinstance(item.get("quote"), str)
                        or not item.get("quote")):
                    errors.append(prefix + "evidence must contain line_id and quote strings.")
        warnings = action["warnings"]
        if (not isinstance(warnings, list)
                or any(w not in ("missing_owner", "missing_deadline", "unresolved_deadline")
                       for w in warnings)):
            errors.append(prefix + "invalid warnings list.")
    meta = result.get("meta")
    if not isinstance(meta, dict) or not META_KEYS <= set(meta):
        errors.append("meta is missing agreed fields.")
    else:
        if not nonnegative_number(meta["latency_ms"]):
            errors.append("meta.latency_ms must be a non-negative finite number.")
        calls = meta["model_calls"]
        if not nonnegative_integer(calls) or calls > (2 if method == "C" else 1):
            errors.append("meta.model_calls exceeds the method's logical call limit or is invalid.")
        elif result.get("status") == "ok" and calls == 0:
            errors.append("An ok live extraction must report at least one model call.")
        for key in ("input_tokens", "output_tokens"):
            if meta[key] is not None and not nonnegative_integer(meta[key]):
                errors.append(f"meta.{key} must be a non-negative integer or null.")
        if not isinstance(meta["model"], str) or not meta["model"].strip():
            errors.append("meta.model must identify the actual model.")
        for key in ("raw_responses", "validation_errors"):
            if not isinstance(meta[key], list) or any(not isinstance(x, str) for x in meta[key]):
                errors.append(f"meta.{key} must be a list of strings.")
    return errors


def source_errors(action, notes):
    lines = [line for line in notes.splitlines() if line.strip()]
    errors = []
    for item in action["evidence"]:
        match = re.fullmatch(r"L(\d{2})", item["line_id"])
        if not match or not 1 <= int(match[1]) <= len(lines):
            errors.append(f"Invalid source line: {item['line_id']}")
        elif item["quote"] != lines[int(match[1]) - 1]:
            errors.append(f"Quote differs from the complete source line {item['line_id']}.")
    joined = "\n".join(item["quote"] for item in action["evidence"])
    if action["owner"] is not None and action["owner"] not in joined:
        errors.append("Owner name is absent from evidence.")
    if action["deadline_text"] is not None and action["deadline_text"].lower() not in joined.lower():
        errors.append("Deadline phrase is absent from evidence.")
    return errors


def predicted_actions(outcome):
    if outcome["status"] == "ok" and not outcome["contract_errors"]:
        return outcome["result"]["actions"]
    return []


def capture_case(record, method, extractor, expected_model):
    started = time.perf_counter()
    result, errors = None, []
    try:
        result = extractor(notes=record["notes"], meeting_date=record["meeting_date"], method=method)
        json.dumps(result, allow_nan=False)
        errors = validate_result(result, method)
    except Exception as exc:
        result = None
        errors = [f"{type(exc).__name__}: {exc}"]
    elapsed = round((time.perf_counter() - started) * 1000, 3)
    meta = result.get("meta", {}) if isinstance(result, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    mismatch = isinstance(meta.get("model"), str) and meta["model"] != expected_model
    if mismatch:
        errors.append(f"Expected model {expected_model!r}, received {meta['model']!r}.")
    outcome = {
        "meeting_id": record["id"], "method": method, "split": record["split"],
        "category": record["category"], "recorded_at": datetime.now(timezone.utc).isoformat(),
        "status": "evaluation_error" if errors else result["status"],
        "result": result, "contract_errors": errors, "latency_ms": elapsed,
        "validation_errors": meta.get("validation_errors", []),
        "model": meta.get("model"), "model_calls": meta.get("model_calls"),
        "input_tokens": meta.get("input_tokens"), "output_tokens": meta.get("output_tokens"),
        "source_errors": [],
    }
    outcome["source_errors"] = [source_errors(action, record["notes"])
                                 for action in predicted_actions(outcome)]
    return outcome, mismatch


def load_outcomes(path, expected_keys, allow_partial_line=False):
    """A killed append may leave only the final JSONL line incomplete."""
    path = Path(path)
    if not path.exists():
        return []
    rows, seen, offset = [], set(), 0
    raw_lines = path.read_bytes().splitlines(keepends=True)
    for index, raw in enumerate(raw_lines):
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            if allow_partial_line and index == len(raw_lines) - 1 and not raw.endswith(b"\n"):
                with path.open("r+b") as stream:
                    stream.truncate(offset)
                break
            raise ValueError(f"Unreadable prediction line {index + 1} in {path}.") from None
        key = (row["meeting_id"], row["method"])
        if key not in expected_keys or key in seen:
            raise ValueError(f"Unexpected or duplicate saved outcome: {key}")
        rows.append(row)
        seen.add(key)
        offset += len(raw)
        if allow_partial_line and index == len(raw_lines) - 1 and not raw.endswith(b"\n"):
            with path.open("ab") as stream:
                stream.write(b"\n")
                stream.flush()
                os.fsync(stream.fileno())
    return rows


def import_engine():
    try:
        module = importlib.import_module("service")
    except ImportError as exc:
        raise ValueError("Cannot import service.py and its dependencies. Complete the engine handoff first. "
                         + str(exc)) from exc
    if not callable(getattr(module, "extract_actions", None)):
        raise ValueError("service.py must export extract_actions(notes, meeting_date=None, method='B').")
    return module.extract_actions


def input_fingerprints(dataset_path, root):
    return {"dataset": digest(dataset_path), "prompts": digest(root / "prompts.json"),
            "service": digest(root / "service.py")}


def run_evaluation(args, extractor=None, root=ROOT):
    dataset_path = Path(args.dataset).resolve()
    dataset = read_json(dataset_path)
    records = select_records(dataset, args.split, args.ids)
    methods = args.methods
    if len(methods) != len(set(methods)):
        raise ValueError("--methods contains duplicates.")
    settings = parse_settings(args.setting)
    if args.dry_run:
        print(f"Selected {len(records)} labelled {args.split} meetings x {len(methods)} methods "
              f"= {len(records) * len(methods)} initial calls; up to "
              f"{len(records) if 'C' in methods else 0} repairs.")
        print("IDs: " + ", ".join(r["id"] for r in records))
        print("Engine: " + ("present (not imported)" if (root / "service.py").exists() else "not supplied yet"))
        print("Dry run: no files written and no model calls made.")
        return
    if not args.model:
        raise ValueError("Live runs require --model with the engine's configured model ID.")
    if not (root / "service.py").is_file():
        raise ValueError("service.py is not supplied yet; use --dry-run to inspect a selection.")
    fingerprints = input_fingerprints(dataset_path, root)
    configuration = {"split": args.split, "ids": [r["id"] for r in records],
                     "methods": methods, "expected_model": args.model,
                     "declared_settings": settings, "sha256": fingerprints}
    directory = Path(args.output_dir)
    manifest_path = directory / "manifest.json"
    if args.resume:
        if not manifest_path.is_file():
            raise ValueError("--resume requires an existing run manifest.")
        manifest = read_json(manifest_path)
        if manifest["configuration"] != configuration:
            raise ValueError("Run inputs, model, settings or selection changed; choose a new output directory.")
        for name in ("dataset", "prompts"):
            if digest(directory / f"{name}.json") != fingerprints[name]:
                raise ValueError(f"Saved {name} snapshot differs from the run manifest.")
    else:
        if directory.exists() and any(directory.iterdir()):
            raise ValueError("Output directory is not empty. Use --resume or choose a new directory.")
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                    "configuration": configuration, "python": sys.version,
                    "evaluator_sha256": digest(Path(__file__)),
                    "settings_note": "Declared settings must also be configured in service.py; "
                                     "the evaluator does not change provider settings."}
        (directory / "dataset.json").write_bytes(dataset_path.read_bytes())
        (directory / "prompts.json").write_bytes((root / "prompts.json").read_bytes())
        write_json(manifest_path, manifest)
    if manifest["evaluator_sha256"] != digest(Path(__file__)):
        raise ValueError("Evaluator changed since this run; choose a new output directory.")
    expected = {(r["id"], method) for r in records for method in methods}
    predictions_path = directory / "predictions.jsonl"
    existing = load_outcomes(predictions_path, expected, allow_partial_line=args.resume)
    completed = {(row["meeting_id"], row["method"]) for row in existing}
    if expected == completed:
        print(f"All {len(expected)} outcomes are already saved; no model calls made.")
        return
    extractor = extractor or import_engine()
    for record in records:
        for method in methods:
            if (record["id"], method) in completed:
                continue
            if input_fingerprints(dataset_path, root) != fingerprints:
                raise ValueError("Inputs changed during evaluation. Progress is saved; start a new frozen run.")
            outcome, mismatch = capture_case(record, method, extractor, args.model)
            changed = input_fingerprints(dataset_path, root) != fingerprints
            if changed:
                outcome["status"] = "evaluation_error"
                outcome["contract_errors"].append("Inputs changed during this extraction.")
                outcome["source_errors"] = []
            with predictions_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(outcome, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            print(f"{record['id']} {method}: {outcome['status']}", flush=True)
            if changed:
                raise ValueError("Inputs changed during evaluation. Progress is saved; start a new frozen run.")
            if mismatch:
                raise ValueError("Model mismatch saved as a failed outcome. Fix configuration and start a new run.")
    print(f"Saved {len(expected)} outcomes in {predictions_path}.")


def load_run(directory):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    configuration = manifest["configuration"]
    for name in ("dataset", "prompts"):
        if digest(directory / f"{name}.json") != configuration["sha256"][name]:
            raise ValueError(f"Saved {name} snapshot differs from the run manifest.")
    dataset = read_json(directory / "dataset.json")
    records = {r["id"]: r for r in select_records(dataset, configuration["split"], configuration["ids"])}
    expected = {(rid, method) for rid in records for method in configuration["methods"]}
    outcomes = load_outcomes(directory / "predictions.jsonl", expected)
    if len(outcomes) != len(expected):
        raise ValueError(f"Run is incomplete: {len(outcomes)} of {len(expected)} outcomes saved. Resume it first.")
    return manifest, records, outcomes


def review_template(args):
    directory = Path(args.run_dir)
    _, records, outcomes = load_run(directory)
    output = Path(args.output)
    if output.exists():
        raise ValueError("Review output already exists; choose another file to preserve existing judgments.")
    reviews = []
    for row in outcomes:
        reviews.append({"meeting_id": row["meeting_id"], "method": row["method"],
                        "reviewer": "", "complete": False,
                        "gold_tasks": [a["task"] for a in records[row["meeting_id"]]["gold_actions"]],
                        "predicted_tasks": [a["task"] for a in predicted_actions(row)],
                        "gold_actions": records[row["meeting_id"]]["gold_actions"],
                        "predicted_actions": predicted_actions(row),
                        "matches": [], "unsupported_prediction_indices": [], "notes": ""})
    write_json(output, {"version": 1, "manifest_sha256": digest(directory / "manifest.json"),
                        "predictions_sha256": digest(directory / "predictions.jsonl"), "reviews": reviews})
    print(f"Prepared {len(reviews)} pending reviews in {output}. No judgments have been filled in.")


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def validate_review(review, gold, predicted):
    if review.get("complete") is not True or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise ValueError("Each outcome needs a named reviewer and complete=true before scoring.")
    gold_seen, predicted_seen = set(), set()
    for match in review["matches"]:
        gi, pi = match["gold_index"], match["prediction_index"]
        if (type(gi) is not int or not 0 <= gi < len(gold) or gi in gold_seen
                or type(pi) is not int or not 0 <= pi < len(predicted) or pi in predicted_seen):
            raise ValueError("Task matches must be one-to-one, using valid zero-based indices.")
        if type(match["owner_correct"]) is not bool or type(match["date_correct"]) is not bool:
            raise ValueError("Matched owner_correct and date_correct judgments must be true or false.")
        gold_seen.add(gi)
        predicted_seen.add(pi)
    unsupported = review["unsupported_prediction_indices"]
    if not isinstance(unsupported, list) or any(type(i) is not int for i in unsupported):
        raise ValueError("Unsupported prediction indices must be a list of integers.")
    if len(unsupported) != len(set(unsupported)) or any(not 0 <= i < len(predicted) or i in predicted_seen for i in unsupported):
        raise ValueError("Unsupported predictions must be unique, valid and unmatched.")


def summarize(outcomes, records, reviews):
    summary = {}
    for method in sorted({row["method"] for row in outcomes}):
        rows = [row for row in outcomes if row["method"] == method]
        count = len(rows)
        totals = Counter()
        for row in rows:
            gold = records[row["meeting_id"]]["gold_actions"]
            predicted = predicted_actions(row)
            review = reviews[(row["meeting_id"], method)]
            validate_review(review, gold, predicted)
            totals.update({"gold_actions": len(gold), "predicted_actions": len(predicted),
                           "matched_actions": len(review["matches"]),
                           "owner_correct": sum(m["owner_correct"] for m in review["matches"]),
                           "date_correct": sum(m["date_correct"] for m in review["matches"]),
                           "unsupported_actions": len(review["unsupported_prediction_indices"]),
                           "source_pass_actions": sum(not errors for errors in row["source_errors"]),
                           "valid_outputs": int(row["status"] == "ok"),
                           "no_action_meetings": int(not gold),
                           "correct_no_action_meetings": int(not gold and row["status"] == "ok" and not predicted)})
        precision = ratio(totals["matched_actions"], totals["predicted_actions"])
        recall = ratio(totals["matched_actions"], totals["gold_actions"])
        f1 = (None if precision is None or recall is None else
              2 * precision * recall / (precision + recall) if precision + recall else 0.0)
        metrics = {
            "outcomes": count, "statuses": dict(Counter(row["status"] for row in rows)),
            "counts": dict(totals), "task_precision": precision, "task_recall": recall, "task_f1": f1,
            "owner_correctness": ratio(totals["owner_correct"], totals["matched_actions"]),
            "date_correctness": ratio(totals["date_correct"], totals["matched_actions"]),
            "source_check_pass_rate": ratio(totals["source_pass_actions"], totals["predicted_actions"]),
            "valid_output_rate": ratio(totals["valid_outputs"], count),
            "no_action_correctness": ratio(totals["correct_no_action_meetings"], totals["no_action_meetings"]),
            "unsupported_action_rate": ratio(totals["unsupported_actions"], totals["predicted_actions"]),
            "median_latency_ms": statistics.median(row["latency_ms"] for row in rows),
        }
        for key in ("model_calls", "input_tokens", "output_tokens"):
            known = [row[key] for row in rows if nonnegative_integer(row.get(key))]
            metrics[key + "_reported_outcomes"] = len(known)
            metrics[key + "_known_sum"] = sum(known) if known else None
            metrics[key + "_total"] = sum(known) if len(known) == count else None
        summary[method] = metrics
    return summary


def score(args):
    directory = Path(args.run_dir)
    manifest, records, outcomes = load_run(directory)
    review_data = read_json(args.reviews)
    if (review_data["manifest_sha256"] != digest(directory / "manifest.json")
            or review_data["predictions_sha256"] != digest(directory / "predictions.jsonl")):
        raise ValueError("Review file belongs to different run inputs or predictions.")
    reviews = {}
    expected = {(row["meeting_id"], row["method"]) for row in outcomes}
    for review in review_data["reviews"]:
        key = (review["meeting_id"], review["method"])
        if key in reviews or key not in expected:
            raise ValueError(f"Unexpected or duplicate review: {key}")
        reviews[key] = review
    if set(reviews) != expected:
        raise ValueError("Every saved outcome, including failures, needs exactly one review.")
    report = {"version": 1, "configuration": manifest["configuration"],
              "scorer_sha256": digest(Path(__file__)),
              "undefined_metric": "null means n/a (zero denominator or incomplete usage reporting)",
              "methods": summarize(outcomes, records, reviews)}
    for output in (args.output, args.table):
        if output and Path(output).exists():
            raise ValueError("Score output already exists; choose another file.")
    if args.output and args.table and Path(args.output).resolve() == Path(args.table).resolve():
        raise ValueError("JSON and Markdown outputs need different paths.")
    if args.output:
        write_json(args.output, report)
    if args.table:
        table_path = Path(args.table)
        table_path.parent.mkdir(parents=True, exist_ok=True)
        table_path.write_text(comparison_table(report), encoding="utf-8")
    print(json.dumps(report, indent=2, allow_nan=False))


def comparison_table(report):
    def display(value):
        return "n/a" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)

    lines = ["# Evaluation comparison", "", f"Split: {report['configuration']['split']}", "",
             "Task precision, recall and F1 use one-to-one human matches. Failed outcomes remain in the run.", ""]
    groups = [
        [("Outcomes", "outcomes"), ("Precision", "task_precision"), ("Recall", "task_recall"),
         ("F1", "task_f1"), ("Owner correctness", "owner_correctness"), ("Date correctness", "date_correctness")],
        [("Valid output", "valid_output_rate"), ("Source check", "source_check_pass_rate"),
         ("No-action correctness", "no_action_correctness"), ("Unsupported actions", "unsupported_action_rate")],
        [("Median latency ms", "median_latency_ms"), ("Model calls", "model_calls_total"),
         ("Input tokens", "input_tokens_total"), ("Output tokens", "output_tokens_total")],
    ]
    for columns in groups:
        lines.append("| Method | " + " | ".join(title for title, _ in columns) + " |")
        lines.append("| --- | " + " | ".join("---" for _ in columns) + " |")
        for method, metrics in report["methods"].items():
            lines.append(f"| {method} | " + " | ".join(display(metrics[key]) for _, key in columns) + " |")
        lines.append("")
    lines.append("Rates range from 0 to 1. n/a means a zero denominator or incomplete usage reporting. "
                 "See the JSON report for counts, statuses and usage coverage.\n")
    return "\n".join(lines)


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run each selected meeting independently with A/B/C.")
    run.add_argument("--dataset", default=str(ROOT / "data" / "meetings.json"))
    run.add_argument("--split", choices=("example", "development", "test"), default="development")
    run.add_argument("--ids", nargs="+", help="Explicit subset for smoke checks; otherwise select the entire split.")
    run.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    run.add_argument("--output-dir", default=str(ROOT / "results" / "runs" / "development"))
    run.add_argument("--model", help="Expected actual model ID, verified against every engine response.")
    run.add_argument("--setting", action="append", default=[], metavar="NAME=VALUE",
                     help="Record a non-secret generation setting already configured in service.py.")
    run.add_argument("--dry-run", action="store_true", help="Check selected labels without importing the engine.")
    run.add_argument("--resume", action="store_true", help="Skip saved outcomes; require unchanged frozen inputs.")
    review = commands.add_parser("review-template", help="Prepare pending human task-match reviews.")
    review.add_argument("--run-dir", required=True)
    review.add_argument("--output", required=True)
    scoring = commands.add_parser("score", help="Score a complete run using completed human reviews.")
    scoring.add_argument("--run-dir", required=True)
    scoring.add_argument("--reviews", required=True)
    scoring.add_argument("--output")
    scoring.add_argument("--table", help="Optional Markdown comparison table; undefined values display as n/a.")
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "run":
            run_evaluation(args)
        elif args.command == "review-template":
            review_template(args)
        else:
            score(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Evaluation stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
