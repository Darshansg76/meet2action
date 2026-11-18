# Meet2Action Project Guide and Viva

Meet2Action turns short English meeting notes into a list of agreed follow-up tasks. Each result keeps the owner when one is supported, the deadline phrase as it was spoken, a calendar date when the rules can resolve it, and exact source evidence. The app lets someone check the result before exporting it. The evaluator compares three prompting methods on the same set of meetings.

## What the team built

| Part | Deliverable | How to verify |
| --- | --- | --- |
| Extraction | One Gemini-backed engine shared by the app and evaluator. It validates model output, checks evidence, resolves supported dates, and optionally repairs an invalid response. | `service.py`, `tests/test_service.py` |
| App | Streamlit notes form, optional meeting date, A/B/C selector, action table, source quotes, diagnostics, and CSV and JSON downloads. | `app.py`; run with `python -m streamlit run app.py` |
| Prompts and dataset | Three fixed prompt examples, written annotation rules, and 60 labelled synthetic meetings with 5 held-out cases per category. | `prompts.json`, `data/meetings.json`, `ANNOTATION_GUIDE.md` |
| Evaluation | Runs each method independently, saves progress and failures, prepares reviews, and scores the reviewed outcomes. | `evaluate.py`, `tests/test_evaluate.py`, `results/README.md` |

The code, labelled dataset, and local setup are in place. The live comparison needs a Gemini credential and model access. The project contains no measured A/B/C scores yet. Offline tests verify the implementation without measuring model quality.

## Member assignments and contributions

### Dhanraj — extraction engine

- **Assigned.** Own `service.py`, the public extraction function, schema, Gemini requests, validation, date resolution, the one-repair policy, and call diagnostics. Write M001–M015 and independently review M016–M018. Explain the engine and demonstrate an ambiguous or rejected result.
- **Delivered.** Added the Gemini service and engine tests, then corrected the SDK structured-response schema. Authored and labelled all 15 assigned meetings. The engine uses the fixed prompts for A/B/C, produces consistent response fields for success and failure, and reports elapsed time, logical calls, available token usage, and validation errors. Commits `0218247`, `34cbaf8`, and `7f54be8`.
- **Review and presentation.** Codex compared the labels for M016–M018 assigned to Dhanraj. The M018 task was corrected to deciding who will audit the permissions. Dhanraj is the primary speaker for model requests, schema and evidence checks, date handling, repair, and engine failure behavior.

### Vismaya — app and downloads

- **Assigned.** Own `app.py`, the meeting form, results table, evidence display, and JSON and CSV exports. Write M016–M030 and independently review M031–M033. Prepare the interface walkthrough and demonstrate a download.
- **Delivered.** Added the Streamlit interface, handoff to the shared engine, export formatting, input validation, stale results, and status messages. Authored and labelled all 15 assigned meetings and recorded reviews for M031–M033. Commits `1bbc9b8` and `ac5f6c7`.
- **Integration changes.** The final integration connects the app directly to the service and makes enabling or disabling the optional date invalidate the previous downloads. The dataset cleanup adds one real action to M025 and removes the two reused prompt-example speaker names from M023 and M024.
- **Presentation.** Vismaya is the primary speaker for the UI flow, what each result column means to the user, session state, stale downloads, and export behavior.

### Dhanush — prompts and dataset coordination

- **Assigned.** Own `prompts.json`, `ANNOTATION_GUIDE.md`, the dataset allocation, and the overall labelling policy. Write M031–M045, coordinate contributions and reviews, and independently review M046–M048. Resolve disputed labels and explain the prompt and data design.
- **Delivered.** Created the prompt rules, few-shot cases, repair rules, 60-ID allocation, guide, and dataset checker. Authored and labelled all 15 assigned meetings and recorded reviews for M046–M048. Commits `74a1ed8` and `2ab2805`.
- **Presentation.** Dhanush is the primary speaker for what qualifies as an action, label and evidence rules, category balance, dataset splits, example selection, and review disagreements.

### Darshan — evaluation and integration

