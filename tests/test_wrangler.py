import json
from pathlib import Path

import pytest

from celld.build import configurations
from celld.wrangler import jsonc, read_config, resolve_target


def project(tmp_path, **overrides):
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src/worker.py").write_text('from celld import App\napp = App\n')
    path = tmp_path / "wrangler.jsonc"
    path.write_text(json.dumps(dict(name='hello', main='src/worker.py',
                                   compatibility_flags=['python_workers'], **overrides)))
    return path


def test_jsonc_preserves_strings_and_accepts_comments_and_trailing_commas():
    assert jsonc(r'''{ // comment
        "url": "https://example.com/a//b", /* comment */
        "escaped": "a\"/*b*/", "text": ",}", "list": [1, /*more*/],
    }''') == dict(url='https://example.com/a//b', escaped='a"/*b*/', text=',}', list=[1])
    with pytest.raises(ValueError, match='Unterminated'):
        jsonc('{/* missing end')
    with pytest.raises(ValueError):
        jsonc('{"bad": unquoted}')


def test_wrangler_entry_and_python_dependencies(tmp_path):
    path = project(tmp_path)
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="different"\ndependencies=["numpy"]\n')
    assert resolve_target(tmp_path) == path
    root, name, apps = configurations(tmp_path)
    assert root == tmp_path and name == 'hello'
    assert apps[0]['entrypoint'] == 'worker'
    assert apps[0]['source'] == 'src'
    assert apps[0]['dependencies'] == ['numpy']


def test_wrangler_only_needs_config_and_function(tmp_path):
    path = project(tmp_path)
    _, _, apps = configurations(path)
    assert apps[0]['dependencies'] == []


@pytest.mark.parametrize('change,match', [
    ({'main': '../outside.py'}, 'project-relative'),
    ({'main': 'src/missing.py'}, 'inside the project'),
    ({'main': 'worker.js'}, '.py file'),
    ({'compatibility_flags': []}, 'python_workers'),
    ({'compatibility_flags': 'python_workers'}, 'list of strings'),
    ({'build': {'command': 'echo unsafe'}}, 'Unsupported'),
    ({'r2_buckets': []}, 'Unsupported'),
])
def test_unsupported_configs_fail_clearly(tmp_path, change, match):
    path = project(tmp_path)
    value = json.loads(path.read_text())
    value.update(change)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match=match):
        read_config(path)
