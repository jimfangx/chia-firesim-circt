#!/usr/bin/env python3
"""Check local session ownership using fake CLIs; never contact a model."""
import contextlib
import fcntl
import io
import json
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import loop
from chia.models.codex import BillingError, CodexLLM, CodexQueryResult


CONFLICT = ('2026-10-21T13:50:15.663402Z ERROR Failed to create session: '
            'thread-store conflict: thread example-session already has an active writer')


class CodexSessionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agent = loop.StreamingCodexLLM(
            work_dir=str(self.root), resume_session=True, timeout_seconds=2, retries=1)
        self.agent._get_node_id = lambda: 'test-worker'
        self.agent._session_id = 'example-session'
        self.automation = patch.object(loop, 'AUTOMATION', self.root)
        self.automation.start()
        self.addCleanup(self.automation.stop)
        profiler = patch('chia.trace.profiler.get_profiler',
                         return_value=SimpleNamespace(enabled=False))
        profiler.start()
        self.addCleanup(profiler.stop)

    def result(self, returncode=0, stderr='', **kw):
        return CodexQueryResult('completed', returncode, stderr, '', **kw)

    def test_resume_never_restores_shared_database_or_rollout(self):
        original = {}
        for name in ('state_5.sqlite', 'state_5.sqlite-wal', 'state_5.sqlite-shm',
                     'sessions/rollout-example-session.jsonl'):
            path = self.root / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'current live state')
            original[name] = path.read_bytes()
        self.agent._codex_home = lambda: str(self.root)
        self.agent._session_state = {name: b'obsolete snapshot' for name in original}
        self.agent._restore_session_state()
        self.assertEqual({name: (self.root / name).read_bytes() for name in original}, original)
        self.assertIsNone(self.agent._session_state)
        self.assertEqual(self.agent._session_id, 'example-session')

    def test_capture_and_sync_transfer_only_session_id(self):
        self.agent._session_state = {'state_5.sqlite': b'obsolete snapshot'}
        response = self.result(session_state={'state_5.sqlite': b'new snapshot'})
        with patch.object(CodexLLM, '_capture_session_state', side_effect=AssertionError('must not copy database')):
            self.agent._capture_session_state(response)
        self.assertEqual(response.session_id, 'example-session')
        self.assertIsNone(response.session_state)
        response.session_id = 'next-session'
        response.session_state = {'state_5.sqlite': b'old payload'}
        self.agent._sync_session(response)
        self.assertEqual(self.agent._session_id, 'next-session')
        self.assertIsNone(response.session_state)
        self.assertIsNone(self.agent._session_state)
        self.assertEqual(response.session_state_paths, ())

    def test_conflict_is_not_billing_even_with_402_timestamp(self):
        with self.assertRaises(loop.SessionWriterConflict) as caught:
            self.agent._classify_error(self.result(1, CONFLICT))
        self.assertIn('thread-store conflict', str(caught.exception))
        restored = pickle.loads(pickle.dumps(caught.exception))
        self.assertIsInstance(restored, loop.SessionWriterConflict)
        self.assertEqual(restored.error_type, 'session_writer_conflict')

    def test_real_billing_errors_keep_their_classification(self):
        with self.assertRaises(BillingError):
            self.agent._classify_error(self.result(1, 'HTTP 402 payment required'))

    def test_success_with_old_error_text_is_not_failed(self):
        self.agent._classify_error(self.result(0, CONFLICT))

    def test_session_conflict_is_not_retried_by_backend(self):
        self.agent.retries = 3
        with patch.object(self.agent, '_run_codex', return_value=self.result(1, CONFLICT)) as run:
            with self.assertRaises(loop.SessionWriterConflict):
                self.agent.prompt('continue')
        self.assertEqual(run.call_count, 1)

    def test_worker_lock_prevents_duplicate_cli_dispatch(self):
        with (self.root / '.codex-exec.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(self.agent, '_run_codex_streamed') as run:
                with self.assertRaises(loop.SessionWriterConflict):
                    self.agent._run_codex('continue')
                run.assert_not_called()
        with patch.object(self.agent, '_run_codex_streamed', return_value=self.result()) as run:
            self.agent._run_codex('continue')
            run.assert_called_once()

    def test_oversized_formatted_prompt_never_starts_cli(self):
        self.agent.system_message = 'x' * loop.PROMPT_MAX_CHARS
        with patch.object(loop.subprocess, 'Popen') as run:
            with self.assertRaisesRegex(ValueError, 'prompt exceeds local'):
                self.agent._run_codex('continue')
        run.assert_not_called()

    def test_failed_agent_response_stops_before_verification(self):
        for response in (self.result(-1, 'writer conflict', success=False),
                         self.result(1, 'failed', success=True),
                         self.result(0, 'missing successful call', success=False)):
            with self.assertRaisesRegex(RuntimeError, 'verification and further implementation'):
                loop._require_codex_success(response)
        loop._require_codex_success(self.result(success=True))

    def test_main_disables_task_retries_and_stops_after_failed_call(self):
        response = self.result(-1, 'writer conflict', success=False)
        prompt = unittest.mock.Mock()
        prompt.options.return_value = prompt
        prompt.chia_remote.return_value = response
        agent = SimpleNamespace(model=None, prompt=prompt)
        with patch.object(loop, 'StreamingCodexLLM', return_value=agent), \
                patch.object(loop.ray, 'init'), patch.object(loop.ray, 'shutdown'), \
                patch.object(loop, 'start_collector'), \
                patch.object(loop, '_live_model', return_value=None), \
                patch.object(loop, '_agent_prompt', return_value='continue'), \
                patch.object(loop, 'get', side_effect=lambda value: value), \
                patch.object(loop, '_verification_graph') as verification, \
                patch.object(sys, 'argv', ['loop.py', '--iterations', '1']), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'verification and further implementation'):
                loop.main()
        prompt.options.assert_called_once_with(max_retries=0, retry_exceptions=False)
        prompt.chia_remote.assert_called_once()
        verification.assert_not_called()

    def test_verification_first_skips_initial_model_call_without_awarding_completion(self):
        prompt = unittest.mock.Mock()
        agent = SimpleNamespace(model=None, prompt=prompt)
        with patch.object(loop, 'StreamingCodexLLM', return_value=agent) as constructor, \
                patch.object(loop.ray, 'init'), patch.object(loop.ray, 'shutdown'), \
                patch.object(loop, 'start_collector'), \
                patch.object(loop, '_live_model', return_value=None), \
                patch.object(loop, '_agent_prompt', side_effect=AssertionError('implementation must wait')), \
                patch.object(loop, '_verification_graph', return_value=[]) as verification, \
                patch.object(loop, '_graph_complete', return_value=True), \
                patch.object(sys, 'argv', ['loop.py', '--iterations', '1', '--verify', 'full',
                                         '--verification-first', '--implementation-timeout-seconds', '14400',
                                         '--verification-config', str(loop.REPO / 'automation/chia_firesim_circt/verification.yaml')]), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'Iteration limit reached'):
                loop.main()
        prompt.options.assert_not_called()
        verification.assert_called_once()
        self.assertEqual(constructor.call_args.kwargs['timeout_seconds'], 14400)

    def test_verification_first_rejects_non_full_modes(self):
        with patch.object(sys, 'argv', ['loop.py', '--verify', 'feature-implementation',
                                       '--verification-first']), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                loop.main()

    def test_unjoined_verification_workers_stop_before_another_model_call(self):
        prompt = unittest.mock.Mock()
        agent = SimpleNamespace(model=None, prompt=prompt)
        with patch.object(loop, 'StreamingCodexLLM', return_value=agent), \
                patch.object(loop.ray, 'init'), patch.object(loop.ray, 'shutdown'), \
                patch.object(loop, 'start_collector'), \
                patch.object(loop, '_live_model', return_value=None), \
                patch.object(loop, '_verification_graph', return_value=[{'node': 'feature_linux',
                    'ok': False, 'workers_joined': False}]), \
                patch.object(sys, 'argv', ['loop.py', '--iterations', '2', '--verify', 'full',
                    '--verification-first', '--verification-config',
                    str(loop.REPO / 'automation/chia_firesim_circt/verification.yaml')]), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'Verification workers were not joined'):
                loop.main()
        prompt.options.assert_not_called()

    def test_implementation_timeout_must_be_positive(self):
        with patch.object(sys, 'argv', ['loop.py', '--implementation-timeout-seconds', '0']), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                loop.main()

    def test_fake_cli_streams_output_and_preserves_resume_id(self):
        def command(*args, output_last_message_path=None, resume_session_id=None):
            self.assertEqual(resume_session_id, 'example-session')
            script = ('import json; from pathlib import Path; '
                      f'Path({output_last_message_path!r}).write_text("finished"); '
                      'print(json.dumps({"type":"thread.started","thread_id":"example-session"}))')
            return [sys.executable, '-u', '-c', script]
        with patch.object(self.agent, '_build_cmd', side_effect=command), contextlib.redirect_stdout(io.StringIO()):
            response = self.agent._run_codex('continue')
        self.assertEqual(response.returncode, 0)
        self.assertEqual(response.result, 'finished')
        self.assertEqual(response.session_id, 'example-session')

    def test_timeout_stops_cli_descendants_and_releases_lock(self):
        pid_file = self.root / 'child.pid'
        script = ('import subprocess, sys, time; from pathlib import Path; '
                  'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                  f'Path({str(pid_file)!r}).write_text(str(child.pid)); '
                  'time.sleep(60)')
        self.agent.timeout_seconds = 0.2
        with patch.object(self.agent, '_build_cmd', return_value=[sys.executable, '-u', '-c', script]), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.agent._run_codex('continue')
        child = int(pid_file.read_text())
        deadline = time.monotonic() + 2
        while Path(f'/proc/{child}/stat').exists():
            state = Path(f'/proc/{child}/stat').read_text().split()[2]
            if state == 'Z':
                break
            if time.monotonic() >= deadline:
                self.fail('CLI child survived the timeout')
            time.sleep(0.02)
        with (self.root / '.codex-exec.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


if __name__ == '__main__':
    unittest.main()