- **Assigned.** Own `evaluate.py`, setup files, README, evaluation results, and final assembly. Write M046–M060 and independently review M001–M003. Connect the app, engine, prompts, and evaluation. Coordinate review of the predictions, prepare the results table, and assemble the final report and demo.
- **Delivered.** Added all 15 assigned labels, the A/B/C evaluator, review template generation, resume support, scoring, initial setup files, and evaluator tests. Commit `30bde62`. Codex compared the labels for M001–M003 assigned to Darshan and recorded the results. The final integration also added the app dependencies, completed the configuration template, and checked the app and engine together offline.
- **Remaining handoff.** Darshan coordinates the live development and held-out runs, allocates ten held-out meetings to each member for prediction review, and generates the final comparison once those judgments exist. Darshan presents the architecture, run workflow, metrics, results status, and integration.

The dataset keeps the original review assignments. Six entries identify Codex as the reviewer of the labels on behalf of the assigned member. The other six have the recorded teammate reviews. `agreed` records agreement with the original label. M018 records a difference and the correction. These label reviews are separate from human review of model predictions after a live run.

## End-to-end flow

1. The user enters notes, optionally enables a meeting date, selects A, B, or C, and clicks **Extract actions**. The app defaults to C. The Python function itself defaults to B for callers that omit the method.
2. The app calls `extract_actions(notes, meeting_date, method)`. The evaluator calls the same function with the notes and date from the selected dataset record.
3. The service checks the input and numbers non-empty lines as L01, L02, and so on. Live input accepts 1–25 non-empty lines and up to 6,000 characters. Authored dataset entries require at least three non-empty lines. All responses allow at most eight actions.
4. The service builds the prompt and asks Gemini for structured JSON. The intermediate model fields are `task`, `owner`, `deadline_text`, `ambiguities`, and `evidence`. Pydantic checks shape and types. The service checks that each quoted line matches the numbered source and that any supplied owner and deadline appear in the evidence.
5. For an invalid response, A and B return a result for review. C can make one repair request using the previous response and validation errors. It revalidates the result and preserves valid original actions by position. This sets an upper bound of two logical requests for C.
6. Python computes final `due_date` and `warnings` after validation. The final action fields are `task`, `owner`, `deadline_text`, `due_date`, `evidence`, and `warnings`. Each evidence item holds `line_id` and the complete source `quote`.
7. The app displays accepted actions, evidence, and diagnostics. Exports use the saved result. On success, the evaluator saves the same fields alongside the meeting ID and method. It also saves unsuccessful outcomes and available diagnostics.

| Method | Prompt | Responses to invalid output | Logical requests per extraction |
| --- | --- | --- | --- |
| A | Shared instructions | Return for review | One initial request |
| B | Instructions plus M031, M032, M033 | Return for review | One initial request |
| C | Same initial prompt as B, run independently | Attempt one repair and validate again | One initial request and at most one repair |

An early input or configuration error can return before any provider request. `model_calls` counts logical requests from the engine. The SDK may handle transport retries separately. The service response has `status`, `actions`, `message`, and `meta`. Metadata includes `latency_ms`, `model_calls`, `input_tokens`, `output_tokens`, `model`, `raw_responses`, and `validation_errors`.

| Status | Meaning | App behavior |
| --- | --- | --- |
| `ok` with actions | Output passed the contract and source checks | Shows the action table, evidence, diagnostics, and fresh downloads |
| `ok` with an empty list | Output passed validation and contains no actions | Shows the no-action message and allows an empty result export |
| `review_required` | Input needs correction or the generated output could not be accepted | Shows the explanation and available validation details |
| `api_error` | Configuration or initial provider request failed | Preserves the input and shows the failure message |

The validation checks source grounding at the text level. A name in a quote may still be a person mentioned in conversation rather than the person responsible. Judging task meaning, who agreed to own it, and which clause a deadline applies to remains part of the human evaluation.

## Dataset and annotation

