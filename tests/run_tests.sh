#!/bin/sh
# ExpAgent test suite — runs every test script as a real subprocess.
# Usage: ./tests/run_tests.sh   (from the repo root)
set -u
cd "$(dirname "$0")/.."

fail=0
for t in tests/test_plan.py tests/test_gate.py tests/test_apply.py tests/test_probe.py tests/test_status.py tests/test_results.py tests/test_ship.py; do
  echo "== $t"
  if ! python3 "$t"; then
    fail=1
  fi
  echo
done

if [ "$fail" -eq 0 ]; then
  echo "ALL TESTS PASSED"
else
  echo "TESTS FAILED"
fi
exit "$fail"
