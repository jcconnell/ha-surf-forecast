"""Load the dependency-free modules without pulling in Home Assistant.

``custom_components/surf_forecast/__init__.py`` imports Home Assistant, which
is not needed to exercise the scoring and parsing logic. These modules are
therefore loaded directly into a synthetic package so ``from .const import ...``
still resolves.
"""

import importlib.util
import sys
import types
from pathlib import Path

COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "surf_forecast"
)
PACKAGE = "surf_forecast_pure"

if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT_DIR)]
    sys.modules[PACKAGE] = package

    # const must load first; parse imports from it.
    for module_name in ("const", "surf", "parse"):
        spec = importlib.util.spec_from_file_location(
            f"{PACKAGE}.{module_name}", COMPONENT_DIR / f"{module_name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{PACKAGE}.{module_name}"] = module
        spec.loader.exec_module(module)
        setattr(package, module_name, module)
