import os
from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis.database import DirectoryBasedExampleDatabase

from scheduler.config import HOME_VAR, SETTING_VARS
from tests.helpers import real_settings  # noqa: F401  (captures the real environment before it's replaced below)

ROOT = Path(__file__).parent.parent

# CP-SAT's run time varies, so there's no per-example deadline. The database path is fixed because the
# suite runs in a temporary working directory (below).
_db = DirectoryBasedExampleDatabase(str(ROOT / ".hypothesis" / "examples"))
settings.register_profile("scheduler", max_examples=100, deadline=None, database=_db)
settings.register_profile("thorough", max_examples=2000, deadline=None, database=_db)  # pytest --hypothesis-profile=thorough
settings.load_profile("scheduler")


@pytest.fixture(autouse=True, scope="session")
def _never_touch_real_settings(tmp_path_factory):
    """No test may read the real settings (a real API key) or use the real cache and daily call count.

    The user-wide folder points at a temporary one, the settings variables are cleared, and the suite runs in an
    empty working directory, so the project's own .env is never read. The live eval reads the real settings on
    purpose, through tests/helpers/real_settings.py. Tests that write settings use their own folders on top.
    """
    mp = pytest.MonkeyPatch()
    mp.setenv(HOME_VAR, str(tmp_path_factory.mktemp("scheduler-home")))
    for var in SETTING_VARS:
        mp.delenv(var, raising=False)
    mp.chdir(tmp_path_factory.mktemp("cwd"))
    yield
    mp.undo()


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LIVE_LLM") == "1":
        return
    skip_live = pytest.mark.skip(reason="real LLM call; set RUN_LIVE_LLM=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


class Settings:
    """A test's own user-wide folder and working folder (both start empty: a true first run)."""

    def __init__(self, home: Path, work: Path):
        self.home, self.work = home, work
        self.user_file = home / "config.env"
        self.project_env = work / ".env"

    def save(self, text: str, project: bool = False) -> Path:
        path = self.project_env if project else self.user_file
        path.write_text(text, encoding="utf-8")
        return path

    def saved(self) -> str:
        return self.user_file.read_text(encoding="utf-8") if self.user_file.is_file() else ""


@pytest.fixture
def fresh_settings(tmp_path, monkeypatch) -> Settings:
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv(HOME_VAR, str(home))
    monkeypatch.chdir(work)
    return Settings(home, work)
