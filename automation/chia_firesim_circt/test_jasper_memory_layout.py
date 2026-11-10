"""Packing/counterexample diagnostics without SSH, proof or compiler launches."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import jasper_memory_layout as layout


FIXTURE = """module buffer(
 input clock, reset, input [3:0] read_addr, write_addr,
 input write_enable, input [63:0] enq_bits_data, input enq_bits_last,
 output [63:0] deq_bits_data, output deq_bits_last
);
 wire [64:0] _ram_ext_R0_data;
 reg [64:0] ram_read_rdata;
 reg other;
 always @(posedge clock) begin
   if (reset) begin
     other <= 1'h0;
   end else begin
     other <= write_enable;
   end
   ram_read_rdata <= _ram_ext_R0_data;
 end
 ram_16x65 ram_ext (
  .R0_addr(read_addr), .R0_en(1'h1), .R0_clk(clock), .R0_data(_ram_ext_R0_data),
  .W0_addr(write_addr), .W0_en(write_enable), .W0_clk(clock),
  .W0_data({enq_bits_data, enq_bits_last})
 );
 assign deq_bits_data = ram_read_rdata[64:1];
 assign deq_bits_last = ram_read_rdata[0];
endmodule
module ram_16x65(
 input [3:0] R0_addr, input R0_en, R0_clk, output [64:0] R0_data,
 input [3:0] W0_addr, input W0_en, W0_clk, input [64:0] W0_data
);
 reg [64:0] Memory[0:15];
 always @(posedge W0_clk) begin
  if (W0_en & 1'h1)
   Memory[W0_addr] <= W0_data;
 end
 assign R0_data = R0_en ? Memory[R0_addr] : 65'bx;
endmodule
module top(input clock);
 buffer readBuffer(.clock(clock));
endmodule
"""
PACKING = {"buffer": {"ram_ext": [["data", 64, 1], ["last", 0, 0]]}}


def provenance(text=FIXTURE):
    return {"schema_version": 1, "kind": "goldengate.native-memory-origins",
            "binding": {"scope": "compiler_emitted_prefix", "bytes": len(text.encode()),
                        "sha256": hashlib.sha256(text.encode()).hexdigest()},
            "memories": [{"origin": "FASEDReadBuffer",
                          "source": {"module": "buffer", "memory": "ram", "read_port": "read",
                                     "write_port": "write", "width": 65, "depth": 16},
                          "fields": [{"name": "data", "hi": 64, "lo": 1}, {"name": "last", "hi": 0, "lo": 0}],
                          "lowered": {"module": "buffer", "memory_instance": "ram_ext", "memory_module": "ram_16x65",
                                      "storage": "Memory", "read_capture": "ram_read_rdata"},
                          "instance_paths": ["top.readBuffer"],
                          "read_capture": {"latency": 1, "enable": "constant_one", "clock": "clock",
                                           "reset": "none", "disabled": "hold", "collision": "pre_edge_data"}}]}


class MemoryLayoutTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "imp.sv"

    def tearDown(self):
        self.temporary.cleanup()

    def recover(self, text=FIXTURE):
        self.path.write_text(text)
        return layout.recover_capture_layouts([self.path])

    def test_packing_and_clocked_capture_are_both_required(self):
        packing, records = self.recover()
        self.assertEqual(packing, PACKING)
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["width"], records[0]["depth"]), (65, 16))
        self.assertEqual(records[0]["read_address"], "read_addr")
        self.assertEqual(records[0]["write_address"], "write_addr")
        self.assertEqual(records[0]["capture_register"], "ram_read_rdata")
        self.assertEqual(records[0]["writer_fields"], [["enq_bits_data", 64], ["enq_bits_last", 1]])

    def test_unknown_or_mismatched_structure_never_proposes_packing(self):
        mutations = [
            ("{enq_bits_data, enq_bits_last}", "{enq_bits_last, enq_bits_data}"),
            ("[63:0] enq_bits_data", "[62:0] enq_bits_data"),
            ("ram_read_rdata[64:1]", "ram_read_rdata[63:0]"),
            ("ram_read_rdata[0]", "ram_read_rdata[1]"),
            ("reg [64:0] ram_read_rdata", "reg [63:0] ram_read_rdata"),
            (".W0_clk(clock)", ".W0_clk(reset)"),
            ("always @(posedge clock)", "always @(negedge clock)"),
            ("ram_read_rdata <= _ram_ext_R0_data;", "if (write_enable) ram_read_rdata <= _ram_ext_R0_data;"),
            ("ram_read_rdata <= _ram_ext_R0_data;", "ram_read_rdata <= _ram_ext_R0_data ^ 65'h1;"),
            (".R0_en(1'h1)", ".R0_en(write_enable)"),
            ("Memory[R0_addr]", "Memory[W0_addr]"),
            ("Memory[W0_addr] <= W0_data", "Memory[R0_addr] <= W0_data"),
            ("always @(posedge W0_clk)", "always @(posedge R0_clk)"),
            ("reg [64:0] Memory[0:15]", "reg [64:0] Memory[0:14]"),
            ("assign deq_bits_last = ram_read_rdata[0];", "assign deq_bits_last = ram_read_rdata[0];\nassign deq_bits_last = reset;"),
            ("ram_read_rdata <= _ram_ext_R0_data;", "ram_read_rdata <= _ram_ext_R0_data; ram_read_rdata <= 65'h0;"),
            (".R0_addr(read_addr)", ".R0_addr(read_addr), .R0_addr(write_addr)"),
        ]
        for old, new in mutations:
            with self.subTest(mutation=(old, new)):
                self.assertIn(old, FIXTURE)
                self.assertEqual(self.recover(FIXTURE.replace(old, new))[0], {})

    def test_duplicate_modules_fail_closed(self):
        self.path.write_text(FIXTURE + FIXTURE)
        with self.assertRaisesRegex(ValueError, "duplicate module"):
            layout.recover_capture_layouts([self.path])

    def test_existing_layout_is_preserved_and_conflict_blocks(self):
        original = {"existing": {"ram_ext": [["tag", 0, 0]]}}
        before = copy.deepcopy(original)
        merged = layout.merge_layouts(original, PACKING)
        self.assertEqual(original, before)
        self.assertEqual(merged["existing"], before["existing"])
        self.assertEqual(layout.merge_layouts(PACKING, PACKING), PACKING)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            layout.merge_layouts(PACKING, {"buffer": {"ram_ext": [["data", 63, 0], ["last", 64, 64]]}})

    def test_provenance_prefix_and_each_structural_join_are_checked(self):
        _, records = self.recover()
        original = provenance()
        self.assertEqual(layout.validate_origins([self.path], original, records)["joined_records"], 1)
        self.path.write_text(FIXTURE + "\n// resources appended after compiler emission\n")
        self.assertEqual(layout.validate_origins([self.path], original, records)["joined_records"], 1)
        self.path.write_text(FIXTURE.replace("write_addr", "other_addr"))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            layout.validate_origins([self.path], original, records)
        self.path.write_text(FIXTURE)
        mutations = []
        for key, value in [("width", 64), ("depth", 32), ("memory", "other"), ("module", "other"), ("read_port", "other")]:
            d = copy.deepcopy(original); d["memories"][0]["source"][key] = value; mutations.append(d)
        for key in ["module", "memory_instance", "memory_module", "storage", "read_capture"]:
            d = copy.deepcopy(original); d["memories"][0]["lowered"][key] = "wrong"; mutations.append(d)
        for key, value in [("enable", "dynamic"), ("clock", "other"), ("reset", "reset"), ("latency", 2)]:
            d = copy.deepcopy(original); d["memories"][0]["read_capture"][key] = value; mutations.append(d)
        d = copy.deepcopy(original); d["memories"][0]["fields"][0]["lo"] = 0; mutations.append(d)
        d = copy.deepcopy(original); d["memories"] *= 2; mutations.append(d)
        d = copy.deepcopy(original); d["memories"] = []; mutations.append(d)
        d = copy.deepcopy(original); d["memories"][0]["instance_paths"] = []; mutations.append(d)
        d = copy.deepcopy(original); d["memories"][0]["instance_paths"] = ["top.wrong"]; mutations.append(d)
        d = copy.deepcopy(original); d["memories"][0]["instance_paths"] = ["top.readBuffer", "top.not_present"]; mutations.append(d)
        for d in mutations:
            with self.subTest(provenance=d), self.assertRaises(ValueError):
                layout.validate_origins([self.path], d, records)
        with self.assertRaisesRegex(ValueError, "no recognized"):
            layout.validate_origins([self.path], original, [])

    def test_staged_wrapper_requires_sidecar_and_retains_original_scanner(self):
        flow = self.root / "flow"
        (flow / "scripts").mkdir(parents=True)
        scanner = flow / "scripts/svinfo.py"
        scanner.write_text("def merged_layouts(paths):\n    return {'preserved': {'ram_ext': [['field', 0, 0]]}}\n")
        old = scanner.read_text()
        sidecar = self.root / "origins.json"
        sidecar.write_text(json.dumps(provenance()))
        self.path.write_text(FIXTURE)
        evidence = self.root / "evidence.json"
        layout.stage_memory_layout_adapter(flow, sidecar, str(evidence))
        self.assertTrue(scanner.read_text().startswith(old))
        code = 'import svinfo,json; print(json.dumps(svinfo.merged_layouts([' + repr(str(self.path)) + '])))'
        completed = subprocess.run([sys.executable, "-c", code], cwd=flow / "scripts", capture_output=True, text=True, check=True)
        got = json.loads(completed.stdout)
        self.assertEqual(got["buffer"], PACKING["buffer"])
        self.assertEqual(got["preserved"], {"ram_ext": [["field", 0, 0]]})
        self.assertEqual(json.loads(evidence.read_text())["provenance"]["joined_records"], 1)
        (flow / "scripts/chia_memory_origins.json").unlink()
        self.assertNotEqual(subprocess.run([sys.executable, "-c", code], cwd=flow / "scripts", capture_output=True).returncode, 0)

    def test_staged_original_scanner_accepts_bound_prefix_with_appended_resources(self):
        original_scanner = Path("/scratch/jfx/circt_compare/sec_flow/scripts/svinfo.py")
        if not original_scanner.is_file():
            self.skipTest("retained external scanner unavailable")
        flow = self.root / "flow"
        (flow / "scripts").mkdir(parents=True)
        shutil.copy2(original_scanner, flow / "scripts/svinfo.py")
        sidecar = self.root / "origins.json"
        sidecar.write_text(json.dumps(provenance()))
        self.path.write_text(FIXTURE + "\nmodule frozen_resource(input x, output y);\nassign y=x;\nendmodule\n")
        evidence = self.root / "evidence.json"
        layout.stage_memory_layout_adapter(flow, sidecar, str(evidence))
        code = 'import svinfo,json; print(json.dumps(svinfo.merged_layouts([' + repr(str(self.path)) + '])))'
        result = subprocess.run([sys.executable, "-c", code], cwd=flow / "scripts", text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), PACKING)
        self.assertEqual(json.loads(evidence.read_text())["provenance"]["joined_records"], 1)
        self.path.write_text(self.path.read_text().replace("65'bx", "65'h0"))
        failure = subprocess.run([sys.executable, "-c", code], cwd=flow / "scripts", text=True, capture_output=True)
        self.assertNotEqual(failure.returncode, 0)
        self.assertIn("hash mismatch", failure.stderr)

    def test_helper_report_hook_preserves_boundary_export_and_only_exports_helper_cex(self):
        if not shutil.which("tclsh"):
            self.skipTest("Tcl interpreter unavailable")
        flow = self.root / "flow"
        (flow / "tcl").mkdir(parents=True)
        library = flow / "tcl/sec_lib.tcl"
        library.write_text('proc sec_flow_save_cex {dir max} {puts "original $max"}\n')
        original = library.read_text()
        layout.stage_helper_cex_reporter(flow)
        self.assertTrue(library.read_text().startswith(original))
        script = '''proc check_sec {args} {
 if {[lindex $args 0] eq "-list"} {return {id0 id1 id2 id3 id4 id5 id6 id7 id8}}
 return [string map {id helper} [lindex $args 2]]
}
proc get_property_list {args} {return {boundary helper0 helper1 helper2 helper3 helper4 helper5 helper6 helper7 helper8}}
proc visualize {args} {puts [join $args |]}
proc sec_flow_log {args} {}
source {LIBRARY}
sec_flow_save_cex {ROOT} 20
'''.replace("LIBRARY", str(library)).replace("ROOT", str(self.root))
        completed = subprocess.run(["tclsh"], input=script, text=True, capture_output=True, check=True)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(completed.stdout.splitlines()[0], "original 20")
        inventory = (self.root / "helper_cex/inventory.tsv").read_text()
        self.assertEqual(inventory.count("exported\t"), 8)
        self.assertNotIn("boundary", inventory)
        self.assertNotIn("helper8", inventory)
        self.assertNotIn("check_sec -prove", library.read_text())

    def test_actual_cutpoint_traces_have_an_independent_budget_and_retain_export_errors(self):
        if not shutil.which("tclsh"):
            self.skipTest("Tcl interpreter unavailable")
        flow = self.root / "flow"
        (flow / "tcl").mkdir(parents=True)
        library = flow / "tcl/sec_lib.tcl"
        library.write_text('proc sec_flow_save_cex {dir max} {puts "original $max"}\n')
        layout.stage_helper_cex_reporter(flow)
        # Names match the actual 2026.06p002 status report. Helpers and plain
        # boundary properties must not consume the automatic-cutpoint budget.
        script = r'''proc check_sec {args} {error "helper inventory unavailable"}
proc get_property_list {args} {
  set result {boundary helper0 <embedded>::AutoProveCutPointsAssertions:unprocessed_CP_assert_extra}
  for {set i 0} {$i < 10} {incr i} {
    lappend result "<embedded>::AutoProveCutPointsAssertions:state${i}_CP_assert"
  }
  return $result
}
proc visualize {args} {
  set i [lsearch -exact $args -property]
  if {$i >= 0 && [string match *state2_CP_assert [lindex $args [expr {$i+1}]]]} {
    error "trace export failed\nretained"
  }
  puts [join $args |]
}
proc sec_flow_log {args} {}
source {LIBRARY}
sec_flow_save_cex {ROOT} 20
'''.replace("LIBRARY", str(library)).replace("ROOT", str(self.root))
        completed = subprocess.run(["tclsh"], input=script, text=True, capture_output=True, check=True)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(completed.stdout.splitlines()[-1], "original 20")
        inventory = (self.root / "auto_cutpoint_cex/inventory.tsv").read_text()
        self.assertEqual(inventory.count("export_started\t"), 8)
        self.assertEqual(inventory.count("exported\t"), 7)
        self.assertEqual(inventory.count("export_error\t"), 1)
        self.assertEqual(inventory.count("deferred\t"), 2)
        self.assertIn("trace export failed retained", inventory)
        self.assertNotIn("helper0", inventory)
        self.assertNotIn("boundary", inventory)
        self.assertNotIn("assert_extra", inventory)
        self.assertIn("inventory_error", (self.root / "helper_cex/inventory.tsv").read_text())
        self.assertNotIn("check_sec -prove", library.read_text())
        self.assertNotIn("assume", layout.CUTPOINT_CEX_HOOK)

    def test_actual_cutpoint_inventory_error_is_retained_without_aborting_reports(self):
        if not shutil.which("tclsh"):
            self.skipTest("Tcl interpreter unavailable")
        library = self.root / "cutpoint.tcl"
        library.write_text('proc sec_flow_save_cex {dir max} {puts "original $max"}\n'
                           + layout.CUTPOINT_CEX_HOOK)
        script = '''proc get_property_list {args} {error "inventory unavailable"}
source {LIBRARY}
sec_flow_save_cex {ROOT} 20
puts "reports completed"
'''.replace("LIBRARY", str(library)).replace("ROOT", str(self.root))
        completed = subprocess.run(["tclsh"], input=script, text=True,
                                   capture_output=True, check=True)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(completed.stdout.splitlines(), ["original 20", "reports completed"])
        inventory = (self.root / "auto_cutpoint_cex/inventory.tsv").read_text()
        self.assertIn("inventory_error\t\t\tinventory unavailable", inventory)

    def test_actual_cutpoints_are_retained_before_a_boundary_export_failure(self):
        if not shutil.which("tclsh"):
            self.skipTest("Tcl interpreter unavailable")
        library = self.root / "cutpoint.tcl"
        library.write_text('proc sec_flow_save_cex {dir max} {error "boundary export failed"}\n'
                           + layout.CUTPOINT_CEX_HOOK)
        script = '''proc get_property_list {args} {
 return {<embedded>::AutoProveCutPointsAssertions:defined_state_CP_assert}
}
proc visualize {args} {}
proc sec_flow_log {args} {}
source {LIBRARY}
catch {sec_flow_save_cex {ROOT} 20} error
puts $error
'''.replace("LIBRARY", str(library)).replace("ROOT", str(self.root))
        completed = subprocess.run(["tclsh"], input=script, text=True,
                                   capture_output=True, check=True)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(completed.stdout.strip(), "boundary export failed")
        inventory = (self.root / "auto_cutpoint_cex/inventory.tsv").read_text()
        self.assertIn("export_started\t", inventory)
        self.assertIn("exported\t", inventory)

    def test_actual_native_backend_and_production_helper_sidecars_join(self):
        root = Path("/scratch/jfx/goldengate-circt-f2-build/iteration27-memory-origins")
        fixtures = [("backend.sv.origin128.sv", ".json", "OriginTest", 128),
                    ("backend.sv.origin256.sv", ".json", "OriginTest", 256),
                    ("actual-helper-reuse1.sv", ".memory-origins.json", "GGFASEDReadBuffer16x8", 128),
                    ("actual-helper-reuse2.sv", ".memory-origins.json", "GGFASEDReadBuffer16x16", 256)]
        for name, suffix, parent, depth in fixtures:
            rtl = root / name
            sidecar = Path(str(rtl) + suffix)
            if not rtl.is_file() or not sidecar.is_file():
                self.skipTest("actual native-emitter fixture unavailable")
            with self.subTest(fixture=name):
                supplement, records = layout.recover_capture_layouts([rtl])
                self.assertEqual(supplement, {parent: {"ram_ext": [["data", 64, 1], ["last", 0, 0]]}})
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["depth"], depth)
                self.assertEqual(layout.validate_origins([rtl], json.loads(sidecar.read_text()), records)["joined_records"], 1)

    def test_retained_all_four_native_and_golden_layouts_preserve_existing_inventory(self):
        scanner_path = Path("/scratch/jfx/circt_compare/sec_flow/scripts/svinfo.py")
        if not scanner_path.is_file():
            self.skipTest("retained external scanner unavailable")
        spec = importlib.util.spec_from_file_location("chia_test_original_svinfo", scanner_path)
        scanner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = scanner
        spec.loader.exec_module(scanner)
        for target in ["rocket", "megaboomv3", "shuttle", "saturn_shuttle"]:
            for kind, root in [("circt", "/scratch/jfx/f2-circt-candidates"), ("sfc", "/scratch/jfx/f2-sfc-goldens")]:
                receipt = Path(root) / target / "complete.json"
                if not receipt.is_file():
                    self.skipTest("retained four-target receipts unavailable")
                manifest = Path(json.loads(receipt.read_text())["manifest"]["path"])
                rtl = manifest.parent / kind / "FireSim-generated.sv"
                with self.subTest(target=target, compiler=kind):
                    before = hashlib.sha256(rtl.read_bytes()).hexdigest()
                    original = scanner.merged_layouts([rtl])
                    supplement, records = layout.recover_capture_layouts([rtl])
                    merged = layout.merge_layouts(original, supplement)
                    for module, instances in original.items():
                        for instance, fields in instances.items():
                            self.assertEqual(merged[module][instance], fields)
                    if kind == "circt":
                        parent = "GGFASEDReadBuffer16x8" if target in ["rocket", "shuttle"] else "GGFASEDReadBuffer16x16"
                        self.assertEqual(supplement, {parent: {"ram_ext": [["data", 64, 1], ["last", 0, 0]]}})
                        self.assertEqual(len(records), 1)
                        self.assertEqual(records[0]["depth"], 128 if target in ["rocket", "shuttle"] else 256)
                    else:
                        self.assertEqual(supplement, {})
                    self.assertEqual(hashlib.sha256(rtl.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