| Member | Authored records | Independent review assignment |
| --- | --- | --- |
| Dhanraj | M001–M015 | M016–M018 |
| Vismaya | M016–M030 | M031–M033 |
| Dhanush | M031–M045 | M046–M048 |
| Darshan | M046–M060 | M001–M003 |

Each member wrote five example or development cases and ten held-out test cases. The 10 example cases are a pool. B and C use only M031–M033. The 10 development cases support debugging and prompt decisions. Keep the 40 test cases held out from prompt changes. A `family_id` keeps related variants together in one split.

| Test category | What it checks |
| --- | --- |
| `clear_task` | One or two clear commitments with named owners |
| `several_tasks` | Three to eight actions, covering multiple people |
| `missing_owner` | An agreed task without a clear assignee |
| `unclear_date` | A stated time the date rules cannot resolve |
| `non_action` | Finished work and suggestions mixed with one or two real test actions |
| `correction` | A later statement replaces a prior owner or deadline |
| `speaker_ambiguity` | A commitment with an unclear speaker or a collective doer |
| `no_action` | Updates, suggestions, or completed work with zero committed actions |

The five empty held-out cases are M013, M028, M043, M058, and M060. M025 now has one clear action alongside finished work and a suggestion. The checker enforces empty test outputs only in `no_action` and verifies five cases per test category. Review M018 illustrates why a reviewer checks what the deadline modifies. The group promised to pick an auditor tomorrow. The audit itself has no stated completion date.

| Evidence in the notes | Final label rule |
| --- | --- |
| “I will” or a request followed by acceptance | Record the commitment and cite the lines that establish it |
| An agreed need without a named person | Keep the task with a null owner and `missing_owner` |
| Tentative ideas, finished work, and updates | Exclude them unless the notes also contain a separate future commitment |
| A correction or cancellation | Keep the final surviving commitment with the correcting and confirming evidence |
| No time phrase | Use null deadline and due date with `missing_deadline` |
| A time phrase without a resolvable date | Preserve the phrase, set due date to null, and add `unresolved_deadline` |

An ISO date such as 2026-11-20 resolves directly, even without a meeting date. With a meeting date, `today`, `tomorrow`, and `in N days` for N from 1 to 30 resolve using calendar arithmetic. Phrases like `Friday`, `next week`, or `end of month` remain visible as text. The warning order is missing owner first, then missing or unresolved deadline if applicable.

## Evaluation and results

Use the commands in [README.md](README.md) to set up the app and run evaluation. The evaluator uses the same inputs for each method, runs A, B, and C independently, and records the expected model and file fingerprints. It keeps copies of the prompts and dataset used. A resumed run reuses completed outcomes and requires the original inputs and configuration. Hold generation settings fixed across methods.

| Step | Lead | Record |
| --- | --- | --- |
| Run two development smoke cases and inspect the app flow | Darshan with Dhanraj and Vismaya | Saved responses with statuses and evidence |
| Run the ten development cases and settle prompt choices | Darshan with Dhanraj and Dhanush | 30 outcomes and the final prompt and engine configuration |
| Run all 40 held-out cases | Darshan | 120 meeting-method outcomes, including any failures |
| Review the predictions | All four members, ten meetings each | For every method, one-to-one task matches and owner and date judgments |
| Calculate and present scores | Darshan | JSON summary and Markdown comparison table from the reviewed outcomes |

A reviewer matches tasks by meaning and uses each predicted and gold task at most once. Correct owner and date judgments include correct nulls. A correct date judgment covers the original deadline phrase and the normalized due date. A duplicate can count as a false positive while referring to a real action. An unsupported prediction has no corresponding agreed action in the notes.

| Metric | Calculation or interpretation |
| --- | --- |
| Task precision | Matched tasks divided by all accepted predicted tasks |
| Task recall | Matched tasks divided by all gold tasks, including gold tasks from failed cases |
| Task F1 | Harmonic mean of precision and recall when those rates are defined |
| Owner and date correctness | Correct field judgments divided by matched tasks |
| Source-check pass rate | Accepted actions whose source lines and stated fields pass the independent text checks, divided by all accepted predictions |
| Valid-output rate | Accepted `ok` outcomes divided by all saved attempts |
| No-action correctness | Valid empty outputs on gold-empty meetings divided by the count of gold-empty meetings |
| Unsupported-action rate | Predictions marked unsupported by the reviewer divided by all accepted predictions |
| Median latency | Median evaluator wall-clock duration across all attempts for a method |
| Calls and tokens | Known usage and coverage, with complete totals only when all outcomes provide the count |

