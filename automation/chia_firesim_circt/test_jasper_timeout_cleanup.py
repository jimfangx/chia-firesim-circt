"""Exercise detached proof-project cleanup with local toy processes only."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import jasper_timeout_cleanup as cleanup
import run_jasper


class JasperTimeoutCleanupTest(unittest.TestCase):
    def test_project_argument_matching_is_exact_and_canonical(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / 'jgproject'
            project.mkdir()
            self.assertTrue(cleanup.matches_project(['jg', '-proj', str(project)], project))
            self.assertTrue(cleanup.matches_project(['jg', '--project', str(project)], project))
            self.assertFalse(cleanup.matches_project(['jg', '-proj', str(project) + '-other'], project))
            self.assertFalse(cleanup.matches_project(['sh', '-c', 'jg -proj ' + str(project)], project))

    def test_stale_pid_identity_is_not_signaled_as_owned(self):
        old = {'pid': 123, 'start_ticks': 456}
        self.assertFalse(cleanup.alive(old, {123: {'start_ticks': 789, 'state': 'S'}}))
        self.assertFalse(cleanup.alive(old, {123: {'start_ticks': 456, 'state': 'Z'}}))

    def test_cleanup_rejects_unscoped_path(self):
        for path in ('/', '/tmp', 'relative/jgproject'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                cleanup.cleanup_project(path)

    def test_copied_flow_records_launcher_without_changing_original(self):
        source = Path('/scratch/jfx/circt_compare/sec_flow/run_sec.py')
        if not source.exists():
            self.skipTest('existing Jasper flow is unavailable')
        before = source.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            flow = Path(temporary)
            (flow / 'run_sec.py').write_bytes(before)
            run_jasper.stage_timeout_cleanup(flow)
            text = (flow / 'run_sec.py').read_text()
            self.assertIn('record_launcher(proj, proc.pid)', text)
            self.assertTrue((flow / 'jasper_timeout_cleanup.py').is_file())
            compile(text, str(flow / 'run_sec.py'), 'exec')
        self.assertEqual(source.read_bytes(), before)

    def test_recorded_session_recovers_orphan_after_console_exits(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / 'proof/jgproject'
            project.mkdir(parents=True)
            parent_code = ('import subprocess,sys,time;'
                           'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
                           'print(p.pid,flush=True);time.sleep(0.3)')
            parent = subprocess.Popen([sys.executable, '-c', parent_code, '-proj', str(project)],
                                      start_new_session=True, stdout=subprocess.PIPE, text=True)
            child_pid = int(parent.stdout.readline())
            try:
                self.assertTrue(cleanup.record_launcher(project, parent.pid))
                parent.wait(timeout=2)
                result = cleanup.cleanup_project(project, 0.1, 1)
                self.assertTrue(result['success'], result)
                self.assertEqual(result['recorded_session'], parent.pid)
                self.assertIn(child_pid, [row['pid'] for row in result['owned']])
            finally:
                for pid in (parent.pid, child_pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                parent.wait(timeout=2)
                parent.stdout.close()

    def test_cli_kills_detached_project_children_and_preserves_other_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / 'ours/jgproject'
            other_project = root / 'other/jgproject'
            project.mkdir(parents=True)
            other_project.mkdir(parents=True)
            marker = project / 'retained-report.txt'
            marker.write_text('counterexamples must survive cleanup')
            child_code = 'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'
            parent_code = ('import subprocess,sys,signal,time;'
                           'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                           f'p=subprocess.Popen([sys.executable,"-c",{child_code!r}],start_new_session=True);'
                           'print(p.pid,flush=True);time.sleep(60)')
            parent = subprocess.Popen([sys.executable, '-c', parent_code, '-proj', str(project)],
                                      start_new_session=True, stdout=subprocess.PIPE, text=True)
            sentinel = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)',
                                         '-proj', str(other_project)], start_new_session=True)
            child_pid = int(parent.stdout.readline())
            try:
                # The helper itself receives --project and must protect itself
                # and all ancestors while killing another process session.
                result = subprocess.run(
                    [sys.executable, '-B', str(Path(cleanup.__file__)), '--project', str(project),
                     '--term-grace-seconds', '0.1', '--kill-grace-seconds', '1'],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads(result.stdout)
                self.assertTrue(receipt['success'], receipt)
                self.assertIn(parent.pid, receipt['roots'])
                self.assertIn(child_pid, [row['pid'] for row in receipt['owned']])
                self.assertIn(parent.pid, receipt['kill_signaled'])
                self.assertIn(child_pid, receipt['kill_signaled'])
                parent.wait(timeout=2)
                self.assertIsNone(sentinel.poll(), 'another project must remain alive')
                self.assertEqual(marker.read_text(), 'counterexamples must survive cleanup')
            finally:
                for pid in (parent.pid, child_pid, sentinel.pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                parent.wait(timeout=2)
                sentinel.wait(timeout=2)
                parent.stdout.close()


if __name__ == '__main__':
    unittest.main()
