"""Stop one timed-out Jasper project and its detached descendants.

Run on the proof host. Match a project only through an exact -proj/--project
argument, then follow descendants. Protect this helper and its ancestors, and
recheck process start times before signaling so PID reuse cannot change scope.
No proof files are removed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import time


def process_table():
    result = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            arguments = (entry / 'cmdline').read_bytes().split(b'\0')
            result[int(entry.name)] = {
                'pid': int(entry.name), 'ppid': int(fields[1]),
                'sid': int(fields[3]), 'start_ticks': int(fields[19]), 'state': fields[0],
                'argv': [v.decode(errors='replace') for v in arguments if v],
            }
        except (OSError, ValueError, IndexError):
            pass
    return result


def ancestors(table, pid):
    protected = {pid}
    while pid in table:
        pid = table[pid]['ppid']
        if pid in protected:
            break
        protected.add(pid)
    return protected


def matches_project(arguments, project):
    for index, argument in enumerate(arguments[:-1]):
        if argument in ('-proj', '--project'):
            value = Path(arguments[index + 1])
            if value.is_absolute() and value.resolve() == project:
                return True
    return False


def alive(identity, table):
    current = table.get(identity['pid'])
    return (current is not None and current['start_ticks'] == identity['start_ticks']
            and current['state'] not in ('Z', 'X'))


def record_launcher(project, pid):
    """Save the detached launcher identity before its console can disappear."""
    project = Path(project).resolve()
    row = process_table().get(pid)
    if row is None:
        return False
    if row['sid'] != pid:
        raise ValueError('proof launcher must own its detached process session')
    record = {'schema_version': 1, 'project': str(project), 'pid': pid,
              'sid': row['sid'], 'start_ticks': row['start_ticks']}
    (project.parent / 'jg-launcher-identity.json').write_text(json.dumps(record) + '\n')
    return True


def cleanup_project(project, term_grace_seconds=10.0, kill_grace_seconds=5.0):
    project = Path(project)
    if not project.is_absolute() or project.name != 'jgproject':
        raise ValueError('cleanup requires the absolute per-run jgproject directory')
    project = project.resolve(strict=True)
    if not project.is_dir():
        raise ValueError('project must be an existing directory')
    if not 0 <= term_grace_seconds <= 30 or not 0 <= kill_grace_seconds <= 30:
        raise ValueError('cleanup grace must be between zero and thirty seconds')
    table = process_table()
    protected = ancestors(table, os.getpid())
    owned = {pid: row for pid, row in table.items()
             if pid not in protected and matches_project(row['argv'], project)}
    identity_file = project.parent / 'jg-launcher-identity.json'
    recorded_session = None
    if identity_file.exists():
        identity = json.loads(identity_file.read_text())
        if (identity.get('schema_version') != 1 or identity.get('project') != str(project)
                or any(type(identity.get(k)) is not int or identity[k] <= 0
                       for k in ('pid', 'sid', 'start_ticks'))
                or identity['pid'] != identity['sid']):
            raise ValueError('invalid recorded proof launcher identity')
        current = table.get(identity['pid'])
        # While a detached session still has members its SID remains reserved;
        # when its PID has been reused, reject the old record instead.
        if current is None or current['start_ticks'] == identity['start_ticks']:
            recorded_session = identity['sid']
            owned.update({pid: row for pid, row in table.items()
                          if pid not in protected and row['sid'] == recorded_session})
    root_pids = sorted(owned)

    def discover(current):
        while True:
            additions = {pid: row for pid, row in current.items()
                         if pid not in protected and pid not in owned
                         and row['ppid'] in owned
                         and alive(owned[row['ppid']], current)}
            if not additions:
                break
            owned.update(additions)

    discover(table)
    signaled = {signal.SIGTERM: set(), signal.SIGKILL: set()}

    def send(sig, current):
        for pid, row in list(owned.items()):
            if pid in protected or pid in signaled[sig] or not alive(row, current):
                continue
            try:
                os.kill(pid, sig)
                signaled[sig].add(pid)
            except ProcessLookupError:
                pass

    for sig, grace in [(signal.SIGTERM, term_grace_seconds), (signal.SIGKILL, kill_grace_seconds)]:
        deadline = time.monotonic() + grace
        while True:
            table = process_table()
            discover(table)
            send(sig, table)
            table = process_table()
            if not any(alive(row, table) for row in owned.values()) or time.monotonic() >= deadline:
                break
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    table = process_table()
    survivors = sorted(pid for pid, row in owned.items() if alive(row, table))
    return {
        'project': str(project), 'success': not survivors, 'roots': root_pids,
        'owned': [{'pid': row['pid'], 'start_ticks': row['start_ticks']} for row in owned.values()],
        'term_signaled': sorted(signaled[signal.SIGTERM]),
        'kill_signaled': sorted(signaled[signal.SIGKILL]), 'survivors': survivors,
        'recorded_session': recorded_session,
        'policy': 'Exact project argument/recorded launcher session and descendants; self/ancestors protected; PID start times checked; files preserved.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--term-grace-seconds', type=float, default=10)
    parser.add_argument('--kill-grace-seconds', type=float, default=5)
    arguments = parser.parse_args()
    result = cleanup_project(arguments.project, arguments.term_grace_seconds, arguments.kill_grace_seconds)
    print(json.dumps(result), flush=True)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