For example, three matches among four predictions and five gold tasks give precision 0.75, recall 0.60, and F1 about 0.667. This is an illustration of the calculation. The measured comparison table comes from saved live outcomes and completed prediction reviews. A zero denominator is displayed as `n/a`. No method ranking is available before the live run and reviews.

## Demo sequence

1. Darshan introduces the problem and the shared engine. Vismaya loads the example and shows the input controls.
2. Vismaya runs C, checks the task table against the notes, and downloads JSON and CSV. Darshan can explain how the evaluator invokes the same function for a dataset record.
3. Dhanraj explains how `tomorrow` resolves when the date is present and why `Friday` stays unresolved. Vismaya disables the date and shows that downloads require a new extraction.
4. Dhanush shows a no-action record such as M058 and uses a correction case such as M056 to explain final-owner evidence. Dhanraj explains what the engine does if it receives an invalid quotation.
5. Darshan shows the evaluator workflow, prediction-review fields, and comparison table if measured results are available. Report the current results status if the live benchmark is still pending.

## Viva questions and who answers them

### Overall project, integration, and evaluation — Darshan

| Viva question | Answer points |
| --- | --- |
| 1. What problem does Meet2Action solve? | It identifies agreed follow-ups in short English meeting notes and makes the evidence available to the person checking the result. |
| 2. What goes in and what comes out? | Raw notes, optional ISO meeting date, and A/B/C method. The output is a status with action records, evidence, warnings, and diagnostics. |
| 3. Who did what on the team? | Dhanraj built the engine, Vismaya built the app, Dhanush owned prompts and data coordination, and Darshan built evaluation and assembled the integration. All four labelled 15 meetings each. |
| 4. Walk through the architecture. | App or evaluator calls one service function. The service builds the prompt, calls Gemini, validates its JSON, and computes dates. The caller displays or records the result. |
| 5. Why share the extraction function? | It keeps validation, dates, prompts, and model behavior consistent between the demo and the benchmark. |
| 6. What was your own contribution? | M046–M060, evaluator, resume and score workflow, setup and integration, and coordination of the final comparison. |
| 7. How do you run the project on another machine? | Install the pinned requirements in a Python environment, copy the env template, set the key and model locally, run the checks, then start Streamlit. |
| 8. What did you verify locally? | Dataset rules, offline engine and evaluator behavior, app-to-engine wiring, statuses and downloads using controlled responses, and dry-run coverage. |
| 9. How do you keep A, B, and C comparable? | Use the same dataset notes, dates, and model settings. Run each method independently. Switch only the prompt method and whether one repair is allowed. |
| 10. Why run development before test? | Use development cases for troubleshooting and prompt decisions. Freeze the configuration before measuring performance on the held-out set. |
| 11. How many model calls should you expect? | 120 initial calls for the 40 test meetings. Up to 40 additional repairs. Development adds 30 initial calls and up to 10 repairs. |
| 12. What does the evaluator save for each run? | Manifest, dataset snapshot, prompt snapshot, and per-case prediction JSONL with status, timing, calls, available tokens, and validation errors. |
| 13. Why save after every outcome? | A stopped run keeps completed cases. A resume can skip them without spending calls again. |
| 14. Why fingerprint the inputs and code? | A hash can detect a changed dataset, prompts, service, or evaluator before a resume. This protects the comparison from mixed configurations. |
| 15. What is a one-to-one task match? | Pair by meaning. Each predicted action and each gold action can appear in at most one pair. |
| 16. When is an owner or date judged correct? | The chosen value matches the evidence and gold label, including a correctly unknown value. Date correctness covers the original phrase and resolved date. |
| 17. What are true positives, false positives, and false negatives? | Matches are true positives. Unmatched predictions are false positives. Unmatched gold tasks are false negatives. |
| 18. How do you calculate precision, recall, and F1? | Use matched divided by predicted, matched divided by gold, and the harmonic mean. Sum counts across meetings before computing the rates. |
| 19. How do failures affect the evaluation? | They stay in saved outcomes and the valid-output denominator. Their gold tasks count toward recall. They provide no accepted predictions. |
| 20. What is unsupported-action rate? | Human-identified unsupported predictions divided by accepted predicted actions. A repeated real task is a false positive but need not be unsupported. |
| 21. How is source correctness different from task correctness? | The source check compares evidence text and field presence. Human matching asks whether the evidence supports the action as an agreed commitment. |
| 22. How do you score no-action meetings? | Count valid empty outputs on gold-empty cases. A failed request with an empty actions list does not qualify. |
| 23. How do you handle undefined rates and missing token counts? | Use null in JSON and n/a in the table. Save known sums and coverage for usage and report complete totals only when all counts are present. |
| 24. Which method won? | Give the measured table after the live run and reviews. Until then, explain the trade-off between examples, repairs, cost, and latency without ranking the methods. |
| 25. Why is the work allocation document absent from Git? | It is a private planning document and is explicitly ignored. Public setup and role documentation live in Markdown. |
| 26. What are the main limitations? | A small synthetic English dataset, short input, limited date formats, provider access, and human review for semantic judgments. |

