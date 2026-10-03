"""Make the vendored library importable as `pysuzukiconnect` in tests,
without importing the Home Assistant integration package.

Integration tests under tests/ha need Home Assistant and
pytest-homeassistant-custom-component; they're skipped when those aren't
installed, so the library tests still run with just aiohttp + pytest.
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent

sys.path.insert(0, str(ROOT / "custom_components" / "suzuki_connect"))
sys.path.insert(0, str(ROOT / "tests"))

collect_ignore_glob = []
if importlib.util.find_spec("pytest_homeassistant_custom_component") is None:
    collect_ignore_glob.append("tests/ha/*")
