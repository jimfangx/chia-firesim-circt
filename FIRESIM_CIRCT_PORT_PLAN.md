# FireSim Golden Gate Scala-FIRRTL → CIRCT Port

## Storage cleanup record (2026-10-14)

At the user's request, 8,731 old uncompressed `.mlir` dumps were removed from
`iteration167-u250-target-matrix` through `iteration189-u250-target-matrix`,
recovering 586 GiB. Their logs, JSON comparisons/annotations, final RTL, and
compressed archives remain; historical report paths to the deleted MLIR are
intentionally unavailable. Iterations 190 onward, the active compiler/build,
and all immutable SFC golden fixtures were preserved. Do not regenerate these
obsolete dumps merely because a historical comparison links to them.

## PRIORITY CHANGE: generalized U250 target matrix and shorter repeated testing (2026-10-13)

The user requested broad target coverage instead of repeatedly running only
the already-passing Rocket tests. At the next implementation turn, prioritize
compiling the U250 target matrix through CIRCT, inventorying failures, and
removing target/bridge/clock/model assumptions exposed by that matrix. Do not
continue an indefinite sequence of optional Print-only attachments without
checking the new targets. This user instruction supersedes the earlier
requirement to execute both long Rocket suites on every unchanged iteration.

SFC golden generation completed successfully for **all 13 recipes** with
`PLATFORM: xilinx_alveo_u250` in
`sims/firesim-staging/sample_config_build_recipes.yaml`. The results are in
the dedicated archive:

```text
/scratch/jfx/fsim-circt/automation/chia_firesim_circt/golden/u250-sfc-20261013/
```

Read `manifest.json` there: it records per-recipe generation status, exact
commands, assembly-jar hashes, input FIRRTL/annotation hashes, corrected
platform names, and output hashes. A `passed` entry contains actual SFC
MIDAS-transformed `design/FireSim-generated.sv` and collateral; `building` or
`failed` is not a usable golden. Never overwrite completed golden outputs.
Generation uses snapshotted assembly jars and separate input/output paths,
without invoking Vivado or modifying the active CIRCT candidate directory.
The resumable generator is
`automation/chia_firesim_circt/generate_u250_sfc_goldens.py`.

The matrix consists of:

| Recipe family | Target configuration | Platform configuration |
| --- | --- | --- |
| Rocket single core, no NIC | `FireSimRocketConfig` | `BaseXilinxAlveoU250Config` |
| Rocket quad core, no NIC | `FireSimQuadRocketConfig` | `BaseXilinxAlveoU250Config` |
| BOOM single core | `FireSimLargeBoomConfig` | `BaseXilinxAlveoU250Config` |
| Rocket single core with NIC | `WithNIC_FireSimRocketConfig` | `BaseXilinxAlveoU250Config` |
| Gemmini Rocket | `FireSimLeanGemminiRocketConfig` | `BaseXilinxAlveoU250Config` |
| Saturn Rocket | `FireSimREFV256D128RocketConfig` | `BaseXilinxAlveoU250Config` |
| Saturn Shuttle | `FireSimGENV256D128ShuttleConfig` | `BaseXilinxAlveoU250Config` |
| Dual Rocket split base, 0, 1 | `WithDefaultFireSimBridges_WithFireSimConfigTweaks_chipyard.DualRocketConfig` | `DualRocketTileQSFPBase`, `DualRocketTileQSFP0`, `DualRocketTileQSFP1` |
| Quad Rocket ring base, 0, 1 | `FireSimQuadRocketSbusRingNoCConfig` | `QuadTileRingNoCU250Base`, `QuadTileRingNoCU250Partition0`, `QuadTileRingNoCU250Partition1` |

The last three recipes name obsolete classes `QuadTileRingNoCBase`,
`QuadTileRingNoC0`, `QuadTileRingNoC1`. The generator explicitly maps them to
the corresponding existing U250 classes above and records requested/effective
values. Do not substitute an unpartitioned base config or silently drop a
partitioned target. Keep each of the 13 recipe results distinct.

For every completed golden, run the **same archived pre-MIDAS FIRRTL and
annotations** through the CIRCT Golden Gate pipeline in a separate candidate
directory, with that recipe's effective platform semantics. Record a matrix
row with SFC status, CIRCT compile status, comparison status, exact artifact
paths, and any unsupported feature. A rejected target is an implementation
gap, not a passing comparison. The next core compiler changes should remove
these gaps across targets; do not replace the target with the known Rocket
configuration just to get a green result.

Compare annotation/model/channel identity, bridge constructors, memory
geometry and behavior, clock/reset/token scheduling, MMIO and driver ABI,
XDC and platform interfaces against the actual SFC outputs. Compiler-generated
names and equivalent lowering can differ: textual SV equality is not required,
but normalized structural checks must be supplemented by behavioral or formal
checks where structure differs. A compile-only pass is not semantic parity.
Schedule targeted metasim execution for newly supported targets and full
coverage periodically; partitioned QSFP designs also need their appropriate
multi-partition integration tests. These fixtures do not prove Linux boot.

Testing cadence now implemented in `automation/chia_firesim_circt/loop.py`:

- Keep CIRCT compilation/provenance and a fresh bare-metal smoke execution
  each iteration, plus focused tests for every changed compiler feature.
- Reuse previously passing combined Rocket-suite results only when emitted
  RTL/collateral, driver sources/build wiring, runtime config, relevant
  environment/tool inputs, and workload version match. Reports explicitly
  identify reused evidence and its original verification directory.
- Run both long suites on input changes, new workload versions, every tenth
  verification at the latest, and before hardware verification. Failures
  invalidate the cache. Standalone workload groups remain required once for
  each new workload version. Do not delete tests merely because they pass.
- Expand compile/comparison coverage to this matrix rather than spending
  unchanged iterations repeating the same long Rocket execution. Integrating
  that matrix into automated harness gates is still required; this note and
  SFC generation alone do not claim CIRCT support for the targets.

## Current host recovery: PCI device absent despite loaded XDMA (2026-10-08 17:06 UTC)

The user's next enumeratefpgas run,
`deploy/logs/2026-10-08--17-01-12-enumeratefpgas-TTWTK7JO8T7V04EG.log` in the
test checkout, loaded xdma.ko successfully and then failed because
`chmod a+rw /dev/xdma*` found no nodes. Live sysfs still lacks
0000:01:00.0, confirming the earlier failed flash's PCI removal has not yet
been recovered. This is not a permission failure on an existing device; do
not suppress chmod failure or make empty XDMA node sets count as success.

The U250's USB JTAG interface is now visible in host sysfs as 0403:6011,
product A-U250-A64G, serial 21320733400E. This supersedes the USB-disconnected
observation below. Its observed USB serial differs from the old database's
UID Xilinx/21320733400XA; verify the fresh Vivado target UID and regenerate
the JTAG-to-BDF mapping rather than assuming the old UID remains correct.
Do not rely on the earlier helper's strict old-UID check for recovery here.

`automation/chia_firesim_circt/u250-recover-xdma.sh` (also copied to
`/tmp/u250-recover-xdma.sh`) invokes the installed golden PCI reconnect path
without programming or checking the old JTAG UID, then verifies PCI presence,
XDMA binding, device nodes, and permissions before retrying enumeratefpgas.
Its shell syntax is checked; hardware execution needs the user's host terminal.
After fresh enumeration succeeds, run infrasetup, require a valid live
fingerprint, then retry Linux. No CIRCT RTL change is indicated by this failure.

## Current host blocker: no USB JTAG target visible (2026-10-08 06:11 UTC)

