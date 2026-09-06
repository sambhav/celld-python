"""Read the supported Wrangler Python project format without executing config."""
import json
from pathlib import Path


def resolve_target(target: Path) -> Path:
    target = target.resolve()
    if target.is_dir():
        for name in ("wrangler.jsonc", "wrangler.json", "pyproject.toml"):
            if (target / name).is_file():
                return target / name
        raise ValueError(f"No wrangler.jsonc, wrangler.json or pyproject.toml in {target}")
    return target


def jsonc(source: str):
    # Keep JSON strings intact, including URLs, escaped quotes and comment-like
    # text. Replacing comments with spaces preserves JSON error locations.
    result = list(source)
    i = 0
    while i < len(source):
        if source[i] == '"':
            i += 1
            while i < len(source):
                if source[i] == '\\':
                    i += 2
                elif source[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
        elif source.startswith('//', i) or source.startswith('/*', i):
            start = i
            if source[i + 1] == '/':
                end = source.find('\n', i)
                i = len(source) if end < 0 else end
            else:
                end = source.find('*/', i + 2)
                if end < 0:
                    raise ValueError('Unterminated JSONC comment')
                i = end + 2
            for n in range(start, i):
                if result[n] not in '\r\n':
                    result[n] = ' '
        else:
            i += 1
    # A second string-aware pass removes only trailing commas.
    cleaned = ''.join(result)
    i = 0
    while i < len(cleaned):
        if cleaned[i] == '"':
            i += 1
            while i < len(cleaned):
                if cleaned[i] == '\\':
                    i += 2
                elif cleaned[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
        else:
            if cleaned[i] == ',':
                j = i + 1
                while j < len(cleaned) and cleaned[j].isspace():
                    j += 1
                if j < len(cleaned) and cleaned[j] in '}]':
                    result[i] = ' '
            i += 1
    return json.loads(''.join(result))


def read_config(path: Path):
    config = jsonc(path.read_text())
    if not isinstance(config, dict):
        raise ValueError('Wrangler config must be an object')
    supported = {'$schema', 'name', 'main', 'compatibility_date', 'compatibility_flags', 'vars'}
    unknown = sorted(config.keys() - supported)
    if unknown:
        raise ValueError('Unsupported Python Wrangler config keys: ' + ', '.join(unknown))
    main = config.get('main')
    if not isinstance(main, str) or Path(main).suffix != '.py':
        raise ValueError('Python Wrangler main must name a .py file')
    entry = Path(main)
    if entry.is_absolute() or '..' in entry.parts or not entry.stem.isidentifier():
        raise ValueError('Python Wrangler main must be a project-relative Python module')
    resolved = (path.parent / entry).resolve()
    if not resolved.is_relative_to(path.parent) or not resolved.is_file():
        raise ValueError('Python Wrangler main must be a file inside the project')
    flags = config.get('compatibility_flags', [])
    if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
        raise ValueError('compatibility_flags must be a list of strings')
    if 'python_workers' not in flags:
        raise ValueError('Add "python_workers" to compatibility_flags')
    if not isinstance(config.get('name'), str):
        raise ValueError('Wrangler config requires a name')
    return config
