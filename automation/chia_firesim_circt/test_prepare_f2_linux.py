"""Validate the real private Linux filesystem overlay without booting hardware."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import prepare_f2_linux as linux


@unittest.skipUnless(shutil.which("mkfs.ext4") and (shutil.which("debugfs") or Path("/usr/sbin/debugfs").exists()),
                     "ext4 filesystem tools required")
class PrivateLinuxImageTest(unittest.TestCase):
    def fixture(self, root):
        template = root / "sims/firesim/deploy/workloads/br-base-uniform.json"
        template.parent.mkdir(parents=True)
        base = root / "base images"
        base.mkdir()
        kernel, image = base / "kernel", base / "rootfs.img"
        kernel.write_bytes(b"\x7fELFfixture-linux-kernel")
        with image.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024)
        subprocess.run(["mkfs.ext4", "-q", "-F", str(image)], check=True, capture_output=True)
        for directory in ("/etc", "/etc/init.d"):
            linux.debugfs(image, "mkdir " + directory, write=True)
        hook = root / "S99run"
        hook.write_text('#!/bin/sh\n/firemarshal.sh\n')
        linux.debugfs(image, 'write ' + linux.quote_debugfs(hook) + ' /etc/init.d/S99run', write=True)
        linux.debugfs(image, 'set_inode_field /etc/init.d/S99run mode 0100775', write=True)
        old = root / "old-script"
        old.write_text('#!/bin/sh\nexit 0\n')
        linux.debugfs(image, 'write ' + linux.quote_debugfs(old) + ' /firemarshal.sh', write=True)
        directory = template.parent / "br-base-uniform"
        template.write_text(json.dumps({"benchmark_name": "br-base-uniform",
                                       "common_bootbinary": os.path.relpath(kernel, directory),
                                       "common_rootfs": os.path.relpath(image, directory),
                                       "common_outputs": ["/etc/os-release"]}))
        return template, kernel, image

    def test_private_executable_overlay_preserves_every_base_byte(self):
        with tempfile.TemporaryDirectory(prefix="chia-linux ") as directory:
            repo = Path(directory)
            template, kernel, image = self.fixture(repo)
            before = [linux.record(path) for path in (template, kernel, image)]
            prepared = linux.prepare_linux_workload(repo, repo / "private assets")
            self.assertEqual(before, [linux.record(path) for path in (template, kernel, image)])
            self.assertEqual(prepared["bootbinary"], kernel)
            self.assertNotEqual(prepared["rootfs"], image)
            self.assertEqual(linux.debugfs(prepared["rootfs"], "cat /firemarshal.sh"), linux.GUEST_SCRIPT)
            self.assertTrue(linux.executable_stat(linux.debugfs(prepared["rootfs"], "stat /firemarshal.sh")))
            self.assertEqual(linux.debugfs(image, "cat /firemarshal.sh"), '#!/bin/sh\nexit 0\n')
            manifest = json.loads(prepared["manifest"].read_text())
            self.assertEqual(manifest["private_rootfs"], linux.record(prepared["rootfs"]))
            self.assertIn("preparation only", manifest["verification_claim"])
            self.assertEqual(prepared["boot_marker"], "CIRCT_LINUX_BOOT: PASS")
            self.assertEqual(prepared["shutdown_marker"], "CIRCT_LINUX_POWEROFF: REQUESTED")
            with self.assertRaises(ValueError):
                linux.prepare_linux_workload(repo, repo / "private assets")

    def test_feature_hook_binary_overlay_round_trip_and_duplicate_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.fixture(repo)
            hook = repo / "feature.sh"
            hook.write_text("#!/bin/sh\n/usr/bin/feature-check\n")
            binary = repo / "feature-check"
            binary.write_bytes(b"\x7fELF\x00\xff\x81feature-binary")
            prepared = linux.prepare_linux_workload(repo, repo / "private", guest_script=hook,
                overlays=[(binary, "/usr/bin/feature-check", 0o755)])
            self.assertEqual(linux.debugfs(prepared["rootfs"], "cat /firemarshal.sh"), hook.read_text())
            manifest = json.loads(prepared["manifest"].read_text())
            self.assertEqual(manifest["overlays"][0]["source"], linux.record(binary))
            self.assertTrue(linux.executable_stat(linux.debugfs(prepared["rootfs"], "stat /usr/bin/feature-check")))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                linux.prepare_linux_workload(repo, repo / "duplicate", guest_script=hook,
                    overlays=[(binary, "/usr/bin/feature-check", 0o755), (binary, "/usr/bin/feature-check", 0o755)])

    def test_unrecognized_or_nonexecutable_boot_hook_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            template, kernel, image = self.fixture(repo)
            linux.debugfs(image, 'set_inode_field /etc/init.d/S99run mode 0100644', write=True)
            with self.assertRaises(ValueError):
                linux.prepare_linux_workload(repo, repo / "private")
            self.assertFalse((repo / "private/br-base-poweroff.img").exists())

    def test_guest_checks_precede_markers_and_explicit_poweroff(self):
        script = linux.GUEST_SCRIPT
        marker = script.index(linux.BOOT_MARKER)
        for check in ("/proc/sys/kernel/ostype", "/etc/os-release", "MemTotal:", "/sys/devices/system/cpu/cpu0"):
            self.assertLess(script.index(check), marker)
        self.assertLess(marker, script.index(linux.POWEROFF_MARKER))
        self.assertLess(script.index(linux.POWEROFF_MARKER), script.index("sync ||"))
        self.assertLess(script.index("sync ||"), script.index("poweroff -f"))
        self.assertIn("CIRCT_LINUX_POWEROFF: FAIL poweroff returned", script)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh") as script_file:
            script_file.write(script)
            script_file.flush()
            subprocess.run(["sh", "-n", script_file.name], check=True)

    @unittest.skipUnless(Path("/sbin/poweroff").exists() and Path("/sys/devices/system/cpu/cpu0").exists(),
                         "Linux guest-check host paths required for shell logic fixture")
    def test_guest_command_failure_and_unexpected_poweroff_return_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / "bin"
            commands.mkdir()
            fake = {
                "uname": '#!/bin/sh\nprintf "Linux\\n"\n',
                "cat": '#!/bin/sh\ncase "$1" in */ostype) printf "Linux\\n";; *) printf "ID=buildroot\\n";; esac\n',
                "grep": '#!/bin/sh\nexit 0\n',
                "sync": '#!/bin/sh\nprintf "sync\\n" >> "$GUEST_TEST_LOG"\n',
                "poweroff": '#!/bin/sh\nprintf "poweroff %s\\n" "$*" >> "$GUEST_TEST_LOG"\nexit 0\n'}
            for name, contents in fake.items():
                path = commands / name
                path.write_text(contents)
                path.chmod(0o755)
            script = root / "firemarshal.sh"
            script.write_text(linux.GUEST_SCRIPT)
            log = root / "guest-commands.log"
            env = {**os.environ, "PATH": str(commands), "GUEST_TEST_LOG": str(log)}
            completed = subprocess.run(["/bin/sh", str(script)], env=env, text=True, capture_output=True)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(linux.BOOT_MARKER, completed.stdout)
            self.assertIn(linux.POWEROFF_MARKER, completed.stdout)
            self.assertIn("CIRCT_LINUX_POWEROFF: FAIL poweroff returned", completed.stderr)
            self.assertEqual(log.read_text(), "sync\npoweroff -f\n")
            log.unlink()
            (commands / "cat").write_text('#!/bin/sh\nexit 1\n')
            failed = subprocess.run(["/bin/sh", str(script)], env=env, text=True, capture_output=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("CIRCT_LINUX_BOOT: FAIL", failed.stderr)
            self.assertNotIn(linux.BOOT_MARKER, failed.stdout)
            self.assertNotIn(linux.POWEROFF_MARKER, failed.stdout)
            self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