The user applied and installed the flash-script fix below. The next infrasetup
(`deploy/logs/2026-10-08--06-08-02-infrasetup-971ITCUH4JLHZFEV.log` in the test
checkout) now correctly fails with Vivado Lab's `ERROR: [Labtoolstcl 44-199]
No matching targets found on connected servers: localhost`. This occurs at
`get_hw_targets`, before selecting a board or programming. The agent's read
of the host USB sysfs inventory contains no FTDI/Xilinx/Digilent programmer;
check/restore the U250 USB JTAG connection to lab, or establish the correct
remote hardware-server connection if the cable is on another host. This is a
hardware-access blocker, not evidence of a CIRCT RTL/driver failure.

The failed flash already removed PCI function 0000:01:00.0 and unloaded XDMA;
the function is currently absent from sysfs. Restore PCI enumeration before
retrying (the installed golden `firesim-fpga-util.py --reconnect-bdf` path
rescans and enables MMIO). After restoring JTAG, first run the golden
`get_serial_dev_for_fpgas.tcl` in batch mode and require the expected UID
Xilinx/21320733400XA. Then reconnect the PCI function, rerun infrasetup, and
require the live fingerprint to return 0x46697265 before runworkload. No RTL
or bitstream rebuild is required to resolve this host connection blocker.

Host-terminal recovery helper: `sudo bash /tmp/u250-jtag-check-and-reconnect.sh`.
It first runs the golden JTAG enumeration Tcl in batch mode and requires the
expected UID before invoking the installed PCI reconnect utility. It never
programs a bitstream. Shell syntax and preflight ordering are checked; it
still needs execution outside this sandbox with the cable connected.

## Immediate FPGA fix: programming failure was reported as success (2026-10-08)

The host-terminal read-only probe returned INIT_DONE=1 and both fingerprint
registers=0xdeadbeef for three consecutive samples. The actual flash session
in `/home/firesim/vivado_lab.log` (05:51:07, PID 94065) identifies the cause:
`program_fpga.tcl` failed at its unconditional initial `close_hw_target` with
`ERROR: [Labtoolstcl 44-142] There is no current hw_target and no option value
given for 'hw_target'.` It never reached `program_hw_devices`. The launcher
used `-mode tcl`, swallowed its captured output on return code zero, and
incorrectly printed `Successfully programmed FPGA`. The FPGA was left with
the previously loaded image/state; successful staging did not mean flashing.

The main worktree now fixes three U250 scripts: guard the initial close with
`current_hw_target -quiet` in both `program_fpga.tcl` and
`get_serial_dev_for_fpgas.tcl`, and use `-mode batch` in `util.py` so sourced
script errors return failure. Preserve and integrate this fix. The patch
`automation/chia_firesim_circt/patches/u250-flash-failure.patch` applies cleanly
to the golden test checkout but still needs application there and installation
to `/usr/local/bin` from that checkout. The existing installer is
`/tmp/align-firesim-u250-scripts.sh --install` (run with sudo).

Verification: actual Vivado Lab 2023.1 with a deliberately failing Tcl script
and no hardware access returns 0 in Tcl mode, 1 in batch mode. Tcl command
stubs validate unopened/opened target cases, missing serial, and failed
programming; the Python launcher test checks batch mode and preservation of
failure status/output. Logs/tests are in `/tmp/u250-flash-script-tests/`.
No bitstream rebuild is required for these host-script fixes. After installing,
rerun infrasetup and require the live master probe and driver's fingerprint
check to pass before runworkload. Reflashing and FPGA Linux boot remain pending.

## FPGA runtime blocker: fingerprint check before Linux (2026-10-08)

The separate `/scratch/jfx/chipyard-circt-test` checkout now has the host
hierarchy patch and successfully produced the U250 Rocket bitstream in
`sims/firesim/deploy/results-build/2026-10-08--04-29-57-circt_u250_firesim_rocket_singlecore/`.
This supersedes the earlier hierarchy note's statements that the patch was
unapplied there and bitstream success unverified. Linux boot is still unverified.

The 05:51:18 FPGA workload exited with driver status 1 before DRAM/bridge
initialization: `FireSim fingerprint: 0xdeadbeef`, then
`Invalid FireSim fingerprint`. The manager's `Job ... completed!` means the
screen session exited; it does not establish successful target execution.

Read-only comparison established that the compiled bitstream's control
decoder, simulation-master bank/MCR file, read/write dispatchers and arbiters,
and F1Shim match the current regenerated RTL. The generated driver uses
INIT_DONE=0x220, PRESENCE_READ=0x224, PRESENCE_WRITE=0x228, consistent with the
built decoder and MCR indexing. Driver and RTL both expect/reset to
0x46697265. The staged bitstream and built bitstream have the same SHA-256:
`8dfb862f491076b1b34d627b6226d597fc6d797a2c403444532884557ca69443`.
There is no evidence yet that the immediate failure requires a CIRCT-specific
driver ABI change.

The golden `firesim-generate-fpga-db.py` deliberately writes 0xDEADBEEF while
enumerating boards, then reflashes them to restore the fingerprint. This is
a diagnostic lead, not proof that enumeration caused this failure. Determine
the live master register values and verify JTAG-to-BDF mapping/reset before
changing the driver or bypassing the fingerprint check. Preserve the actual
run logs and report failed driver exits as failures in verification results.

`automation/chia_firesim_circt/u250-master-probe.c` is a read-only, volatile
MMIO probe for these checked register addresses. It compiles with warnings
as errors and passes regular-file fixtures for valid, enumeration-marker,
and invalid values without modifying the fixtures. Actual hardware access
is not available in this agent's sandbox; the probe still needs a host-terminal
run. After correcting hardware state, require the matching driver's
`+check-fingerprint` to pass before retrying Linux.

## FPGA priority: preserve host hierarchy normalization (2026-10-08)

The U250 Rocket build in the separate `/scratch/jfx/chipyard-circt-test`
checkout crashed in Vivado 2023.2.2 while processing the shell-injected
`RAM_STYLE ULTRA` synthesis constraint. Its compiler-emitted synthesis XDC
matches the SFC comment-only file, and the shell command is unchanged. The
CIRCT assembly pipeline instead produced 98 nested `sim` instances versus
one in SFC. This is a suspected crash trigger, not a confirmed root cause.

A host hierarchy normalization fix is now implemented in this main worktree:
`sim/goldengate-circt/lib/Support/SimulatorRTL.cpp` and
`tools/goldengate.cpp`. Preserve these changes and the new
`test/Support/HostHierarchyTest.cpp`; do not duplicate or replace the work.
Driver headers are prepared against the assembly boundaries first, then
CIRCT's native inliner collapses single-use construction wrappers before XDC
path resolution. Removed wrappers become private so their unused bodies are
eliminated. New declaration names lose repeated `sim_` prefixes with collision
handling. Target, SRAM, helper modules, attached inner symbols, and explicitly
referenced XDC modules retain their hierarchy. Inline blackbox sources are
attached before dead-module elimination; the retained JSON archive is historical
and is not replayed against deleted assembly modules.

Verification: host hierarchy, XDC emission, and interface-header unit tests
pass. Applying the final normalization to the failing checkout's actual Rocket
boundary removes 97 wrappers, reduces the FPGATop-to-cache path from 106 to 9
instance levels, leaves one `sim` level, emits a shallow generated-clock XDC,
and passes Verilator lint with fatal warnings enabled. A 1,024-cycle sequential
comparison matches the original 98-wrapper register path cycle for cycle.
Artifacts and logs are under `/tmp/fsim-wrapper-collapse/`.

The patch for the separate test checkout is
`automation/chia_firesim_circt/patches/host-hierarchy-normalization.patch`;
`git apply --check` succeeds there. It has not been applied to that checkout.
Commit the integrated main-worktree change through the existing checkpoint
procedure, then run the required fresh driver/runtime regression gates.
Rebuild CIRCT RTL and retry Vivado synthesis before claiming the segfault or
FPGA bitstream build is fixed. Runtime and bitstream success remain unverified
for this hierarchy change.

## Priority: fix clean CIRCT metasim builds (2026-10-08)

**At the next iteration, integrate the missing `.const.vh` emission and build
dependencies before proceeding with further compiler features.** The fresh
`/scratch/jfx/chipyard-circt-test` checkout failed `builddriver` with
`No rule to make target 'FireSim-generated.const.vh'`. CIRCT emits `.const.h`
and `.defines.vh`, but does not emit the Verilog interface constants header
required by `sim/midas/src/main/cc/Makefile` and the metasim `top.sv`.
`.defines.vh` contains DDR shell settings and cannot substitute for `.const.vh`.

The main Chia worktree still contains an SFC-generated `.const.vh` dated
2026-10-01. Earlier successful metasim gates therefore do not establish that
this header is generated by CIRCT or that a fresh checkout builds successfully.
At the time of this note, the fix is applied as uncommitted changes in the test
checkout, but is absent from the main Chia compiler and published checkpoints.

A prepared patch is preserved at
`automation/chia_firesim_circt/patches/emit-const-vh.patch`. Inspect and adapt it
to the current compiler revision; do not overwrite other shared-worktree edits.
It adds native `.const.vh` emission from validated FPGATop/F1Shim port widths
in `MetasimInterfaceHeader.cpp`, extends the header tests, and adds the emitted
file to the Golden Gate grouped outputs/copy step and Verilator dependencies
in `sim/make/goldengate.mk` and `sim/make/verilator.mk`. Commit and push the
integrated fix using the existing checkpoint procedure.

Focused header tests, emission from the test checkout's Rocket boundary, and
Verilator lint of its simulator RTL passed for the prepared patch. A complete
driver build and runtime regression with the fix still need verification.
Have the verification harness build with a fresh output directory, or archive
the existing mutable generated `.const.vh` before rebuilding. Require a newly
CIRCT-emitted `circt-ingestion/FireSim-generated.const.vh` and its copied
top-level counterpart before `builddriver`, then run all required metasim
regressions. Preserve the immutable SFC golden fixtures. FireSim manager
actions remain owned by the verification harness.

## Updated notes:

1. The FireSim FAME disseratation is here: scratch/jfx/fsim-circt/dissertation.pdf with a markdown version here: scratch/jfx/fsim-circt/dissertation.md for your reference.
2. For upcoming iterations, use subagents for independent analysis or coding when useful. Keep shared-worktree edits coordinated through the main agent. Wait for all spawned subagents, incorporate their results, and close or stop them before returning.
3. Push to the 2 working github repos (git@github.com:jimfangx/chia-firesim-circt.git for the outer repo, and https://github.com/jimfangx/chia-firesim-submodule-circt for changes in sims/firesim) as soon as there is significant progress (as backup), does't matter if it doens't pass my first baremetal test. Push frequently.
4. The user-requested Rocket bare-metal regression suite in `automation/chia_firesim_circt/workloads/circt-metasim-bare/` is now a required Chia verification gate. At the next iteration, inspect and integrate the gates specified in **Required Chia Rocket bare-metal regression gate** below. The existing smoke workload alone is insufficient. Minimal harness changes needed to run these regressions and return their results to the implementation agent are explicitly in scope; this wiring is verification work, not a completed compiler milestone. FireSim manager actions remain owned by the verification harness.

IMPORTANT: Inspect the last section of /scratch/jfx/fsim-circt/automation/chia_firesim_circt/logs/codex_20261004_204001.log, which represents the last run; continue from progress made there.

Additionally: manage space, remove or zip/compress old logs as needed. AVOID RUNNING OUT OF DISK SPACE.


## Mission

Port FireSim's Golden Gate compiler from the legacy Scala FIRRTL Compiler (SFC) infrastructure to CIRCT/MLIR while preserving FireSim simulation semantics.

The long-term target is:

```text
Chisel / target generator
        |
        | FIRRTL + annotations
        v
+------------------------------+
| CIRCT Golden Gate compiler   |
|                              |
| target transformation        |
| channel/model construction   |
| FAME-1                       |
| abstract model / FAME-2      |
| FAME-5 model threading       |
| bridge integration           |
| simulator synthesis          |
+------------------------------+
        |
        v
CIRCT HW / Seq / Comb / SV
        |
        v
SystemVerilog + metadata + XDC
        |
        v
