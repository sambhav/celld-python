"""Same-runner retained/released snapshot input A/B; not a throughput benchmark."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'experiments/rust-pyodide')]
from harness import measured_node
from test_celld import publish_local, request
builder = importlib.import_module('celld.build')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    assert args.rounds > 0
    out = HERE / 'build' / ('memory-' + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    binary = Path(shutil.which('celld'))
    report = dict(source_sha=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        native_sha=os.environ.get('CELLD_NATIVE_SHA',''), binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        run_url=f"https://github.com/{os.environ.get('GITHUB_REPOSITORY','sambhav/celld-python')}/actions/runs/{os.environ.get('GITHUB_RUN_ID','')}",
        cpu=subprocess.check_output(['lscpu'], text=True), smoke=args.smoke,
        store='local SQLite development object store; no remote S3', samples=[])
    projects = {}
    original_patch = builder.release_snapshot_input
    try:
        for release in (False, True):
            builder.release_snapshot_input = original_patch if release else lambda source: source
            project = builder.build(ROOT / 'examples/fleet.toml', out / f'project-{release}')
            host = project / 'host.js'
            source = host.read_text()
            marker = '  for(const [name,source] of Object.entries({...sdk,...app.sources}))'
            assert source.count(marker) == 1
            source = source.replace(marker, '  console.log("CELLD_SNAPSHOT_INPUT="+(py._api.config._loadSnapshot?.byteLength || 0));\n' + marker)
            host.write_text(source)
            publish_local(project, out / f'publish-{release}.log')
            projects[release] = project
    finally:
        builder.release_snapshot_input = original_patch
    for round_ in range(args.rounds):
        for release in ((False, True) if round_ % 2 == 0 else (True, False)):
            for isolates in (1, 4):
                log = out / f'node-{round_}-{release}-{isolates}.log'
                with measured_node(projects[release], log, stateless_isolates=isolates) as node:
                    def call(_):
                        status, body, headers = request(node.port, '/hello/hello', {'name':'Sam'},
                            headers={'x-celld-call-id':uuid.uuid4().hex})
                        assert status == 200 and json.loads(body)['result'] == 'Hello, Sam', (status, body)
                        assert not headers.get('x-celld-replayed')
                    with ThreadPoolExecutor(max_workers=32) as clients:
                        list(clients.map(call, range(32)))
                    inputs = [int(value) for value in re.findall(r'CELLD_SNAPSHOT_INPUT=(\d+)', log.read_text())]
                    assert len(inputs) == isolates, (isolates, inputs, log.read_text()[-3000:])
                    assert all(value == 0 if release else value > 30_000_000 for value in inputs)
                    census = []
                    for _ in range(8):
                        call(None)
                        census.append(node.state())
                        time.sleep(.25)
                    row = dict(round=round_, release_input=release, max_stateless_isolates=isolates,
                        initialized_runtimes=len(inputs), retained_input_bytes=sum(inputs),
                        rss_bytes=statistics.median(state['rss_bytes'] for state in census),
                        in_use_bytes=statistics.median(state['in_use_bytes'] for state in census),
                        rss_samples=[state['rss_bytes'] for state in census])
                    report['samples'].append(row)
                    (out / 'measurements.json').write_text(json.dumps(report, indent=2)+'\n')
                    print('MEMORY_SAMPLE='+json.dumps(row), flush=True)
    report['summary'] = []
    for release in (False, True):
        for isolates in (1, 4):
            rows = [s for s in report['samples'] if s['release_input']==release and s['max_stateless_isolates']==isolates]
            report['summary'].append(dict(release_input=release, max_stateless_isolates=isolates,
                rounds=len(rows), rss_bytes=statistics.median(row['rss_bytes'] for row in rows),
                in_use_bytes=statistics.median(row['in_use_bytes'] for row in rows),
                retained_input_bytes=statistics.median(row['retained_input_bytes'] for row in rows)))
    (out / 'measurements.json').write_text(json.dumps(report, indent=2)+'\n')
    print('MEMORY_REPORT='+json.dumps(report), flush=True)

if __name__ == '__main__':
    main()
