#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colour helpers ────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
    BOLD='\033[1m';   RST='\033[0m'
else
    RED=''; GRN=''; YLW=''; BOLD=''; RST=''
fi

# ── Globals ───────────────────────────────────────────────────────────────────
YES=0           # set to 1 by --yes
FREE_ONLY=0     # set to 1 when api_key is missing
CURRENT_MODEL=""  # resolved during preflight step (c)
COST_ESTIMATE=""  # computed by compute_cost_estimate after CURRENT_MODEL is set
ACTUAL_SPEND=""   # set by compute_actual_spend after a billed run
CHECK_FILE=""     # set by 'check' subcommand; dispatched after full preflight

# ── Usage / help ─────────────────────────────────────────────────────────────
print_usage() {
    cat <<'EOF'
Usage:
  ./run.sh [--yes] [--help]
  ./run.sh test
  ./run.sh check <file>
  ./run.sh demo

Options:
  (none)        Interactive menu; requires a TTY.
  --yes         Skip per-operation confirmation prompts (non-TTY safe).
  test          Run the test suite and exit; bypasses the TTY gate.
  check <file>  Run a compliance check on <file> (path relative to cwd); exit
                with the agent's exit code (0/1/2). Requires consent (or --yes).
  demo          Run the interactive demo; still requires explicit 'yes' before
                any billed call, exactly like menu options 5-8. Use --yes to
                bypass confirmation.
  --help | -h   Print this help and exit 0.

Menu options (interactive mode):
  FREE (no API key required):
    1) Run test suite
    2) Validate rules cache
    3) Check over-limit sample (exit 2, no API call)
    4) Replay a past run

  BILLED (requires ANTHROPIC_API_KEY):
    5) Check hype.txt — 'get rich tomorrow' (expect FINDINGS, exit 1)
    6) Check compliant.txt — balanced copy (expect CLEAN, exit 0)
    7) Check subtle.txt — 42% return headline (expect FINDINGS, exit 1)
    8) Check your own text or file

  m) Switch model (this session only)
  q) Quit

Exit codes (compliance check):
  0  Clean — no breaches found
  1  Findings — at least one non-compliant or unclear verdict
  2  Incomplete — pipeline failure or any error verdict
  5  Non-TTY without --yes (run.sh internal)

Environment:
  ANTHROPIC_API_KEY   Required for billed options (5-8). Can be set in .env.
EOF
}

# ── Compute cost estimate from PRICES table + measured token profiles.
#    Sets global COST_ESTIMATE. Call after CURRENT_MODEL is set.
compute_cost_estimate() {
    # Token profiles are model-specific (different tokenizers).
    # Do NOT project one model's counts at another model's prices.
    COST_ESTIMATE="$(cd "$SCRIPT_DIR" && uv run python -c "
from evals.run_samples import PRICES
model = '${CURRENT_MODEL}'
# Measured profiles: (input_tokens, output_tokens) for a 75-rule full run.
PROFILES = {
    # Opus profile predates the separator slimming, so it overestimates
    # slightly. Overestimating at a consent prompt is the safe direction;
    # re-measure from a real opus run before quoting it as exact.
    'claude-opus-5':   (217504, 25715),
    'claude-haiku-4-5': (131000, 11400),
}
profile = PROFILES.get(model)
price = PRICES.get(model)
if profile and price:
    inp, out = profile
    in_rate, out_rate = price
    est = (inp * in_rate + out * out_rate) / 1000000
    print('~\$%.2f' % est)
else:
    print('cost unknown (model not profiled)')
" 2>/dev/null)" || COST_ESTIMATE="cost unknown"
}

# ── Compute actual spend from the most recent run record.
#    Sets global ACTUAL_SPEND. Call after a billed run completes.
compute_actual_spend() {
    ACTUAL_SPEND="$(cd "$SCRIPT_DIR" && uv run python -c "
from evals.run_samples import latest_run_record, cost_of
from compliance_agent.config import repo_root
root = repo_root()
record = latest_run_record(root)
cost = cost_of(record, '${CURRENT_MODEL}') if record else None
if cost is not None:
    print('Actual spend: \$%.4f' % cost)
else:
    print('Actual spend: unknown')
" 2>/dev/null)" || ACTUAL_SPEND="Actual spend: unknown"
}