FireSim FPGA / metasim flow
```

Do **not** interpret this project as merely translating `FAMETransform.scala` into C++.

Current Golden Gate contains substantial machinery around FAME:

```text
annotations
hierarchy manipulation
bridge extraction
model extraction
channel discovery
clock-domain discovery
channel excision
FAME-1
FAME-5
RAM models
simulation mapping
FPGA specialization
host-side bridge elaboration
metadata/collateral generation
```

The migration must therefore happen incrementally with the Scala implementation serving as the executable reference.

As of September 2026, current FireSim still documents its input as requiring Chisel 3.6 / Scala-FIRRTL-compatible FIRRTL.

Current CIRCT release `firtool-1.160.0` was released September 25, 2026. Pin the compiler to a specific CIRCT revision rather than tracking `main`; `1.160.0` is a reasonable initial baseline.

---

# 1. Fundamental project rules

The agent implementing this project must follow these rules.

### Rule 1: Preserve the old compiler as an oracle

Do not remove or modify the Scala Golden Gate implementation during the initial port.

Every CIRCT feature should be tested against:

```text
same FIRRTL
same annotation file
      |
      +------------+
      |            |
      v            v
 Scala GG       CIRCT GG
      |            |
      v            v
 reference       candidate
 simulator       simulator
```

Functional equivalence matters more than textual RTL equivalence.

### Rule 2: Port semantics, not Scala AST structure

Avoid writing a literal line-for-line translation of FIRRTL's Scala AST.

For example:

```scala
case Connect(_, WRef(...), ...)
```

should normally become an MLIR operation analysis or rewrite based on SSA values and FIRRTL operations.

Use CIRCT infrastructure wherever possible:

- FIRRTL dialect
- MLIR SSA
- symbol tables
- `InstanceGraph`
- FIRRTL annotations
- inner symbols
- hierarchical paths
- standard FIRRTL lowering passes
- HW/Seq/Comb dialects
- ExportVerilog

CIRCT already represents a FIRRTL circuit as `firrtl.circuit`, FIRRTL modules as operations, registers as `firrtl.reg`/`firrtl.regreset`, etc.

### Rule 3: Do not rewrite FIRRTL→Verilog

CIRCT already owns this problem.

Golden Gate should terminate in ordinary CIRCT IR and use the standard backend.

### Rule 4: Do not eliminate Chisel during this project

Removing the **Scala FIRRTL Compiler** and removing **Scala/Chisel generators** are separate projects.

FireSim bridges currently have another RTL elaboration stage in which BridgeModules are themselves generated from Chisel.

For the first complete CIRCT implementation:

```text
Chisel remains allowed for RTL generation.

Scala FIRRTL transforms do not.
```

### Rule 5: Do not build a Golden Gate MLIR dialect before FAME-1 works

A `gg` dialect may eventually be desirable, but don't make the initial compiler depend on it.

Phase 1:

```text
FIRRTL dialect
    |
C++ Golden Gate passes
    |
FIRRTL dialect
```

Only introduce a Golden Gate dialect after the correctness boundary is understood.

---

# 2. Establish the exact reference pipeline

Before implementing anything, inspect the current FireSim checkout.

The primary source of truth is:

```text
sim/midas/src/main/scala/midas/passes/MidasTransforms.scala
```

Current Golden Gate runs, approximately:

```text
initial FIRRTL normalization
    ↓
optional FireAxe transformation
    ↓
PlusArgs wiring
    ↓
BridgeExtraction
    ↓
AutoCounterTransform
AssertionSynthesis
PrintSynthesis
TriggerWiring
GlobalResetConditionWiring
    ↓
ChannelClockInfoAnalysis
UpdateBridgeClockInfo
    ↓
WrapTop
LabelMultiThreadedInstances
    ↓
optional model transforms
    ↓
ExtractModel
    ↓
PromotePassthroughConnections
    ↓
FAMEDefaults
FindDefaultClocks
ChannelExcision
InferModelPorts
    ↓
FAMETransform                 ← FAME-1
    ↓
DefineAbstractClockGate
AddRemainingFanoutAnnotations
    ↓
MultiThreadFAME5Models        ← FAME-5
    ↓
InlineInstances
EmitAndWrapRAMModels
SimulationMapping
HostSpecialization
```

That ordering is visible directly in current `MidasTransforms.scala`.

Before writing C++, generate and archive all of the existing debug artifacts produced around these boundaries:

```text
pre-partition.fir
post-bridge-extraction.fir
post-wrap-top.fir
pre-extract-model.fir
post-extract-model.fir
post-promote-passthrough.fir
post-fame-defaults.fir
post-find-default-clocks.json
post-channel-excision.fir
post-channel-excision.json
post-infer-model-ports.fir
post-infer-model-ports.json
post-fame-transform.fir
post-fame-transform.json
pre-fame5-transform.fir
post-fame5-transform.fir
post-fame5-transform.json
post-gen-sram-models.fir
```

Do this for at least:

```text
GCD
simple combinational target
simple stateful target
multi-channel target
multi-clock target
bridge-containing target
threaded/FAME-5 target
Rocket
```

These files become golden fixtures.

### Recorded SFC golden reference — FireSimRocketConfig / U250

The following two local directories are the recorded reference from the
existing Scala FIRRTL Compiler (SFC) FireSim flow.  They are intentionally
named with `sfc-golden-2026-10-01`; do not use either as a mutable build
output directory.

```text
/scratch/jfx/fsim-circt/sims/firesim-staging/generated-src/firechip.chip.FireSim.FireSimRocketConfig.sfc-golden-2026-10-01
/scratch/jfx/fsim-circt/sims/firesim/deploy/results-build/2026-10-01--04-55-23-circt_u250_firesim_rocket_singlecore/cl_xilinx_alveo_u250-firesim-FireSim-FireSimRocketConfig-BaseXilinxAlveoU250Config.sfc-golden-2026-10-01
```

The first directory is the primary compiler oracle: it contains the SFC
generated FIRRTL, annotations, graph, DTS, register maps, and transformed
RTL/collateral for `FireSimRocketConfig`.  A CIRCT implementation must compare
its corresponding outputs against this fixture before it advances to a later
verification gate.

The second directory is the corresponding U250 FireSim build-tree reference
for the same quintuplet.  Use it to compare emitted FPGA-facing RTL and
collateral structure.  It is a build-tree fixture, not evidence by itself
that a bitstream completed; completion must still be established by the
FireSim manager log and the generated HWDB entry.

The reference was produced through the standard manager flow, whose compiler
phase is:

```bash
firesim -c config_runtime.yaml -b config_build.yaml \
  -r config_build_recipes.yaml -a config_hwdb.yaml buildbitstream
```

This command's existing `replace-rtl` step is the SFC baseline.  The Chia loop
must preserve these fixtures and report any candidate/output diff alongside
the verification-run stdout/stderr and FireSim-manager log paths.

### Chia verification gate: Verilator metasimulation

After a CIRCT `replace-rtl` compile succeeds, the next required gate is a
FireSim metasimulation using **Verilator**, not VCS and not an FPGA.  The
transient metasim configuration must set:

```yaml
metasimulation:
  metasimulation_enabled: true
  metasimulation_host_simulator: verilator
```

Run the normal FireSim manager metasim workflow (`builddriver`,
`launchrunfarm`, `infrasetup`, then `runworkload`) against the transformed RTL
using the existing smoke workload and the required Rocket regression suites
below. Retain `br-base-uniform.json` for the separate Linux integration gate.
Advance to `buildbitstream` for the U250 only after the required Verilator
gates pass. The harness must retain complete stdout, stderr, and the FireSim
manager log path for each gate.

#### Required Chia Rocket bare-metal regression gate

The test sources, binaries, FireSim workload descriptors, validator, and
coverage documentation are in:

```text
/scratch/jfx/fsim-circt/automation/chia_firesim_circt/workloads/circt-metasim-bare/
```

Read that directory's `README.md` before wiring the gate. These tests cover
integer arithmetic, memory/cache and store masks, control flow, atomics, CSRs,
traps, Sv39/PMP, CLINT interrupts/WFI, floating point, instruction-cache
coherence, CRC32/SHA-256, matrix multiplication, pointer chasing, and direct
UART output. Run the same ELF artifacts through each compiler's simulator.

**Required execution and cadence:**

1. On initial integration, and whenever the test sources, runtime, platform
   assumptions, or toolchain change, build all 14 new ELF binaries and their
   descriptors. Run the portable programs through Spike and validate the log
   checker. Archive the resulting ELF hashes and reference results by version.
   Spike validates the workloads; it does not replace CIRCT metasimulation.
2. After every successful CIRCT `replacertl` compile and Verilator build in
   the `metasim`, `boot`, or `full` verification profiles, the harness must
   run these workloads in order against that candidate RTL:

   - `circt-metasim-bare`: the existing quick smoke gate;
   - `circt-fame-suite`: all 11 portable groups, currently 90,141 checks;
   - `circt-fame-rocket-suite`: all portable groups plus direct UART/FIFO
     traffic and eight ordered output epochs, currently 90,805 checks.

   A `compile`-only profile does not constitute a regression-suite pass.
3. During first integration and before declaring the Rocket/FAME-1 milestone
   passed, also run all 12 standalone `circt-fame-<group>` programs in metasim:
   `integer`, `memory`, `control`, `atomics`, `csr`, `traps`, `vm`,
   `interrupts`, `fp`, `icache`, `workloads`, and `uart`. Thereafter use the
   relevant standalone programs to diagnose a failing combined-suite group.
4. Stop progression to U250/Linux gates on any required workload, validator,
   or cleanup failure. Do not increment a regression-success streak from the
   original smoke marker alone. Resume compiler work using the failing group's
   logs; do not skip, weaken, or replace checks merely to obtain a pass.

Prepare the suite from the project environment with:

```bash
fame_workload_root=/scratch/jfx/fsim-circt/automation/chia_firesim_circt/workloads/circt-metasim-bare
make -C "$fame_workload_root" suite manifests
make -C "$fame_workload_root" validate-spike
(cd "$fame_workload_root" && python3 -m unittest -v test_validator)
```

`firesim-workloads/` contains the generated descriptors. Install each selected
descriptor in `sims/firesim/deploy/workloads/`; its absolute ELF and dummy
rootfs paths permit direct FireSim execution without changing the original
FireMarshal smoke manifest. Use a separate transient runtime YAML per workload
and a unique suffix tag containing the iteration and workload name. Select
`metasimulation_enabled: true`, `metasimulation_host_simulator: verilator`,
and `workload_name: <binary-name>.json`. The existing
`make_metasim_runtime_config.py` supports `--workload-name` and `--suffix-tag`;
ensure the emitted YAML actually selects Verilator. Reuse the candidate build
only when its RTL and collateral identity match this iteration's compile.

For each selected workload, use the manager-owned `launchrunfarm`,
`infrasetup`, and `runworkload` sequence, with `builddriver` where needed.
Always call `terminaterunfarm` after a launched farm, including setup, runtime,
timeout, or validator failures. Keep both a target-cycle limit and an external
wall-time timeout; a stalled WFI/tohost path or a missing final result is a
failed test. Set sufficient limits to complete the suite and retain the chosen
limits in the logs. Do not race the active smoke run or another verification
run over shared runtime configs, run directories, or manager slots.

**Acceptance must use the full validator, not a PASS substring search.**
Resolve the exact `uartlog` belonging to this iteration/workload, then run:

```bash
python3 "$fame_workload_root/check_uart.py" /path/to/this-run/uartlog \
  --binary circt-fame-suite \
  --reference "$fame_workload_root/spike-results/expected-results.json"
