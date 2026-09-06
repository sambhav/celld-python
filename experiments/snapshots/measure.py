"""Same-runner snapshot A/B: cold start, durable wake, and public stateless I/O."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'experiments/rust-pyodide')]
from harness import measured_node, wait_inactive
from test_celld import publish_local, request
from celld.build import build, lock
from celld.dev import free_port


def timed(port, path, arguments, headers=None):
    start = time.perf_counter_ns()
    status, body, response_headers = request(port, path, arguments,
        headers={'x-celld-call-id':uuid.uuid4().hex, **(headers or {})})
    elapsed = (time.perf_counter_ns() - start) / 1e6
    assert status == 200 and not response_headers.get('x-celld-replayed'), (status, body)
    return elapsed, json.loads(body)['result']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--smoke', action='store_true', help='Correctness only; no performance claims')
    parser.add_argument('--cold-only', action='store_true')
    args = parser.parse_args()
    assert args.rounds > 0 and args.seconds > 0
    out = HERE / 'build' / uuid.uuid4().hex[:12]
    out.mkdir(parents=True)
    binary = shutil.which('celld')
    report = dict(source_sha=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        native_sha=os.environ.get('CELLD_NATIVE_SHA', ''),
        binary_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        run_url=f"https://github.com/{os.environ.get('GITHUB_REPOSITORY','sambhav/celld-python')}/actions/runs/{os.environ.get('GITHUB_RUN_ID','')}",
        cpu=subprocess.check_output(['lscpu'], text=True),
        store='local SQLite development object store; no remote S3',
        smoke=args.smoke, rounds=args.rounds, seconds=args.seconds,
        protocol='HTTP/1.1 keep-alive, unique call IDs, all replies checked, no retries', samples=[])

    def record(row):
        report['samples'].append(row)
        (out / 'measurements.json').write_text(json.dumps(report, indent=2) + '\n')
        print('SNAPSHOT_SAMPLE=' + json.dumps(row), flush=True)

    projects = {}
    for snapshot in (False, True):
        project = build(ROOT / 'examples/fleet.toml', out / f'fleet-{snapshot}', snapshot=snapshot)
        projects[snapshot] = project
        publish_local(project, out / f'publish-{snapshot}.log')
    # Alternate ordering each round. Each first call gets a fresh process.
    for round_ in range(args.rounds):
        for snapshot in ((False, True) if round_ % 2 == 0 else (True, False)):
            for workload, path, arguments, expected in [
                ('hello', '/hello/hello', {'name':'Sam'}, 'Hello, Sam'),
                ('numpy', '/math/mean', {'values':[1,2,6]}, 3.0),
                ('state', '/counters/increment', {'counter_id':uuid.uuid4().hex}, None),
            ]:
                log = out / f'cold-{snapshot}-{round_}-{workload}.log'
                with measured_node(projects[snapshot], log, idle_seconds=2, stateless_isolates=1) as running:
                    headers = {'x-celld-context':'{"actor":"benchmark"}'}
                    first, value = timed(running.port, path, arguments, headers)
                    assert value == expected if workload != 'state' else value['total'] == 1
                    row = dict(phase='cold', snapshot=snapshot, workload=workload, round=round_,
                        native_ready_ms=running.startup_ms, first_request_ms=first,
                        ready_to_result_ms=running.startup_ms + first, rss_bytes=running.state()['rss_bytes'])
                    if workload == 'state':
                        scopes = [s for s in running.state()['residents'] if s.startswith('PythonCell:')]
                        assert scopes
                        inactive = wait_inactive(running, scopes)
                        wake, value = timed(running.port, path, arguments, headers)
                        assert value['total'] == 2
                        row.update(wake_request_ms=wake, idle_wait_ms=inactive['wait_ms'],
                            inactive_cells=len([s for s in inactive['state']['residents'] if s.startswith('PythonCell:')]))
                    record(row)
    if not args.cold_only:
        driver = out / 'load'
        subprocess.run(['go','build','-o',str(driver),'.'], cwd=ROOT / 'experiments/throughput/load', check=True)
        address = f'127.0.0.1:{free_port()}'
        upstream = subprocess.Popen([str(driver),'--serve',address], stdout=subprocess.DEVNULL)
        try:
            import urllib.request
            def stats(path='stats'):
                with urllib.request.urlopen(f'http://{address}/{path}', timeout=5) as response:
                    return response.read() if path == 'reset' else json.load(response)
            for _ in range(100):
                try:
                    stats()
                    break
                except OSError:
                    time.sleep(.05)
            else:
                raise RuntimeError('Upstream did not start')
            for workload in ('hello', 'io'):
                app = out / ('app-' + workload)
                (app / 'src').mkdir(parents=True)
                (app / 'pyproject.toml').write_text('[project]\nname="throughput"\nversion="0.1.0"\ndependencies=[]\n')
                source = ((ROOT / 'experiments/throughput/io_app.py').read_text().replace('__UPSTREAM_URL__', f'http://{address}/price?delay_ms=10')
                    if workload == 'io' else 'from celld import App\napp = App\n@app.function\ndef hello(name: str) -> str:\n    return f"Hello, {name}"\n')
                (app / 'src/app.py').write_text(source)
                shutil.copytree(ROOT / 'examples/.celld-python/cache', app / '.celld-python/cache')
                lock(app)
                for snapshot in (False, True):
                    template = build(app, out / f'template-{workload}-{snapshot}', snapshot=snapshot)
                    for isolates in (1, 2, 4):
                        project = out / f'warm-{workload}-{snapshot}-{isolates}'
                        shutil.copytree(template, project)
                        publish_local(project, out / (project.name + '-publish.log'))
                        with measured_node(project, out / (project.name + '.log'), stateless_isolates=isolates) as running:
                            command = [str(driver),'--url',f'http://127.0.0.1:{running.port}/hello',
                                '--workload',workload,'--clients','256']
                            # Warm the public SDK pool, with its real concurrency bounds.
                            subprocess.run(command + ['--count','4'], check=True, capture_output=True)
                            for round_ in range(args.rounds):
                                if workload == 'io':
                                    stats('reset')
                                result = subprocess.run(command + ['--seconds',str(args.seconds)],
                                    check=True, capture_output=True, text=True)
                                row = json.loads(result.stdout)
                                assert row['errors'] == 0 and row['requests'] > 0
                                row.update(phase='warm', snapshot=snapshot, workload=workload, round=round_,
                                    max_stateless_isolates=isolates, rss_bytes=running.state()['rss_bytes'])
                                if workload == 'io':
                                    upstream_stats = stats()
                                    assert upstream_stats['requests'] == upstream_stats['completed'] == row['requests'], (row, upstream_stats)
                                    assert upstream_stats['active'] == 0
                                    row['upstream'] = upstream_stats
                                record(row)
                        shutil.rmtree(project)
        finally:
            upstream.terminate()
            upstream.wait(timeout=5)
    report['summary'] = []
    groups = {(s['phase'], s['snapshot'], s['workload'], s.get('max_stateless_isolates', 1)) for s in report['samples']}
    for phase, snapshot, workload, isolates in sorted(groups):
        rows = [s for s in report['samples'] if (s['phase'], s['snapshot'], s['workload'], s.get('max_stateless_isolates', 1)) == (phase, snapshot, workload, isolates)]
        summary = dict(phase=phase, snapshot=snapshot, workload=workload, max_stateless_isolates=isolates, n=len(rows))
        for key in ('native_ready_ms','first_request_ms','ready_to_result_ms','wake_request_ms','rps','p50_ms','p95_ms','p99_ms','rss_bytes'):
            if key in rows[0]:
                summary[key] = statistics.median(s[key] for s in rows)
        report['summary'].append(summary)
    (out / 'measurements.json').write_text(json.dumps(report, indent=2) + '\n')
    print('SNAPSHOT_REPORT=' + json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
