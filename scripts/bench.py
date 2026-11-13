#!/usr/bin/env python3
import argparse
import base64
import csv
import http.cookiejar
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import time
from datetime import datetime
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
LAB = 'gretl-jenkins-lab'
SCENARIOS = {
    'A1': ('dynamic', 'shell', 'none'),
    'A2': ('dynamic', 'gradle', 'none'),
    'A3': ('dynamic', 'gradle', 'monorepo'),
    'B1': ('reusable', 'shell', 'none'),
    'B2': ('reusable', 'shell', 'none'),
    'B3': ('reusable', 'gradle', 'none'),
    'B4': ('reusable', 'gradle', 'monorepo'),
    'C1': ('static', 'shell', 'none'),
    'C2': ('static', 'gradle', 'none'),
    'C3': ('static', 'gradle', 'monorepo'),
    'D1': ('static', 'cli', 'none'),
    'G1': ('static', 'shell', 'small'),
    'LEGACY': ('static', 'legacy', 'monorepo'),
    'IDLE': ('idle-test', 'shell', 'none'),
}
METRICS = ('submit_ms', 'queue_ms', 'provision_s', 'containers_s',
           'agent_ready_s', 'workspace_ms', 'pipeline_tail_ms', 'unattributed_ms', 'checkout_ms', 'first_action_ms', 'runtime_ms', 'total_ms')
MARKER = re.compile(r'^BENCH ([\w-]+) (\d+)(?: (.*))?$')


def kube(*args):
    cmd = ['kubectl', '--kubeconfig', str(ROOT / '.local/kubeconfig'),
           '--context', 'k3d-' + LAB, '-n', LAB, *args]
    return subprocess.check_output(cmd, text=True, timeout=20)