```

The portable suite must match the archived checks and signatures; its current
aggregate signature is `0x78194504c338c229`. The checker requires one complete
suite, the correct ordered test events, consistent counts and signatures,
and no failure/fatal markers. For UART-bearing programs it also requires all
eight direct `FAME_UART: DATA` lines and `FAME_UART: END` exactly once, in
order, within the UART test. The ordinary FESVR printf transcript does not
substitute for target UART output.

Establish an SFC metasim baseline for the **same ELF hashes** in a separate,
mutable reference run directory; preserve the existing immutable SFC compiler
and U250 build fixtures. Capture a validated full Rocket-suite baseline with:

```bash
python3 "$fame_workload_root/check_uart.py" /path/to/sfc/uartlog \
  --binary circt-fame-rocket-suite --write-reference /path/to/sfc-expected.json
python3 "$fame_workload_root/check_uart.py" /path/to/circt/uartlog \
  --binary circt-fame-rocket-suite --reference /path/to/sfc-expected.json
```

Use the corresponding binary name/reference entry for standalone programs.
Until the UART-bearing SFC baseline exists, still run the CIRCT program and
the validator without `--reference`, and explicitly report that differential
comparison is pending. Do not bless candidate output as an SFC baseline or
claim differential parity from Spike alone. Before milestone sign-off, repeat
supported memory-latency/bandwidth and bridge-backpressure configurations,
retaining matching SFC/CIRCT workload signatures.

Store per-workload command stdout/stderr, FireSim manager logs, exact result
directory and UART path, validator output, runtime YAML, ELF hashes, and
reference identity beneath the existing iteration verification directory.
Use distinct command/gate names so suites cannot overwrite smoke logs. Return
all required gate return codes and failed group names in the next Codex
iteration's feedback. Report which suites ran and their signatures; the label
`full` or a manager exit status alone is not evidence of their execution.

**Harness activation is required.** The current `loop.py` hardcodes the smoke
workload in `verify_with_firesim`; add the minimal dispatch, workload selection,
validation, and result aggregation needed for these gates. A running driver or
Ray worker may retain previously loaded Python code. Confirm activation from
the next verification transcript and actual suite result directories; edits
to this plan or to a source file alone do not prove the active runner adopted
the gates. If it still runs only the smoke workload, explicitly report the
new gate as pending activation and resolve the runner integration before
counting a regression pass. Keep manager execution in the harness and return
to core compiler work after this required integration.

These Rocket workloads supplement Milestone 7's independent FAME semantic
testbench. They do not replace arbitrary token-schedule/host-reset checks,
multiclock tests, FAME-5 tests, or enabled Print/AutoCounter bridge tests.

#### Manual Verilator metasim command sequence

Use this manager-owned sequence when manually exercising the same gate.  It
does not SSH directly to a build or FPGA machine; FireSim Manager owns any
required run-farm communication.

```bash
source /home/firesim/miniforge3/etc/profile.d/conda.sh
cd /scratch/jfx/fsim-circt/sims/firesim
source ./sourceme-manager.sh --skip-ssh-setup
cd deploy

python3 /scratch/jfx/fsim-circt/automation/chia_firesim_circt/make_metasim_runtime_config.py \
  --source config_runtime.yaml \
  --output config_runtime_metasim.yaml

# Confirm the generated metasim configuration and installed simulator.
grep -A6 '^metasimulation:' config_runtime_metasim.yaml
verilator --version

# Gate 1: CIRCT replace-RTL path. Ensure the CIRCT-selection change is active.
firesim -c config_runtime.yaml -b config_build.yaml \
  -r config_build_recipes.yaml -a config_hwdb.yaml replacertl

# Gate 2: Verilator metasimulation of the resulting transformed RTL.
firesim -c config_runtime_metasim.yaml -b config_build.yaml \
  -r config_build_recipes.yaml -a config_hwdb.yaml builddriver

firesim -c config_runtime_metasim.yaml -b config_build.yaml \
  -r config_build_recipes.yaml -a config_hwdb.yaml launchrunfarm

firesim -c config_runtime_metasim.yaml -b config_build.yaml \
  -r config_build_recipes.yaml -a config_hwdb.yaml infrasetup

firesim -c config_runtime_metasim.yaml -b config_build.yaml \
  -r config_build_recipes.yaml -a config_hwdb.yaml runworkload
```

After `launchrunfarm`, always clean up the run farm on success or failure:

```bash
firesim -c config_runtime_metasim.yaml -b config_build.yaml \
  -r config_build_recipes.yaml -a config_hwdb.yaml terminaterunfarm
```

Do not run `buildbitstream` until `runworkload` passes.

---

# 3. Repository/build structure

Initially create a standalone CIRCT-based Golden Gate compiler rather than changing `firtool`.

Suggested source tree:

```text
firesim/
  sim/
    goldengate-circt/
      CMakeLists.txt

      include/
        goldengate/
          Analysis/
          Transforms/
          Support/

      lib/
        Analysis/
          AnnotationAnalysis.cpp
          ChannelAnalysis.cpp
          ClockAnalysis.cpp
          ModelAnalysis.cpp

        Transforms/
          FAME1.cpp
          FAME5.cpp
          BridgeExtraction.cpp
          ChannelExcision.cpp
          InferModelPorts.cpp
          ...

        Support/
          AnnotationUtils.cpp
          TargetUtils.cpp

      tools/
        goldengate/
          goldengate.cpp

      test/
        Analysis/
        Transforms/
        Integration/
```

The executable should eventually be:

```bash
goldengate-circt
```

Do not overload `firtool`.

The initial invocation should resemble:

```bash
goldengate-circt \
  target.fir \
  --annotation-file target.anno.json \
  --output-dir build/goldengate-circt
```

Debug mode must support IR dumps:

```bash
goldengate-circt \
  target.fir \
  --annotation-file target.anno.json \
  --mlir-print-ir-after-all
```

CIRCT's recommended developer build uses LLVM/MLIR as a submodule and CMake/Ninja; build either `circt-opt` or `firtool` first to establish the environment.

Use:

```bash
git clone https://github.com/llvm/circt.git --recursive
cd circt

git checkout firtool-1.160.0
git submodule update --init --recursive

cmake -G Ninja llvm/llvm -B build \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DLLVM_ENABLE_ASSERTIONS=ON \
  -DLLVM_TARGETS_TO_BUILD=host \
  -DLLVM_ENABLE_PROJECTS=mlir \
  -DLLVM_EXTERNAL_PROJECTS=circt \
  -DLLVM_EXTERNAL_CIRCT_SOURCE_DIR="$PWD" \
  -DLLVM_ENABLE_LLD=ON

ninja -C build firtool circt-opt
```

Pin both:

```text
CIRCT tag
LLVM submodule hash
```

in FireSim's build metadata.

Never silently update them.

---

# 4. Milestone 0 — prove CIRCT can ingest Golden Gate's input

Do this before implementing a FAME pass.

Take the exact:

```text
post-infer-model-ports.fir
post-infer-model-ports.json
```

from the Scala pipeline.

Make CIRCT parse them.

The success criterion is:

```text
FIRRTL + annotation file
       ↓
CIRCT parser
       ↓
valid firrtl.circuit
       ↓
dump IR
```

No transformation yet.

Check all FireSim annotation classes found in the file.

Create a script:

```text
tools/list-firesim-annotations.py
```

that scans annotation JSON and emits the distinct `class` fields and counts.

Produce a table like:

```text
annotation class                               count
----------------------------------------------------
FAMEChannelConnectionAnnotation               ...
FAMEChannelPortsAnnotation                    ...
FAMETransformAnnotation                       ...
BridgeIOAnnotation                            ...
...
```

Do this for GCD, bridge tests, multiclock tests, FAME-5 tests, and Rocket.

This table defines the annotation migration surface.

CIRCT already supports arbitrary annotations, attaches annotations directly to operations/ports, and specifically supports annotation files through `firtool --annotation-file`. `LowerTypes` also propagates annotations to lowered ground fields.

---

# 5. Milestone 1 — build annotation access infrastructure

Create:

```cpp
namespace goldengate {

struct AnnotationClasses {
  static constexpr StringLiteral FAMETransform = "...";
  static constexpr StringLiteral ChannelConnection = "...";
  ...
};

}
```

Do **not** scatter literal Java/Scala annotation class strings throughout transformations.

Write utilities such as:

```cpp
SmallVector<firrtl::Annotation>
getAnnotations(Operation *op);

