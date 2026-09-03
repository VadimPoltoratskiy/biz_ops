# Pipeline Architecture — Anti-Patterns

Apply these rules to every changed file under `src/compliance_agent/`. Cite file path and line number for each finding.

The design rule the whole package rests on: stages are pure input→output functions, and `pipeline.py` is the only module that sequences them, catches their errors, records stage results, and decides the exit code.

## CRITICAL

- **A stage module writes to disk** — `ingest.py`, `decompose.py`, `evaluate.py`, and `report.py` must not write files. All run persistence goes through `runlog.py`, called from `pipeline.py`. A stage that writes artifacts breaks the guarantee that a failed run still records what it managed to produce.
- **Exit-code precedence broken** — the contract is fail-safe: 2 (incomplete) wins over 1 (findings), which wins over 0. Any change that lets a run with an `error` verdict return 0 or 1 is a correctness breach, not a style issue.
- **Evidence quote not verified in Python** — `evaluate.py` must confirm that `evidence_quote` is an exact substring of the marketing text before it is stored. Trusting the model's quote lets a fabricated citation reach the report.
- **Stage failure returns without persisting** — every failure path in `run_check` must attempt `_save_incomplete_run` before returning 2. A bare `return 2` loses the audit trail for the run that most needs one.

## HIGH

- **A prompt string outside `prompts.py`** — all prompt text lives in `prompts.py`. An inline prompt in `llm.py`, `decompose.py`, or `evaluate.py` splits the surface that has to be reviewed when prompt behaviour changes.
- **`models.py` imports a sibling module** — it is the shared data layer and must import nothing from its siblings. Any `from compliance_agent.<sibling>` there creates a cycle.
- **Per-rule failure not isolated** — one rule's evaluation error must not sink the run; it becomes an `error` verdict. But a wholesale failure (every rule errored) must not be recorded as a successful stage either.
- **More than one `append_history` call per run** — the run log contract is exactly one history line per run. Two calls double-count the run in `history.jsonl`.

## MEDIUM

- **Retry logic outside `llm.py`** — retry/backoff and the auth/bad-request/retry-exhausted error taxonomy belong in `llm.py`. Duplicating them in a caller means two policies that drift.
- **Silent exception swallowing outside the best-effort artifact write** — `_save_incomplete_run` deliberately swallows to avoid masking the original failure. Anywhere else, a bare `except: pass` hides a real fault.
- **Config read outside `config.py`** — environment variables are read once into the frozen `Settings`. A stray `os.environ.get` elsewhere bypasses it.
