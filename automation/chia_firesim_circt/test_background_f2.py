"""Background F2 isolation/queue contracts; no proofs, synthesis or cloud calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import background_f2 as bg


class BackgroundF2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "source"
        self.repo.mkdir()

    def file(self, path, content="data"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def fake_manifest(self, directory, identity="candidate", key="hardware"):
        repo = directory / "repo"
        repo.mkdir(parents=True)
        source = self.file(repo / "driver.cc")
        data = {"identity": identity, "hardware_key": key, "source_repo": str(self.repo),
                "repo": str(repo), "source_files": [bg.record(source)],
                "shared_immutable_images": [], "targets": {}}
        for target in bg.TARGETS:
            rtl = self.file(directory / "inputs" / target / "circt/FireSim-generated.sv", target)
            data["targets"][target] = {"recipe": {}, "recipe_name": target, "config": {},
                                       "circt_rtl": str(rtl), "files": [bg.record(rtl)]}
        bg.write(directory / "snapshot.json", data)
        return data

    def test_frozen_sources_and_images_are_independent_of_live_mutation(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        source = self.file(self.repo / "driver.cc", "dirty driver")
        root_env = self.file(self.repo / "env.sh", "CY_DIR=" + str(self.repo))
        fs_env = self.file(self.repo / "sims/firesim/env.sh", "FS_DIR=" + str(self.repo / 'sims/firesim'))
        for rel in ["software/firemarshal/images/firechip/br-base/br-base-bin",
                    "software/firemarshal/images/firechip/br-base/br-base.img"]:
            self.file(self.repo / rel)
        self.file(self.repo / ".conda-env/conda-meta/history")
        # Clone requires a commit; the copied content below is intentionally dirty.
        subprocess.run(["git", "add", "driver.cc"], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "fixture"], cwd=self.repo, check=True)
        source.write_text("uncommitted new driver")
        out = Path(self.temp.name) / "snapshot"
        frozen, files, images = bg.copy_checkout(self.repo, out, [self.repo], [source, root_env, fs_env])
        self.assertEqual((frozen / "driver.cc").read_text(), "uncommitted new driver")
        self.assertNotEqual(source.stat().st_ino, (frozen / "driver.cc").stat().st_ino)
        self.assertIn(str(frozen), (frozen / "env.sh").read_text())
        self.assertNotIn(str(self.repo), (frozen / "env.sh").read_text())
        source.write_text("next candidate")
        for item in images:
            original = self.repo / Path(item["path"]).relative_to(frozen)
            original.write_text("newly rebuilt image")
            self.assertFalse(Path(item["path"]).is_symlink())
        bg.validate(files + images)
        self.assertTrue((frozen / ".conda-env").is_symlink())

    def test_nested_snapshot_keeps_tracked_extensionless_manager_and_makefiles(self):
        def git(root, *args):
            return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
        firesim = self.repo / 'sims/firesim'
        firesim.mkdir(parents=True)
        for root in (self.repo, firesim):
            git(root, 'init', '-q')
        manager = self.file(firesim / 'deploy/firesim', '#!/bin/sh\necho archived-manager\n')
        manager.chmod(0o755)
        root_make = self.file(self.repo / 'Makefile', 'all:\n\t@echo root\n')
        fs_make = self.file(firesim / 'Makefile', 'all:\n\t@echo firesim\n')
        self.file(self.repo / 'env.sh', 'CY_DIR=' + str(self.repo))
        self.file(firesim / 'env.sh', 'FS_DIR=' + str(firesim))
        helper = self.file(firesim / 'deploy/helper.py', 'print("helper")\n')
        alias = firesim / 'deploy/helper-alias.py'
        alias.symlink_to('helper.py')
        git(firesim, 'add', 'deploy', 'Makefile', 'env.sh')
        git(firesim, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '-qm', 'FireSim fixture')
        git(self.repo, '-c', 'advice.addEmbeddedRepo=false', 'add', 'Makefile', 'env.sh', 'sims/firesim')
        git(self.repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '-qm', 'root fixture')
        for rel in ('software/firemarshal/images/firechip/br-base/br-base-bin',
                    'software/firemarshal/images/firechip/br-base/br-base.img', '.conda-env/conda-meta/history'):
            self.file(self.repo / rel)
        # A snapshot must use the dirty live bytes, while preserving HEAD's
        # tracked path identities through both private indexes.
        manager.write_text('#!/bin/sh\necho uncommitted-manager\n')
        root_make.write_text('all:\n\t@echo dirty-root\n')
        original_indexes = {root: (root / '.git/index').read_bytes() for root in (self.repo, firesim)}
        roots, files = bg.source_files(self.repo)
        self.assertEqual(set(roots), {self.repo, firesim})
        for required in (manager, root_make, fs_make):
            self.assertIn(required, files)
        first, first_files, first_images = bg.copy_checkout(
            self.repo, Path(self.temp.name) / 'first', roots, files)
        second_roots, second_files = bg.source_files(first)
        second, second_bindings, second_images = bg.copy_checkout(
            first, Path(self.temp.name) / 'second', second_roots, second_files)
        for original in (manager, root_make, fs_make, helper):
            relative = original.relative_to(self.repo)
            self.assertIn(first / relative, second_files)
            self.assertEqual((second / relative).read_bytes(), original.read_bytes())
            self.assertNotEqual((second / relative).stat().st_ino, original.stat().st_ino)
        self.assertTrue(os.access(second / manager.relative_to(self.repo), os.X_OK))
        self.assertFalse((second / alias.relative_to(self.repo)).is_symlink())
        self.assertEqual((second / alias.relative_to(self.repo)).read_bytes(), helper.read_bytes())
        self.assertEqual({root: (root / '.git/index').read_bytes() for root in (self.repo, firesim)},
                         original_indexes)
        for snapshot in (first, second):
            for root in (snapshot, snapshot / 'sims/firesim'):
                tracked = git(root, 'ls-files', '-z').stdout.decode().split('\0')
                self.assertIn('Makefile', tracked)
                if root != snapshot:
                    self.assertIn('deploy/firesim', tracked)
        manager.write_text('later manager mutation')
        root_make.write_text('later Makefile mutation')
        bg.validate(first_files + first_images + second_bindings + second_images)

    def test_frozen_design_rejects_changed_rtl_and_driver(self):
        directory = Path(self.temp.name) / "frozen"
        data = self.fake_manifest(directory)
        design = bg.frozen_design(data, "rocket")
        design["circt_rtl"].write_text("corruption")
        with self.assertRaisesRegex(ValueError, "Frozen background"):
            bg.frozen_design(data, "rocket")
        design["circt_rtl"].write_text("rocket")
        Path(data["source_files"][0]["path"]).write_text("different driver")
        with self.assertRaisesRegex(ValueError, "Frozen background"):
            bg.frozen_design(data, "rocket")

    def test_source_closure_keeps_board_template_filenames_and_excludes_deliveries(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        base = self.repo / "sims/firesim/platforms/f2/aws-fpga-firesim-f2/hdk/cl/developer_designs"
        defines = self.file(base / "cl_firesim/design/cl_firesim_defines.vh")
        rtl = self.file(base / "cl_firesim/design/cl_firesim.sv")
        delivery = self.file(base / "cl_candidate/design/FireSim-generated.sv")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        with mock.patch.object(bg, "source_repositories", return_value=[self.repo]):
            roots, files = bg.source_files(self.repo)
        self.assertIn(defines, files)
        self.assertIn(rtl, files)
        self.assertNotIn(delivery, files)

    def test_hardware_identity_ignores_compiler_tests_but_binds_driver_and_rtl(self):
        driver = self.file(self.repo / "sims/firesim/sim/midas/src/main/cc/driver.cc")
        compiler = self.file(self.repo / "sims/firesim/sim/goldengate-circt/lib/Transform.cpp")
        tests = self.file(self.repo / "automation/chia_firesim_circt/test_other.py")
        rtl = self.file(self.repo / "candidate/FireSim-generated.sv")
        designs = {"rocket": {"circt_rtl": rtl, "recipe": {"PLATFORM": "f2"}}}
        files = [driver, compiler, tests]
        old = bg.hardware_key(designs, files, self.repo)
        compiler.write_text("new transform, same emitted design")
        tests.write_text("new test")
        self.assertEqual(old, bg.hardware_key(designs, files, self.repo))
        driver.write_text("runtime change")
        self.assertNotEqual(old, bg.hardware_key(designs, files, self.repo))
        driver.write_text("data")
        rtl.write_text("new hardware")
        self.assertNotEqual(old, bg.hardware_key(designs, files, self.repo))

    def test_launch_deduplicates_actual_inputs_without_reassigning_semantic_identity(self):
        root = bg.root_directory(self.repo)
        old = root / "old/job.json"
        bg.write(old, {"job_id": "old-job", "phase": "passed", "hardware_key": "hardware", "identity": "old"})
        client = mock.Mock()
        with mock.patch.object(bg, "freeze", side_effect=lambda r, d, c, s: self.fake_manifest(d)), \
                mock.patch("ray.job_submission.JobSubmissionClient", return_value=client):
            result = bg.launch({}, "new", {}, self.repo)
        self.assertTrue(result["deduplicated"])
        self.assertEqual(result["identity"], "old")
        self.assertEqual(result["requested_identity"], "candidate")
        client.submit_job.assert_not_called()
        self.assertTrue((root / "new/reuse.json").exists())

    def test_newest_pending_supersedes_old_pending_without_stopping_active(self):
        root = bg.root_directory(self.repo)
        bg.write(root / "active/job.json", {"job_id": "active", "phase": "running", "hardware_key": "a"})
        bg.write(root / "pending/job.json", {"job_id": "pending", "phase": "queued", "hardware_key": "b"})
        client = mock.Mock()
        with mock.patch.object(bg, "freeze", side_effect=lambda r, d, c, s: self.fake_manifest(d)), \
                mock.patch("ray.job_submission.JobSubmissionClient", return_value=client):
            result = bg.launch({}, "new", {}, self.repo)
        self.assertEqual(result["phase"], "queued")
        client.stop_job.assert_called_once_with("pending")
        client.submit_job.assert_called_once()
        self.assertEqual(json.loads((root / "pending/job.json").read_text())["phase"], "superseded")
        self.assertEqual(json.loads((root / "active/job.json").read_text())["phase"], "running")

    def test_snapshot_failure_retains_blocked_receipt_and_never_submits(self):
        with mock.patch.object(bg, "freeze", side_effect=ValueError("stale semantic evidence")), \
                mock.patch("ray.job_submission.JobSubmissionClient") as client:
            result = bg.launch({}, "bad", {}, self.repo)
        self.assertEqual(result["phase"], "blocked")
        self.assertIn("stale semantic", result["error"])
        client.assert_not_called()

    def test_runner_executes_all_four_with_frozen_inputs_and_global_build_lock(self):
        directory = bg.root_directory(self.repo) / "run"
        data = self.fake_manifest(directory)
        bg.write(directory / "job.json", {"phase": "queued", "job_id": "job"})
        seen = []
        def run():
            target = sys.argv[sys.argv.index("--target") + 1]
            design = module.inputs(Path(data["repo"]), target)
            self.assertEqual(design["circt_rtl"].read_text(), target)
            seen.append(target)
            self.assertEqual(os.environ["CHIA_F2_BUILD_LOCK"], str(self.repo / 'automation/chia_firesim_circt/logs/.f2-build.lock'))
            bg.write(Path(os.environ["CHIA_REPORT_PATH"]), {"passed": target != "megaboomv3"})
            return int(target == "megaboomv3")
        module = mock.Mock(main=run)
        with mock.patch.dict(sys.modules, {"run_f2": module}), mock.patch.dict(os.environ):
            self.assertEqual(bg.run_snapshot(directory / "snapshot.json"), 1)
        self.assertEqual(tuple(seen), bg.TARGETS)
        self.assertEqual(json.loads((directory / "job.json").read_text())["phase"], "failed")
        self.assertEqual(len(json.loads((directory / "result.json").read_text())["targets"]), 4)

    def test_superseded_job_does_not_start_cloud_actions(self):
        directory = bg.root_directory(self.repo) / "old"
        self.fake_manifest(directory)
        bg.write(directory / "job.json", {"phase": "superseded"})
        self.assertEqual(bg.run_snapshot(directory / "snapshot.json"), 0)

    def test_dead_worker_cannot_be_reused_as_running_and_retains_failure(self):
        path = bg.root_directory(self.repo) / "dead/job.json"
        bg.write(path, {"phase": "running", "job_id": "dead"})
        client = mock.Mock()
        client.get_job_info.return_value.status = "FAILED"
        result = bg.refresh_job(client, path, json.loads(path.read_text()))
        self.assertEqual(result["phase"], "failed")
        self.assertIn("FAILED", result["error"])
        self.assertEqual(json.loads(path.read_text())["phase"], "failed")


if __name__ == "__main__":
    unittest.main()
