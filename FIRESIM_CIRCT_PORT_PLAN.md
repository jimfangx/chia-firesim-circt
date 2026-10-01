# FireSim Golden Gate Scala-FIRRTL → CIRCT Port

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
and `br-base-uniform.json`.  Advance to `buildbitstream` for the U250 only
after this Verilator gate passes.  The harness must retain complete stdout,
stderr, and the FireSim manager log path for this gate.

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
