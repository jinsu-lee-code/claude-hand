"""Run the tests as `python -m unittest discover -s tools/tests` does and, on GitHub Actions, also report each
failure as an annotation: annotations show on the run's summary page (and through the API) for anyone, while the
log of a job can only be read after signing in to GitHub.

  python tools/tests/ci.py
"""

import os
import sys
import unittest
from pathlib import Path

ANNOTATIONS = 10   # GitHub shows at most ten error annotations of one step
TRACE_CHARS = 1500  # the end of a traceback: the assertion and what it compared


def annotation(test: unittest.TestCase, trace: str) -> str:
    """One `::error` workflow command; line breaks and percent signs are escaped as GitHub asks."""
    message = trace.strip()[-TRACE_CHARS:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::error title={test.id()}::{message}"


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent))
    result = unittest.TextTestRunner().run(suite)
    if os.environ.get("GITHUB_ACTIONS"):
        failed = result.failures + result.errors
        for test, trace in failed[:ANNOTATIONS]:
            print(annotation(test, trace))
        if len(failed) > ANNOTATIONS:
            print(f"::error title=more failures::{len(failed) - ANNOTATIONS} more failed; see the log of this step")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
