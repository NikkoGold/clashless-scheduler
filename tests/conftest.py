import os

import pytest
from hypothesis import settings

# CP-SAT's run time varies, so there's no per-example deadline
settings.register_profile("scheduler", max_examples=100, deadline=None)
settings.register_profile("thorough", max_examples=2000, deadline=None)  # pytest --hypothesis-profile=thorough
settings.load_profile("scheduler")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LIVE_LLM") == "1":
        return
    skip_live = pytest.mark.skip(reason="real LLM call; set RUN_LIVE_LLM=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