SmallVector<firrtl::Annotation>
getPortAnnotations(FModuleLike module, unsigned port);

bool hasAnnotation(Operation *, StringRef className);

std::optional<firrtl::Annotation>
getAnnotation(Operation *, StringRef className);
```

Use CIRCT's existing annotation APIs rather than parsing raw attribute dictionaries everywhere. Current CIRCT exposes `AnnoTarget`, `OpAnnoTarget`, `PortAnnoTarget`, and helpers for retrieving FIRRTL annotations.

## Critical annotation rule

Do **not** attempt to reproduce SFC `RenameMap` globally.

CIRCT annotation handling differs from SFC; in particular, custom annotations do not automatically acquire all of SFC's rename/inlining behavior. A 2026 CIRCT migration issue documents exactly this sort of divergence around module inlining.

Instead, categorize every annotation as one of:

```text
Consumed:
    GG pass reads and deletes it.

Preserved:
    annotation remains attached to unchanged entity.

Transferred:
    pass moves annotation to replacement operation/port.

Expanded:
    one source target becomes N targets.

Serialized:
    annotation becomes Golden Gate runtime/collateral metadata.
```

Each custom pass must explicitly implement the appropriate behavior.

Add verifier tests ensuring no meaningful Golden Gate annotation simply disappears.

---

# 6. Milestone 2 — build stable target identity

SFC's implementation frequently thinks in terms of:

```text
CircuitTarget
ModuleTarget
InstanceTarget
ReferenceTarget
```

Do not replace this with raw strings.

Construct a Golden Gate target abstraction backed by CIRCT symbols:

```cpp
struct GGTarget {
  firrtl::CircuitOp circuit;
  firrtl::FModuleLike module;

  // Optional hierarchical context.
  SmallVector<firrtl::InstanceOp> instancePath;

  // Optional local target.
  Operation *operation = nullptr;
  std::optional<unsigned> port;
  std::optional<unsigned> fieldID;
};
```

Where stable references need to survive structural transforms, use:

```text
inner symbols
hierarchical paths
symbol refs
```

rather than storing names such as:

```text
"Top.tile_0.core.regfile.r3"
```

CIRCT FIRRTL already supports inner symbols on ports and many internal declarations.

Implement:

```cpp
GGTarget resolveAnnotationTarget(...);
hw::InnerRefAttr makeStableRef(...);
```

Add tests that survive:

```text
LowerTypes
module cloning
port expansion
inlining
module extraction
```

---

# 7. Milestone 3 — hierarchy and instance analysis

Do not port FireSim's hierarchy bookkeeping literally.

Use CIRCT's:

```cpp
circt::firrtl::InstanceGraph
```

CIRCT's instance graph tracks modules and every location where they are instantiated and supports top-down/bottom-up traversal.

Build a thin Golden Gate wrapper:

```cpp
class GGHierarchyAnalysis {
public:
  FModuleOp getTop();
  ArrayRef<InstanceRecord *> uses(FModuleLike module);
  SmallVector<InstancePath> enumerateInstances(FModuleLike);
  bool isUniquelyInstantiated(FModuleLike);
};
```

Test:

```text
Top
 ├─ A
 │   └─ C
 └─ B
     └─ C
```

Ensure both `C` instance paths are distinguishable.

This matters later for:

```text
bridge extraction
model extraction
FAME-5 grouping
hierarchical annotations
FireAxe
```

---

# 8. Milestone 4 — recreate the pre-FAME model/channel representation

Golden Gate fundamentally works on a graph:

```text
Models = nodes
Channels = edges
Tokens = values moving along channels
```

FireSim's documentation defines Golden Gate this way and describes models as consuming/producing token streams while remaining latency-insensitive.

Create in-memory analyses first; **do not create a dialect yet**.

Suggested structures:

```cpp
struct GGClockDomain {
  Value targetClock;
  std::string name;

  uint64_t multiplier;
  uint64_t divisor;
  unsigned mfmr;
};

enum class ChannelDirection {
  Sink,
  Source
};

struct GGChannelPort {
  unsigned modulePort;
  firrtl::FIRRTLBaseType type;
};

struct GGChannel {
  StringAttr name;
  ChannelDirection direction;

  firrtl::FModuleOp module;

  SmallVector<GGChannelPort> ports;

  GGClockDomain *clockDomain;

  bool isClockChannel;
  bool isLoopback;
};

struct GGModel {
  firrtl::FModuleOp module;

  SmallVector<GGChannel *> inputs;
  SmallVector<GGChannel *> outputs;
};
```

Then build:

```cpp
GGModelAnalysis
GGChannelAnalysis
GGClockAnalysis
```

from the annotations left by `InferModelPorts`.

---

# 9. Milestone 5 — port FAME channel analysis before FAME hardware rewriting

This is one of the most important milestones.

Current FAME-1 depends on information equivalent to:

```text
modelInputChannelPortMap
modelOutputChannelPortMap
connectivity
transformedModules
transformedSources
transformedSinks
transformedLoopbacks
targetClockChInfo
```

The current `FAMETransform.scala` uses this information before performing the actual RTL rewrite.

Implement it separately.

The central problem is combinational dependency:

```text
Does output channel O require input channel I
to compute O's next token?
```

For each transformed module construct:

```text
input-channel → output-channel dependency graph
```

Example:

```text
input A ─────┐
             ├── combinational logic ── output X
input B ─────┘

input C ─────────────────────────────── output Y
```

should produce:

```text
X depends on {A, B}
Y depends on {C}
```

Be careful about:

```text
mux conditions
wire aliases
output used as an internal wire
nodes
casts
primitive operations
memory read ports
register boundaries
```

A register **breaks combinational dependency**.

Do not simply recursively traverse all SSA operands past sequential operations.

Create a reusable function:

```cpp
bool isCombinational(Operation *);

DenseSet<Value>
findCombinationalInputDependencies(Value output);
```

The current Scala code explicitly uses connectivity information to derive output-to-input channel dependencies before establishing valid signals.

### Validation

For every reference module, print:

```text
MODULE Foo

INPUT CHANNELS
  in0: a,b
  in1: c

OUTPUT CHANNELS
  out0: x
  out1: y

DEPENDENCIES
  out0 <- {in0}
  out1 <- {in0,in1}
```

Generate the equivalent information from old Golden Gate and compare.

Do not proceed until these match.

---

# 10. Milestone 6 — implement single-clock FAME-1

Only now port `FAMETransform`.

Begin with **single-clock only**.

Reject multiple clocks with a clear diagnostic initially.

The existing transform performs approximately:

```text
1. Add hostClock.
2. Add hostReset.
3. Add targetCycleFinishing.
4. Replace target IO with decoupled channel interfaces.
5. Create fired state for each channel.
6. Compute input readiness.
7. Compute output validity.
8. Determine when all required tokens have fired.
9. Gate target clock.
10. Replace target clock references.
11. Rewire FAME top.
```

Current `FAMETransform.scala` implements these steps directly, including channel metadata, port replacement, per-channel fired registers, finishing rules, target clock buffers, and top-level rewiring.

## Channel interface

For compatibility, initially reproduce the same external interface shape rather than inventing new types.

Conceptually:

```text
Input target port:

input x

becomes:

input  x_sink_valid
output x_sink_ready
input  x_sink_bits
```

and output:

```text
output y_source_valid
input  y_source_ready
output y_source_bits
```

Whether CIRCT retains these as bundles temporarily or scalarizes them should be chosen to minimize pass-order complexity.

Do **not** introduce ESI yet.

ESI already has typed latency-insensitive `!esi.channel<T>` values and valid-ready channel wrapping, so it is worth evaluating later, but ESI's own documentation still warns that the dialect is evolving.

## Channel state

For each data channel create:

```text
fired : host-clocked register
```

The semantics must match the Scala implementation:

```text
input ready =
    targetCycleFinishing && !fired

output valid =
    required_input_valids && !fired
```

while the fired flag ensures a token is transferred only once during one target cycle.

## Cycle completion

Build:

```text
allFiredOrFiring
```

and derive:

```text
targetCycleFinishing
```

Only then generate target clock advancement.

### First test design

Use something smaller than Rocket:

```verilog
always @(posedge clock) begin
    counter <= counter + input;
end

assign output = counter;
```

Apply arbitrary backpressure and arbitrary delays to input tokens.

Verify:

```text
reference target state after target-cycle N
==
FAME-1 state after committed target-cycle N
```

for thousands of randomized schedules.

---

# 11. Milestone 7 — build an independent FAME semantic testbench

Do not depend only on FireSim integration tests.

Create a host-side randomized tester.

For an original RTL design and transformed model:

```text
Reference:
  target cycle 0
  target cycle 1
  target cycle 2
  ...

FAME:
  host 0  stall
  host 1  accept input A
  host 2  output blocked
  host 3  accept input B
  host 4  target advances
  ...
```

Randomize:

```text
input token arrival
output ready
clock token arrival
reset duration
```

Record:

```text
target-cycle index
architectural state
output tokens
```

Compare by **target cycle**, not host cycle.

The core invariant should be:

\[
S_\mathrm{RTL}(n) = S_\mathrm{FAME}(n)
\]

for every completed target cycle \(n\).

And:

\[
T_\mathrm{RTL,out} = T_\mathrm{FAME,out}
\]

for every output token sequence.

---

# 12. Milestone 8 — multi-clock FAME-1

Do this only after single-clock FAME-1 passes.

Current FireSim represents clock-channel metadata with rational clock descriptions and per-clock MFMR values. The current API describes MFMR as the minimum host-cycle spacing between clock edges, used for timing relaxation.

Create:

```cpp
struct RationalClockInfo {
  std::string name;
  uint64_t multiplier;
  uint64_t divisor;
  unsigned mfmr;
};
```

Port:

```text
clock channel recognition
clock token consumption
per-clock enable state
per-clock input enable
per-clock output enable
host-clock gating
target-clock substitution
```

The Scala implementation currently creates a clock buffer for each target clock, stores the current clock-token state in host-clocked enable registers, and generates target clocks from the host clock plus `targetCycleFinishing`.

### Constraint generation

Do **not** emit XDC strings directly from FAME-1.

Instead create metadata:

```cpp
struct GGGeneratedClockConstraint {
  hw::InnerRefAttr clockBufferOutput;
  std::string name;
  unsigned mfmr;
};
```

Later lower it through:

```text
GG constraint metadata
       ↓
