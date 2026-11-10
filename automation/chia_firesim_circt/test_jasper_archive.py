"""Exercise Jasper collection without running any formal tools or SSH."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

import run_jasper
import yaml


class JasperLECScopeTest(unittest.TestCase):
    def test_observed_status_list_schema_accepts_only_complete_unbounded_proofs(self):
        category = {"Status": "Complete", "Description": "", "proven": ["AutoProveCutPointsAssertions:state_CP_assert"],
                    "bounded_proven": [], "cex": [], "undetermined": [], "unprocessed": [], "waived": []}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "lec_auto_cutpoints.json"
            path.write_text(json.dumps({"Auto cutpoints proof": category, "Session": {"Version": "2026.06p002"}}))
            self.assertEqual(run_jasper.lec_cutpoint_signoff(root)["cutpoint_property_count"], 1)
            invalid = []
            for status in ("bounded_proven", "cex", "undetermined", "unprocessed", "waived"):
                invalid.append(dict(category, **{status: ["bad_CP_assert"]}))
            invalid += [dict(category, proven=[]), dict(category, proven="not a list"),
                        dict(category, proven=[None]), dict(category, proven=[""]),
                        dict(category, proven=category["proven"] * 2),
                        dict(category, unknown=[]), {key: value for key, value in category.items() if key != "waived"}]
            for report in invalid:
                path.write_text(json.dumps({"Auto cutpoints proof": report}))
                with self.subTest(report=report), self.assertRaises(ValueError):
                    run_jasper.lec_cutpoint_signoff(root)

    def test_retained_jasper_report_remains_failed_after_schema_support(self):
        retained = Path(__file__).parent / (
            "logs/verification/20261021T204512Z-pid2577644-iter4-full/jasper/rocket/lec/"
            "proof/reports/lec_auto_cutpoints.json")
        if not retained.is_file():
            self.skipTest("retained iteration-4 proof archive is unavailable")
        original = retained.read_bytes()
        detail = json.loads(original)
        category = detail["Auto cutpoints proof"]
        self.assertEqual(category["Status"], "Incomplete")
        self.assertEqual(len(category["proven"]), 21)
        self.assertEqual(len(category["undetermined"]), 35)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "lec_auto_cutpoints.json"
            path.write_bytes(original)
            with self.assertRaisesRegex(ValueError, "incomplete"):
                run_jasper.lec_cutpoint_signoff(root)
            # Synthetic parser positive control only; never alter archived proof.
            category["Status"] = "Complete"
            category["proven"] += category["undetermined"]
            category["undetermined"] = []
            path.write_text(json.dumps(detail))
            self.assertEqual(run_jasper.lec_cutpoint_signoff(root)["cutpoint_property_count"], 56)
        self.assertEqual(retained.read_bytes(), original)

    def test_actual_cutpoint_inventory_cannot_be_replaced_by_helper_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "lec_points.txt").write_text("EQUIVALENT helper_a\n")
            with self.assertRaises(FileNotFoundError):
                run_jasper.lec_cutpoint_signoff(root)
            detail = root / "lec_auto_cutpoints.json"
            for report in ({"Status": "Complete"},
                           {"Status": "Incomplete", "Properties": [
                               {"property_name": "cut_a", "property_status": "proven"}]},
                           {"Status": "Complete", "Properties": [
                               {"property_name": "cut_a", "property_status": "undetermined"}]},
                           {"Status": "Complete", "Properties": [
                               {"property_name": "cut_a", "property_status": "cex"}]},
                           {"Status": "Complete", "Properties": [
                               {"property_name": "cut_a", "property_status": "proven"}] * 2}):
                detail.write_text(json.dumps(report))
                with self.subTest(report=report), self.assertRaises(ValueError):
                    run_jasper.lec_cutpoint_signoff(root)
            detail.write_text(json.dumps({"Auto cutpoints proof": {
                "Status": "Complete", "Properties": [
                    {"property_name": "cut_a", "property_status": "proven"}]}}))
            result = run_jasper.lec_cutpoint_signoff(root)
            self.assertEqual(result["cutpoint_property_count"], 1)
            self.assertEqual(result["properties"], ["cut_a"])

    def test_staged_tcl_hook_only_requests_authoritative_report(self):
        if not shutil.which("tclsh"):
            self.skipTest("Tcl interpreter unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tcl").mkdir()
            library = root / "tcl/sec_lib.tcl"
            library.write_text('proc sec_flow_signoff_summary {dir} {puts "original_signoff $dir"}\n')
            original = library.read_text()
            run_jasper.stage_lec_reporter(root)
            self.assertTrue(library.read_text().startswith(original))
            script = ('proc check_sec {args} {puts [join $args "|"]}\n'
                      'source {' + str(library) + '}\nsec_flow_signoff_summary {' + str(root) + '}\n')
            completed = subprocess.run(["tclsh"], input=script, text=True, capture_output=True, check=True)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(completed.stdout.splitlines(), [
                "original_signoff " + str(root),
                "-signoff_info|-get_category_status|auto_cutpoints|-detailed|-json|-file|" +
                str(root / "lec_auto_cutpoints.json") + "|-force|-silent"])


class JasperMemoryTest(unittest.TestCase):
    def test_both_modes_pin_requested_budget_and_single_proofgrid_job(self):
        config = yaml.safe_load(Path(__file__).with_name("verification.yaml").read_text())
        self.assertEqual(run_jasper.DEFAULT_MAX_MEM_GIB, 128)
        for mode in ("lec", "sec"):
            argv = config["jasper"][mode]["argv"]
            with self.subTest(mode=mode):
                self.assertEqual(argv[argv.index("--mode") + 1], mode)
                self.assertEqual(argv[argv.index("--max-mem-gb") + 1], "128")
                self.assertEqual(argv[argv.index("--jobs") + 1], "1")

    def test_normal_flow_peak_uses_sampled_gib(self):
        result = run_jasper.memory_evidence(
            "SEC_FLOW: peak memory of this run's Jasper processes: 105 GB\n")
        self.assertEqual(result["sampled_peak_rss_gib"], 105)
        self.assertFalse(result["memory_guard_aborted"])
        self.assertIn("rounds", result["measurement"])

    def test_abort_retains_observed_peak_and_failure(self):
        result = run_jasper.memory_evidence(
            "SEC_FLOW: peak memory of this run's Jasper processes: 99 GB\n"
            "SEC_FLOW: ABORTED by memory guard: 129 GB RSS > --max-mem-gb 128\n")
        self.assertEqual(result["sampled_peak_rss_gib"], 129)
        self.assertTrue(result["memory_guard_aborted"])

    def test_killed_or_unstarted_flow_has_unknown_peak(self):
        result = run_jasper.memory_evidence("jasper exited rc=-9\n")
        self.assertIsNone(result["sampled_peak_rss_gib"])
        self.assertFalse(result["memory_guard_aborted"])


class JasperArchiveTest(unittest.TestCase):
    def archive(self, path, entries):
        with tarfile.open(path, "w") as stream:
            for name, kind, value in entries:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.mode = 0o7777  # Never apply remote ownership or permission bits.
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    member.linkname = value
                if kind == tarfile.REGTYPE:
                    member.size = len(value)
                    stream.addfile(member, io.BytesIO(value))
                else:
                    stream.addfile(member)

    def test_jasper_internal_aliases_do_not_hide_regular_logs_or_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, directory = root / "results.tar", root / "evidence"
            directory.mkdir()
            self.archive(archive, [
                ("proof", tarfile.DIRTYPE, None),
                ("proof/reports/summary.txt", tarfile.REGTYPE, b"incomplete\n"),
                ("proof/jgproject/jg.log", tarfile.SYMTYPE, "sessionLogs/session_0/jg_session_0.log"),
                ("proof/jgproject/sessionLogs/session_0/proveCache", tarfile.SYMTYPE, "../../work/proveCache"),
                ("proof/jgproject/sessionLogs/session_0/settings/preferences.conf", tarfile.SYMTYPE,
                 "../../../settings/preferences.conf"),  # Actual Jasper archive has this dangling alias.
                ("proof/jgproject/sessionLogs/session_0/jg_session_0.log", tarfile.REGTYPE, b"ABORTED\n"),
                ("proof/summary-hardlink", tarfile.LNKTYPE, "proof/reports/summary.txt")])
            result = run_jasper.collect_proof_archive(archive, directory)
            self.assertEqual(result["regular_files"], 2)
            self.assertEqual(len(result["skipped_links"]), 4)
            self.assertEqual((directory / "proof/reports/summary.txt").read_text(), "incomplete\n")
            self.assertEqual((directory / "proof/jgproject/sessionLogs/session_0/jg_session_0.log").read_text(), "ABORTED\n")
            self.assertFalse((directory / "proof/jgproject/jg.log").exists())
            self.assertFalse(any(path.is_symlink() for path in directory.rglob("*")))
            self.assertEqual((directory / "proof/reports/summary.txt").stat().st_mode & 0o7000, 0)

    def rejects_atomically(self, entries):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, directory = root / "results.tar", root / "evidence"
            directory.mkdir()
            self.archive(archive, [("proof/config.json", tarfile.REGTYPE, b"{}"), *entries])
            with self.assertRaises(ValueError):
                run_jasper.collect_proof_archive(archive, directory)
            self.assertFalse((directory / "proof").exists())

    def test_rejects_traversal_absolute_and_noncanonical_names(self):
        for name in ("../escape", "proof/../escape", "/proof/absolute", "other/file", "proof//file",
                     "proof/./file", "", "./proof/file", "proof/file/"):
            with self.subTest(name=name):
                self.rejects_atomically([(name, tarfile.REGTYPE, b"unsafe")])

    def test_rejects_escaping_or_absolute_link_targets_even_though_links_are_skipped(self):
        for kind, target in ((tarfile.SYMTYPE, "../../escape"), (tarfile.SYMTYPE, "/etc/passwd"),
                             (tarfile.LNKTYPE, "outside/file"), (tarfile.LNKTYPE, "proof/../../escape"),
                             (tarfile.LNKTYPE, "/proof/file"), (tarfile.SYMTYPE, "")):
            with self.subTest(kind=kind, target=target):
                self.rejects_atomically([("proof/alias", kind, target)])

    def test_rejects_devices_fifos_and_other_nonregular_members(self):
        for kind in (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE, b"Z"):
            with self.subTest(kind=kind):
                self.rejects_atomically([("proof/special", kind, None)])

    def test_rejects_duplicate_and_conflicting_paths_in_either_order(self):
        self.rejects_atomically([("proof/config.json", tarfile.REGTYPE, b"replacement")])
        for kind, value in ((tarfile.REGTYPE, b"file"), (tarfile.SYMTYPE, "internal"),
                            (tarfile.LNKTYPE, "proof/internal")):
            entries = [("proof/alias", kind, value), ("proof/alias/child", tarfile.REGTYPE, b"bad")]
            for ordered in (entries, list(reversed(entries))):
                with self.subTest(kind=kind, ordered=ordered):
                    self.rejects_atomically(ordered)

    def test_rejects_existing_subtree_and_symlink_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "results.tar"
            self.archive(archive, [("proof/config.json", tarfile.REGTYPE, b"{}")])
            for mode in ("directory", "symlink", "dangling-symlink"):
                directory = root / mode
                directory.mkdir()
                proof = directory / "proof"
                if mode == "directory":
                    proof.mkdir()
                    (proof / "keep").write_text("preserve")
                else:
                    proof.symlink_to(root if mode == "symlink" else root / "absent", target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "existing proof"):
                    run_jasper.collect_proof_archive(archive, directory)
                if mode == "directory":
                    self.assertEqual((proof / "keep").read_text(), "preserve")
                else:
                    self.assertTrue(proof.is_symlink())

    def test_collection_does_not_create_missing_signoff_or_passing_verdict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "results.tar"
            self.archive(archive, [("proof/RESULT.txt", tarfile.REGTYPE, b"ABORTED: memory guard\n")])
            run_jasper.collect_proof_archive(archive, root)
            with self.assertRaises(FileNotFoundError):
                run_jasper.signoff(root / "proof", "lec")


if __name__ == "__main__":
    unittest.main()
