#!/usr/bin/env bash
#
# The lint half of the pre-publish gate. One implementation, called by both
# publish_public_repo.sh (which blocks the push) and .github/workflows/ci.yml (which shows a
# contributor the same result), so the two can never disagree about what "clean" means.
#
#   bash scripts/lint.sh          # check, exit non-zero on any finding
#   bash scripts/lint.sh --fix    # apply the fixes ruff can make safely, then check
#
# WHY ONLY ruff, AND ONLY THESE RULES
#
# F  = pyflakes: unused imports, unused variables, undefined names. Real defects, essentially
#      no false positives. Measured 2026-08-25 on this tree: 7 findings, 7 genuine.
# E9 = syntax and IO errors. A file that cannot parse must never reach the public repo.
#
# Deliberately NOT the full default rule set: E501 line length and the import-sorting rules
# would rewrite most of this tree for style, and a gate that demands a 400-line diff on its
# first run is a gate that gets switched off.
#
# WHY NO SECURITY SCANNER, measured rather than assumed (2026-08-25):
#   bandit   15 findings on the application modules, 15 false positives — it read the string
#            'False' as a hardcoded password and flagged os.chmod(DIR, 0o750) as permissive,
#            which is the mode the estate charter requires.
#   semgrep  1 finding across server.py, call_log.py and graph_files.py — the same 0o750.
# Neither flags the one regression that actually matters here (hmac.compare_digest swapped for
# ==), so neither would have earned its runtime. That regression is pinned in
# test_smoke.py::test_the_security_properties_no_behaviour_test_can_see instead, which catches
# it with no false positives at all. Revisit if the code grows a real attack surface.
#
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SRC"

MODE="check"
[[ "${1:-}" == "--fix" ]] && MODE="fix"

# Tracked files only: .claude/worktrees/ holds other sessions' checkouts of this same repo, and
# linting those reports every finding two or three times over.
mapfile -t PY < <(git -C "$SRC" ls-files '*.py')
if ((${#PY[@]} == 0)); then
  echo "no tracked python files — nothing to lint" >&2
  exit 1
fi

RUFF="${BIA_RELEASE_RUFF:-ruff}"
if ! command -v "$RUFF" >/dev/null 2>&1; then
  if [[ -x "$SRC/.venv/bin/ruff" ]]; then
    RUFF="$SRC/.venv/bin/ruff"
  else
    echo "ruff not found. It is pinned in requirements.txt; this tree's .venv is uv-managed" >&2
    echo "and has no pip, so install it with uv:" >&2
    echo "    uv pip install --python .venv/bin/python -r requirements.txt" >&2
    exit 1
  fi
fi

# --isolated: no pyproject.toml or ruff.toml in this tree, and none wanted. The rule set is the
# line below, in the file that runs it, rather than in a config a reader has to go and find.
if [[ "$MODE" == "fix" ]]; then
  "$RUFF" check --isolated --select F,E9 --fix "${PY[@]}"
fi
"$RUFF" check --isolated --select F,E9 --output-format concise "${PY[@]}"
echo "  lint clean: ${#PY[@]} tracked python files, ruff F,E9"
