# Evaluation results

No live benchmark has been run. The engine, provider configuration and remaining labels are pending. Offline test fixtures are kept in `tests/` and are not evaluation measurements.

Use the commands in the root [README](../README.md) to create a run under `results/runs/`. Each run contains:

- `manifest.json`: frozen selection, model, declared settings and source fingerprints.
- `dataset.json` and `prompts.json`: exact input snapshots.
- `predictions.jsonl`: one saved outcome per meeting and method, including failures.
- `reviews.json`: human task matches and owner/date judgments after template completion.
- `summary.json` and `comparison.md`: scores generated from the completed reviews.

The run directory is ignored by Git. After the team reviews outputs for the final submission, place the selected complete run in a deliberately named submission directory and stage only the intended artifacts. Preserve raw outputs and failures so every score can be traced to its inputs and reviewed matches. Keep API keys, local `.env` files and the private Word work plan out of Git.

The submission comparison must cover all 40 test meetings with A, B and C (120 outcomes). Any smoke or development result must be labelled as such. Use `n/a` for zero-denominator scores and unavailable usage; never fill in unmeasured values.
