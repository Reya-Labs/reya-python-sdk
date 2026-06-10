from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def _get_version() -> str:
    """Return the installed SDK version.

    Prefer distribution metadata so the version resolves when the SDK is installed
    as a wheel or in editable mode, where pyproject.toml is not shipped next to the
    package. Fall back to reading pyproject.toml when running from a source checkout
    that is not installed as a distribution, and finally to "unknown".
    """
    try:
        return version("reya-python-sdk")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        try:
            with open(pyproject_path, "rb") as f:
                pyproject_data = tomllib.load(f)
            return str(pyproject_data["project"]["version"])
        except (FileNotFoundError, KeyError):
            return "unknown"


SDK_VERSION = _get_version()
