"""Offline tests for the extraction service using a mocked Gemini provider."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import service


NOTES = (
    "Chair: Welcome.\n"
    "Aditi: I will send the list tomorrow.\n"
    "Chair: Thanks."
)
VALID_ACTION = {
    "task": "Send the list",
    "owner": "Aditi",
    "deadline_text": "tomorrow",
    "ambiguities": [],
    "evidence": [
        {"line_id": "L02", "quote": "Aditi: I will send the list tomorrow."}
    ],
}


def provider_response(actions, prompt_tokens=8, output_tokens=5):
    return SimpleNamespace(
        text=json.dumps({"actions": actions}),
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
        ),
    )


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.environment_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.environment_directory.cleanup)
        self.env_path_patch = patch(
            "service._ENV_PATH",
            Path(self.environment_directory.name) / ".env",
        )
        self.env_path_patch.start()
        self.addCleanup(self.env_path_patch.stop)
        self.environment = patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-key-not-a-credential", "GEMINI_MODEL": "fixture-model"},
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.create_client_function = service._create_client
        self.client_patch = patch("service._create_client", return_value=SimpleNamespace(close=lambda: None))
        self.mock_client = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def test_dotenv_configuration_loads_from_service_relative_path_without_leaking_key(self):
        secret_fixture = "offline-dotenv-secret-fixture"
        with tempfile.TemporaryDirectory() as directory:
            dotenv_path = Path(directory) / ".env"
            dotenv_path.write_text(
                f"GEMINI_API_KEY={secret_fixture}\nGEMINI_MODEL=dotenv-fixture-model\n",
                encoding="utf-8",
            )
            with patch("service._ENV_PATH", dotenv_path), patch.dict(
                os.environ, {}, clear=True
            ):
                api_key, model = service._load_configuration()
                self.assertEqual(api_key, secret_fixture)
                self.assertEqual(model, "dotenv-fixture-model")

                with (
                    patch("service._create_client") as create_client,
                    patch(
                        "service._generate_content",
                        side_effect=RuntimeError("offline provider fixture failure"),
                    ),
                ):
                    result = service.extract_actions(NOTES, method="A")

            create_client.assert_called_once_with(secret_fixture)
            self.assertEqual(result["status"], "api_error")
            self.assertNotIn(secret_fixture, json.dumps(result))

    def test_missing_model_uses_configured_default(self):
        with tempfile.TemporaryDirectory() as directory:
            dotenv_path = Path(directory) / ".env"
            dotenv_path.write_text(
                "GEMINI_API_KEY=offline-key-fixture\n",
                encoding="utf-8",
            )
            with patch("service._ENV_PATH", dotenv_path), patch.dict(
                os.environ, {}, clear=True
            ):
                _, model = service._load_configuration()
        self.assertEqual(model, "gemini-3.8-flash")

    def test_a_b_and_c_use_independent_prompts_and_one_call_when_valid(self):
        prompts = {}
        for method in ("A", "B", "C"):
            with self.subTest(method=method):
                with patch(
                    "service._generate_content",
                    return_value=provider_response([VALID_ACTION]),
                ) as generate:
                    result = service.extract_actions(NOTES, "2026-11-11", method)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["meta"]["model_calls"], 1)
                self.assertEqual(generate.call_count, 1)
                prompt = generate.call_args.args[2]
                prompts[method] = prompt
                self.assertIn("Meeting notes:", prompt)
                if method == "A":
                    self.assertNotIn("Few-shot examples:", prompt)
                else:
                    self.assertIn("Few-shot examples:", prompt)
                    self.assertIn("M031", prompt)
                self.assertEqual(result["actions"][0]["due_date"], "2026-11-12")
        self.assertEqual(prompts["B"], prompts["C"])

    def test_valid_empty_action_result(self):
        with patch("service._generate_content", return_value=provider_response([])):
            result = service.extract_actions(NOTES, method="B")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["actions"], [])

    def test_invalid_inputs_return_review_required_without_provider_calls(self):
        invalid_cases = [
            ("", None, "A"),
            ("\n \n", None, "B"),
            ("\n".join(f"Speaker {i}: update" for i in range(26)), None, "A"),
            ("x" * 6001, None, "B"),
            (NOTES, "2026-02-30", "A"),
            (NOTES, "11/11/2026", "A"),
            (NOTES, None, "D"),
        ]
        for notes, meeting_date, method in invalid_cases:
            with self.subTest(method=method, meeting_date=meeting_date, length=len(notes)):
                self.mock_client.reset_mock()
                with patch("service._generate_content") as generate:
                    result = service.extract_actions(notes, meeting_date, method)
                self.assertEqual(result["status"], "review_required")
                self.assertEqual(result["actions"], [])
                self.mock_client.assert_not_called()
                generate.assert_not_called()

    def test_date_normalization_boundaries_and_warning_order(self):
        date_lines = [
            "Aditi: I will send it today.",
            "Bala: I will send it tomorrow.",
            "Chetan: I will send it in 30 days.",
            "Deepa: I will send it in 31 days.",
            "Esha: I will send it Friday.",
        ]
        actions = []
        for index, (owner, deadline) in enumerate(
            [
                ("Aditi", "today"),
                ("Bala", "tomorrow"),
                ("Chetan", "in 30 days"),
                ("Deepa", "in 31 days"),
                ("Esha", "Friday"),
            ],
            start=1,
        ):
            actions.append(
                {
                    "task": "Send it",
                    "owner": owner,
                    "deadline_text": deadline,
                    "ambiguities": [],
                    "evidence": [
                        {"line_id": f"L{index:02d}", "quote": date_lines[index - 1]}
                    ],
                }
            )
        notes = "\n".join(date_lines)
        with patch("service._generate_content", return_value=provider_response(actions)):
            result = service.extract_actions(notes, "2026-12-01", "A")
        self.assertEqual(
            [action["due_date"] for action in result["actions"]],
            ["2026-12-01", "2026-12-02", "2026-12-31", None, None],
        )
        self.assertEqual(
            result["actions"][3]["warnings"], ["unresolved_deadline"]
        )

        no_date_notes = "Chair: Welcome.\nAditi: I will send the list today.\nChair: Thanks."
        relative_action = dict(
            VALID_ACTION,
            deadline_text="today",
            evidence=[{"line_id": "L02", "quote": "Aditi: I will send the list today."}],
        )
        with patch(
            "service._generate_content",
            return_value=provider_response([relative_action]),
        ):
            no_date = service.extract_actions(no_date_notes, method="A")
        self.assertIsNone(no_date["actions"][0]["due_date"])
        self.assertEqual(no_date["actions"][0]["warnings"], ["unresolved_deadline"])

    def test_relative_date_overflow_is_unresolved_not_an_exception(self):
        notes = "Chair: Welcome.\nAditi: I will send the list tomorrow.\nChair: Thanks."
        with patch("service._generate_content", return_value=provider_response([VALID_ACTION])):
            result = service.extract_actions(notes, "9999-12-31", "A")
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["actions"][0]["due_date"])
        self.assertEqual(result["actions"][0]["warnings"], ["unresolved_deadline"])

    def test_iso_deadline_survives_without_meeting_date(self):
        notes = "Chair: Welcome.\nAditi: Send the list on 2026-12-24.\nChair: Thanks."
        action = dict(
            VALID_ACTION,
            deadline_text="2026-12-24",
            evidence=[{"line_id": "L02", "quote": "Aditi: Send the list on 2026-12-24."}],
        )
        with patch("service._generate_content", return_value=provider_response([action])):
            result = service.extract_actions(notes, method="A")
        self.assertEqual(result["actions"][0]["due_date"], "2026-12-24")

    def test_provider_helper_uses_json_mode_and_pydantic_schema(self):
        generate_content = Mock(return_value=object())
        client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        service._generate_content(client, "fixture-model", "prompt")
        config = generate_content.call_args.kwargs["config"]
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertIs(config.response_schema, service._DraftResponse)

    def test_create_client_configures_http_timeout(self):
        with patch("service.genai.Client") as create_client:
            self.create_client_function("fixture-api-key")

        create_client.assert_called_once_with(
            api_key="fixture-api-key",
            http_options=service.types.HttpOptions(timeout=60000),
        )

    def test_invalid_evidence_is_rejected_for_a_and_b(self):
        invalid = dict(VALID_ACTION)
        invalid["evidence"] = [{"line_id": "L02", "quote": "I will send the list tomorrow."}]
        for method in ("A", "B"):
            with self.subTest(method=method):
                with patch("service._generate_content", return_value=provider_response([invalid])) as generate:
                    result = service.extract_actions(NOTES, method=method)
                self.assertEqual(result["status"], "review_required")
                self.assertEqual(result["actions"], [])
                self.assertEqual(generate.call_count, 1)

    def test_c_repairs_invalid_action_and_preserves_valid_action(self):
        original_valid = dict(VALID_ACTION)
        invalid = {
            "task": "Review the list",
            "owner": None,
            "deadline_text": None,
            "ambiguities": [],
            "evidence": [{"line_id": "L02", "quote": "wrong quote"}],
        }
        repaired_valid = dict(VALID_ACTION, task="Changed valid action")
        repaired_invalid = dict(
            invalid,
            evidence=[{"line_id": "L03", "quote": "Chair: Thanks."}],
        )
        with patch(
            "service._generate_content",
            side_effect=[
                provider_response([original_valid, invalid]),
                provider_response([repaired_valid, repaired_invalid], 9, 6),
            ],
        ) as generate:
            result = service.extract_actions(NOTES, "2026-11-11", "C")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["meta"]["model_calls"], 2)
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(result["actions"][0]["task"], "Send the list")
        self.assertIn("your previous answer was rejected", generate.call_args.args[2].lower())
        self.assertEqual(result["meta"]["input_tokens"], 17)
        self.assertEqual(result["meta"]["output_tokens"], 11)

    def test_c_makes_only_one_repair_and_discards_all_actions_if_still_invalid(self):
        invalid = dict(
            VALID_ACTION,
            evidence=[{"line_id": "L02", "quote": "not the source"}],
        )
        with patch(
            "service._generate_content",
            side_effect=[
                provider_response([invalid]),
                provider_response([invalid]),
            ],
        ) as generate:
            result = service.extract_actions(NOTES, method="C")
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["meta"]["model_calls"], 2)
        self.assertEqual(generate.call_count, 2)

    def test_c_repair_can_remove_an_unsupported_action(self):
        invalid = dict(
            VALID_ACTION,
            evidence=[{"line_id": "L02", "quote": "not the source"}],
        )
        with patch(
            "service._generate_content",
            side_effect=[provider_response([invalid]), provider_response([])],
        ) as generate:
            result = service.extract_actions(NOTES, method="C")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["meta"]["model_calls"], 2)
        self.assertEqual(generate.call_count, 2)

    def test_c_repair_cannot_add_new_actions(self):
        invalid = dict(
            VALID_ACTION,
            evidence=[{"line_id": "L02", "quote": "not the source"}],
        )
        with patch(
            "service._generate_content",
            side_effect=[
                provider_response([invalid]),
                provider_response([VALID_ACTION, VALID_ACTION]),
            ],
        ):
            result = service.extract_actions(NOTES, method="C")
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["actions"], [])

    def test_c_will_not_drop_valid_actions_to_meet_the_eight_action_limit(self):
        too_many = [dict(VALID_ACTION, task=f"Send the list {index}") for index in range(9)]
        with patch(
            "service._generate_content",
            side_effect=[provider_response(too_many), provider_response(too_many[:8])],
        ):
            result = service.extract_actions(NOTES, method="C")
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["meta"]["model_calls"], 2)

    def test_api_errors_are_safe_and_do_not_leak_credentials(self):
        with patch(
            "service._generate_content",
            side_effect=RuntimeError("test-key-not-a-credential"),
        ):
            result = service.extract_actions(NOTES, method="B")
        self.assertEqual(result["status"], "api_error")
        self.assertEqual(result["actions"], [])
        serialized = json.dumps(result)
        self.assertNotIn("test-key-not-a-credential", serialized)
        self.assertEqual(result["meta"]["model_calls"], 1)

    def test_unavailable_provider_text_returns_review_required(self):
        class ResponseWithoutText:
            usage_metadata = None

            @property
            def text(self):
                raise RuntimeError("no text available")

        with patch("service._generate_content", return_value=ResponseWithoutText()):
            result = service.extract_actions(NOTES, method="A")
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["meta"]["raw_responses"], [""])

    def test_missing_api_key_returns_api_error_without_client_creation(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            self.mock_client.reset_mock()
            result = service.extract_actions(NOTES)
        self.assertEqual(result["status"], "api_error")
        self.assertEqual(result["actions"], [])
        self.mock_client.assert_not_called()

    def test_credentials_in_notes_or_model_errors_are_never_returned(self):
        secret = os.environ["GEMINI_API_KEY"]
        self.mock_client.reset_mock()
        result = service.extract_actions(NOTES + "\n" + secret)
        self.assertEqual(result["status"], "review_required")
        self.assertNotIn(secret, json.dumps(result))
        self.mock_client.assert_not_called()

        model_action = dict(VALID_ACTION)
        model_action[secret] = "unexpected field"
        with patch(
            "service._generate_content",
            return_value=provider_response([model_action]),
        ):
            result = service.extract_actions(NOTES, method="A")
        self.assertEqual(result["status"], "review_required")
        self.assertNotIn(secret, json.dumps(result))

    def test_contract_keys_and_warnings_are_exact(self):
        notes = "Chair: Welcome.\nAditi: I will send the list tomorrow.\nChair: The dashboard is due Friday."
        ownerless = {
            "task": "Check the dashboard",
            "owner": None,
            "deadline_text": None,
            "ambiguities": ["No owner is named.", "No deadline is stated."],
            "evidence": [{"line_id": "L02", "quote": "Aditi: I will send the list tomorrow."}],
        }
        unresolved = {
            "task": "Thank the chair",
            "owner": None,
            "deadline_text": "Friday",
            "ambiguities": ["No owner is named."],
            "evidence": [{"line_id": "L03", "quote": "Chair: The dashboard is due Friday."}],
        }
        with patch(
            "service._generate_content",
            return_value=provider_response([ownerless, unresolved]),
        ):
            result = service.extract_actions(notes, method="A")
        self.assertEqual(set(result), {"status", "actions", "message", "meta"})
        self.assertEqual(
            set(result["actions"][0]),
            {"task", "owner", "deadline_text", "due_date", "evidence", "warnings"},
        )
        self.assertEqual(
            result["actions"][0]["warnings"], ["missing_owner", "missing_deadline"]
        )
        self.assertEqual(
            result["actions"][1]["warnings"], ["missing_owner", "unresolved_deadline"]
        )
        self.assertEqual(
            set(result["meta"]),
            {
                "latency_ms",
                "model_calls",
                "input_tokens",
                "output_tokens",
                "model",
                "raw_responses",
                "validation_errors",
            },
        )


if __name__ == "__main__":
    unittest.main()