def connection(url):
    secret = json.loads(kube('get', 'secret', 'jenkins', '-o', 'json'))['data']
    user = base64.b64decode(secret['jenkins-admin-user'])
    password = base64.b64decode(secret['jenkins-admin-password'])
    auth = base64.b64encode(user + b':' + password).decode()
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = build_opener(ProxyHandler({}), NoRedirect(),
                          HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener.addheaders = [('Authorization', 'Basic ' + auth)]

    def call(path, form=None):
        headers = crumb if form is not None else {}
        request = Request(url + path, data=None if form is None else urlencode(form).encode(),
                          headers=headers)
        with opener.open(request, timeout=20) as response:
            return response.read().decode(), response.headers

    crumb = {}
    data = json.loads(call('/crumbIssuer/api/json')[0])
    crumb[data['crumbRequestField']] = data['crumb']
    return call


def pods():
    result = {}
    for pod in json.loads(kube('get', 'pods', '-o', 'json'))['items']:
        for container in pod['spec']['containers']:
            for field in ('env', 'command', 'args'):
                container.pop(field, None)
        pod['metadata'].pop('annotations', None)
        result[pod['metadata']['name']] = pod
    return result


def timestamp(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)


def durations(row, details, markers):
    def at(name):
        return markers.get(name, (None, ''))[0]

    def delta(end, start):
        return end - start if end is not None and start is not None else None

    build = details.get('build', {})
    queue = details.get('queue', {})
    pod = details.get('pod') or {}
    accepted = queue.get('inQueueSince')
    ready = at('agent-ready')
    created = started = None
    if pod:
        row['pod_uid'] = pod['metadata']['uid']
        if row.get('new_pod'):
            created = timestamp(pod['metadata']['creationTimestamp'])
            starts = [timestamp(c['state']['running']['startedAt'])
                      for c in pod.get('status', {}).get('containerStatuses', [])
                      if 'running' in c.get('state', {})]
            if len(starts) == len(pod['spec']['containers']):
                started = max(starts)
    def seconds(end, start):
        elapsed = delta(end, start)
        return round(elapsed / 1000) if elapsed is not None else None

    checkout = delta(at('checkout-end'), at('checkout-start'))
    runtime = delta(at('runtime-end'), at('runtime-start'))
    workspace = delta(at('workspace-end'), at('workspace-start'))
    tail = delta(at('pipeline-end'), at('runtime-end'))
    total = details.get('elapsed_ms')
    before_runtime = delta(at('runtime-start'), details['sent'])
    residual = total - before_runtime - runtime - tail if None not in (total, before_runtime, runtime, tail) else None
    row.update(submit_ms=delta(accepted, details['sent']),
               queue_ms=delta(ready, accepted), provision_s=seconds(created, accepted),
               containers_s=seconds(started, created), agent_ready_s=seconds(ready, started),
               checkout_ms=checkout, workspace_ms=workspace, pipeline_tail_ms=tail,
               unattributed_ms=residual,
               first_action_ms=delta(at('first-action'), at('runtime-start')),
               runtime_ms=runtime,
               total_ms=details.get('elapsed_ms'), build=build.get('number'),
               revision=markers.get('revision', (None, ''))[1])


def run(call, scenario, iteration, daemon):
    agent, runtime, checkout = SCENARIOS[scenario]
    before = pods()
    if scenario == 'B1':
        for name, pod in before.items():
            if name.startswith('gretl-reusable-'):
                kube('delete', 'pod', name, '--wait=true', '--timeout=20s')
        before = pods()
    row = dict(scenario=scenario, iteration=iteration, agent=agent, runtime=runtime, arch=os.uname().machine,
               checkout=checkout, daemon=daemon, status='ERROR')
    details = {'sent': time.time_ns() // 1000000}
    markers = {}
    observed = dict(before)
    start = time.monotonic()
    try:
        _, headers = call('/job/bench/buildWithParameters',
                          dict(AGENT=agent, RUNTIME=runtime, CHECKOUT=checkout,
                               DAEMON=str(daemon).lower()))
        queue_path = urlsplit(headers['Location']).path
        build_path = None
        while time.monotonic() - start < 600:
            observed.update(pods())
            if not build_path:
                details['queue'] = json.loads(call(queue_path + 'api/json')[0])
                executable = details['queue'].get('executable')
                if executable:
                    build_path = urlsplit(executable['url']).path
            if build_path:
                details['build'] = json.loads(call(build_path + 'api/json')[0])
                if not details['build']['building'] and details['build']['result']:
                    details['elapsed_ms'] = round((time.monotonic() - start) * 1000)
                    break
            time.sleep(0.5)
        else:
            raise TimeoutError('build exceeded 600 seconds')
        details['console'] = call(build_path + 'consoleText')[0]
        for line in details['console'].splitlines():
            match = MARKER.search(re.sub(r'^\S+\s+(?=BENCH )', '', line))
            if match:
                name, stamp, value = match.groups()
                markers.setdefault(name, (int(stamp), value or ''))
        pod_name = markers.get('pod', (None, ''))[1]
        details['pod'] = observed.get(pod_name)
        if details['pod']:
            details['events'] = json.loads(kube(
                'get', 'events', '--field-selector',
                'involvedObject.uid=' + details['pod']['metadata']['uid'], '-o', 'json'
            ))['items']
        previous = before.get(pod_name)
        if details['pod']:
            row['new_pod'] = not previous or previous['metadata']['uid'] != details['pod']['metadata']['uid']
        events = details.get('events', [])
        row['image_cache'] = ('pull' if any(e['reason'] == 'Pulling' for e in events)
                              else 'cached') if row.get('new_pod') else 'running'
        row['status'] = details['build']['result']
        if row['status'] == 'SUCCESS':
            expected_new = scenario.startswith('A') or scenario in ('B1', 'IDLE')
            if not details['pod'] or 'first-action' not in markers or row['new_pod'] != expected_new:
                row['status'] = 'INVALID'
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        details['error'] = str(error)
    durations(row, details, markers)
    return row, details


def summary(rows):
    result = {}
    for scenario in dict.fromkeys(row['scenario'] for row in rows):
        attempts = [row for row in rows if row['scenario'] == scenario]
        valid = [row for row in attempts if row['status'] == 'SUCCESS']
        stats = {'attempts': len(attempts), 'successful': len(valid),
                 'error_rate': 1 - len(valid) / len(attempts)}
        for metric in METRICS:
            values = sorted(row[metric] for row in valid if row.get(metric) is not None)
            if values:
                stats[metric] = dict(n=len(values), median=statistics.median(values),
                                     p90=values[math.ceil(len(values) * .9) - 1],
                                     min=values[0], max=values[-1])
        result[scenario] = stats
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('scenarios', nargs='*')
    parser.add_argument('-n', '--iterations', type=int, default=10)
    parser.add_argument('--daemon', action='store_true')
    parser.add_argument('--port', type=int, default=18080)
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'gretl-jenkins-spec/measurements')
    args = parser.parse_args()
    scenarios = args.scenarios or list(SCENARIOS)[:11]
    if args.iterations < 1 or any(s not in SCENARIOS for s in scenarios):
        parser.error('choose a positive iteration count and scenarios ' + ', '.join(SCENARIOS))
    if kube('config', 'current-context').strip() != 'k3d-' + LAB:
        parser.error('unexpected Kubernetes context')
    output = args.output / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True)
    call = connection(f'http://127.0.0.1:{args.port}')
    rows = []
    fields = ('scenario', 'iteration', 'agent', 'runtime', 'checkout', 'daemon', 'build',
              'pod_uid', 'new_pod', 'revision', 'arch', 'image_cache', 'status', *METRICS)
    with (output / 'runs.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        file.flush()
        for scenario in scenarios:
            for iteration in range(1, args.iterations + 1):
                row, details = run(call, scenario, iteration, args.daemon)
                (output / f'{scenario}-{iteration}.json').write_text(json.dumps(details, indent=2))
                rows.append(row)
                writer.writerow(row)
                file.flush()
                print(scenario, iteration, row['status'], row['pod_uid'] if 'pod_uid' in row else '', flush=True)
    (output / 'summary.json').write_text(json.dumps(summary(rows), indent=2) + '\n')
    print(output)
    return int(any(row['status'] != 'SUCCESS' for row in rows))


if __name__ == '__main__':
    os.umask(0o077)
    raise SystemExit(main())
