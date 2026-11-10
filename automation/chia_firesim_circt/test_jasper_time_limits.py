"""Proof-launch time policy, using local toy commands and no proof/cloud jobs."""
import contextlib
import io
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

import yaml

import run_jasper


class JasperTimeLimitTest(unittest.TestCase):
    def base_arguments(self):
        return ["--repo", "/tmp/test-repo", "--target", "rocket", "--mode", "lec"]

    def test_default_has_24h_proof_and_report_grace_and_keeps_memory_guard(self):
        arguments = run_jasper.build_parser().parse_args(self.base_arguments())
        self.assertEqual(arguments.prove_time, "24h")
        self.assertEqual(arguments.wall_timeout_seconds, 87000)
        self.assertEqual(arguments.max_mem_gb, 128)
        self.assertEqual(arguments.jobs, 1)

    def test_explicit_finite_operator_budgets_remain_available(self):
        arguments = run_jasper.build_parser().parse_args(
            self.base_arguments() + ["--prove-time", "45m", "--wall-timeout-seconds", "3600"])
        self.assertEqual(arguments.prove_time, "45m")
        self.assertEqual(arguments.wall_timeout_seconds, 3600)

    def test_explicit_unlimited_overrides_remain_available(self):
        arguments = run_jasper.build_parser().parse_args(
            self.base_arguments() + ["--prove-time", "0s", "--wall-timeout-seconds", "0"])
        self.assertEqual(arguments.prove_time, "0s")
        self.assertEqual(arguments.wall_timeout_seconds, 0)
        shell = run_jasper.proof_shell(["true"], "/tmp/run", "/tmp/license", 0)
        self.assertNotIn("timeout", shlex.split(shell))

    def test_chia_configuration_preserves_report_and_archive_grace(self):
        config = yaml.safe_load(Path(__file__).with_name("verification.yaml").read_text())
        for mode in ("lec", "sec"):
            with self.subTest(mode=mode):
                adapter = config["jasper"][mode]
                argv = adapter["argv"]
                self.assertEqual(argv[argv.index("--prove-time") + 1], "24h")
                self.assertEqual(int(argv[argv.index("--wall-timeout-seconds") + 1]), 86400 + 600)
                self.assertEqual(adapter["timeout_seconds"], 86400 + 1200)
                self.assertEqual(argv[argv.index("--max-mem-gb") + 1], "128")
                self.assertEqual(argv[argv.index("--jobs") + 1], "1")

    def test_negative_or_fractional_wall_deadlines_are_rejected(self):
        for value in ("-1", "1.5", "none"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    run_jasper.build_parser().parse_args(
                        self.base_arguments() + ["--wall-timeout-seconds", value])
                self.assertEqual(error.exception.code, 2)

    def test_default_command_has_remote_deadline_and_preserves_arguments(self):
        command = ["python3", "sec_flow/run_sec.py", "--prove-time", "0s", "--spec", "a file;literal.sv"]
        before = command.copy()
        shell = run_jasper.proof_shell(command, "/tmp/a remote", "/tmp/license script.sh")
        self.assertIn("timeout", shlex.split(shell))
        self.assertIn("87000", shlex.split(shell))
        self.assertIn(shlex.join(command), shell)
        self.assertEqual(command, before)
        self.assertIn("'/tmp/license script.sh'", shell)
        self.assertIn("'/tmp/a remote/bundle'", shell)

    def test_explicit_wall_deadline_installs_remote_timeout(self):
        shell = run_jasper.proof_shell(["python3", "flow.py"], "/tmp/run", "/tmp/license", 17)
        self.assertEqual(shlex.split(shell)[-6:],
                         ["timeout", "--signal=TERM", "--kill-after=30s", "17", "python3", "flow.py"])

    def test_remote_shell_rejects_invalid_deadline_types(self):
        for value in (-1, 0.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_jasper.proof_shell(["true"], "/tmp/run", "/tmp/license", value)

    def test_unlimited_local_toy_command_completes_and_explicit_cap_expires(self):
        with tempfile.TemporaryDirectory(prefix="jasper time policy ") as directory:
            root = Path(directory)
            (root / "bundle").mkdir()
            license_script = root / "license test.sh"
            license_script.write_text("export CHIA_TIME_POLICY_TEST=staged\n")
            command = [sys.executable, "-c", "import os,time;time.sleep(1.2);print(os.environ['CHIA_TIME_POLICY_TEST'])"]
            shell = run_jasper.proof_shell(command, root, license_script, 0)
            completed = subprocess.run(["bash", "-lc", shell], text=True, capture_output=True, timeout=5)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "staged")
            shell = run_jasper.proof_shell(command, root, license_script, 1)
            completed = subprocess.run(["bash", "-lc", shell], text=True, capture_output=True, timeout=5)
            self.assertEqual(completed.returncode, 124)


if __name__ == "__main__":
    unittest.main()
