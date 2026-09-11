#!/usr/bin/env sh
# Runs the Python tool tests.
#
#   sh tests/tools.sh
#
# The tools carry PEP 723 metadata and declare no dependencies, so they run
# under a plain interpreter as well as under uv. uv is preferred because it is
# what the tools are documented to run under; a bare interpreter is the
# fallback for CI, which has python3 and no uv.
#
# Skips rather than fails when neither is present. The C suites cover the
# comparison itself, and a machine without Python should still be able to
# check that. REQUIRE_PYTHON=1 turns the skip into a failure, for CI, where a
# runner that has lost its interpreter should not pass by testing less.

set -eu

here="$(cd "$(dirname "$0")" && pwd)"

if command -v uv >/dev/null 2>&1; then
    runner="uv run"
elif [ -x "$HOME/.local/bin/uv" ]; then
    runner="$HOME/.local/bin/uv run"
elif command -v python3 >/dev/null 2>&1; then
    runner="python3"
elif [ "${REQUIRE_PYTHON:-0}" = 1 ]; then
    echo "tools.sh: no uv and no python3, and REQUIRE_PYTHON=1 says that is a failure" >&2
    exit 1
else
    echo "tools.sh: no uv and no python3, skipping the Python tool tests"
    exit 0
fi

echo "Running Python tool tests with ${runner%% *}"
for suite in "$here"/unit/test_*.py; do
    # shellcheck disable=SC2086
    $runner "$suite"
done
