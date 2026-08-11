"""FlashCrucible top-level package."""

from importlib.metadata import PackageNotFoundError, version as _installed_version

#: One authoritative home for the version. It was previously hardcoded in
#: `capabilities.py` as well as in `pyproject.toml`, so a release would have
#: reported the old number to every caller that asked.
try:
    __version__ = _installed_version("flashcrucible")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