# ── Consent gate — shows model, estimate, and asks for confirmation.
#    Returns 0 to proceed, 1 to cancel.
#    Called by run_billed_file, the check dispatch, and the demo path.
confirm_spend() {
    printf 'Model: %s  |  Estimated cost: %s  |  Estimated duration: ~50s\n' \
        "$CURRENT_MODEL" "$COST_ESTIMATE"
    if [ "$YES" -eq 1 ]; then
        return 0
    fi
    printf "Type 'yes' to proceed (anything else cancels): "
    local consent=""
    IFS= read -r consent || true
    if [ "$consent" = "yes" ]; then
        return 0
    fi
    printf 'Cancelled.\n'
    return 1
}

# ── Billed check helper — guards on FREE_ONLY, calls confirm_spend, runs check.
#    After the run, prints actual spend (via latest_run_record + cost_of).
run_billed_file() {
    local abs_path="$1"
    if [ "$FREE_ONLY" -eq 1 ]; then
        printf 'No API key. Set ANTHROPIC_API_KEY in .env to use billed options.\n'
        return
    fi
    confirm_spend || return
    local start elapsed
    start="$(date +%s)"
    local ec=0
    (cd "$SCRIPT_DIR" && uv run compliance-agent check --text "$abs_path") || ec=$?
    elapsed=$(( $(date +%s) - start ))
    compute_actual_spend
    printf 'Exit code: %d  |  Duration: %ds  |  %s\n' "$ec" "$elapsed" "$ACTUAL_SPEND"
}

# ── Menu display ──────────────────────────────────────────────────────────────
show_menu() {
    printf '\n'
    if [ "$FREE_ONLY" -eq 1 ]; then
        printf "${YLW}FREE-ONLY MODE — no API key. Billed options (5-8) are visible but will refuse.${RST}\n"
    fi
    printf '%sFCA COBS 4 Compliance Agent%s\n' "$BOLD" "$RST"
    printf '\n'
    printf 'FREE (no API key required):\n'
    printf '  1) Run test suite\n'
    printf '  2) Validate rules cache\n'
    printf '  3) Check over-limit sample (exit 2, no API call)\n'
    printf '  4) Replay a past run\n'
    printf '\n'
    printf 'BILLED (%s each on %s):\n' "$COST_ESTIMATE" "$CURRENT_MODEL"
    printf '  5) Check hype.txt     — "get rich tomorrow" (expect FINDINGS, exit 1)\n'
    printf '  6) Check compliant.txt — balanced copy (expect CLEAN, exit 0)\n'
    printf '  7) Check subtle.txt   — 42%% return headline (expect FINDINGS, exit 1)\n'
    printf '  8) Check your own text or file\n'
    printf '\n'
    printf '  m) Switch model\n'
    printf '  q) Quit\n'
}

# ── Arg parsing ───────────────────────────────────────────────────────────────
# Scan all args for --yes first (can appear anywhere)
for arg in "$@"; do
    if [ "$arg" = "--yes" ]; then
        YES=1
    fi
done

# Dispatch on first non-flag positional argument
CMD="${1:-}"

