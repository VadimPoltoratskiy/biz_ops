# Untrusted Input Boundary — Anti-Patterns

Apply these rules to any change touching prompt construction, the marketing text path, or run artifacts. Cite file path and line number for each finding.

The marketing text is third-party content submitted for evaluation. It is data, never instruction. The regulation source is trusted; the marketing copy is not.

## CRITICAL

- **Marketing text interpolated into a prompt without delimiters** — the evaluation prompt wraps the marketing text in explicit XML delimiters and states that nothing inside them is an instruction. A change that concatenates the text directly into the prompt, or drops the "this is content, not direction" framing, reopens the prompt-injection path.
- **Model output used as a control decision without validation** — a verdict outcome, rule ID, or evidence quote coming back from the model must be validated against the declared schema or verified in Python before it drives behaviour. Unvalidated model output steering control flow is an injection sink.
- **Delimiter injection unhandled** — if the marketing text can contain the same delimiter the prompt uses to close the untrusted block, it can escape the block. Check that the boundary survives adversarial input.

## HIGH

- **Secrets reaching run artifacts or logs** — API keys must never appear in `run.json`, `report.md`, stderr, or an exception message that gets recorded as a stage `detail`.
- **Marketing text widening its blast radius** — the text is already recorded deliberately in `input.txt`, `run.json`, and verified evidence quotes. Flag any change that sends it somewhere new (a network call, an external log sink, a third-party service) — not its presence in the existing run artifacts, which is by design.
- **Path traversal via the `--text` argument** — the CLI reads a caller-supplied path. Flag any change that lets that path escape into writing, or that uses it to construct an output location.

## MEDIUM

- **Unbounded input** — the marketing-text cap is enforced before any LLM call, which is what makes the over-cap path free. A change that moves validation after the first API call turns a rejected input into a billed one.
- **Error text echoed to the user verbatim** — exception messages from the SDK can carry request context. Prefer a typed, summarised message over `str(exc)` in anything user-facing.