Xilinx constraint emitter
       ↓
firesim.xdc
```

Reproduce current semantics:

```text
create_generated_clock
set_multicycle_path <MFMR> -setup
set_multicycle_path <MFMR-1> -hold
```

but keep FPGA-specific strings outside FAME logic.

---

# 13. Milestone 9 — port the immediate FAME-1 neighborhood

At this point CIRCT can transform:

```text
post-infer-model-ports
     ↓
FAME-1
```

Now widen the CIRCT boundary one pass at a time, backwards.

Recommended order:

```text
InferModelPorts
      ↓
ChannelExcision
      ↓
FindDefaultClocks
      ↓
FAMEDefaults
      ↓
PromotePassthroughConnections
      ↓
ExtractModel
```

For every pass:

```text
1. Feed it Scala-produced input.
2. Run CIRCT pass.
3. Compare resulting semantics/annotations.
4. Replace that Scala pass in hybrid pipeline.
5. Run integration tests.
6. Only then port the preceding pass.
```

This creates a continuously working compiler:

```text
Scala front half
      |
CIRCT growing middle/back half
```

instead of a branch that does not run for months.

---

# 14. Milestone 10 — `ChannelExcision`

This transformation should convert inter-model connectivity into explicit channel-facing model ports.

Conceptually:

```text
Before:

Model A -------- Model B
```

becomes:

```text
Model A --> top-level channel source

top-level channel sink --> Model B
```

Golden Gate's published compiler description explicitly identifies `ChannelExcision` as the boundary where inter-model connectivity is removed and replaced by top-level IO; `InferModelPorts` then determines model-port/channel relationships.

Implement this using:

```text
InstanceGraph
symbol tables
port mutation utilities
annotation transfer
```

Avoid string-based hierarchy reconstruction.

---

# 15. Milestone 11 — `InferModelPorts`

This should produce a canonical mapping:

```text
model
  channel C0
      payload ports {a,b,c}
      direction source
      clock CLK0

  channel C1
      payload ports {x}
      direction sink
      clock CLK1
```

Do not make FAME-1 rediscover this.

Store it as either:

```text
A. FIRRTL annotations

or

B. CIRCT-native attributes
```

During the compatibility phase prefer annotations because you can compare directly with the Scala output.

Later convert to native Golden Gate attributes if desired.

---

# 16. Milestone 12 — `FAMEDefaults` and model graph construction

Implement default channel creation for inter-model connectivity not explicitly grouped by a bridge/model annotation.

The goal is to construct a complete graph:

```text
GGModelGraph {
    models
    channels
    source/sink ownership
    clock domain
    fanout
    loopbacks
}
```

Add:

```bash
goldengate-circt --print-model-graph
```

Example output:

```text
MODEL hub
  INPUT  UART_out
  OUTPUT tile0_req
  OUTPUT tile1_req

MODEL tile0
  INPUT  tile0_req
  OUTPUT tile0_resp

CHANNEL tile0_req
  hub -> tile0
  clock = base_clock
```

This textual model graph should become one of the project's most useful debugging tools.

---

# 17. Milestone 13 — model extraction

Port `ExtractModel`.

Do not start with FireAxe.

Handle ordinary FAME optimization candidates first.

Use `InstanceGraph` to:

```text
identify labeled model instances
promote/extract their interfaces
maintain unique instance identity
retain required module definitions
update channel annotations
```

The resulting hierarchy should match the simulator star topology:

```text
               bridge
                  |
                  v
model <-------- hub --------> model
                  |
                  v
               bridge
```

Golden Gate's documented target-transformation process intentionally converts hierarchy into this eventual simulator topology.

---

# 18. Milestone 14 — bridge extraction / FAME-2 infrastructure

Treat “FAME-2” primarily as **abstract model substitution infrastructure**, not as a single mechanical pass analogous to FAME-1.

FireSim's conceptual model distinguishes:

```text
cycle-exact models
abstract models

CPU-hosted models
FPGA-hosted models
```

and Target-to-Host Bridges introduce these alternate implementations into the target graph.

Port:

```text
BridgeExtraction
BridgeIO annotations
bridge channel creation
bridge constructor argument preservation
bridge clock metadata
bridge instance identity
```

### Keep bridge elaboration in Scala initially

Define a clean boundary:

```text
CIRCT Golden Gate
       |
       | bridge manifest
       v
Scala/Chisel bridge elaborator
       |
       | FIRRTL
       v
CIRCT
```

Example manifest:

```json
{
  "bridges": [
    {
      "id": "uart0",
      "class": "UARTBridgeModule",
      "constructor": {
        "...": "..."
      },
      "clockDomain": "base"
    }
  ]
}
```

The manifest must contain everything required for deterministic bridge RTL elaboration.

Do not pass arbitrary JVM object pointers across the boundary.

---

# 19. Milestone 15 — AutoCounter / Print / Assertion / Trigger synthesis

Once bridge infrastructure exists, port these transformations one at a time:

```text
AutoCounterTransform
AssertionSynthesis
PrintSynthesis
TriggerWiring
GlobalResetConditionWiring
```

These should largely become:

```text
find target operations
       ↓
create explicit channel/bridge representation
       ↓
move relevant metadata
       ↓
remove or replace original operation
```

Do not attempt to port these simultaneously.

Each feature already has small FireSim integration tests.

Current FireSim's integration-test flow is specifically:

```text
elaborate small Chisel design
compile through Golden Gate
build metasimulator
run
post-process result
```

and the documentation recommends targeted tests such as `GCDF1Test`.

Use this infrastructure heavily.

---

# 20. Milestone 16 — FAME-5 analysis

Only start FAME-5 after FAME-1 and bridge-containing Rocket simulations work.

Current FireSim still contains:

```text
LabelMultiThreadedInstances
MultiThreadFAME5Models
ImplementThreadedMems
FAME5Info
ThreadedMem
MuxingMultiThreader
...
```

in its public Golden Gate API.

`MultiThreadFAME5Models` remains a transform in current FireSim.

The FAME-5 compiler must determine:

```text
which model instances are equivalent/threadable
which state belongs to an instance
which combinational logic can be shared
which memories need thread expansion
how the active thread ID is scheduled
how channel IO is muxed/demuxed
```

Create an explicit analysis:

```cpp
struct FAME5Group {
  FModuleOp modelType;
  SmallVector<InstancePath> instances;
  unsigned numThreads;

  SmallVector<Operation *> stateOps;
  SmallVector<firrtl::MemOp> memories;
};
```

Validate the grouping separately before modifying IR.

---

# 21. Milestone 17 — FAME-5 register/state threading

For each threaded model:

```text
original:

reg state : UInt<W>
```

conceptually becomes:

```text
state[NUM_THREADS]

read_state =
    state[currentThread]

on target advance:
    state[currentThread] = nextState
```

Preserve:

```text
reset behavior
initialization
enable semantics
clock semantics
widths
annotations
debug names where practical
```

Start only with ordinary registers.

Reject models with unsupported state types initially.

Create tiny tests:

```text
2 copies of counter
4 copies of accumulator
2 copies of FSM
```

Compare them to physically replicated reference models.

---

# 22. Milestone 18 — FAME-5 memory threading

Handle memories separately.

Conceptually:

```text
logical:

thread0 : mem[DEPTH]
thread1 : mem[DEPTH]
...
```

may become:

```text
physical:

mem[NUM_THREADS * DEPTH]
```

with:

```text
physical_address =
    concat(threadID, logicalAddress)
```

where legal.

But do **not** assume concatenation is always valid.

Check:

```text
non-power-of-two depth
masking
read latency
write latency
read-under-write behavior
read/write ports
multiple clocks
memory macro mapping
```

CIRCT's FIRRTL memory representation records depth, latency, masks, read-under-write behavior, etc., so preserve these semantics explicitly.

Keep RAM-model emission as a separate later lowering.

---

# 23. Milestone 19 — FAME-5 IO scheduling

Implement the active-thread scheduler and channel muxing.

Conceptually:

```text
                         +------------------+
thread 0 channels ------>|                  |
thread 1 channels ------>| shared FAME model|---->
thread 2 channels ------>|                  |
                         +------------------+
                                  ^
                                  |
                            currentThread
```

The scheduler must ensure that:

```text
thread N consumes only thread N's input token
thread N modifies only thread N's state
thread N emits only thread N's output token
```

Use explicit test instrumentation to detect cross-thread state contamination.

Randomly stall different threads independently.

---

# 24. Milestone 20 — decide whether to introduce a Golden Gate dialect

Only after the FIRRTL implementation achieves functional parity should the team evaluate a `gg` dialect.

A likely useful representation is:

```mlir
gg.model @tile {
  ...
}

gg.channel @tile_req
    source @hub
    sink @tile
    clock @base

gg.bridge @uart0 {
    implementation = "UARTBridge"
}

gg.thread_group @tiles {
    count = 4
}
```

Potential types:

```mlir
!gg.channel<i64>
!gg.clock_token
!gg.model_token<...>
```

Potential operations:

```text
gg.model
gg.channel
gg.bridge
gg.clock_domain
gg.thread_group
gg.constraint
```

However, consider using or interoperating with CIRCT ESI rather than duplicating generic channel semantics.

ESI already models typed, point-to-point latency-insensitive channels and valid/ready signaling.

Likely architecture:

```text
GG dialect:
    simulator-specific semantics
    target-cycle semantics
    bridge identity
    clock tokens
    model threading

