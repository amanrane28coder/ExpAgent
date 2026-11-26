#!/usr/bin/env python3
"""Tests for gate/probe.py — runs the probe runner as a subprocess and
asserts its summary: every case verdict matches expectation.

The probe itself runs the REAL gate/gate.py as a subprocess on temp files;
this test only guards the probe's expectations against drift. No mocks,
no network, no Flagsmith calls.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "gate" / "probe.py"

CASE_NAMES = [
    "clean-50-50",
    "weights-sum-99",
    "variant-0pct",
    "unknown-metric",
    "rollout-jump-10-to-100",
    "kill-switch-off",
]


def main():
    passed = failed = 0

    def check(name, ok, detail=""):
        nonlocal passed, failed
        if ok:
            print("PASS %s" % name)
            passed += 1
        else:
            print("FAIL %s: %s" % (name, detail))
            failed += 1

    result = subprocess.run([sys.executable, str(PROBE)],
                            capture_output=True, text=True, cwd=ROOT)
    out = result.stdout + result.stderr
    check("probe exits 0", result.returncode == 0, out)
    check("probe summary is 6/6 as expected",
          "probe: 6/6 as expected" in out, out)
    for case in CASE_NAMES:
        check("probe prints case %s" % case, case in out, out)
    check("probe shows a BLOCK reason for weights-sum-99",
          "sum to 99" in out, out)
    check("probe shows a BLOCK reason for rollout jump",
          "rollout jump" in out, out)

    print("test_probe: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
