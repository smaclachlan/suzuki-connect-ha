"""Make the vendored library importable as `pysuzukiconnect` in tests,
without importing the Home Assistant integration package."""
import pathlib
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).parent / "custom_components" / "suzuki_connect")
)