### Engine and model behavior — Dhanraj

| Viva question | Answer points |
| --- | --- |
| 27. What is the public interface of the engine? | `extract_actions(notes, meeting_date=None, method="B")` returns the shared JSON-compatible dictionary. |
| 28. Why use a structured response schema? | It asks Gemini for a predictable JSON structure. Local parsing and validation still check what was actually returned. |
| 29. What does Pydantic check? | Draft action fields, types, permitted nulls, and extra fields. Separate logic checks evidence, action counts, and dates. |
| 30. What is the difference between a draft and a final action? | The draft includes `ambiguities`. A final action uses Python-computed `due_date` and `warnings`. |
| 31. How do you number and validate evidence lines? | Skip blank lines, number the rest from L01, preserve text, and compare each quote to the full referenced line. |
| 32. What if evidence names a person who did not agree to own the task? | Name presence is only an automated check. The prompt requires agreement and the human reviewer checks whether the assignment is supported. |
| 33. How does `tomorrow` resolve? | Add one calendar day to the supplied ISO meeting date. Without that date, keep the phrase and leave the due date null. |
| 34. How do you handle an explicit ISO date without a meeting date? | If it is a valid calendar date, it resolves directly. |
| 35. Why leave Friday and next week unresolved? | The project has fixed date rules. Preserving vague language avoids making up a date. |
| 36. What is the difference between missing and unresolved deadlines? | Missing means the notes state no deadline. Unresolved means a phrase is present but the rules cannot convert it. |
| 37. How do you handle corrections? | The prompt directs the model to return the final surviving commitment with correcting evidence. The engine checks the returned evidence and the reviewer checks the meaning. |
| 38. When does method C repair? | After a draft fails structure or evidence validation. A valid initial response needs only one request. |
| 39. What goes into the repair prompt? | The original task context, the repair rules, prior output, and specific validation errors. |
| 40. How do you protect already-valid actions during repair? | The service retains valid original action positions, checks that they can be preserved, and validates the assembled result. |
| 41. What if a repair fails? | Return `review_required` with validation information and zero accepted actions. Do not keep retrying. |
| 42. When do you return `api_error`? | When configuration is missing or invalid, prompt loading fails, or the initial provider request cannot complete. |
| 43. What happens if the user sends empty notes or an invalid date? | The service rejects invalid input before calling Gemini and returns a review status with a description of the problem. |
| 44. How are time and token usage recorded? | Use a monotonic timer for elapsed time. Report SDK token metadata when available and include the repair call when applicable. |
| 45. Does the system train or fine-tune Gemini? | It sends prompts to the configured Gemini model. Few-shot examples guide inference within the request. |