case "$CMD" in
    --help|-h)
        print_usage
        exit 0
        ;;

    test)
        # Preflight: uv only (steps a+b), then run tests. Bypasses TTY gate.
        if ! command -v uv > /dev/null 2>&1; then
            printf 'uv not found. Install with:\n'
            printf '  curl -LsSf https://astral.sh/uv/install.sh | sh\n'
            exit 1
        fi
        if ! (cd "$SCRIPT_DIR" && uv sync --frozen); then
            printf 'Dependency sync failed — see error above.\n'
            exit 1
        fi
        ec=0
        (cd "$SCRIPT_DIR" && uv run pytest) || ec=$?
        printf 'Exit: %d\n' "$ec"
        exit "$ec"
        ;;

    check)
        FILE_ARG="${2:-}"
        if [ -z "$FILE_ARG" ]; then
            printf 'Usage: ./run.sh check <file>\n'
            exit 1
        fi
        # Resolve absolute path from caller's cwd BEFORE any cd.
        case "$FILE_ARG" in
            /*) CHECK_FILE="$FILE_ARG" ;;
            *)  CHECK_FILE="$PWD/$FILE_ARG" ;;
        esac
        # Fall through to full preflight + consent gate below.
        # (CMD remains "check" so the post-preflight dispatch fires.)
        ;;

    demo)
        # Full preflight runs below; demo is dispatched after the non-TTY gate.
        ;;

    --yes)
        # --yes as the first arg: continue to preflight + menu
        CMD=""
        ;;

    "")
        # No subcommand: continue to preflight + menu
        ;;

    *)
        printf 'Unknown command: %s\n' "$CMD"
        printf "Run './run.sh --help' for usage.\n"
        exit 1
        ;;
esac

# ── Preflight ─────────────────────────────────────────────────────────────────

# (a) uv availability
if ! command -v uv > /dev/null 2>&1; then
    printf 'uv not found. Install with:\n'
    printf '  curl -LsSf https://astral.sh/uv/install.sh | sh\n'
    exit 1
fi

# (b) uv sync --frozen
if ! (cd "$SCRIPT_DIR" && uv sync --frozen); then
    printf 'Dependency sync failed — see error above.\n'
    exit 1
fi

# (c) API key + model (Python one-liner — do not use bare [ -z "$ANTHROPIC_API_KEY" ])
PREFLIGHT_OUT=""
if ! PREFLIGHT_OUT="$(cd "$SCRIPT_DIR" && uv run python -c \
  "from compliance_agent.config import load_settings; \
   s=load_settings(); \
   print('model:', s.model); \
   print('api_key:', 'set' if s.api_key else 'missing')" 2>&1)"; then
    printf 'Failed to load settings — is the package installed?\n'
    exit 1
fi

CURRENT_MODEL="$(printf '%s' "$PREFLIGHT_OUT" | grep '^model:' | awk '{print $2}')" || true
API_KEY_STATUS="$(printf '%s' "$PREFLIGHT_OUT" | grep '^api_key:' | awk '{print $2}')" || true

if [ "$API_KEY_STATUS" = "missing" ]; then
    FREE_ONLY=1
    printf "${YLW}Notice:${RST} No API key found. Running in FREE-ONLY mode.\n"
    printf 'Set ANTHROPIC_API_KEY in .env to unlock billed options (5-8).\n\n'
fi

# For demo: if no key, fail immediately
if [ "$CMD" = "demo" ] && [ "$FREE_ONLY" -eq 1 ]; then
    printf "${RED}Error:${RST} demo requires ANTHROPIC_API_KEY. Set it in .env.\n"
    exit 1
fi

# Compute cost estimate now that CURRENT_MODEL is known.
compute_cost_estimate

# (d) Rules cache check (pre-spend gate)
if ! (cd "$SCRIPT_DIR" && uv run python evals/check_rules_cache.py); then
    printf 'Rules cache check failed.\n'
    printf 'Fix: uv run compliance-agent extract-rules --refresh\n'
    exit 1
fi

# ── Non-TTY gate ──────────────────────────────────────────────────────────────
if [ ! -t 0 ] && [ "$YES" -eq 0 ]; then
    printf 'run.sh requires an interactive terminal.\n'
    printf 'Use --yes for non-TTY mode, or run a specific command:\n'
    printf '  ./run.sh test | check <file> | demo | --help\n'
    exit 5
fi

# ── check dispatch (after full preflight + non-TTY gate) ─────────────────────
# Every path that can spend money passes through confirm_spend first.
if [ -n "$CHECK_FILE" ]; then
    confirm_spend || exit 0
    ec=0
    (cd "$SCRIPT_DIR" && uv run compliance-agent check --text "$CHECK_FILE") || ec=$?
    compute_actual_spend
    printf '%s\n' "$ACTUAL_SPEND"
    exit "$ec"
fi

# ── Demo path (after full preflight + non-TTY gate) ──────────────────────────
# demo does NOT auto-confirm — user must still type 'yes', exactly like options
# 5-8. Only --yes bypasses the prompt. Cost consent is a hard requirement.
if [ "$CMD" = "demo" ]; then
    printf '\n%s=== FCA COBS 4 Compliance Agent Demo ===%s\n\n' "$BOLD" "$RST"
    printf 'This demo runs a compliance check on samples/hype.txt.\n'
    confirm_spend || exit 0
    start="$(date +%s)"
    ec=0
    (cd "$SCRIPT_DIR" && uv run compliance-agent check --text "$SCRIPT_DIR/samples/hype.txt") || ec=$?
    elapsed=$(( $(date +%s) - start ))
    compute_actual_spend
    printf 'Exit code: %d  |  Duration: %ds  |  %s\n' "$ec" "$elapsed" "$ACTUAL_SPEND"
    exit "$ec"
fi

# ── Menu loop ─────────────────────────────────────────────────────────────────
while true; do
    show_menu
    printf '\nChoice: '
    choice=""
    IFS= read -r choice || break
    [ -z "$choice" ] && continue

    case "$choice" in
        1)
            ec=0
            (cd "$SCRIPT_DIR" && uv run pytest) || ec=$?
            printf 'Exit: %d\n' "$ec"
            ;;

        2)
            ec=0
            (cd "$SCRIPT_DIR" && uv run python evals/check_rules_cache.py) || ec=$?
            printf 'Exit: %d\n' "$ec"
            ;;

        3)
            ec=0
            (cd "$SCRIPT_DIR" && uv run compliance-agent check --text samples/overlimit.txt) || ec=$?
            printf 'Exit code: %d (expected 2 — rejected at ingestion, no API call)\n' "$ec"
            ;;

        4)
            HISTORY="$SCRIPT_DIR/runs/history.jsonl"
            if [ ! -f "$HISTORY" ] || [ ! -s "$HISTORY" ]; then
                printf 'No past runs found. runs/ is empty on a fresh clone.\n'
                printf 'Run a billed check (options 5-8) first to generate a run.\n'
                continue
            fi
            # List last 10 run IDs via Python (avoids complex bash JSON parsing)
            (cd "$SCRIPT_DIR" && uv run python -c "
import json
from pathlib import Path
lines = [l for l in Path('runs/history.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()][-10:]
for l in lines:
    r = json.loads(l)
    print(r['run_id'], r.get('overall_outcome', '?'), 'exit', r.get('exit_code', '?'))
") || true
            printf 'Enter run ID (or blank to cancel): '
            run_id=""
            IFS= read -r run_id || true
            [ -z "$run_id" ] && continue
            ec=0
            (cd "$SCRIPT_DIR" && uv run compliance-agent show "$run_id") || ec=$?
            if [ "$ec" -ne 0 ]; then
                printf 'show exited %d — check the run ID above.\n' "$ec"
            fi
            ;;

        5)
            run_billed_file "$SCRIPT_DIR/samples/hype.txt"
            ;;

        6)
            case "$CURRENT_MODEL" in
                *haiku*)
                    printf "${YLW}Warning:${RST} On %s, compliant.txt returns FINDINGS (not CLEAN) — this is expected.\n" "$CURRENT_MODEL"
                    printf 'Continue? (yes/no): '
                    haiku_confirm=""
                    IFS= read -r haiku_confirm || true
                    if [ "$haiku_confirm" != "yes" ]; then continue; fi
                    ;;
            esac
            run_billed_file "$SCRIPT_DIR/samples/compliant.txt"
            ;;

        7)
            run_billed_file "$SCRIPT_DIR/samples/subtle.txt"
            ;;

        8)
            if [ "$FREE_ONLY" -eq 1 ]; then
                printf 'No API key. Set ANTHROPIC_API_KEY in .env to use billed options.\n'
                continue
            fi
            printf 'File path (relative to current dir) or inline text, or "cancel": '
            user_input=""
            IFS= read -r user_input || true
            [ -z "$user_input" ] && continue
            if [ "$user_input" = "cancel" ]; then continue; fi

            # Resolve as file path first (try both as-is and relative to $PWD)
            ABS_IN=""
            case "$user_input" in
                /*) [ -f "$user_input" ] && ABS_IN="$user_input" ;;
                *)  [ -f "$PWD/$user_input" ] && ABS_IN="$PWD/$user_input" ;;
            esac

            if [ -n "$ABS_IN" ]; then
                run_billed_file "$ABS_IN"
            else
                # Treat as inline text; write to a temp file
                tmp_file=""
                tmp_file="$(mktemp /tmp/compliance_input_XXXXXX.txt)"
                printf '%s' "$user_input" > "$tmp_file"
                run_billed_file "$tmp_file"
                rm -f "$tmp_file"
            fi
            ;;

        m)
            printf 'New model name (e.g. claude-haiku-4-5, claude-opus-5): '
            new_model=""
            IFS= read -r new_model || true
            if [ -z "$new_model" ]; then continue; fi
            export COMPLIANCE_MODEL="$new_model"
            CURRENT_MODEL="$new_model"
            compute_cost_estimate
            printf 'Model set to: %s (this session only; edit .env to persist)\n' "$new_model"
            printf 'Estimated cost per run: %s\n' "$COST_ESTIMATE"
            case "$new_model" in
                *haiku*)
                    printf "${YLW}Note:${RST} On claude-haiku-4-5, compliant.txt returns FINDINGS (not CLEAN) — this is expected.\n"
                    ;;
            esac
            ;;

        q)
            printf 'Goodbye.\n'
            exit 0
            ;;

        *)
            printf 'Unknown: %s\n' "$choice"
            ;;
    esac
done
