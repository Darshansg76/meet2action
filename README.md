# Meet2Action

Meet2Action turns short English meeting notes into agreed tasks with owners, deadline phrases, resolved dates, exact source quotes, and warnings. The Streamlit app and the A/B/C evaluator both use the Gemini extraction engine in `service.py`.

The dataset has 60 labelled meetings and five empty-action test cases. The engine and app are integrated. The live benchmark and prediction reviews require a configured Gemini key.

See [Project Guide and Viva](PROJECT_GUIDE.md) for the architecture, the four teammates’ assignments and contributions, the demo, and the viva question bank.

## Run locally

Use Python 3.10 or newer. From PowerShell in the repository folder, run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python tools/check_data.py
python -m unittest discover -s tests
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` in place of `python` in the commands.

Copy `.env.example` to `.env` if you do not already have one. Set `GEMINI_API_KEY` locally and use a Gemini model ID available to your account in `GEMINI_MODEL`. The example uses the engine's configured default. The Word allocation document and local `.env` are ignored by Git.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
python -m streamlit run app.py
```

In the app, use **Load example**, then **Extract actions**. Inspect the source evidence and download JSON or CSV. Editing notes, the date or method requires a fresh extraction before downloading again.

## Run the evaluation

Check both selections offline. Dry runs make no model calls.

```powershell
python evaluate.py run --split development --dry-run
python evaluate.py run --split test --dry-run
```

Set `$meet2actionModel` to the same model ID used in `.env`. Start with two development meetings. Confirm the responses and evidence, then run the full development split. Lock prompts, dataset, and model configuration before running the held-out split.

```powershell
$meet2actionModel = "gemini-3.8-flash"
python evaluate.py run --split development --ids M048 M049 --model $meet2actionModel --output-dir results/runs/smoke
python evaluate.py run --split development --model $meet2actionModel --output-dir results/runs/development-final
python evaluate.py run --split test --model $meet2actionModel --output-dir results/runs/test-final
```

Use `--resume` with the original run command to skip saved outcomes after an interruption. Use a new output directory if inputs or settings change. The full test run saves 120 outcomes. Development plus test requires 150 initial calls and allows up to 50 repair calls.

Generate the review file after the run completes. The four members review ten held-out meetings each across all three methods. Match predictions to gold tasks by meaning using zero-based indices. Each pair needs an owner and date judgment. Mark unsupported unmatched predictions, record reviewer names, and set `complete` on each outcome, including failures.

```powershell
python evaluate.py review-template --run-dir results/runs/test-final --output results/runs/test-final/reviews.json
python evaluate.py score --run-dir results/runs/test-final --reviews results/runs/test-final/reviews.json --output results/runs/test-final/summary.json --table results/runs/test-final/comparison.md
```

See the [annotation guide](ANNOTATION_GUIDE.md) for labelling rules and the [results folder](results/README.md) for evaluation artifacts.
