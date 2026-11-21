"""The real environment and working directory, captured before conftest swaps in temporary ones.

Only the opt-in live eval may use these: everything else must run against temporary settings, so no test reads a
real API key or uses up the real daily call count.
"""
import os
from pathlib import Path

REAL_ENVIRON = dict(os.environ)
REAL_CWD = Path.cwd()
