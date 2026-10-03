# INSIGHTS.md — Regulation Compliance Agent

Session learnings accumulated over time. Treat as high-confidence guidance.
Read before working in this area. Update at session end via /engineering-insights.

---

## Patterns

### 2026-09-03 — Verifying `source_quote` as an exact substring is the cheapest hallucination check
Every `ExtractedRule` carries a verbatim `source_quote`, so checking each one against
`data/regulations/fca-cobs-4-financial-promotions.txt` with a plain `in` test tells you whether
the extractor cited or paraphrased. 73 of 75 passed; the 2 failures were whitespace
(one U+00A0, one dropped space), not invention.

### 2026-09-03 — The compliant control sample is what makes the breach findings meaningful
`samples/compliant.txt` returning `0 non-compliant` (run `20260903-165633-ca10af`) is the run
that proves the agent is not a yes-machine. Without it, `samples/hype.txt` finding 18 breaches
is indistinguishable from a tool that flags everything. Keep both samples in any demo.

### 2026-09-03 — Mock at the `compliance_agent.llm` boundary, never below it
All 106 tests run with no `ANTHROPIC_API_KEY` because `llm.py` is the only module importing the
Anthropic SDK. `tests/conftest.py` patches `llm.extract_rules` / `llm.evaluate_rule`; nothing
below that line is stubbed, so the stages under test are real.

### 2026-09-03 — `ExtractedRule`'s shape is what forces checkable rules
Requiring a binary `check_question`, a `precondition`, and concrete `failure_indicators` per
rule is what stops the model restating paragraphs — the schema does the work, not prose in the
prompt asking nicely. See `models.ExtractedRule` and the DROP criteria in `prompts.py`.

## Decisions

### 2026-09-03 — Rules cached by source hash, auto-invalidated
`decompose.get_rules` keys `rules/<source_id>.json` on a SHA-256 of the source file and
re-extracts on mismatch. Chosen over per-run extraction so a reviewer can audit decomposition
quality by reading the committed JSON with no API key and no spend.

### 2026-09-03 — Exit 2 beats exit 1: an incomplete check never reports as a pass
`report.compute_overall_outcome` gives `incomplete` precedence over `findings`, so any rule with
outcome `error` yields exit 2 even when confirmed breaches are also present. A compliance tool
that reports "pass" on a check it did not finish is worse than one that reports nothing.

### 2026-09-03 — `unclear` and `not-applicable` are first-class verdicts
Without an `unclear` outcome the model resolves ambiguity toward `compliant`, because that is
the safe answer when evidence points neither way. Both are defined in `models.VerdictOutcome`
and `unclear` maps to exit 1, not 0 — it is an escalation, not a soft pass.

### 2026-09-03 — One `AsyncAnthropic` client per fan-out, not per rule
`evaluate.evaluate_all_rules` constructs a single client and threads it into
`llm.evaluate_rule` as a parameter. A client per rule defeats connection pooling across a
75-call fan-out and leaks connections.

### 2026-09-03 — Confidence is categorical, not numeric
`models.ConfidenceLevel` is `high|medium|low`. A self-reported 0.0-1.0 score from an LLM implies
a precision it does not have and invites false thresholding downstream.

## Mistakes

### 2026-09-03 — `max_tokens=8192` truncated extraction; thinking tokens share the budget
`llm.extract_rules` ran at 8192 with `thinking={"type": "adaptive"}`. Thinking tokens draw from
the same budget, so the rules JSON was cut mid-string at ~25k chars and surfaced as a pydantic
`json_invalid` error — not an obvious limit error. Fixed with `EXTRACTION_MAX_TOKENS = 32000`,
a streamed call, and a `stop_reason == "max_tokens"` guard.

### 2026-09-03 — A deterministic failure burned the whole retry budget
The same truncation retried 4 times (5m39s, real spend) because the retry loop treats any
non-classified exception as retryable. Truncation is deterministic: the same prompt at the same
budget fails identically every time. `llm.extract_rules` now needs its explicit
`except LLMBadRequestError: raise` clause **before** the trailing `except Exception`, or the
fail-fast guard gets swallowed and retried anyway.

### 2026-09-03 — Per-rule isolation masked a total failure as a successful stage
With a fresh rules cache and an invalid key, all 75 evaluation calls failed, yet
`pipeline.run_check` recorded `evaluation success=True` because per-rule errors are isolated
into `error` verdicts by design. Now branches on `error_count == len(verdicts)` to record the
stage as failed with cause `fail-fast-nonretryable`. Verified by run `20260903-171621-fee917`.

### 2026-09-03 — Two plan-supplied values were wrong and had to be corrected during implementation
`PLAN-SPEC-01.md` specified `build-backend = "setuptools.backends.legacy:build"` (no such
backend; the correct one is `setuptools.build_meta`) and `config.repo_root()` as
`Path(__file__).parents[3]`, which overshoots the repo root by one — `parents[2]` is correct
from `src/compliance_agent/config.py`. Verify plan-supplied constants rather than transcribing.

## Context

### 2026-09-03 — The regulation source is a one-time manual extraction, not a runtime fetch
`data/regulations/fca-cobs-4-financial-promotions.txt` was scraped from five FCA Handbook HTML
pages on 2026-09-03 and committed; the provenance header lists the URLs. Nothing in the agent
fetches it. It measures 69,757 bytes but 69,534 Unicode code points — the two differ because of
em dashes and curly quotes, which matters when quoting a size figure.

### 2026-09-03 — A check costs ~$1.73; input tokens dominate
75 rules means 75 evaluation calls, ~218k input and ~26k output tokens on `claude-opus-5`
(run `20260903-165409-9aee76`). The ~2,900-token evaluation prompt is re-sent per rule, so
prompt caching on the stable system prefix is the highest-value optimisation available.

### 2026-09-03 — CySEC was ruled out on format, not relevance
CySEC DI87-09 governs CFD marketing directly but is published only as a Greek-language PDF,
failing the plain-text-source constraint. Circulars C108 and C334 do **not** cover marketing —
C334 is the Investor Compensation Fund. Do not revisit CySEC without an English text edition.

### 2026-09-03 — Patch targets differ by import style
`pipeline.py` does `from compliance_agent.evaluate import evaluate_all_rules`, so tests must
patch `compliance_agent.pipeline.evaluate_all_rules`. `decompose.py` calls `llm.extract_rules`
via the module reference, so that one is patched at `compliance_agent.llm.extract_rules`.

### 2026-09-03 — `evaluate_all_rules` owns the only `asyncio.run()` call site
Call it from ordinary synchronous tests and let it create its own loop; calling it from inside
a running loop raises `RuntimeError`. `pipeline.py` and `cli.py` contain no asyncio.

## Open Questions

### 2026-09-03 — Are 75 rules an over-extraction from 82 provisions?
The DROP criteria were meant to filter obligations binding internal processes rather than
marketing copy, yet nearly every provision survived. Spec criteria AC-14 and AC-15 (every
`check_question` genuinely binary and answerable from the text alone) are marked NOT VERIFIABLE
by automation in `verifications/Verification-SPEC-01.md` — they need a human to read the cache.

### 2026-09-03 — No measurement of whether the verdicts are correct
Nothing scores precision or recall of the compliance judgments. A few dozen blurbs labelled by
someone who knows COBS would turn "the output looks plausible" into a number, and would settle
the over-extraction question above.
