from pathlib import Path
import re


def create(directory: Path):
    name = directory.resolve().name.lower().replace("_", "-")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name):
        raise ValueError("Choose a directory name starting with a letter and containing letters, digits or hyphens")
    files = {
        "pyproject.toml": f'[project]\nname = "{name}"\nversion = "0.1.0"\ndependencies = []\n',
        "src/app.py": '''from celld import App

app = App


@app.function
def hello(name: str = "world") -> str:
    return f"Hello, {name}"
''',
    }
    if any((directory / name).exists() for name in files):
        raise ValueError("Project files already exist")
    for name, source in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return directory
