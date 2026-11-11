"""Offline evaluator checks. Fixtures are not benchmark measurements."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import evaluate


def action(task="Send the list", owner="Aditi", deadline="tomorrow", due="2026-11-12"):
    return {"task": task, "owner": owner, "deadline_text": deadline, "due_date": due,
            "evidence": [{"line_id": "L02", "quote": "Aditi: I will send the list tomorrow."}],
            "warnings": []}


def response(actions=None, status="ok", **meta_overrides):
    meta = {"latency_ms": 12, "model_calls": 1, "input_tokens": None,
            "output_tokens": None, "model": "offline-fixture", "raw_responses": [],
            "validation_errors": []}
    meta.update(meta_overrides)
    return {"status": status, "actions": actions or [], "message": "", "meta": meta}


def review(matches=None, unsupported=None):
    return {"reviewer": "Offline test fixture", "complete": True, "matches": matches or [],
            "unsupported_prediction_indices": unsupported or []}


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.record = {"id": "X001", "split": "development", "category": "clear_task",
                       "status": "labelled", "meeting_date": "2026-11-11",
                       "notes": "Chair: Welcome.\n\nAditi: I will send the list tomorrow.\nChair: Thanks.",
                       "gold_actions": [action()]}
        self.dataset_path = self.root / "meetings.json"
        evaluate.write_json(self.dataset_path, {"records": [self.record]})
        evaluate.write_json(self.root / "prompts.json", {"shared_rules": "offline fixture"})
        (self.root / "service.py").write_text("# Offline fixture; never imported.\n", encoding="utf-8")
        self.run_dir = self.root / "run"

    def args(self, *extra):
        return evaluate.parser().parse_args([
            "run", "--dataset", str(self.dataset_path), "--output-dir", str(self.run_dir),
            "--model", "offline-fixture", *extra])

    def run_fixture(self, extractor=None, *extra):
        extractor = extractor or Mock(return_value=response([action()]))
        with contextlib.redirect_stdout(io.StringIO()):
            evaluate.run_evaluation(self.args(*extra), extractor=extractor, root=self.root)
        return extractor

    def outcomes(self):
        return [json.loads(line) for line in (self.run_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_each_method_runs_independently_and_resume_makes_no_calls(self):
        extractor = self.run_fixture()
        self.assertEqual([call.kwargs["method"] for call in extractor.call_args_list], ["A", "B", "C"])
        self.assertTrue(all(call.kwargs["meeting_date"] == "2026-11-11" for call in extractor.call_args_list))
        resumed = self.run_fixture(None, "--resume")
        resumed.assert_not_called()
        self.assertEqual(len(self.outcomes()), 3)

    def test_failure_is_saved_and_other_cases_continue(self):
        extractor = Mock(side_effect=[RuntimeError("offline failure"), response([]), response([])])
        self.run_fixture(extractor)
        rows = self.outcomes()
        self.assertEqual([r["status"] for r in rows], ["evaluation_error", "ok", "ok"])
        self.assertIn("offline failure", rows[0]["contract_errors"][0])
        self.assertIsNone(rows[0]["input_tokens"])

    def test_model_mismatch_is_saved_and_stops_comparison(self):
        extractor = Mock(return_value=response(model="another-model"))
        with self.assertRaisesRegex(ValueError, "Model mismatch"):
            self.run_fixture(extractor)
        self.assertEqual(extractor.call_count, 1)
        self.assertEqual(self.outcomes()[0]["status"], "evaluation_error")

    def test_frozen_inputs_and_settings_cannot_change_on_resume(self):
        self.run_fixture()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_fixture(None, "--resume", "--setting", "temperature=0")
        (self.root / "prompts.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_fixture(None, "--resume")

    def test_input_change_mid_run_preserves_completed_progress(self):
        def changed_input(**kwargs):
            (self.root / "prompts.json").write_text("{}", encoding="utf-8")
            return response([action()])
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.run_fixture(Mock(side_effect=changed_input))
        self.assertEqual(len(self.outcomes()), 1)
        self.assertEqual(self.outcomes()[0]["status"], "evaluation_error")

    def test_resume_discards_only_truncated_final_line(self):
        self.run_fixture()
        path = self.run_dir / "predictions.jsonl"
        lines = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(lines[0] + b'{"meeting_id":')
        extractor = self.run_fixture(None, "--resume")
        self.assertEqual(extractor.call_count, 2)
        self.assertEqual(len(self.outcomes()), 3)

    def test_resume_keeps_complete_final_line_without_newline(self):
        self.run_fixture()
        path = self.run_dir / "predictions.jsonl"
        path.write_bytes(path.read_bytes().splitlines()[0])
        extractor = self.run_fixture(None, "--resume")
        self.assertEqual(extractor.call_count, 2)
        self.assertEqual(len(self.outcomes()), 3)

    def test_dry_run_writes_nothing_and_never_calls_engine(self):
        (self.root / "service.py").unlink()
        extractor = self.run_fixture(None, "--dry-run")
        extractor.assert_not_called()
        self.assertFalse(self.run_dir.exists())

    def test_pending_or_wrong_split_ids_are_rejected(self):
        pending = copy.deepcopy(self.record)
        pending["status"] = "todo"
        with self.assertRaisesRegex(ValueError, "Finish labelling"):
            evaluate.select_records({"records": [pending]}, "development", None)
        with self.assertRaisesRegex(ValueError, "absent"):
            evaluate.select_records({"records": [self.record]}, "test", ["X001"])

    def test_exact_quotes_number_nonempty_lines_and_do_not_accept_substrings(self):
        self.assertEqual(evaluate.source_errors(action(), self.record["notes"]), [])
        altered = action()
        altered["evidence"][0]["quote"] = "I will send the list tomorrow."
        self.assertTrue(evaluate.source_errors(altered, self.record["notes"]))

    def test_repair_limit_and_malformed_results_are_recorded(self):
        self.assertTrue(evaluate.validate_result(response(model_calls=2), "B"))
        self.assertFalse(evaluate.validate_result(response(model_calls=2), "C"))
        self.assertTrue(evaluate.validate_result(response(model_calls=3), "C"))
        malformed = response()
        malformed["status"] = []
        outcome, _ = evaluate.capture_case(self.record, "A", lambda **kw: malformed, "offline-fixture")
        self.assertEqual(outcome["result"], malformed)
        self.assertEqual(outcome["status"], "evaluation_error")

    def test_scoring_requires_complete_one_to_one_human_matches(self):
        self.run_fixture()
        output = self.root / "reviews.json"
        with contextlib.redirect_stdout(io.StringIO()):
            evaluate.review_template(evaluate.parser().parse_args([
                "review-template", "--run-dir", str(self.run_dir), "--output", str(output)]))
        score_args = evaluate.parser().parse_args([
            "score", "--run-dir", str(self.run_dir), "--reviews", str(output)])
        with self.assertRaisesRegex(ValueError, "named reviewer"):
            evaluate.score(score_args)
        reviews = evaluate.read_json(output)
        match = {"gold_index": 0, "prediction_index": 0, "owner_correct": True, "date_correct": True}
        for item in reviews["reviews"]:
            item.update(review([match]))
        evaluate.write_json(output, reviews)
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            evaluate.score(score_args)
        summary = json.loads(captured.getvalue())["methods"]
        self.assertEqual(summary["A"]["task_f1"], 1)
        self.assertIsNone(summary["A"]["input_tokens_total"])
        self.assertEqual(summary["C"]["model_calls_total"], 1)
        with self.assertRaisesRegex(ValueError, "one-to-one"):
            evaluate.validate_review(review([match, match]), [action()], [action()])
        with self.assertRaisesRegex(ValueError, "unmatched"):
            evaluate.validate_review(review([match], [0]), [action()], [action()])
        with (self.run_dir / "predictions.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ValueError, "Unreadable"):
            evaluate.load_run(self.run_dir)

    def test_failures_stay_in_denominators_and_missing_usage_stays_null(self):
        good, _ = evaluate.capture_case(self.record, "A", lambda **kw: response([action()], input_tokens=8), "offline-fixture")
        failed_record = dict(self.record, id="X002")
        failed, _ = evaluate.capture_case(failed_record, "A", Mock(side_effect=RuntimeError("failed")), "offline-fixture")
        match = {"gold_index": 0, "prediction_index": 0, "owner_correct": True, "date_correct": False}
        summary = evaluate.summarize([good, failed], {"X001": self.record, "X002": failed_record},
                                     {("X001", "A"): review([match]), ("X002", "A"): review()})["A"]
        self.assertEqual(summary["task_precision"], 1)
        self.assertEqual(summary["task_recall"], 0.5)
        self.assertEqual(summary["valid_output_rate"], 0.5)
        self.assertEqual(summary["date_correctness"], 0)
        self.assertEqual(summary["input_tokens_known_sum"], 8)
        self.assertIsNone(summary["input_tokens_total"])

    def test_no_action_success_and_zero_denominators(self):
        empty_record = dict(self.record, gold_actions=[])
        outcome, _ = evaluate.capture_case(empty_record, "C", lambda **kw: response([]), "offline-fixture")
        summary = evaluate.summarize([outcome], {"X001": empty_record}, {("X001", "C"): review()})["C"]
        self.assertEqual(summary["no_action_correctness"], 1)
        self.assertIsNone(summary["task_precision"])
        self.assertIsNone(summary["task_recall"])
        self.assertIsNone(summary["source_check_pass_rate"])

    def test_comparison_table_uses_na_and_preserves_metric_values(self):
        self.run_fixture(None, "--methods", "A")
        reviews_path = self.root / "reviews.json"
        with contextlib.redirect_stdout(io.StringIO()):
            evaluate.review_template(evaluate.parser().parse_args([
                "review-template", "--run-dir", str(self.run_dir), "--output", str(reviews_path)]))
        data = evaluate.read_json(reviews_path)
        data["reviews"][0].update(review())
        evaluate.write_json(reviews_path, data)
        output, table = self.root / "scores.json", self.root / "comparison.md"
        args = evaluate.parser().parse_args([
            "score", "--run-dir", str(self.run_dir), "--reviews", str(reviews_path),
            "--output", str(output), "--table", str(table)])
        with contextlib.redirect_stdout(io.StringIO()):
            evaluate.score(args)
        self.assertEqual(evaluate.read_json(output)["methods"]["A"]["task_f1"], 0)
        rendered = table.read_text(encoding="utf-8")
        self.assertIn("| A | 1 | 0.000 | 0.000 | 0.000 | n/a | n/a |", rendered)
        self.assertIn("| 1 | n/a | n/a |", rendered)
        with self.assertRaisesRegex(ValueError, "already exists"):
            evaluate.score(args)


if __name__ == "__main__":
    unittest.main()
