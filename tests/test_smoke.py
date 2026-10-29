import importlib
import pkgutil

import pytest

import scheduler

MODULES = sorted(
    m.name for m in pkgutil.walk_packages(scheduler.__path__, prefix="scheduler.")
)


@pytest.mark.parametrize("name", MODULES)
def test_every_module_imports(name):
    importlib.import_module(name)
