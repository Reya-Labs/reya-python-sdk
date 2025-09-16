from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def _get_version() -> str:
    current_file = Path(__file__)
    pyproject_path = current_file.parent.parent / "pyproject.toml"

    try:
        with open(pyproject_path, "rb") as f:
            pyproject_data = tomllib.load(f)
        return str(pyproject_data["project"]["version"])
    except (FileNotFoundError, KeyError, Exception):
        raise ValueError("Failed to read version from pyproject.toml")


SDK_VERSION = _get_version()