ESI:
    generic transport/channel representation where applicable
```

Do not force Golden Gate's target-time semantics into ESI if they do not fit.

---

# 25. Milestone 21 — move FAME to the Golden Gate dialect, if adopted

If `gg` proves worthwhile:

```text
FIRRTL
    |
gg-extract-model-graph
    |
GG
    |
gg-fame1
    |
gg-thread-models
    |
gg-lower-models
    |
HW + Seq + Comb + ESI
    |
SV
```

This is preferable to repeatedly converting:

```text
high FIRRTL
→ low FIRRTL
→ FAME-generated high-ish FIRRTL
→ low FIRRTL
```

The old FAME transform currently declares `LowForm` input and `HighForm` output, reflecting the awkward fact that it analyzes lowered RTL but creates structured channel interfaces again.

A Golden Gate dialect removes that historical constraint.

---

# 26. Milestone 22 — CIRCT pass pipeline

The final compiler should expose an explicit pipeline.

Example:

```text
goldengate-circt
|
|-- FIRRTL import
|
|-- FIRRTL normalization
|     width/reset inference as required
|     canonicalization
|     expand whens at appropriate point
|
|-- gg-import-annotations
|
|-- gg-bridge-extraction
|-- gg-debug-synthesis
|-- gg-clock-analysis
|-- gg-wrap-top
|-- gg-label-threaded-models
|-- gg-extract-models
|-- gg-promote-passthrough
|-- gg-fame-defaults
|-- gg-find-default-clocks
|-- gg-channel-excision
|-- gg-infer-model-ports
|
|-- gg-fame1
|-- gg-fame5
|
|-- gg-memory-lowering
|-- gg-simulation-mapping
|-- gg-host-specialization
|
|-- FIRRTL -> HW
|-- HW/Seq/Comb legalization
|-- ExportVerilog
|
|-- collateral emitters
```

Do not blindly reproduce:

```text
HighFirrtlToMiddleFirrtl
MiddleFirrtlToLowFirrtl
ResolveKinds
```

Those names and phases are SFC concepts.

Determine the actual invariant needed by each new CIRCT pass.

For example:

```text
FAME combinational analysis requires:
    no unresolved `when` last-connect semantics

therefore require:
    firrtl-expand-whens
```

CIRCT's `firrtl-expand-whens` explicitly resolves last-connect semantics and removes `when` operations.

Similarly, decide deliberately whether FAME operates before or after `LowerTypes`.

CIRCT's `LowerTypes` expands aggregate types and is already instance-graph aware.

---

# 27. Pass invariant documentation

Every Golden Gate pass must have a header comment containing:

```text
Required input invariants:
  - ...
  - ...

Annotations consumed:
  - ...

Annotations produced:
  - ...

IR mutations:
  - ...

Analyses required:
  - ...

Analyses preserved:
  - ...

Output invariants:
  - ...
```

Example:

```cpp
/// GGFAME1Pass
///
/// Requires:
///   * channel ports have been inferred
///   * no unresolved FIRRTL when semantics
///   * each data channel has exactly one clock domain
///
/// Consumes:
///   * FAMETransformAnnotation
///   * FAMEChannelPortsAnnotation
///
/// Produces:
///   * GG clock-constraint metadata
///
/// Mutates:
///   * transformed module ports
///   * target clocks
///   * top-level channel connectivity
```

This is mandatory.

It prevents recreating the implicit compiler-state assumptions that make old FIRRTL pipelines difficult to maintain.

---

# 28. Golden test hierarchy

Create four test layers.

## Layer A — MLIR pass tests

Use `FileCheck`.

Example:

```text
test/Transforms/FAME1/simple.mlir
test/Transforms/FAME1/dependencies.mlir
test/Transforms/FAME1/backpressure.mlir
test/Transforms/FAME1/multiclock.mlir
test/Transforms/FAME5/registers.mlir
test/Transforms/FAME5/memory.mlir
```

Check IR structure, not generated formatting.

## Layer B — differential compiler tests

Input:

```text
same FIRRTL + annotations
```

Run:

```text
Scala GG
CIRCT GG
```

Extract normalized structural descriptions:

```text
models
channels
clock domains
channel dependencies
bridge metadata
thread groups
```

Compare them.

## Layer C — Verilator behavioral tests

Compile both generated simulators.

Apply the same token stream and compare:

```text
target-cycle count
output tokens
bridge-visible behavior
termination
```

## Layer D — existing FireSim integration tests

Reuse the current FireSim integration system. Current documentation specifically lists:

```bash
make testOnly \
  TARGET_PROJECT=midasexamples \
  SCALA_TEST=firesim.midasexamples.GCDF1Test
```

as the preferred targeted integration mechanism.

---

# 29. Required test progression

Do not jump directly to Rocket.

Use exactly this progression:

```text
1. combinational add
2. one-register counter
3. input-dependent accumulator
4. two independent input channels
5. output depending on one of several inputs
6. multiple outputs with different dependency sets
7. output backpressure
8. input starvation
9. nested modules
10. multiply instantiated module
11. simple memory
12. multiport memory
13. one bridge
14. two bridges
15. rational clock bridge
16. two target clocks
17. FAME-5 x2 counter
18. FAME-5 x4 stateful design
19. FAME-5 memory
20. GCD FireSim test
21. Rocket
22. multicore Rocket
23. BOOM
24. representative FireAxe target
```

A stage may only advance when all earlier tests pass.

---

# 30. Verification criteria for FAME-1

Declare FAME-1 functionally complete only when all of these hold:

```text
[ ] same target-cycle state trajectory

[ ] same output token sequence

[ ] arbitrary input latency tolerated

[ ] arbitrary output backpressure tolerated

[ ] no token duplicated

[ ] no token dropped

[ ] no target-cycle skipped

[ ] reset semantics equivalent

[ ] combinational channel dependency semantics equivalent

[ ] clock gating equivalent

[ ] multiclock token scheduling equivalent

[ ] bridge channels unaffected

[ ] existing GCDF1 test passes

[ ] Rocket boots in metasimulation

[ ] required Rocket bare-metal suites pass through the active Chia harness

[ ] identical Rocket ELF artifacts match SFC/CIRCT regression signatures,
    including direct UART output
```

Do not use generated RTL diff as the correctness criterion.

CIRCT will legitimately produce different RTL.

---

# 31. Verification criteria for FAME-5

Declare FAME-5 complete only when:

```text
[ ] N threaded instances match N ordinary independent FAME-1 models

[ ] register state never crosses threads

[ ] memory state never crosses threads

[ ] reset initializes every thread correctly

[ ] thread scheduling does not alter target-visible behavior

[ ] independent channel stalls work correctly

[ ] bridge-visible behavior is identical

[ ] thread ID cannot affect combinational semantics except state/IO selection

[ ] memory masks and latencies are preserved

[ ] Rocket multicore threaded configuration behaves identically
```

---

# 32. FAME-2 / abstract-model verification

For every abstract model/bridge:

```text
target-side bridge annotation
         ↓
bridge extraction
         ↓
bridge manifest
         ↓
BridgeModule elaboration
         ↓
simulator instance
```

Verify:

```text
constructor args identical
channel widths identical
channel direction identical
clock domain identical
bridge index/identity stable
generated driver metadata identical
runtime plusargs continue to work
```

Because abstraction may intentionally not be cycle-exact with the original implementation RTL, do not require internal RTL equivalence.

Require its documented model contract.

---

# 33. Integration strategy with existing FireSim

Add a build selection variable:

```text
GOLDENGATE_COMPILER=scala
GOLDENGATE_COMPILER=circt
```

Default to:

```text
scala
```

until CIRCT reaches parity.

Then allow:

```bash
make ... GOLDENGATE_COMPILER=circt
```

Both paths must consume the same:

```text
FIRRTL_FILE
ANNO_FILE
target configuration
```

Both paths should emit the same **logical classes of outputs**:

```text
simulator RTL
bridge metadata
driver headers
memory collateral
XDC
simulation mapping
```

They do not need identical filenames internally if Makefile adaptation is clean.

---

# 34. Collateral manifest

Do not have dozens of passes independently write random output files.

Define a compiler result manifest:

```json
{
  "rtl": [...],
  "bridges": "...",
  "memoryModels": "...",
  "constraints": "...",
  "driverHeaders": [...],
  "simulationMapping": "...",
  "compilerVersion": {
    "firesim": "...",
    "circt": "...",
    "goldengate": "..."
  }
}
```

Make downstream FireSim consume this manifest.

This will substantially simplify compiler/backend boundaries.

---

# 35. Diagnostics

Golden Gate CIRCT errors must be actionable.

Bad:

```text
assert(srcClockPorts.size() == 1)
```

Good:

```text
error: channel 'tile0_mem_req' has 2 source clock domains
note: expected exactly one clock domain for a FAME model channel
note: candidate clocks:
      ~Top|Hub>clock0
      ~Top|Hub>clock1
```

Prefer MLIR diagnostics attached to relevant operations.

Remove legacy `???`, assertions for user input, and generic runtime exceptions.

---

# 36. Debugging commands the compiler should support

Implement eventually:

```text
--print-annotations
--print-instance-graph
--print-model-graph
--print-channel-dependencies
--print-clock-domains
--print-fame5-groups
--dump-ir-before=<pass>
--dump-ir-after=<pass>
--verify-each
```

For development also support normal:

```text
--mlir-print-ir-before-all
--mlir-print-ir-after-all
--mlir-print-ir-after-failure
```

A Golden Gate compiler should be inspectable as a compiler.

---

# 37. Performance work comes after correctness

Initially build with:

```text
RelWithDebInfo
LLVM assertions ON
MLIR verify-each
```

Do not optimize compile time.

Once Rocket works, measure:

```text
Scala GG wall time
CIRCT GG wall time

