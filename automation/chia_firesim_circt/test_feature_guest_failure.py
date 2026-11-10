#!/usr/bin/env python3
"""Execute guest hooks with isolated local commands; never power off the host."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

import run_f2


HERE = Path(__file__).resolve().parent


class GuestFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'poweroff.log'
        self.case = self.root / 'nic-case'
        self.case.write_text('normal 0 eth0 128\nnormal 1 eth1 128\n')
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        TEST_ROOT=str(self.root), TEST_POWEROFF_LOG=str(self.log),
                        CIRCT_NIC_CASE=str(self.case))
        self.command('uname', '#!/bin/sh\nprintf "%s\\n" "${TEST_UNAME:-Linux}"\n')
        # Avoid inspecting or changing the host network. Guest sysfs/module
        # setup is tested separately in the actual private-rootfs adapter.
        self.command('ls', '#!/bin/sh\nexit 0\n')
        self.command('sync', '#!/bin/sh\nprintf "sync\\n" >> "$TEST_POWEROFF_LOG"\n')
        self.command('poweroff', '''#!/bin/sh
printf 'poweroff %s\n' "$*" >> "$TEST_POWEROFF_LOG"
if [ "${TEST_SHUTDOWN:-return}" = terminate_parent ]; then
  kill -KILL "$PPID"
fi
exit "${TEST_POWEROFF_RC:-0}"
''')

    def command(self, name, script):
        path = self.bin / name
        path.write_text(script)
        path.chmod(0o755)
        return path

    def run_hook(self, name):
        return subprocess.run(['/bin/sh', str(HERE / 'feature_workloads' / name)],
                              env=self.env, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=10)

    def assert_failed_shutdown(self, completed, marker, returncode):
        self.assertEqual(completed.returncode, returncode, completed.stdout)
        self.assertIn(marker, completed.stdout)
        self.assertIn('returncode=' + str(returncode), completed.stdout)
        self.assertIn('CIRCT_LINUX_BOOT: FAIL', completed.stdout)
        self.assertNotIn('CIRCT_LINUX_POWEROFF: REQUESTED', completed.stdout)
        self.assertEqual(self.log.read_text().splitlines(), ['sync', 'poweroff -f'])
        # Even with a driver PASS appended, the existing Linux gate rejects
        # the failure transcript rather than treating shutdown as success.
        uart = ('Linux version fixture\n' + completed.stdout + '\nreboot: Power down\n'
                '*** PASSED *** after 99 cycles\nScript done COMMAND_EXIT_CODE="0"\n')
        with self.assertRaises(ValueError):
            run_f2.validate_uart(uart)

    def test_thread_sigill_powers_off_and_retains_original_failure(self):
        self.command('circt-thread-state', '#!/bin/sh\nulimit -c 0\nkill -ILL "$$"\n')
        self.env['TEST_POWEROFF_RC'] = '9'
        self.assert_failed_shutdown(self.run_hook('thread_guest.sh'),
                                    'CIRCT_THREAD_RESULT: FAIL stage=thread_execution', 132)

    def test_thread_setup_failure_powers_off_without_executing_workload(self):
        self.env['TEST_UNAME'] = 'NotLinux'
        self.command('circt-thread-state', '#!/bin/sh\nexit 66\n')
        self.assert_failed_shutdown(self.run_hook('thread_guest.sh'),
                                    'CIRCT_THREAD_RESULT: FAIL stage=setup', 1)

    def test_thread_success_reaches_poweroff_after_workload(self):
        self.command('circt-thread-state', '''#!/bin/sh
[ "$#" -eq 1 ] && [ "$1" -eq 4 ] || exit 66
printf 'CIRCT_THREAD_RESULT: PASS threads=4\n'
''')
        self.env['TEST_SHUTDOWN'] = 'terminate_parent'
        completed = self.run_hook('thread_guest.sh')
        self.assertEqual(completed.returncode, -signal.SIGKILL)
        self.assertNotIn('FAIL', completed.stdout)
        self.assertLess(completed.stdout.index('CIRCT_THREAD_RESULT: PASS'),
                        completed.stdout.index('CIRCT_LINUX_POWEROFF: REQUESTED'))
        self.assertEqual(self.log.read_text().splitlines(), ['sync', 'poweroff -f'])

    def test_missing_nic_case_powers_off_instead_of_returning_to_login(self):
        self.case.unlink()
        self.assert_failed_shutdown(self.run_hook('nic_guest.sh'),
                                    'CIRCT_NIC_FAILURE: packet workload stage=setup', 1)

    def worker(self, fail_first=False):
        self.command('circt-nic-packets', '''#!/usr/bin/env python3
import os, pathlib, signal, sys, time
root = pathlib.Path(os.environ['TEST_ROOT'])
widget = int(sys.argv[2])
(root / ('worker-%d.pid' % widget)).write_text(str(os.getpid()))
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if widget == 0 and os.environ.get('TEST_FAIL_FIRST') == '1':
    deadline = time.monotonic() + 5
    while not (root / 'worker-1.pid').exists():
        if time.monotonic() > deadline: sys.exit(99)
        time.sleep(.01)
    sys.exit(7)
time.sleep(30)
''')
        if fail_first:
            self.env['TEST_FAIL_FIRST'] = '1'

    def assert_workers_reaped(self):
        for path in self.root.glob('worker-*.pid'):
            with self.assertRaises(ProcessLookupError):
                os.kill(int(path.read_text()), 0)

    def test_nic_failure_kills_and_joins_only_its_remaining_workers(self):
        self.worker(fail_first=True)
        unrelated = subprocess.Popen(['sleep', '30'])
        try:
            completed = self.run_hook('nic_guest.sh')
            self.assert_failed_shutdown(completed,
                                        'CIRCT_NIC_FAILURE: packet workload stage=packet_join', 7)
            events = [json.loads(line.split('CIRCT_NIC_WORKER ', 1)[1])
                      for line in completed.stdout.splitlines() if 'CIRCT_NIC_WORKER ' in line]
            self.assertEqual([e['event'] for e in events], ['launched', 'launched', 'joined', 'joined'])
            self.assertEqual([e['returncode'] for e in events if e['event'] == 'joined'], [7, 137])
            self.assert_workers_reaped()
            self.assertIsNone(unrelated.poll(), 'an unrelated process was signalled')
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)

    def test_nic_signal_runs_failure_shutdown_and_reaps_owned_workers(self):
        self.worker()
        process = subprocess.Popen(['/bin/sh', str(HERE / 'feature_workloads/nic_guest.sh')],
                                   env=self.env, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while len(list(self.root.glob('worker-*.pid'))) != 2:
                if time.monotonic() > deadline:
                    self.fail('guest workers did not start')
                time.sleep(.01)
            process.send_signal(signal.SIGTERM)
            output, _ = process.communicate(timeout=5)
            self.assert_failed_shutdown(subprocess.CompletedProcess(process.args, process.returncode, output),
                                        'CIRCT_NIC_FAILURE:', 143)
            self.assert_workers_reaped()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def test_normal_nic_lifecycle_still_finishes_before_successful_shutdown(self):
        self.command('circt-nic-packets', '#!/bin/sh\nexit 0\n')
        self.env['TEST_SHUTDOWN'] = 'terminate_parent'
        completed = self.run_hook('nic_guest.sh')
        self.assertEqual(completed.returncode, -signal.SIGKILL)
        events = [json.loads(line.split('CIRCT_NIC_WORKER ', 1)[1])
                  for line in completed.stdout.splitlines() if 'CIRCT_NIC_WORKER ' in line]
        self.assertEqual(events, [dict(widget=0, event='launched'), dict(widget=1, event='launched'),
                                  dict(widget=0, event='joined', returncode=0),
                                  dict(widget=1, event='joined', returncode=0)])
        self.assertIn('CIRCT_LINUX_POWEROFF: REQUESTED', completed.stdout)
        self.assertNotIn('FAIL', completed.stdout)
        self.assertEqual(self.log.read_text().splitlines(), ['sync', 'poweroff -f'])


if __name__ == '__main__':
    unittest.main()