### App and exports — Vismaya

| Viva question | Answer points |
| --- | --- |
| 46. Why Streamlit? | It provides Python forms, tables, session state, and downloads for a small app using the Python service. |
| 47. What happens after clicking Extract actions? | Check input limits, pass notes, date, and method to the service, then save and render the returned response. |
| 48. What lives in session state? | Current notes, selected date and method, saved response, and a flag that marks the saved response stale. |
| 49. How do you prevent exporting an outdated result? | Editing notes, toggling or changing the date, changing the method, or loading an example marks the old result stale and hides downloads until extraction runs again. |
| 50. What did the optional-date bug do? | The date checkbox changed the input but did not mark the result stale. Its change now triggers the same callback as the other controls. |
| 51. Does downloading trigger a new Gemini request? | Downloads serialize the stored result. The provider is called from Extract actions. |
| 52. Why support JSON and CSV? | JSON preserves nested evidence and metadata. CSV offers one row per action for spreadsheet use. |
| 53. How do you prevent CSV cells from being treated as formulas? | Prefix formula-like text with an apostrophe and use Python’s CSV writer for quoting. |
| 54. How do you display unknown fields? | The app uses unassigned, unspecified, or unresolved labels and shows warnings. The structured response preserves nulls. |
| 55. How does a no-action meeting appear? | The app shows a valid no-action message. The CSV has a header and no task rows. |
| 56. How do you handle a failed or suspicious model output? | Show the error or validation message, keep the original input, and only expose downloads for a fresh accepted result. |
| 57. Why was the fallback removed? | The real engine is now present. Import failures should be visible instead of presenting a successful sample output as a real extraction. |
| 58. Which evidence details can users inspect? | The referenced source line ID and the complete quote for each evidence item. |

### Prompts, data, and annotation — Dhanush

| Viva question | Answer points |
| --- | --- |
| 59. How did you design the dataset? | Synthetic short notes across eight categories. Four members wrote 15 cases each, with 10 examples, 10 development cases, and 40 held-out test cases overall. |
| 60. What are the eight test categories? | Clear task, several tasks, missing owner, unclear date, non-action, correction, speaker ambiguity, and no action. There are five test cases in each. |
| 61. Why use synthetic notes? | They make it possible to balance cases and label them without relying on private meeting transcripts. The trade-off is lower realism. |
| 62. What qualifies as an action? | A commitment, an accepted request, or an agreed task that needs doing. Mere brainstorming, finished work, and updates are excluded. |
| 63. What is the difference between non-action and no-action cases? | Held-out non-action cases mix distractors with a real commitment. No-action cases have no committed tasks at all. |
| 64. How do you label an agreed task without an assignee? | Keep the task, set owner to null, attach the line that supports it, and add `missing_owner`. |
| 65. What is a gold action? | A source-supported reference task with owner, original deadline, normalized due date, evidence, and warnings. The prediction is compared with it later. |
| 66. Which few-shot examples are used and why? | M031 has several tasks and a missing owner, M032 shows a correction, and M033 shows no actions. These teach distinct patterns from the example split. |
| 67. What is data leakage here? | Letting test cases or related variants influence prompt construction. Separate splits, family grouping, and removing reused few-shot speaker names reduce overlap. |
| 68. How does the checker differ from independent review? | The checker verifies structure, quotes, split counts, and date and warning rules. Reviewers resolve what the notes mean. |
| 69. What did the M018 review catch? | “Tomorrow” was attached to deciding who would audit the permissions. The audit itself was never assigned that deadline. |
| 70. Why did you change M025? | Its tentative blog idea and completed interviews produced an extra empty held-out case. Adding one explicit follow-up restores the distinction between the test categories. |
| 71. Which cases have no gold tasks on the test split? | M013, M028, M043, M058, and M060. |
| 72. What is the difference between the two kinds of review? | Independent label review checks the reference answers from the source notes. Prediction review uses the saved model output to match tasks and judge owner and date correctness. |