peak RSS
IR operation counts
time/pass
```

Then profile the expensive analyses.

Potential wins include:

```text
SSA connectivity traversal
cached InstanceGraph
parallel per-module FAME transformations
parallel channel analysis
less repeated FIRRTL lowering
```

---

# 38. Do not port legacy infrastructure unnecessarily

Whenever old code invokes a generic FIRRTL transform, ask:

```text
Does CIRCT already implement the same semantic operation?
```

Examples likely handled by CIRCT:

```text
ExpandWhens
LowerTypes
constant folding/canonicalization
dead-code elimination
reset inference/lowering
instance graph
module inlining
memory lowering
FIRRTL → HW
Verilog emission
```

Use CIRCT's existing passes when semantics match.

Only Golden Gate-specific logic should be reimplemented.

---

# 39. Recommended project stages / PR sequence

Keep every change reviewable.

Suggested series:

```text
TAG 1
CIRCT build integration + empty goldengate-circt tool

TAG 2
FIRRTL + FireSim annotation ingestion

TAG 3
Golden Gate annotation utilities + stable target identity

TAG 4
instance/model/channel/clock analyses

TAG 5
single-clock FAME-1

TAG 6
FAME randomized semantic testbench

TAG 7
multiclock FAME-1 + constraint metadata

TAG 8
InferModelPorts

TAG 9
ChannelExcision

TAG 10
FindDefaultClocks + FAMEDefaults

TAG 11
ExtractModel + passthrough promotion

TAG 12
BridgeExtraction + bridge manifest

TAG 13
debug/AutoCounter transforms

TAG 14
FAME-5 grouping and register threading

TAG 15
FAME-5 memory threading

TAG 16
FAME-5 scheduling

TAG 17
RAM models + SimulationMapping

TAG 18
host specialization / complete metasim flow

TAG 19
Rocket/BOOM parity

TAG 20
optional Golden Gate dialect prototype

TAG 21+
FireAxe migration
```

Avoid a single commit. You can incrementally commit to the repository, and use tags to label each milestone.

---

# 40. What should be considered “version 1 complete”

Version 1 does **not** need to remove Scala from FireSim.

It is complete when:

```text
target Chisel elaboration
      |
      v
FIRRTL + annotations
      |
      v
CIRCT Golden Gate
      |
      +--> invokes/consumes Chisel-generated bridge modules as needed
      |
      v
simulator SystemVerilog
      |
      v
existing FireSim metasim / FPGA infrastructure
```

and the CIRCT path successfully handles representative:

```text
FAME-1
abstract models / bridges
FAME-5
multiclock
memories
AutoCounter
printf/assertions
Rocket
BOOM
```

At that point the major dependency on the **Scala FIRRTL compiler** is gone.

---

# 41. What should explicitly remain out of scope until later

Do not initially attempt:

```text
removing Chisel
rewriting every BridgeModule in C++
rewriting FireSim runtime
rewriting FPGA shell logic
replacing Vivado
rewriting FireAxe simultaneously
upstreaming Golden Gate into CIRCT
changing FAME semantics
optimizing generated RTL beyond parity
using ESI everywhere
inventing a new simulator runtime
```

Those can follow after parity.

---

# 42. Highest-risk technical issues

Prioritize investigation of these risks.

## Risk A — annotation identity

Highest risk.

SFC `RenameMap` behavior is deeply embedded in old custom transforms. CIRCT requires more explicit annotation handling.

Build this infrastructure first.

## Risk B — exact combinational channel dependencies

A subtle mistake can introduce:

```text
deadlock
unnecessary stalls
incorrect early output firing
```

Test aggressively.

## Risk C — multiclock scheduling

FAME semantics depend on clock-token scheduling rather than ordinary generated clocks.

Do not “simplify” this into standard clock division without proving equivalence.

## Risk D — FIRRTL pass ordering

FAME relies on particular normalization properties.

Document invariants rather than trying to reproduce SFC's named forms.

## Risk E — bridge generator boundary

BridgeModules are Chisel generators.

Preserve a clean JVM elaboration boundary instead of trying to eliminate them.

## Risk F — FAME-5 memories

Memory semantics and host mapping are considerably trickier than ordinary registers.

Treat this as a separate milestone.

---

# 43. First concrete task for the implementation agent

Do **not** begin by writing `FAME1.cpp`.

The first task is:

```text
1. Check out current FireSim.

2. Run GCDF1Test with existing Scala Golden Gate.

3. Save every FIRRTL/annotation dump around:
      BridgeExtraction
      WrapTop
      ExtractModel
      ChannelExcision
      InferModelPorts
      FAMETransform
      MultiThreadFAME5Models

4. Build CIRCT pinned to firtool-1.160.0.

5. Parse:
      post-infer-model-ports.fir
      post-infer-model-ports.json

6. Dump CIRCT FIRRTL MLIR.

7. Enumerate:
      modules
      module instances
      ports
      annotations
      FAME channels
      model annotations
      clock annotations

8. Produce a machine-readable comparison report.

9. Commit the fixtures and report.

10. Only then implement channel analysis.
```

The first milestone should therefore produce **no transformed RTL**.

It should prove that the old compiler's FAME-1 input is completely understood inside CIRCT.

---

# 44. Second concrete task

Implement:

```text
GGHierarchyAnalysis
GGAnnotationAnalysis
GGClockAnalysis
GGChannelAnalysis
```

Add:

```bash
goldengate-circt \
    post-infer-model-ports.fir \
    --annotation-file post-infer-model-ports.json \
    --print-model-graph \
    --print-channel-dependencies
```

Make its output agree with the corresponding Scala analysis.

Only when this works should the agent begin IR mutation.

---

# 45. Third concrete task

Implement single-clock FAME-1 for one module.

Do not initially support:

```text
multiple transformed modules
multiclock
FAME-5
bridges
RAM optimization
```

Support:

```text
one transformed model
one target clock
N input channels
N output channels
arbitrary combinational dependencies
register state
host backpressure
```

Then connect it to the randomized semantic testbench.

This will validate the core FAME mechanism independently of the rest of FireSim.

---

# 46. Architectural end state

A successful migration should ultimately look like:

```text
                            Target Generator
                                  |
                            FIRRTL + Annos
                                  |
                                  v
                    +----------------------------+
                    | CIRCT FIRRTL front end     |
                    +-------------+--------------+
                                  |
                       target transformation
                                  |
                    +-------------v--------------+
                    | Golden Gate model graph    |
                    |                            |
                    | models                     |
                    | channels                   |
                    | clocks                     |
                    | bridges                    |
                    +-------------+--------------+
                                  |
             +--------------------+--------------------+
             |                    |                    |
             v                    v                    v
          FAME-1           abstract models           FAME-5
             |                    |                    |
             +--------------------+--------------------+
                                  |
                                  v
                    simulator implementation IR
                                  |
                    +-------------+--------------+
                    | CIRCT standard lowering    |
                    | FIRRTL/HW/Seq/Comb/SV      |
                    +-------------+--------------+
                                  |
                  +---------------+---------------+
                  |                               |
                  v                               v
             SystemVerilog                    metadata
                  |                               |
                  v                               v
              Vivado /                         runtime
             Verilator                         drivers
```

The architectural boundary to preserve is:

```text
Golden Gate decides WHAT simulator hardware exists.

CIRCT decides HOW ordinary RTL is lowered and emitted.
```

That separation is the main design principle of this port.

---

# 47. Final success criteria

The port is successful when all of the following are true:

```text
[ ] no Golden Gate transform depends on Scala FIRRTL IR classes

[ ] CIRCT consumes the normal FireSim FIRRTL + annotation handoff

[ ] FAME-1 matches target-cycle semantics under arbitrary stalls

[ ] multi-clock targets preserve rational-clock token semantics

[ ] abstract models / bridges work unchanged from a user's perspective

[ ] FAME-5 gives identical independent target state for every thread

[ ] memory threading preserves FIRRTL memory semantics

[ ] AutoCounter / printf / assertion features work

[ ] standard FireSim driver generation continues to work

[ ] GCDF1 and related integration tests pass

[ ] Rocket boots successfully

[ ] BOOM boots successfully

[ ] FPGA builds complete

[ ] generated simulators are deterministic

[ ] old Scala Golden Gate and new CIRCT Golden Gate can coexist during migration

[ ] compiler passes have explicit invariants and annotation contracts

[ ] ordinary FIRRTL/SV lowering is delegated to CIRCT
```

Do not delete the old implementation until the new compiler passes this matrix.

---

# 48. Sources the implementation agent should keep open

Current FireSim compiler pipeline:

`sim/midas/src/main/scala/midas/passes/MidasTransforms.scala`. It shows the real Golden Gate pass ordering and establishes where FAME-1 and FAME-5 sit today.

Current FAME-1 implementation:

`sim/midas/src/main/scala/midas/passes/fame/FAMETransform.scala`. It is the executable semantic reference for target clock substitution, channel state, valid/ready generation, combinational dependencies, top rewiring, and XDC metadata.

FireSim's LI-BDN description should be treated as the semantic specification for models/channels/tokens.

FireSim's bridge documentation is the specification for abstract/CPU-hosted model integration and the second Chisel elaboration stage.

The Golden Gate dissertation remains useful for understanding why `BridgeExtraction → WrapTop → ExtractModel → ChannelExcision → InferModelPorts → FAMETransform` is structured this way.

For CIRCT implementation details use the current FIRRTL dialect, annotation, pass, `LowerTypes`, and `InstanceGraph` documentation rather than relying on historical firtool behavior.

The implementation agent should consider the existing Scala code the **reference implementation**, FireSim's LI-BDN/FAME descriptions the **semantic specification**, and CIRCT/MLIR the **new implementation substrate**.
