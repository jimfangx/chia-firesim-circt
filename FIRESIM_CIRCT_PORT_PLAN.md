# Parameterized native control routing — November 11, 2026

The native control path now derives geometry from the complete allocated widget
catalog, matching the legacy Scala router's parameterization. For N normal
regions, one-hot routes have N bits and encoded targets have ceil(log2(N+1))
bits, including the error endpoint at index N. The write-route queue has N
entries. Read/write trackers retain their independent 64 transaction slots and
12-bit tags; route data widths and retirement-port counts grow with N.

Removed the 63-region checks consistently from allocation, decoder, widget
binding, AR/AW/W dispatch, read/write tracking, both response arbiters and late
widget bindings. TracerV and NIC bank discovery accepts larger contiguous
constructor-index catalogs. Actual address-space overflow, overlapping regions,
malformed names, missing banks, duplicate identities and incompatible port
schemas still reject. FIRRTL width/i32-index representability is checked before
narrowing catalog sizes. Existing supported control-bus profiles (25/32-bit
addresses and 12-bit IDs) are unchanged; this work does not claim arbitrary AXI
parameter support or unrestricted whole-design SFC equivalence.

Validation:

- Eleven focused CTest executables pass, including read/write tracker collision,
  retirement and reset stress checks at 64, 65 and 128 regions.
- Composed generated-IR execution passes for 1, 63, 64, 65, 127, 128 and 129
  regions, exercising high one-hot bits, queue fill/stall/drain/wrap/reset,
  independent AW/W dispatch, AR tracker backpressure and high-index arbitration.
- Mixed production catalog fixtures with 64 and 65 NICs plus the same number of
  TracerV banks pass allocation (138 and 140 total regions). Existing malformed
  catalog and actual address-capacity rejection cases are retained.
- Full composed control IR for the 1-region and 63-region cases is byte-identical
  before/after this change. The original implementation rejects the 64-region
  negative control.
- The independently generated 64-MegaBoom/FAME5 checkpoint passes allocation of
  74 regions including 64 TracerV widgets, then decoder, write queue, AW/W
  dispatch, actual widget binding, AR dispatch/tracker, R/B arbiters, AW tracker,
  SimulationMaster bank attachment, MCR mapping and final binding. IR verification
  passes after every stage. This checkpoint replay does not establish final RTL,
  Vivado, FPGA workload or Jasper success.

The count parameterization, earlier clock-alias proof, shared-source fanout
repair and lookup improvements use catalog/IR connectivity rather than design
names or tile counts. No BOOM-specific or 64-core special case was added.
Evidence: `/scratch/jfx/control-region-audit-20261111/` (production replay log,
geometry tests and before/after IR comparison).

# 64-tile MegaBoomV3 / FAME5 production integration audit — November 11, 2026

The supplied 64-tile config (trace enabled before cloning), tested with
`WithModelMultiThreading_WithMultiCycleRamModels_FRFCFS16GBQuadRankLLC4MB_BaseF2Config`,
initially stopped before RTL emission at the control catalog's 63-region limit
(now repaired by the parameterization above).
`ControlAddressDecode.cpp` rejected TracerV bank index 63; the read/write routing
interfaces also assumed 1–63 regions. Sixty-four TracerV widgets exceeded
that limit before other peripherals are counted. All 64 configuration/MCR
pairs exist with contiguous indices 0–63; the initial failure was a capacity
rejection, not a missing bank. The follow-up repairs all affected stages and
the matching NIC bank-index cap while retaining real address-space bounds.

Completed in this audit:

- Reproduced the reported clock failure after exactly 6,148 passthrough promotions.
  All 261 hub clock ports structurally resolve to one physical clock root.
  Clock inference now proves hierarchical/feedback aliases, accepts repeated
  identical clock drivers and selects a component-local alias rooted in the
  declared hub domain. Genuine clock ambiguity remains rejected; existing
  unambiguous clock names and the four-clock SFC SRAM fixture are preserved.
- Fixed redundant model-source fanout connections in ChannelExcision, allowing
  InferModelPorts to complete without weakening its uniqueness checks.
- Constructed and verified the complete FAME graph: four definitions, 193 logical
  model instances, 28,834 data endpoints, one clock domain and one physical
  threading group with 64 shared threads. Both selected RAM definitions were
  materialized. Host UART, TracerV, CPU streams, LoadMem and memory-controller
  integration reached control-address allocation.
- Removed three read-only/dormant-path costs: a repeated full connection scan,
  whole-port-list construction for single-port metadata, and debug-target
  transfers when no debug targets exist. Sixteen focused tests pass; twelve
  generated graph/RTL/annotation artifacts are byte-identical across four
  before/after fixture runs. A complete optimized 64-tile compile remains
  unverified; these audit times are not speed benchmarks.

Evidence and retained checkpoints:
`/scratch/jfx/boom64-native-audit-20261111/audit-result.json`.
The pre-allocation checkpoint is
`native-component/post-fame-fased-frfcfs-mmio-bank.mlir` under that directory.
Local elaboration required recorded clone-safe TraceIO/prototype-annotation
front-end overlays; those sources were restored after generating the audit
inputs. No Vivado, FPGA workload or Jasper run was launched for this audit.

# WithMultiCycleRamModels SFC parity repairs — November 11, 2026

The bounded SFC source/executable audit found and repaired these supported-case
gaps in the native async RAM path:

* Model promotion now reserves existing module port names and moves actual
  retained target selectors through every promoted instance context. Duplicate
  records keep their order; constructor/description/format strings stay opaque.
* Hierarchical selected-port `DontTouch` targets now retain their terminal leaf
  identity through aggregate lowering, FAME channelization, transport activation
  and RAM materialization. Invalid hierarchy edges and unsupported native-only
  symbol/attached-annotation relocation reject before deleting their identities.
* Shared input channels use per-command delivery state. A token is acknowledged
  only after all consumers have accepted it, including serialized commands with
  non-overlapping ready pulses. Host reset clears delivery state, not memory.
* Distinct read-response fields sharing an output channel use a complete valid/
  ready join. An incomplete packet cannot escape when read commands arrive at
  different times. Multiple producers of the same payload field still reject.

Portable generated-RTL regressions are registered as
`goldengate-multicycle-ram-sharing` and
`goldengate-multicycle-ram-response-join`. They compare real SFC references on
defined safe cases and use independent initialized-model-memory scoreboards for
skewed traffic, backpressure, repeated tokens and in-flight host resets. Negative
controls detect dropped/duplicated responses, premature input acknowledgements
and premature output packets. These bounded model tests do not zero target DRAM.
Both repaired extraction fixtures also match complete SFC annotation arrays
exactly at extraction and LowForm; the selected-RAM regression exercises the
entire retained-target path through native RAM materialization.

Do not reproduce the SFC bugs found by these probes: partial input sharing can
lose tokens, grouped output valid can expose stale fields, and the mutable-set
construction gives unstable logical-port-to-host-index ordering. Native command
order remains deterministic. Host arbitration/index timing and source memory
write collisions with undefined original behavior cannot establish a blanket
whole-design equivalence claim. SFC itself rejects combined read/write ports,
empty command vectors, non-UInt payloads and inconsistent port widths; these are
not missing native feature cases. No additional concrete SFC-defined-behavior
gap emerged from this bounded audit, which is not an exhaustive proof.

Exactly four matched enabled designs compile after these repairs. RTL logic,
bridge/model annotations and runtime collateral match the prior checked
candidates. Only source-location comments retained during RAM-instance cloning
change Rocket/MegaBoomV3 RTL bytes and their validated memory-origin binding
hashes. The two Shuttle outputs remain byte identical. Preserve the evidence in
`/scratch/jfx/mcram-parity-implementation-20261111/` and the executable SFC audit in
`/scratch/jfx/mcram-parity-audit-20261111/`. Full-top Jasper LEC/SEC and enabled
F2/Linux signoff remain open; the checked symbolic initial-memory relation and
existing full-top ACK/transport obligations below still apply.

# Configurable multi-cycle RAM models — November 11, 2026

`WithMultiCycleRamModels`, `MCRams`, the native
`--enable-multicycle-ram-models` flag and make's
`GOLDENGATE_CIRCT_MULTICYCLE_RAM_MODELS=enabled` now enable the optional selected
RAM flow. Default-off behavior is preserved. The production path uses the
generalized FAME graph, including other models and FAME5, then discovers and
materializes selected wrappers from typed memory-port annotations. Geometry and
port counts are inferred; no target-specific names, dimensions or selection
counts are encoded. Multiple replacements commit atomically.

Matched enabled SFC/native builds now exist for exactly Rocket, MegaBoomV3,
plain Shuttle and Saturn Shuttle. Rocket preserves `BaseF2Config`; the other
three preserve `WithAutoILA_FRFCFS16GBQuadRankLLC4MB_BaseF2Config`, with
`WithMultiCycleRamModels` prepended on both sides. Earlier enabled native
replays used `BaseF2Config` for all four and establish compilation only, not
matched SFC equivalence. Combined multiclock/AutoCounter/Print and four-thread
FAME5 representatives also compile. Rocket selects one RAM
definition, MegaBoomV3 two, and the two Shuttle representatives none. These are
observed test results, not compiler selection rules. The four-thread case
retains four shared logical threads. A fresh enabled Rocket SFC reference and
the generalized native output pass a parameterized RTL differential test over
2,032 transactions, 8,237 host cycles and 5,219 initialized response-data checks,
including masks, disabled writes and backpressure; a disabled-write mutant
fails. The optimized SFC model removes target-reset inputs, so that differential
does not establish target-reset coverage. Unwritten memory stays unspecified.

The model follows SFC's async register-file RAM contract; it does not introduce
a replacement model for arbitrary original latencies. Unsupported port ABIs
fail explicitly. Enabled F2 Linux/runtime and Jasper signoff remain pending.
Evidence, including earlier failed topology attempts, is retained under
`/scratch/jfx/multicycle-ram-models-20261111/`.

Full-design equivalence is a separate acceptance gate. Compare complete emitted
RTL, real driver headers, public interfaces, bridge/model channel contracts and
resolved clock paths for the four matched profiles. Preserve aggregate-instance
namespace rules and retarget only actual annotation selectors. Use immutable
feature-specific Jasper manifests for both full-top LEC and SEC; never silently
reuse a baseline proof. Complete signoff must include the selected RAMs and
all other observable design state. Shared symbolic initial-memory correspondence
requires checked identities and transition relations; neither independent
uninitialized memories nor a layout helper proves equivalence. Do not initialize
DRAM to make comparisons pass, or waive a defined behavioral mismatch.

Current full-design work and its staged proof bundles are retained under
`/scratch/jfx/mcram-full-equivalence-20261111/`. Staging, syntax checks and
RAM-model differential simulation are development evidence, not Jasper signoff.

The final enabled compiler passes static compatibility and actual typed
endpoint/clock-route checks for all four profiles: 320 channels, no naming
deltas. Complete RTL/driver syntax checks and both Jasper staging modes pass
for each. Rocket's 2R/1W 64-bit RAM and MegaBoomV3's 6R/4W 65-bit and 12R/6W
64-bit RAMs pass generated-RTL differential simulation, including defined
multiwriter collisions and subsequent reads; a highest-writer mutant fails.
These tests compare initialized model data only, not arbitrary live unwritten
memory. Native instance namespace handling now preserves zero-width deletion,
native symbols, valid unique names and final annotation identities. In a
default-disabled Rocket replay, only three internal divide/square-root instance
labels change; other RTL text and runtime collateral are unchanged, and memory
origin records differ only in their RTL binding hash. Earlier byte-identical
default-disabled results predate this namespace compatibility fix.

Strict SFC contract equality remains false for the existing native F2
explicit-ACK protocol extension; static compatibility does not waive its
behavioral proof. Full-top LEC/SEC also needs complete checked symbolic
initial-memory correspondence, which the current single read-buffer provenance
record does not provide. Extend logical-memory origins and both emitted storage
decoders, validate a total address/bit bijection, couple corresponding arbitrary
initial cells, and prove preservation under actual clocks, reset, masks and
collision rules. Retain the full boundary and cutpoint obligations. Do not
fabricate a relation certificate from names or assume helper equalities.

# Native CHIRRTL handoff — November 11, 2026

Production FireSim now consumes Chipyard's native `.fir` output directly through
CIRCT. The target makefrag no longer requests `--emit-legacy-sfc` or links the
FIRRTL2-emitted `.sfc.fir`. Chisel elaboration remains Scala. The importer removes
only a complete matching mirrored annotation array before GG passes, preserving
intentional duplicate annotations and distinct embedded/sidecar payloads.

Exactly four baseline replays passed: Rocket, MegaBoomV3, plain Shuttle and
Saturn Shuttle. Native and legacy-input RTL code is identical with comments
excluded; runtime headers and other collateral are byte identical. Memory-origin
records are identical, with updated binding hashes verified against the actual
emitted RTL (source comments differ). A fresh native-only Rocket elaboration
and full compilation passed without producing `.sfc.fir`; native-input assertion
compilation also passed with the same assertion runtime header. Annotation
handoff tests and a stale-symlink make regression pass. Evidence is under
`/scratch/jfx/native-chirrtl-input-20261111/`. Separate SFC golden tooling retains
explicit legacy emission for reference comparisons.

# Synthesized assertions — November 11, 2026

The opt-in `WithSynthAsserts`/`--enable-synth-asserts` native path now implements:
native assertion and legacy failing-stop export, hierarchy and instance
exclusions, clock-domain grouping, global-reset masking, queued host transport,
failure ID/message/cycle MMIO, resume/disable controls, and existing runtime
driver/header integration. Five native tests pass, including a 6,000-cycle
generated-RTL reference-model check and runtime-header compilation. Rocket
compiles with 650 assertions across two domains; the combined AutoCounter/Print
case compiles with 650 assertions in one domain. Disabled native RTL and runtime
collateral match the saved Rocket output byte for byte. Evidence is under
`/scratch/jfx/synth-assertions-20261111/`, including failed integration attempts.
These local checks are development evidence; require matched SFC
comparisons and enabled F2/runtime and Jasper verification before claiming
assertion feature signoff. Existing seven-case Linux passes predate this feature.

# Source distribution checkpoint — November 10, 2026

The integrated implementation passed all seven enabled F2 Linux/runtime/
poweroff/cleanup representatives together on November 9, plus four baseline
static comparisons. Source reproduction starts at [REPRODUCING.md](REPRODUCING.md).
Jasper LEC/SEC, enabled-feature SFC comparisons and updated compiler-speed
measurements remain separate signoff obligations. The older active-phase notes
below are retained history, not claims that those runs are still active.

# FireSim Golden Gate SFC → CIRCT port

## Active phase: integrate and verify all seven feature cases (2026-11-09)

The four NIC Linux/packet/poweroff/cleanup representatives passed together on
November 8, with four baseline static comparisons and all workers joined. Their
latest 28 source/test repairs are now integrated into the main workspace. The
configured compiler is pinned to `19dadd357e38cf9113cb3aad34f050a43f82d05b083ca58a1c0bf4ec901c4ac5`;
all 840 native source inventory hashes match that compiler's captured sources.
Preserve the prior NIC evidence and failed runs.

Complete the user's next two steps: finish main-workspace integration and obtain
fresh passing runtime evidence for all seven representatives on one final source
identity: FAME5, combined AutoCounter/Print, multiple clocks/models, normal NIC,
two NICs, compile-time loopback and per-widget runtime loopback. Run the dedicated
`--verify feature-linux-repair` mode. It checks exactly four baseline targets,
then all seven enabled F2 Linux cases, joins every worker/farm, and feeds a
failure into the next implementation turn. A source change invalidates earlier
iteration acceptance; require all seven passes together. A scoped Linux success
is not full port acceptance. Keep Jasper and compiler benchmarks disabled during
this work, and leave DRAM zeroing disabled. Keep unwritten memory unspecified
and checks of initialized payloads, token order, counters and packets strict.

Do not mutate inputs during verification. Reuse hardware only on exact matching
identity and real build receipts; do not reuse old runtime passes for the new
candidate. Use available cores within memory and shared Vivado locks. The main
implementation agent may use as many subagents as useful and joins them before
returning. If compiler sources change, build a new immutable compiler pin and
update both the active verification YAML and the main default configuration.

Read `/scratch/jfx/chia-integrated-feature-suite-20261109/` and
`automation/chia_firesim_circt/logs/feature-linux-repair-active.json` for evidence.

## NIC repair iteration 5: bring up each interface before packet bind (2026-11-08)

The latest four baseline syntax/static-semantic checks and the single normal
NIC Linux representative pass. The two-NIC run `linux-aa3d35078d186507` reaches
real Linux userspace, completes all 128 checked packets on widget 0, and fails
widget 1's first send with `Network is down`. Both peer streams retain consecutive
tokens through approximately 611 million beats. The guest failure path powers
off, the driver exits zero, and owned farm cleanup succeeds; the packet failure
correctly prevents acceptance. Preserve the failed run and its joined peer logs.

The packet workload binds its AF_PACKET socket while eth1 is still down, then
sets IFF_UP. Linux packet bind records ENETDOWN on that socket; NETDEV_UP registers
the packet hook without clearing the pending error, which the send allocation
path consumes. Bring each requested interface up before creating and binding its
packet socket. Keep setup, send and packet errors fatal, without retrying failed
sends or relaxing traffic checks. Test the actual workload's ordering against
an initially down second interface and retain negative setup/send controls.

Native compiler, FPGA logic and host bridge sources remain unchanged. Verify
current-source production compile/link and the actual static RV64GC guest ELF;
hardware reuse requires identical generated hardware, and runtime receipts must
bind the changed guest source. Evidence is under
`/scratch/jfx/chia-nic-runtime-repair-20261107/iteration5-*`.
All four current-source NIC Linux/packet/poweroff/driver-zero/cleanup results
remain required from the separate harness. Full feature, formal and compiler-speed
acceptance remain incomplete. `LOOP_COMPLETE: no`.

## NIC repair iteration 4: stable-head F2 reads and boot-cycle budget (2026-11-08)

The retained normal-NIC run `linux-d8965657a1c5cb33` reaches 332353 complete
peer rounds, with all 304102995 outgoing 512-bit tokens consecutive and no
sequence error. Heartbeats advance throughout the two-hour run at approximately
295000 target cycles per second. Its Linux output through 0.332555 seconds is
byte-identical to the successful compile-time-loopback run. That reference has
no further output until 1.631678 seconds and powers off after 4256116097 target
cycles; this run transfers only 2128720965 NIC target-cycle tokens before its
deadline. Preserve the failed run, raw abort diagnostics and successful owned
farm cleanup. The evidence indicates a runtime progress budget problem; no
userspace packets or poweroff occurred, and the run remains failed.

The production F2 backend reads each stable 64-byte source head with sixteen
blocking DWORD reads before its explicit ACK. Reduce physical read overhead
using aligned streaming reads of the write-combining BAR4 mapping, restricted
to the explicit-ACK protocol. Preserve legacy destructive reads, complete every
payload before ACK, and retain the ACK write/readback/fences before the next
head. Native PCI read validation must accept naturally aligned narrow physical
reads while preserving malformed transaction rejection, non-destructive PCI
reads and unchanged full-width DMA consumption. Header allocation recognizes
only the exact validated alignment comparisons and retains strict DMA grant
checks. The native compiler is rebuilt and pinned under a new immutable hash;
the active verification YAML compiler references and candidate root are updated
atomically. Nonblocking sink ownership, strict packet checks and DRAM zeroing
policy remain unchanged.

Evidence is under `/scratch/jfx/chia-nic-runtime-repair-20261107/iteration4-*`.
Focused checks and production compile/link do not establish physical throughput
or Linux acceptance. Exactly four current-source NIC Linux/packet/poweroff/
driver-zero/cleanup results remain required from the separate harness. Full
feature, formal and compiler-speed acceptance remain incomplete.
`LOOP_COMPLETE: no`.

## NIC repair iteration 3: cooperative peer waits and abort evidence (2026-11-08)

The iteration-2 single-NIC replay timed out after 7200 seconds with its driver
screen still running. Its farm cleanup succeeded, but the timeout killed the
manager before its atexit peer collector ran. The retained result has no UART,
heartbeat, driver or peer transcript. Preserve
`logs/background-features/linux-1e09882cb2397051`; its timeout cannot establish
either token conservation, Linux progress or the physical cause of the stall.

The production NIC driver busy-waited inside tick after publishing a batch.
A delayed or failed normal peer therefore prevented other NICs, TSI and the
heartbeat from being serviced. Retain an explicit waiting-for-peer state across
scheduler ticks, publish each outgoing batch once, and transfer the reply only
after its acquire flag is observed. A zero push still retains identical bytes
and ownership until complete transfer; nonzero short transfers remain fatal.
Report peer-wait ownership separately at finish. Failure recovery must stop/join
owned peers and retain raw slot diagnostics before terminating the owned farm,
without marking an aborted job complete or granting a runtime pass.

The actual emitted write helper, independent AW/W/B buffers and 3072-entry FIFO
pass a bounded integrated regression: 174908 physical fragments produce exactly
6400 byte-correct words, every AXI response is checked and FIFO occupancy is
checked each cycle through full-capacity stalls. This does not establish the
cause of the physical timeout. Native compiler sources and pinned compiler are
unchanged in this iteration. Evidence is under
`/scratch/jfx/chia-nic-runtime-repair-20261107/iteration3-*`.

Current-source production compile/link and focused driver/lifecycle checks are
implementation evidence only. The separate harness must obtain exactly four
baseline static-semantic passes followed by all four current-source NIC Linux,
packet, poweroff, driver-zero and farm-cleanup results. Full feature, formal and
compiler-speed acceptance remain separate and incomplete. `LOOP_COMPLETE: no`.

## NIC repair iteration 2: partial PCIS writes (2026-11-07)

The retained iteration-1 repair replay failed at target cycle 177100557,
before userspace packets. First sink occupancy was 2182/3072 at round 12206;
subsequent backpressure repeats every 313 rounds, exactly the TSI driver's
2004765-cycle credit interval. The owned farm cleanup succeeded. Preserve this
failure under `logs/background-features/linux-07041f98a3ddf09e`.

Actual emitted queue/address/count wiring is correct (NIC source 592, TracerV
source 596, NIC sink 600). The F2 BAR4 SDK maps write-combining memory and writes
ordinary DWORDs; the shell preserves partial WSTRB. The old production native
write helper enqueues every physical W beat as a complete 512-bit token. A
bounded execution of that actual emitted helper reproduces two queue tokens
from two halves of one cache line. This is a confirmed transport defect; its
occurrence in the retained physical run remains an inference because WSTRB was
not captured there.

Repair CPUStreamWrite with independent byte-mask/payload/line ownership per
sink, emitting once after all 64 bytes arrive. Partial physical transactions
still receive AXI responses; final completion respects FIFO and response
backpressure. Overlap, unfinished-line address changes, crossed stream windows,
zero strobes and oversized writes remain errors. Reset defines mask/control,
leaving invalid payload storage unspecified. The F2 host fences every complete
64-byte line before starting the next, enforcing the single unfinished-line
contract even across write-combining flushes.

The previously retained peer sequence checker was absent from the live source;
restore it and its duplicate/skip/rollover checks. Do not infer outgoing sequence
continuity from the old peer's silence. Use acquire/release shared-memory flags,
reject short pulls before publication and report retained batch ownership at
driver finish. Zero pushes retain the same reply, and nonzero short writes
remain fatal.

Evidence is under `/scratch/jfx/chia-nic-runtime-repair-20261107/iteration2-*`.
The Release compiler was rebuilt and pinned to a new immutable hash path;
the active verification YAML compiler references and candidate root were
updated atomically. The actual emitted production RTL passes 3462 physical
fragments into 128 byte-correct, exactly-once words with completion stalls.
Changed RTL
requires a new hardware identity. Exactly four baseline static checks and all
four fresh NIC Linux/packet/poweroff/driver-zero/cleanup results remain required.
No FPGA, Jasper or speed acceptance follows from the bounded regressions.
`LOOP_COMPLETE: no`.

## Immediate priority: diagnose and repair NIC runtime (2026-11-07)

Formal work is deprioritized during this repair. The original full verification
job was stopped with all available results preserved; its exact Shuttle LEC
project cleanup reports no surviving owned processes. Do not launch additional
formal proofs until the NIC runtime blocker is resolved. Final port acceptance
still requires the mandatory formal/semantic/FPGA/speed gates; interrupted proofs
are incomplete, never waived.

The native NIC wrapper-route fix passed all four production configurations and
the four baseline semantic checks. The real single-NIC F2 run built
agfi-00ffeef5cb7ecf9e7, exchanged24414idle-token rounds and entered Linux6.16,
then exited1 after CPU-to-FPGA push returned0/58560bytes. The zero is produced
before any PCI write when fewer than915beats are free in the3072beat queue.
Its actual hardware occupancy was not logged. All old results, peers and farms
are preserved/cleaned; the other six Linux cases were not dispatched.

Prioritize source-driven host and actual-emitted-RTL diagnosis: establish whether
capacity reflects genuine backpressure, stale control data or token drift. Fix
the host's nonblocking API misuse without dropping/duplicating a pending peer
batch; retain fatal malformed/nonzero-short transfers and add strict exactly-once
controls. Log the first actual count/queue-capacity and any recovery on hardware.
Do not claim token correctness or Linux success solely from a safe retry.

Work in the repair checkout and freeze each accepted snapshot. Recheck exactly
four baseline targets before real F2 Linux/NIC packet verification. Reuse the
retained AGFI only with matching hardware identity; changed host drivers still
require new source-bound receipts. Keep DRAM zeroing disabled and source/peer
ownership/cleanup strict. Join implementation subagents centrally.

Read `/scratch/jfx/chia-nic-runtime-repair-20261107/` and the latest
`automation/chia_firesim_circt/logs/feature-linux-active.json` for live evidence.

## Immediate priority: resolve non-formal production failures (2026-11-06)

Continue production repairs and feature FPGA/Linux verification independently of
long-running Jasper proofs. Existing formal jobs may finish against their frozen
candidate; their join must not delay isolated implementation work. Retain all
formal results and the 24-hour / 128-GiB proof limits. Final acceptance still
requires Jasper, enabled-feature semantics, Linux/feature checks and compiler-speed
evidence against the matching candidate.

The multiclock counter reset/acquisition checker repair now passed a fresh F2
Linux, counter/Print, workload and poweroff run:
`/scratch/jfx/chia-multiclock-counter-repair-20261105/verification-20261105T201721Z/feature-linux/result.json`.
The prior non-NIC recovery also passed FAME5 and AutoCounter/Print. Preserve the
original failures. The remaining immediate blocker is the NIC ACK route validator:
follow the unique live CPU control aggregate through wrapper instances, check
exact types/connections and reject ambiguous/partial overrides; retain actual
MCRFile WSTRB capture validation. Do not waive the validator. Then compile the
NIC representatives and validate the repaired shared-memory ownership driver on
F2 with real Linux packets, joined peers and farm cleanup.

Use isolated source and immutable compiler snapshots for background work. Check
exactly four baseline static-semantic targets before FPGA dispatch. Serialize
Vivado using the shared build lock; stop queued cases on failure while joining
owned workers/farms. Keep DRAM zeroing disabled. Existing Linux passes and native
regressions establish only their recorded scope. A failed FPGA case feeds the
next repair immediately, without waiting for Jasper. Use as many subagents as
useful; the main implementation agent integrates and joins them before returning.

Track the repair and new job in `/scratch/jfx/chia-nic-priority-repair-20261106/`.
Read `automation/chia_firesim_circt/logs/feature-linux-active.json` for its latest
job and evidence. Do not mutate a snapshot once its verification starts.

## Multiclock observation contract correction (2026-11-05)

The retained F2 run completed Linux and poweroff, but the old checker conflated
target-register reset edges with globally masked source tokens. New exact-input
SFC compilation plus executable matched SFC/CIRCT traces establish both quantities
independently. For phase-zero clocks, reset length R and frequency m/d, the
identity reset prefix is I=1+floor((R-1)m/d); the masked source prefix is
A=1+ceil((R-1)m/d). Check identity against local-I and accumulation against
local-A, exactly. The first periodic Print is determined by A-I; do not fit an
offset to observed counters or permit arbitrary missing records.

Build monitor manifests from the generated ResetPulse default and ClockBridge
declarations, with separate reset/accumulation/Print expectations. The current
slow domain has I25/A26 and no count-zero Print; the fast domain has I=A50.
Keep corruption, wrong-phase and missing-event controls strict. Preserve the
original failed result and publish rechecks separately. Native compiler/RTL and
DRAM initialization policy are unchanged; hardware may be reused only after
matching its recorded identity. Matched boundary regressions are supporting
evidence, not whole-design Jasper or final feature acceptance.

## Immediate production repair: F2 source-stream completion (2026-11-04)

The address-lane-based F2 stream fix failed real FAME5 Linux verification:
the host replayed a retained Print head until the 7200-second workload timeout.
Do not accept its local tests as hardware signoff or rebuild other cases
unchanged. The bounded production-RTL regression reproduces the exact failure.

Use an explicit F2 source protocol: PCI reads retain the queue head; after each
complete 64-byte read, the host fences, writes one acknowledgement to that
source's existing count-register address, reads back the register to flush the
posted write, and fences before reading the next head. Keep NIC writes intact.
Validate actual ACK wiring, source-only permissions and machine-readable ordered
protocol metadata; report this extension separately from raw SFC driver ABI
identity. Keep all five original stream parameters and every other ABI field
strict. Preserve legacy DMA behavior and default legacy host constructors.

Require exact-failure and speculative/aligned-read regressions, real host-driver
decode/flush checks, then fresh four-target static comparisons and a new FAME5
FPGA Linux run before claiming this repair works on hardware. During the repair,
use `feature_linux.stop_on_case_failure: true` so a failure stops further queued
builds while owned workers/farms still join. All seven enabled cases and the
separate matched-feature semantic/Jasper obligations remain required for full
acceptance. DRAM zeroing stays disabled.

Formal must account for the changed platform protocol explicitly: raw SFC PCI
reads and native ACK reads have different side effects. Require a checked
host/transport refinement or matched abstract stream contract before claiming
LEC/SEC equivalence across that boundary. Retain the raw ABI difference and
protocol scope in receipts; a static compatibility result or Linux boot cannot
waive that proof obligation. Protocol metadata version2 additionally validates
actual control-adapter write-strobe capture; the version1 candidate was rejected
before another hardware build because its count-register strobes were tied zero.

## Active phase: resume full verification (user update, 2026-11-03)

The user has explicitly requested resuming FPGA builds/runs and Jasper LEC/SEC.
This supersedes the formal-disabled development phase below. Read
`CHIA_OPERATOR_VERIFICATION_20261103.md` first. Verify the saved candidate before
another implementation call: exactly four baseline semantic comparisons, then
parallel FireSim F2 and Jasper nodes, join, compiler-speed benchmark and required
feature acceptance. Preserve the 24h proof budget, 128 GiB guard and disabled
DRAM zero-out. Do not mutate verification inputs while nodes are running.

The seven representative production compiles, driver builds and workload/tool
checks establish bounded implementation readiness, not FPGA or formal acceptance.
Enabled-case execution still needs integration into the acceptance graph: matched
SFC semantic and Jasper producers, feature F2 dispatch/collection and strict
hash-bound receipts. Keep that work explicit in subsequent implementation turns;
the read-only feature audit cannot execute cases or grant baseline-only completion.
Use as many subagents as useful, integrate centrally and join them before returning.

## Active development cadence: semantic checks, then continue (2026-11-02)

Keep formal verification disabled until all required feature implementations are
complete. After each implementation turn, run the baseline syntax/static-semantic
comparison on exactly Rocket, MegaBoomV3, Shuttle and Saturn Shuttle. If it fails,
repair the failures in the next turn. If all four pass, freeze the candidate and
dispatch its FireSim FPGA build/run in the background, then proceed immediately
to the next feature implementation without waiting for FPGA or Jasper results.

Background FPGA work must consume an isolated snapshot of the accepted RTL,
collateral, drivers, manager/build inputs and workload/checker policy. Later
compiler or platform edits must not change a running build. Serialize local
Vivado use, retain per-candidate results and failures, reuse identical actual
hardware/runtime inputs with provenance, and bound the pending queue. Background
failures feed subsequent implementation; they do not block independent work or
become passes. The implementation agent joins its own subagents before returning;
the independently tracked background FPGA node may outlive that turn.

This cadence supersedes older instructions to join all long verification nodes
before every implementation turn. The dedicated feature-implementation phase
must not automatically start LEC/SEC or compiler benchmarks. Feature readiness
is a separate artifact-backed milestone, and final acceptance still requires
the existing formal, Linux/feature and compiler-speed evidence. Do not declare
the overall port complete from semantic success or implementation readiness.

## Current phase: finish feature implementation first (2026-11-02)

The user explicitly requested stopping formal verification and finishing the
remaining implementation to reach feature completeness as the first priority.
Read `CHIA_OPERATOR_FEATURE_FIRST_20261102.md` first. Work through the actual
production compiler/F2 platform, drivers, enabled workloads/checkers and real
execution adapters for FAME5, combined AutoCounter/Print, generalized multi-clock
models and normal/multiple/loopback NICs. Close required unsupported production
shapes rather than accumulating standalone diagnostic helpers.

Use the dedicated feature-implementation phase: keep coding across turns with
parallel feature owners and centrally integrated changes. Do not launch Jasper
or wait for background baseline FPGA/benchmark cycles between these turns.
Run meaningful SFC semantic, RTL, driver and enabled execution checks needed for
the feature work. Join all workers and owned jobs before returning. Report
implementation readiness separately from final verification; a stage milestone
must be backed by runnable production artifacts and successful command receipts.

Final semantic, separate LEC/SEC, compiler-speed and feature-exercising Linux
acceptance requirements remain unchanged and incomplete until demonstrated.
Formal proof issues must not hold up independent feature implementation in this
phase. Preserve prior diagnostics, SFC goldens and correct CIRCT behavior; leave
DRAM zero-out disabled. QSFP remains deferred. This directive supersedes older
instructions below to run the full formal graph after every implementation turn.

## Primary implementation milestone: feature completeness (2026-11-02)

Focus implementation on completing the production FAME5, AutoCounter/Print,
generalized multi-clock/model, and NIC paths. Read
`CHIA_OPERATOR_FEATURE_COMPLETENESS_20261102.md` at the beginning of the next
turn. Finish actual platform composition, configuration enablement, generated
shared MMIO/stream/header/driver wiring, bounded Linux feature workloads and
execution adapters. More standalone transforms or diagnostic CLIs alone do not
close this milestone. Use concurrent feature owners, integrate shared APIs
centrally and join every worker before returning to CHIA.

Report implementation, production integration, Linux exercise and formal signoff
separately for each stream. Independent feature implementation need not wait for
formal success; continue formal diagnosis with its own owner when resources
permit. Final port completion still requires all semantic, LEC/SEC, Linux,
compiler-speed and enabled-feature evidence gates. Keep exactly four baseline
recipes and the seven bounded enabled representatives in feature_scope.yaml;
do not enumerate sample configurations. Preserve the 24h/128GiB Jasper policy,
immutable SFC goldens and justified CIRCT corrections; leave DRAM zeroing off.
QSFP remains deferred. This priority supersedes older ordering in the historical
checkpoints below without dropping their outstanding correctness obligations.


## Immediate implementation priorities (user update, 2026-11-01)

Focus the next implementation turns on **production integration and fixing the
critical Jasper issues**. Component-only progress is insufficient: connect the
implemented transforms through the actual FireSim compiler, platform, generated
driver/header and workload paths for bounded feature-enabled representatives.

1. Diagnose and resolve the retained Jasper failures using the frozen inputs,
   counterexamples, state maps and session archives. Establish justified
   initial-state and queue/state correspondence, distinguish mapping/setup
   defects from defined-behavior compiler defects, and fix focused reproducers
   before another unchanged full-top proof. Obtain separate complete LEC and
   SEC; do not allow Rocket LEC failure to permanently prevent SEC diagnostics.
2. Finish production enablement and integration for FAME5, AutoCounter/Print,
   generalized multi-clock models and NIC. Prioritize direct top-input Print
   passthrough, FAME5 reset/startup alignment and dynamic nested composition,
   full F2 platform/transport/driver composition, and NIC loopback ABI/pauser
   semantics. Connect real feature execution adapters and check actual feature
   activity during Linux before clean poweroff.

Use concurrent subagents with explicit file ownership for independent work;
the main implementation agent integrates and joins every worker before returning.
Keep exactly four baseline targets and bounded enabled-feature cases. Preserve
immutable SFC goldens and justified CIRCT fixes. Unspecified unwritten RAM need
not match bit-for-bit; leave DRAM zero-out disabled. Keep defined controls and
initialized/written data strict, the 24-hour Jasper proof budget and 128 GiB
memory guard, and the existing semantic/Linux/formal acceptance requirements.
The latest completed graph is
`automation/chia_firesim_circt/logs/verification/20261030T132059Z-pid1846599-iter1-full/graph.json`.
Its baseline semantic/Linux/compiler-speed passes do not establish enabled-feature
or formal acceptance. Read its diagnostics and the preserved October 30 proof
archives when resuming. QSFP remains deferred.

Recovery update (2026-10-30): read `CHIA_OPERATOR_RECOVERY_20261030.md` first.
The stalled unlimited Jasper attempt is stopped and its raw proof state,
counterexample inventory, logs and exact inputs are preserved for diagnosis.
Use those results to classify and fix correspondence/defined-behavior issues
before another long proof. The normal loop now uses the 24h policy below.

## Jasper proof duration (user update, 2026-10-30)

Both LEC and SEC now use a **24-hour proof limit** (`--prove-time 24h`).
This replaces the prior unlimited-time policy after the retained Rocket run
hit an internal engine limit and left its parent session waiting. Bound the
remote proof/report wrapper at 87,000 seconds (24h + 10m report grace), and
CHIA's adapter at 87,600 seconds (another 10m for archive/local cleanup).
Preserve logs, counterexamples, configuration, input hashes and the retained
stalled session/project for diagnosis. An interrupted or incomplete proof must
remain failed verification and inform the next implementation turn.
After a remote hard timeout, clean up only that project's recorded launcher
session and descendants before collecting its partial archive. Retain the
cleanup receipt and leave unrelated Jasper projects and all proof files intact.

Keep the 128 GiB process RSS watchdog, one proof job, finite SSH setup/archive
transfer timeouts, and strict signoff. Explicit zero-time overrides remain
supported for intentional unlimited experiments, but the normal loop must use
the 24h policy. Do not clear DRAM or weaken initial-state/protocol obligations
as a timeout workaround. Resume from retained diagnostics before launching a
fresh candidate; preserve all existing compiler work and immutable SFC goldens.

Recovery update (2026-10-26): before new implementation, read
`CHIA_OPERATOR_RECOVERY_20261026.md`. The prior implementation turn was
interrupted by model capacity failure and left partial shared-worktree changes.
Reconcile and validate them before handing a candidate to CHIA. The last
completed baseline verification is iteration 6 of the October 23 driver;
its acceptance does not apply to the partial changes.

## User clarification: unspecified RAM contents are not bit-exact (2026-10-25)

Uninitialized RAM is intentionally unspecified. Do not require arbitrary
unwritten contents or empty-queue payload bits to be equal between SFC and
CIRCT, and do not clear RAM in CIRCT merely to satisfy the comparator. Keep
strict correctness for reset-defined state, valid/ready/control, specified
read/write/mask/latency/collision behavior and observable valid payloads.
For explicitly uninitialized memory that is legitimately read before writing,
model the same abstract nondeterministic initial memory under the documented
representation relation rather than assuming zero or independently comparing
arbitrary samples. Prove written/initialized state and subsequent transactions
correspond. This does not permit live valid data to become unconstrained.

Keep `host_debug.zero_out_dram: false` for the CHIA loop's normal F2 tests.
The user explicitly rejected enabling zero-out to make formal comparisons
pass because clearing DRAM is slow. Do not add +zero-out-dram, automatic
pre-clearing, or extra all-memory initialization runs as a workaround. Accept
mismatches established to arise solely from unspecified unwritten memory;
retain comparisons for loaded/written data and all defined behavior. Record
the initialized/loaded regions and abstraction relation in proof evidence.

Implement a justified memory/protocol-aware LEC/SEC contract and targeted
negative controls: invalid unwritten payload differences must pass; corruption
of written or explicitly initialized data, wrong valid/ready, lost/reordered
transactions and wrong byte masks must fail. Document qualification and initial
state relations in receipts. Update the current blanket --no-valid-qualify and
X/reset-signoff handling only with these explicit obligations; do not waive
unclassified undriven logic, suppress all X checks, or assume helper assertions.

FireSim `host_debug.zero_out_dram: true` generates `+zero-out-dram`. The driver
calls loadmem->zero_out_dram() before loading the memory image. With that option
explicitly requested by the user for a separate run, require zeroed DRAM followed by the specified image writes, and record
the option in both sides' initialization contract/runtime identity. With it
disabled, unwritten DRAM contents remain unspecified. This option does not
initialize every internal queue RAM or model-local memory. Check actual emitted
runtime configuration rather than assuming the option is currently enabled.

Apply this contract in the next implementation turn. Update the formal
adapters with focused positive/negative checks and retain separate LEC
and SEC requirements. Historical reports below retain their original
scope; they are not acceptance under this clarified contract.

Updated 2026-10-23. This is the active implementation charter. The four-target
F2 milestone is required but does not complete the port. The expanded feature
requirements below are mandatory too. The old thirteen-target U250 matrix and
Rocket success streak remain superseded. CHIA enforces verification boundaries;
the compiler remains the engineering work product.

## Required full-port feature scope (user update, 2026-10-23)

Implement FAME5, enabled AutoCounter/Print integration, multi-clock/generalized
models, and NIC support. These are active completion requirements, not optional
work contingent on the four baseline targets. QSFP and partition transport are
deferred; a new Golden Gate dialect remains optional. Do not silently expand
this request into a QSFP project or an all-config acceptance matrix.

Keep exactly Rocket, MegaBoomV3, plain Shuttle and Saturn Shuttle as the fixed
baseline acceptance set. Add only bounded, explicitly feature-enabled oracle
cases needed to exercise the required capabilities. `feature_scope.yaml` under
`automation/chia_firesim_circt` declares their coverage. Existing baseline Linux
and timing passes do not demonstrate features that were disabled in those runs.

| Required stream | Implementation and coverage |
| --- | --- |
| FAME5 | Threadable-model grouping; per-thread registers/reset/state; threaded memories preserving masks, latency and read-under-write semantics; active-thread scheduling and channel mux/demux. Verify two/four-thread state and memory isolation, independent stalls, resets, and a genuinely threaded Linux-capable representative against the corresponding enabled SFC transform. Annotation labeling alone is insufficient. |
| AutoCounter/Print | Integrate enabled AutoCounter and Print through the real compiler, bridge/trigger/reset/clock wiring, F2 platform assembly and driver metadata. Exercise emitted prints and counter values on the Linux representative before poweroff; disabled annotations or isolated synthesis helpers cannot pass. |
| Multi-clock/generalized models | Remove selected-target single-model/hub and clock-shape assumptions where the SFC pipeline supports more. Verify multiple models/clock domains, rational ratios, reset, pipe/ready-valid/aggregate channels and independent backpressure. Couple scheduling to complete transformed models and exercise the enabled multi-clock Linux representative. Existing clock-generator unit tests alone are insufficient. |
| NIC | Complete F2 NIC/compiler/driver integration and normal-mode Linux packet exchange, with bounded multiple-widget and loopback coverage matching the existing SFC API. Validate stream/control identities, packet contents, ordering, backpressure, reset and distinct NIC bindings; link presence or Linux boot without network traffic is insufficient. Use local/host-connected networking; QSFP is not required. |

Use as many subagents as useful. Prefer concurrent feature owners for these four
streams, plus independent formal diagnosis or acceptance support when resources
permit. Declare file ownership before editing shared passes; integrate shared
model/annotation/clock APIs centrally. Establish FAME5 state/memory interfaces
before dependent scheduling changes, and coordinate model generalization with
NIC/instrumentation consumers. The main agent joins every subagent, resolves
conflicts and completes checks before returning. Do not leave implementation
workers editing the candidate during CHIA proofs or FPGA runs.

The verification standard is unchanged for every feature-enabled representative:

1. Generate/freeze the corresponding enabled SFC oracle and compare CIRCT syntax
   and semantic contracts on the same input. Use small feature-specific tests as
   supporting evidence, not as a replacement for the full representative.
2. After semantic success, run separate Jasper LEC and SEC and F2 verification in
   their own nodes, with explicit enablement, assumptions, artifact hashes and
   counterexamples. Missing, bounded, inconclusive or blackboxed coverage fails
   acceptance. Feature correspondence must be justified, not assumed.
3. Require Linux userspace boot, feature-specific exercise/checks, and clean
   `poweroff` with driver/script exit zero. Reuse the br-base-uniform template
   with private rootfs/workload extensions; preserve base images.

Keep compiler-speed acceptance on the four baseline designs: matched SFC/CIRCT
transformation/compile medians, excluding all workload/proof/FPGA runtime. Record
feature-case compile costs separately without enumerating every configuration.

The `features` completion-audit node requires hash-bound semantic, LEC, SEC and
Linux/F2 evidence for all declared feature capabilities. Store case receipts
under `logs/feature-evidence/<candidate-identity>/<feature>/`; do not edit the
hashed scope registry to append report hashes, which would create a circular
identity dependency. Implement actual case execution in the verification nodes;
the audit cannot turn implementation-agent checkboxes into verification passes.
Until those cases/adapters and complete receipts exist, overall completion must
remain blocked even if the four baseline nodes pass.

Reuse bitstreams when all actual hardware build inputs match; reuse Linux runs
only when hardware, driver, workload and verification-policy identities match.
Retain the original receipts and record reuse explicitly. Do not rerun Vivado
merely because an unrelated compiler validation or documentation file changed.

F2 workload update (2026-10-22): acceptance now requires `br-base-uniform.json`
Linux boot into userspace followed by explicit `sync; poweroff -f` on each of
the four targets. Reuse its built kernel and a private rootfs copy with the
FireMarshal late-init command hook; preserve the base image/template. Require
ordered kernel/userspace/shutdown UART evidence and successful driver exit.
Earlier four-target checksum-smoke passes remain scoped historical evidence;
they do not establish Linux boot. Guest runtime is excluded from compiler speed.

Gate policy correction (2026-10-21): the first node is a pre-build syntax and
static-semantic comparison. It must be able to pass independently of Jasper,
Vivado and FPGA execution. When all four selected candidates pass, dispatch
Jasper LEC/SEC and the local F2 build/run branch in parallel. Do not restore an
unconditional failed verdict or require post-synthesis evidence before building.
Full completion still requires those downstream results and compiler timing.

The semantic, Jasper and FireSim commands in `verification.yaml` are connected
to executable adapters. Jasper dispatches separate state-matching and sequential
strategies; separate LEC proof coverage remains incomplete. FireSim builds
the frozen accepted CIRCT artifacts locally, then deploys its own new AGFI and
checks a terminating F2 workload. A matched compiler-timing replay adapter is
now configured and passed all four targets for candidate `57d1df95d91d5c514a708a7d8d9e90f948c1ed1b15f4c44fa4fa59313d12ab96`.
These results must be rerun after compiler changes. Earlier
iteration checkpoints below describe the gate before this correction; their
unconditional semantic-failure policy and null-adapter status are superseded.

Loop feedback must summarize verification status and diagnostic paths within a
bounded prompt. Preserve complete inventories/tool reports in `graph.json` and
per-node files; never embed those full results in an implementation request.
Use `--feedback-from <graph.json>` when restarting to retain diagnostic context
without accepting stale verification.

Recovery checkpoint: iteration 20 of run `20261021T143428Z-pid563173` passed
all four pre-build comparisons. Jasper/F2 then stopped before tool execution
because the CIRCT provenance inventory also includes golden RTL with the same
basename. `downstream_common.inputs` now selects only the validated compiler's
own output subtree. This selector fix has been checked on all four receipts;
it does not establish any Jasper, bitstream or FPGA result. The next prompt
previously exceeded the CLI limit by embedding the 3.9 MB graph; bounded feedback
and the worker prompt guard now prevent that failure.

## What is implemented, and what the logs establish

The native implementation lives in `sims/firesim/sim/goldengate-circt/`, with
integration in `sims/firesim/sim/make/goldengate.mk`. It already contains FIRRTL
annotation/target/hierarchy/clock/model/channel analyses; FAME transformations;
SRAM models; bridge engines; simulator assembly; control/MMIO logic; driver
headers; XDC; and the U250 shim. Inspect the implementation before recreating
these pieces. Presence of a pass does not establish parity across targets.

| Area | Evidence and current limit |
| --- | --- |
| Interface headers and hierarchy | `.const.vh` emission and build dependencies are implemented in `lib/Support/MetasimInterfaceHeader.cpp` and `sim/make/goldengate.mk`; the old missing-header action is obsolete. Host hierarchy normalization is implemented in `lib/Support/SimulatorRTL.cpp` and `tools/goldengate.cpp`. |
| FASED | Parameterized admission, retirement, request limits, statistics and histograms have focused tests and SFC cone comparisons. Iteration 262 reports ten passing FASED CTests and 212,996 compared histogram cycles across four fixtures. This is scoped evidence. |
| U250 target replay | Last complete result is iteration 262: three compiled targets (Rocket single, quad, NIC), four compiler failures, six unsupported platform profiles. `full_matrix_parity` is false. Eight annotation boundaries match, six differ, twelve are unreached. |
| Historical target failures | The iteration-262 U250 replay rejected wider TracerV and non-ten-flight FASED/MMIO shapes. Later native work supports 1–7 retirement lanes and selected 10/20-flight F2 paths; do not repeat those old errors as current diagnoses. The broader old U250 recipes remain unvalidated. Partition/QSFP support is still deferred. |
| Runtime | FireSim Manager Rocket metasim smoke and combined regressions exist. Iteration 262 executes fresh smoke and reuses suites last executed at iteration 259 with matching input identities. `rocket_differential` still says `pending SFC UART-bearing baseline`. |
| Latest partial work | Iteration 263 is incomplete: ENOSPC in MMIO comparison and a concurrent-compression failure; some `result.json` files are empty. Rocket's scoped MMIO comparison covers 48 routes and 11,058 samples. Neither that nor partial matrix data is full parity. |
| AWS F2 | Native `BaseF2Config` boundary support now carries 32-bit control addresses, physical PCIM tieoffs, no QSFP, F2 hierarchy/XDC and driver interface constants. Focused platform tests and standalone shell syntax/lint pass; see `sim/goldengate-circt/test/F2Platform.md`. This is not exact-target parity. `FRFCFS16GBQuadRankLLC4MB_BaseF2Config` now selects the native functional/timing model and 68-word MMIO/driver path with explicit platform capacity. Focused composition, syntax and exact four-target FASED header ABI checks pass; full target output/semantic and platform comparisons remain incomplete. Selected DDR3 AutoILA inputs have no debug probes and their SFC logs skip ILA creation. |
| Baseline acceptance checkpoint | Recovery run `20261021T204512Z-pid2577644`, iteration 6, passed all four static comparisons, F2 Linux boot/poweroff runs and repeated compiler-speed gates for the same input identity. CIRCT/SFC compiler ratios were about 0.526/0.264/0.556/0.526. Jasper Rocket LEC remained invalid: 35 boundary targets proven, 30 counterexamples, 19 undetermined; separate successful LEC/SEC results remain missing. These baseline passes do not prove disabled full-port features. |
| Required full-port backlog | Scoped FAME5 grouping, per-thread state/async and synchronous RAM, CSV source-to-global-control/header assembly, memory/combinational model boundaries and NIC components now have matched SFC comparisons (latest checkpoint 37 below). Production threading and full feature composition remain incomplete, including enabled Print, broader model/clock topology and compatible NIC loopback driver wiring. Every required enabled semantic/LEC/SEC/Linux feature receipt remains mandatory; QSFP is deferred. |

Read these evidence sources rather than the entire 198 MB transcript:

- `sims/firesim/sim/goldengate-circt/test/U250TargetMatrix.md`, especially its final iteration-262 section.
- Generated U250 Rocket directory `sims/firesim/sim/generated-src/xilinx_alveo_u250/xilinx_alveo_u250-firesim-FireSim-FireSimRocketConfig-BaseXilinxAlveoU250Config/`, then `iteration262-u250-target-matrix/matrix-verdict.json` and the partial `iteration263-u250-target-matrix/` logs.
- `automation/chia_firesim_circt/logs/verification/20261006T131125Z-pid1620159-iter262-full/regression-results.json` and original `iter259-full/` suite evidence.
- `automation/chia_firesim_circt/logs/codex_20261006_131125.log` for targeted investigation of the final compiler changes.

Historical U250 bitstream, fingerprint, JTAG, PCI and flash-script investigations
are useful platform history, not active AWS blockers or proof of Linux boot.
Preserve their patches and logs. Old storage cleanup removed 8,731 uncompressed
MLIR dumps from iterations 167–189 (586 GiB); do not regenerate them to satisfy
historical links. Preserve immutable goldens, current outputs and useful compact
evidence; avoid concurrent compression/deletion of files still being written.

## Acceptance set: exactly four configurations

Use `sims/firesim-staging/sample_config_build_recipes.yaml` as the recipe source.
`automation/chia_firesim_circt/selected_config_build_recipes.yaml` freezes exactly
these four entries. Do not enumerate every sample recipe, rerun the old matrix
as an acceptance test, or multiply variants within a family.

| Target | Selected F2 recipe | Target configuration |
| --- | --- | --- |
| Rocket | `firesim_rocket_singlecore_no_nic_l2_lbp` | `WithDefaultFireSimBridges_WithFireSimHighPerfConfigTweaks_chipyard.RocketConfig` |
| MegaBoomV3 | `firesim_megaboom_singlecore_no_nic_l2_llc4mb_ddr3` | `WithDefaultFireSimBridges_WithFireSimTestChipConfigTweaks_chipyard.MegaBoomV3Config` |
| Shuttle | `firesim_shuttle_singlecore_no_nic_l2_llc4mb_ddr3` | `WithDefaultFireSimBridges_WithFireSimTestChipConfigTweaks_chipyard.ShuttleConfig` |
| Saturn Shuttle | `firesim_saturn_GENV256D128_shuttle_singlecore_no_nic_l2_llc4mb_ddr3` | `FireSimGENV256D128ShuttleConfig` |

Plain Shuttle is absent from the sample YAML. Its recipe is explicitly derived
from the F2 MegaBoom no-NIC recipe by changing only `TARGET_CONFIG`; the existing
class is in `generators/chipyard/src/main/scala/config/ShuttleConfigs.scala`.
The recipe uses the full composed target
`WithDefaultFireSimBridges_WithFireSimTestChipConfigTweaks_chipyard.ShuttleConfig`,
not bare `chipyard.ShuttleConfig`. `WithFireSimTestChipConfigTweaks` includes
`WithFireSimDesignTweaks`, providing FireSim clock/reset/harness settings,
clock-gating removal and target IO tweaks. `WithDefaultFireSimBridges` adds the
bridge binders and FAME-related settings. Saturn's existing
`FireSimGENV256D128ShuttleConfig` already composes `WithDefaultFireSimBridges`
and `WithFireSimConfigTweaks` around the Saturn Shuttle target in
`generators/firechip/chip/src/main/scala/TargetConfigs.scala`. Both recipes must
still run through the actual SFC or CIRCT Golden Gate transformation pipeline;
the configuration wrappers alone do not establish successful transformation.
Its inherited frequency request is provisional until validated by a build.
This is a new golden to generate, not an existing archived result. Do not use
Saturn Shuttle twice or substitute LargeBoom for MegaBoomV3.

Rocket uses `BaseF2Config`; the other three use
`WithAutoILA_FRFCFS16GBQuadRankLLC4MB_BaseF2Config`. Implement these actual
platform/compiler/shim semantics. Never rename U250 outputs as F2 or strip
unsupported configuration fragments to make a comparison pass.

## Correctness node: golden syntax and semantic comparison

For each selected recipe, generate an immutable SFC transform once, retaining
exact pre-MIDAS FIRRTL and annotations, transformed RTL and collateral, source
revisions, jar/compiler hashes, commands and recipe/platform settings. Compile
the same archived input through CIRCT into a separate candidate directory.
Use distinct compiler-only golden generation: it must not invoke Vivado.

The old `golden/u250-sfc-20261013/manifest.json` and October-1 Rocket fixtures
remain historical references. They cannot stand in for the four new F2 goldens.
Do not overwrite completed golden artifacts. Reject partial/empty/failed results.

Keep this first gate to syntax and normalized semantic comparison, without
long workload execution or an all-config test matrix. Parse/lint both emitted
RTL designs with identical libraries and defines, then compare:

- Top/module ports, widths, directions, clocks/resets, and supported primitives.
- Annotation targets and constructors, model/channel identities and token scheduling.
- Memory geometry, masks, latency and read/write/reset behavior.
- Bridge configuration, MMIO register meaning and driver/header ABI.
- Simulator/platform connections and XDC clock/hierarchy constraints.

Names and equivalent lowerings may differ. Normalize only justified differences
and record them; textual equality is unnecessary. A syntax pass, annotation-only
match or compile success cannot replace semantic comparison. Unresolved semantic
differences block this node and therefore block bitstream builds. Jasper is the
independent proof node for deeper equivalence and justified correspondence.
The node must report a result for all four targets and a content identity of
inputs, compiler, outputs, collateral and comparison policy.

`CompareF2Semantics.py` reports `verification_stage: prebuild`. Its `semantic`
field means the explicitly selected static-semantic contracts match; it is not
full RTL equivalence. Require every declared static check and both complete
SFC/CIRCT RTL plus driver-header syntax arms to pass. Failed or missing checks
remain in `unresolved_obligations` and block fan-out. Collect all four target
verdicts, including failures, rather than stopping at Rocket.

Stateful equivalence, wider annotation effects, post-synthesis pin/timing
validation and actual F2 execution belong in `deferred_obligations`, owned by
Jasper or FireSim. Their absence must not block this pre-build pass. The report
keeps `full_equivalence: false`; Jasper and F2 remain mandatory for the final
completion decision. Never turn a downstream placeholder or partial build into
a passing proof/runtime result.

## Jasper node: both LEC and SEC

Run on `rocky@32.185.44.117` (SSH alias `eda`, configured in `~/.ssh/config`).
User-provided installation: `/tools/Cadence/jasper_2026.06p002`. Use `/scratch/jfx`
for staged immutable inputs and per-candidate run directories. Check installation,
licenses, executable version, cores and memory on that machine before use.
Read-only SSH validation now succeeds and confirms the requested binary and
32 cores/122 GiB RAM. Source `/tools/flexlm/flexlm.sh` remotely for license
configuration. License availability and actual proof success remain unverified.

Start with the example at `/scratch/circt_compare`; this checkout has an example
at `/scratch/jfx/circt_compare/sec_flow/`. Read its README, scripts and assumptions.
Its old hardcoded Jasper version is not the requested installation. Adapt and
pin the version explicitly. `run_sec.py` supports module/top matching, reset,
state mapping and memory budgets; inspect its actual flags before invoking it.
The example's SEC compare-point reports are not a separate LEC proof.

For each of the four paired SFC/CIRCT outputs, require separate LEC and SEC
results. Define clock/reset initialization, observable interfaces, state/port
correspondence, memory models, blackboxes and any legal nondeterminism. Explain
which combinational cones LEC covers and which stateful boundaries SEC covers.
If a transformed host interface requires token/transaction correspondence,
encode and justify that contract; do not declare cycle equivalence by assumption.
Keep uncovered logic and full-system composition obligations explicit.

Archive scripts, version, inputs/hashes, assumptions, proved/failed/unresolved
counts, counterexamples and logs. Bounded, inconclusive, timeout, unpaired outputs,
blackboxed functionality or a missing proof are not a full pass. Historical
module-local SEC results do not establish top-level equivalence or this four-target
acceptance set. Parallelize independent proofs within remote RAM/license limits;
the example warns that full-system proof processes can each consume substantial RAM.

## FireSim node: local F2 build, manager-launched FPGA verification

As soon as all four golden comparisons pass, dispatch this node in parallel
with Jasper. Do not wait for a Rocket smoke streak or Jasper completion to start
the long bitstream work. Both branches must finish successfully before accepting
the candidate. Do not mutate candidate RTL while a build or proof consumes it.

Follow the current FireSim documentation and the checked-out manager schemas:

- [F2 repository setup](https://docs.fires.im/en/latest/getting-started-guides/aws-ec2-f2-getting-started/setting-up-the-firesim-repo/)
- [Build an F2 image](https://docs.fires.im/en/latest/getting-started-guides/aws-ec2-f2-getting-started/building-a-firesim-afi/)
- [Run a single-node F2 simulation](https://docs.fires.im/en/latest/getting-started-guides/aws-ec2-f2-getting-started/running-simulations/running-a-single-node-simulation/)

Initialize Conda with `source /home/ubuntu/miniforge3/etc/profile.d/conda.sh`,
then from `sims/firesim` run `source ./sourceme-manager.sh`. Noninteractive
SSH/login shells need this explicit initialization to activate `.conda-env`.
On F2, do not pass `--skip-ssh-setup`; SSH setup must load `~/firesim.pem`
for run-host access.
`firesim managerinit --platform f2` is one-time interactive setup if not already
configured; do not repeatedly initialize or overwrite an existing deployment.
`~/firesim.pem` is the run-host key if SSH investigation is needed.

Use isolated build/runtime/HWDB files for the selected recipes. Build on THIS
machine, overriding the default AWS build farm:

```yaml
build_farm:
  base_recipe: build-farm-recipes/externally_provisioned.yaml
  recipe_arg_overrides:
    default_build_dir: /scratch/jfx/firesim-f2-build
    build_farm_hosts: [localhost]
```

The manager still needs working localhost SSH and installed F2 Vivado/shell tools.
Select only the four accepted recipes in `builds_to_run`; schedule local builds
within available CPU/RAM rather than provision remote build instances. Validate
CIRCT selection on the manager's actual `make` command: `buildbitstream` can
regenerate RTL. Bind the synthesized RTL/collateral hashes to the accepted
semantic candidate; an SFC fallback cannot pass.

Run YAML uses `run-farm-recipes/aws_ec2.yaml` (`AWSEC2F2` in this checkout),
a unique `run_farm_tag`, `run_farm_hosts_to_use: [{f2.6xlarge: 1}]`, disabled
metasimulation, `no_net_config`, one node, and the newly generated candidate
HWDB/AGFI. Execute through FireSim Manager with explicit `-b`, `-c`, `-r`, `-a`:

```text
buildbitstream → launchrunfarm → infrasetup → runworkload
```

Verify all four selected designs on F2; one F2 host may be reused serially with
the proper image/driver. Preserve AGFI_INFO, synthesis/timing status, image IDs,
CIRCT provenance, workload/driver exits, UART checks and runtime results.
Require real target PASS/termination and the defined boot/workload checks; a
manager “Job completed” line alone is insufficient. Always collect evidence and
run `terminaterunfarm -q` for the node's tagged farm on success or failure.
Never use a prebuilt unrelated image as candidate verification.

## Speed node: faster CIRCT transformation than SFC

Optimize alongside correctness; retain identical FireSim functionality. Use
native CIRCT/MLIR parallelism and independent-module work wherever safe. Build
with all available cores (`ninja -j`, `make -j`, or equivalent); honor CPU affinity
and RAM constraints and avoid nested oversubscription. Audit disabled threading
and serial whole-design traversals. Keep deterministic output and explicit
analysis invalidation. JVM startup disappears from the native compiler stage;
remaining target/bridge Chisel elaboration may still use the JVM.

Benchmark exactly the four accepted inputs. Measure the wall time to take each
design through the FireSim compiler with CIRCT versus SFC: start the compiler
from the same archived pre-MIDAS FIRRTL and annotations, and stop when equivalent
transformed simulator RTL and required collateral are fully emitted. Include
compiler process startup and every required compilation/transformation stage;
record compiler-invoked bridge elaboration costs explicitly if present.

The speed metric is compiler transformation/compile time only. Exclude simulator
or FPGA workload runtime, driver execution, target-generation elaboration before
the compiler handoff, Vivado/bitstream builds, deployment and Jasper proof time.
Those verification stages remain in their own nodes; their duration is not a
speed acceptance criterion. There is no separate end-to-end workflow timing gate.

Report median repeated compiler measurements, peak RSS, core counts, exact
commands/tool revisions, cache state and phase boundaries. Compare equal resource
limits and equivalent outputs. Separate cold/JVM-startup and warmed measurements;
do not compare a cached no-op to a fresh compilation. The adapter identifies this
timing boundary with `timing_scope: firesim_compiler`.

The goal is median CIRCT compiler time / median SFC compiler time < 1 for each
target. Report regressions per target; an average cannot hide one slow
configuration. Run timing after local bitstream work finishes so Vivado does
not skew the results. Inspect pass-level timing to choose speed work.

## CHIA graph and completion ownership

`automation/chia_firesim_circt/loop.py --verify full` uses separate CHIA tasks:

```text
main implementation agent (joins its subagents before returning)
  → semantic comparison, exactly four targets
      → FireSim F2 build/run ─┐
      → Jasper LEC + SEC ────┴→ join → compiler-time benchmark
        → required-feature evidence audit → completion decision
```

Use as many subagents as you see fit for independent analysis, implementation
and verification support; there is no task-specific limit on delegation.
Coordinate shared-worktree edits and integrate every subagent's results. The
main implementation agent owns completion and must join all subagents before returning.
CHIA sees that call as done when it returns; no subordinate may be left to finish
implementation afterward. The outer loop awaits every verification branch before
another implementation turn. Verification functions, not the implementation LLM,
own long proof/build/run jobs. Keep CHIA as a small enforcement abstraction.

On this shared worker, resume Codex by session ID against its local session
store; never restore snapshots of shared Codex SQLite/WAL/SHM or rollout files.
The implementation worker holds a separate call lock and disables Ray retries.
A session-writer conflict or unsuccessful LLM call stops the loop before further
verification or source edits. Do not delete another client's session locks.

`verification.yaml` defines adapter commands, selected artifacts and report
contracts. Missing adapters/artifacts, stale reports or unproved obligations
must fail closed and feed useful evidence to the next implementation turn.
The initial adapter placeholders are pending work, not a working formal or F2
flow. Complete them against real outputs as the compiler gains F2 support.
Every report must bind to the same candidate; invalidate on changed inputs.

Old `compile`/`metasim` modes and Rocket workloads remain useful focused diagnostics.
They cannot satisfy the new completion gate. Do not repeat long Rocket suites on
unchanged output merely to accumulate passes. Run focused compiler tests for
changes; reuse execution evidence only with matching content identity.

Accept `LOOP_COMPLETE: yes` only when the main agent declares completion AND all
five current nodes pass: four-target syntax/semantic comparison, LEC plus SEC,
F2 Linux build/run, compiler speed, and full required-feature acceptance. The
feature audit must cover all four required streams; a baseline-only pass cannot
complete the port. Exhausted iteration limits mean incomplete. No automatic
push/publication is required by this charter; preserve unrelated repository edits.

## Implementation order and architectural rules

1. Inspect the latest complete checkpoint; fix real F2 platform/header/shim and configuration support, preserving U250 behavior.
2. Freeze the four matching SFC goldens and input manifests. Add the real syntax/semantic adapter and resolve differences, starting with wider TracerV, FASED constructor assumptions and required AutoILA/F2 memory settings.
3. Connect actual Jasper LEC and SEC adapters and the local-build/F2 manager adapter. Semantic success starts both branches; use their failures to drive compiler fixes.
4. Record a fair four-target timing baseline, optimize native passes and concurrency, then rerun affected correctness checks and timings.

Preserve the Scala Golden Gate oracle and both compiler paths during migration.
Port semantic contracts, not Scala ASTs. Delegate ordinary FIRRTL-to-Verilog
lowering to CIRCT. Keep target generation and host bridge Chisel elaboration
until independently justified changes remove them. Preserve annotation identity
through lowering, channel/token ordering, reset/memory behavior, bridge ABI and
collateral. FAME5, enabled AutoCounter/Print, generalized multi-clock models and
NIC support are mandatory concurrent implementation streams. Preserve the fixed
four-target milestone while adding bounded feature-enabled acceptance cases.
QSFP/partition transport is deferred and a new dialect remains optional.

Useful source anchors: `midas/passes/MidasTransforms.scala`,
`midas/passes/fame/FAMETransform.scala`, the native compiler's `lib/`, `tools/`
and focused `test/` suites, plus `dissertation.md`/`dissertation.pdf` in this repo.

## Implementation checkpoint: iteration 2 (2026-10-21)

CHIA iteration 1 stopped before comparison because `rocket.sfc_artifacts` was
empty. No exact selected F2 golden comparison has run. `verification.yaml`
continues to fail closed on missing complete artifacts and adapters.

The BaseF2 boundary implementation from iteration 1 remains in place. Iteration
2 adds one-to-seven-lane TracerV token/trigger/header support (including optional
writeback channels), explicit JSON-null annotation export, and default
LatencyPipe queue sizing from `min(16, maxFlight)`. Eight focused compiler tests
passed; the main compiler builds. Scoped evidence and limitations are in native
`test/TracerVWidthSupport.md` and `test/FASEDCapacity.md`.

`test/Support/GenerateF2SFCGoldens.py` now prepares fresh, immutable per-attempt
pre-MIDAS inputs and SFC collateral for exactly the four selected F2 recipes.
Its eight fast contract tests and all-four read-only prerequisite check pass.
It uses pinned historical compiler jars with matching scoped Scala/dependency
receipts, never historical U250 design artifacts; independent jar reproduction
remains outside that provenance claim. CHIA has not run actual generation yet.

FRFCFS16GBQuadRankLLC4MB is still explicitly rejected. It requires the real
FR-FCFS DDR3 scheduler, LLC and 32-request functional structures plus its own
MMIO catalog; accepting default LatencyPipe fragments would be incorrect.
Other host-memory recorded-profile guards still need selected-input validation.
Next work needs complete paired artifacts/semantic comparison and these actual
compiler contracts. Jasper LEC/SEC, F2 execution and compiler-speed acceptance
remain unestablished.

## Implementation checkpoint: iteration 3 (2026-10-21)

CHIA iteration 2 again stopped at the empty Rocket SFC artifact list, before
any compiler comparison. The semantic node now has a real preparation command
in `verification.yaml`: initialize Conda and the manager environment, then run
the fixed-four SFC generator under `/scratch/jfx/f2-sfc-goldens`. Preparation
runs inside CHIA before candidate identity, reuses only validated completed
receipts, and requests only missing targets. It does not establish parity;
missing CIRCT outputs and the unset semantic comparison adapter still block.
No actual SFC generation was launched from the implementation turn.

Eight host-memory/deinterleaver stages now validate positive `maxFlight`
instead of requiring ten. Their buffers, arbitration and physical geometry are
independent of this target-total limit; five capacity profiles produce identical
hardware/annotations, and eight focused tests pass. See `test/FASEDCapacity.md`.
Target constructor geometry remains distinct from physical F2 memory geometry.

The native compiler also contains a reusable selected-organization DRAM bank
state tracker, porting the Scala bank legality/state and monotonic timing
downcounters. It is a scheduler primitive, not a complete FRFCFS implementation;
the platform configuration remains explicitly rejected until rank/refresh,
scheduling, LLC, functional capacities and MMIO are connected and validated.
Exact-four semantic, LEC/SEC, F2 and compiler-speed results remain pending.

## Implementation checkpoint: native DDR3 rank timing (2026-10-21)

The latest available CHIA result (`20261021T124058Z-pid215481-iter45-full`)
now finds complete immutable SFC receipts for all four exact targets. It still
blocks at the absent Rocket CIRCT artifact list; no full-target comparison has
run. Existing source/worktree changes and all goldens are preserved.

The native compiler now contains the selected DDR3 rank tracker, composing eight
bank trackers with cross-rank CAS delays, four-activation timing and refresh
state/interval priority. Its emitted RTL passes 92,947-cycle, 55-field comparisons
against each of the exact MegaBoomV3, plain Shuttle and Saturn Shuttle SFC rank
cones. Five focused CTests pass, including 25,000 rank assertion checks. This is
component evidence, not target parity; FRFCFS16GBQuadRankLLC4MB remains rejected.
See `sim/goldengate-circt/test/FASEDRankStateTracker.md` for commands and scope.

One 180-second capped exact-input Rocket diagnostic reached FASED host-memory
buffering without rejection, but did not emit RTL. Its 5 GB of repeated debug
snapshots motivated an optional MLIR serializer: normal baseline compilation
skips debug MLIR; `--emit-debug-snapshots` restores it, and diagnostic/stop modes
retain it. All passes and required collateral remain. A focused test verifies
identical diagnostics and 18 non-MLIR outputs across the snapshot switch.
Direct make wiring now requests Release optimization. Neither change has a
matched compiler-speed result. The development compiler builds successfully.

Full-target semantics, separate LEC/SEC, F2 runtime and four-target compiler
speed remain incomplete. CHIA owns those long jobs. Next compiler work is the
refresh arbiter and FRFCFS scheduler, followed by backend/LLC/MMIO integration;
complete candidate outputs are still needed to configure comparison adapters.

## Implementation checkpoint: composed FRFCFS command core (2026-10-21)

The current CHIA feedback (`20261021T143428Z-pid563173-iter1-full`) confirms all
four immutable SFC preparations and blocks at the incomplete Rocket CIRCT
artifact inventory. No full-target gate has passed. BaseF2 boundary support is
already present; the selected FRFCFS/LLC profile is still explicitly rejected.

The native compiler now contains a composed command scheduler with four rank
trackers, refresh arbitration and an eight-entry collapsing reference window.
It implements legal oldest CAS, refresh/row-command priority, same-cycle entry
updates and admission, auto-precharge, bank-ready bookkeeping and backend
metadata/latency. Seven focused CTests pass, including 10,000 latency-wrap
checks. The compiler builds successfully with eight parallel build jobs.

Read-only immutable SFC comparisons pass for MegaBoomV3, plain Shuttle and
Saturn Shuttle: refresh arbitration covers 118,304 vectors; the reference
window covers 32,377 cycles per target. Composed scheduler replay checks all
three receipts against their two distinct complete SFC timing-model dependency
cones, with 51,007 cycles per distinct cone. Each run accepts 1,288 references,
covers all sixteen window-depth/page-policy phases, exercises ACT/PRE/CASW/
CASR/REF and host stalls, and drains completely. The untouched SFC model supplies
only the decoded requests, backend availability and cycle to the native core;
native commands/readiness/state are independently compared. This is scoped
component simulation, not four-target parity or formal proof. Normalizations,
assumptions, exact commands and evidence are in
`sim/goldengate-circt/test/FASEDFRFCFSCommandScheduler.md`.

Next implement the actual four-entry modular-deadline read/write backend,
then connect transaction arbitration, address decode, LLC, power counters and
MMIO. Extracted capacities must remain target-specific: Rocket/plain Shuttle
have maxFlight 10 and ID reuse one; MegaBoomV3/Saturn have maxFlight 20 and ID
reuse two. Scala's default 32 is capped by these constructors. Existing LBP
response queues have different timing and cannot replace the DDR3 backend.

All SFC goldens and unrelated shared edits remain intact. Complete CIRCT
design/collateral inventories, semantic/Jasper/F2/speed adapters and their
results are still missing. No long CHIA verification jobs were duplicated and
no compiler-speed claim is made.

## Implementation checkpoint: composed FRFCFS timing controller (2026-10-21)

Iteration 3 retains the same CHIA blocker (`iter2-full`): no complete Rocket
CIRCT RTL/collateral inventory, hence no full semantic candidate. BaseF2 support
is present; the selected DDR3 platform is still rejected. Its diagnostic now
accurately names controller integration, constructor-derived functional limits
and LLC/MMIO rather than claiming every selected target has 32 read/write slots.

The native compiler now builds an eight-entry unified transaction frontend,
independent four-entry modular-deadline read/write backend and a composed timing
controller. The controller owns the raw-address decoder, 64-bit target-cycle
counter, frontend/core/backend feedback and completion timing. It preserves
10/20-write constructor capacities, round-robin unreset state, reset-time queue
payload writes, fire-gated state/reset, full-width programmable shifts and rank
mask priority. Six focused CTests pass; the compiler builds with eight jobs.

Immutable component comparisons pass for MegaBoomV3, plain Shuttle and Saturn
Shuttle. Backend replay covers 56,061 cycles per target; frontend replay covers
60,114/60,104 cycles for its two distinct cones. The composed controller checks
54,007 cycles per distinct full SFC timing-model cone with all three exact
receipt bindings. Only raw LLC requests, parent target pending counts and LLC
response readiness are replayed; native arbitration, decode, cycle, command/rank
state and backend feedback are independently computed and compared. Each run
accepts 1,170 references, exercises 639 full-backend cycles, 3,825 blocked-frontend
cycles, 295 competing-request cycles, all sixteen depth/page-policy phases and
ACT/PRE/CASW/CASR/REF, and drains completely. Target traffic respects one request
per channel/ID, with at most eight reads/four writes. A corrected functional
response adapter retains IDs across stalls; earlier combinational-ID-echo
attempts are superseded. No SFC source or immutable output was edited.

All four golden receipt/manifest/full SFC RTL hashes were rechecked unchanged.
Exact commands, scope, assumptions and report locations are documented in
`sim/goldengate-circt/test/FASEDFRFCFSController.md`; final composed evidence is
`/scratch/jfx/goldengate-circt-f2-build/iteration3-controller-legal/result.json`.
These are component simulations, not full-target semantics, Jasper LEC/SEC,
F2 runtime or a compiler-speed result. The development build is unoptimized;
no compiler timing claim is made.

Next port the actual selected LLC (synchronous metadata/programming-time BRAM
initialization, eight MSHRs, hazards, eviction/refill ordering and replacement
LFSR), then integrate functional admission/release, MMIO/power/statistics and
AutoILA/platform behavior. Complete four-target candidate outputs are still
needed before configuring the real CHIA verification adapters. No long CHIA
verification jobs were duplicated, and all implementation subagents were joined.

## Implementation checkpoint: selected LLC timing model (2026-10-21)

Iteration 4 retains CHIA's missing complete Rocket CIRCT design/collateral
inventory blocker (`iter3-full`). The native compiler now builds the selected
eight-way, 4096-set, eight-MSHR LLC: metadata RAM and replacement LFSR, registered
MSHR enables/hazards/completions, four two-entry queues, the eight-state FSM,
occupancy, dirty eviction/refill ordering and statistics. Tags preserve the
selected 32-bit storage versus 35-bit comparison distinction. Synchronous reset
does not clear the programming-initialized metadata RAM.

A complete standalone differential found and fixed an actual host-stall bug:
ordinary latency-one FIRRTL memory lowering invalidated metadata output on a
disabled read, while emitted SFC holds its output registers. Native metadata now
uses explicit unreset capture/hold registers over an asynchronous read, retaining
one target-cycle latency and old-data read/write behavior. Seven focused CTests
and the compiler build pass. Metadata tests cover 18,000 vectors; MSHR tests
cover 24,017 transitions.

Immutable comparisons bind MegaBoomV3, plain Shuttle and Saturn Shuttle to their
exact selected F2 SFC attempts. MSHR replay passes 54,007 cycles per distinct
full-model cone. The complete LLC independently computes its state from shared
external requests/completions; all three targets share one literal five-module
LLC cone. It compares 107 fields before/after edges for 77,131 cycles and drains
3,544 reads and 1,273 writes, covering 3,772 hits, 1,045 misses, 290 dirty
evictions, 998 refills, all states, programmable geometry/MSHR counts, hazards,
high addresses and host/backing-memory stalls. Its response-consumer-ready and
zero-initial-state assumptions remain explicit. The existing SFC eight-byte
block read-miss refill hang is preserved and compared for 700 cycles before a
recovery reset; this does not establish liveness for that corner.

Final evidence is
`/scratch/jfx/goldengate-circt-f2-build/iteration4-llc-model-verified/result.json`;
exact commands, hashes, tests and limits are documented in
`sim/goldengate-circt/test/FASEDLLCModel.md`. Scala sources and all four immutable
golden receipt/manifest/full-RTL identities remain unchanged. No duplicate long
CHIA verification jobs were launched; all implementation subagents were joined.

Next integrate the LLC with the existing FRFCFS controller, constructor-specific
functional admission/release, MMIO/power/statistics and AutoILA/platform wiring.
The selected DDR3 platform remains explicitly rejected until that integration
is complete. Complete four-target outputs and semantic/Jasper/F2/speed passes
are still missing; no full-target parity, formal or compiler-speed claim is made.

## Implementation checkpoint: composed FRFCFS target timing (2026-10-21)

Iteration 5 retains CHIA's missing complete Rocket CIRCT artifact inventory
blocker (`iter4-full`). The native compiler now composes LLC, DRAM controller,
AXI response release, target pending counters, occupancy histograms and rank
power counters. Native handshake counts and commands/state drive native feedback;
no oracle internal signal supplies a native input. The selected capacities are
10 for Shuttle and 20 for MegaBoomV3/Saturn. Target-fire/reset gating, multibeat
release and protocol assertions preserve the scoped Scala contracts.

All three exact DDR3 golden bindings pass against their two distinct literal
28-module timing-model cones: 495 fields before/after edges for 58,712 cycles per
cone, with 789 reads/394 writes fully drained, multibeat traffic, independent R/B
stalls, all eight LLC states and all DRAM command types. The standalone releaser
also covers simultaneous pipe replacement. Seven focused CTests and the main
compiler build pass. All four immutable receipt/manifest/full-RTL and archived
pre-MIDAS hashes remain unchanged; Scala is untouched.

Commands, counts, initialization/traffic assumptions and normalizations are in
`sim/goldengate-circt/test/FASEDFRFCFSTimingModel.md`; final component evidence is
`/scratch/jfx/goldengate-circt-f2-build/iteration5-timing-model-verified/result.json`.
This comparison excludes actual functional data memory, host/MMIO and platform
composition. It is not full-target parity, formal proof or compiler timing.

Next propagate explicit platform-derived functional capacity (FRFCFS base 32,
LBP base 16, both capped by edge maxFlight), audit selected idReuse two host
buffering/deinterleaving, then connect the distinct FRFCFS admission/MMIO/header
and AutoILA/platform contracts. The selected DDR3 platform remains explicitly
rejected. Complete four-target artifacts and semantic/Jasper/F2/speed passes are
still absent. Adapters intentionally remain unset until complete candidate
outputs exist. No long CHIA verification jobs were duplicated; all implementation
subagents were joined. The development build is unoptimized and no speed claim
is made.


## Implementation checkpoint: functional capacity and ID reuse (2026-10-21)

Iteration 6 retains CHIA's missing complete Rocket candidate inventory blocker
(`iter5-full`). Functional host outstanding counts, ingress AW FIFO, credits,
ordering, issue and deadlock checks now share an explicit platform capacity
profile. Base 16 remains the default; validated base 32 supports the selected
FRFCFS contract, capped by target maxFlight to 10/20. Malformed metadata rejects
atomically. The LBP request-limit MMIO bank rejects base 32 because those words
are absent from FRFCFS. The retained bridge constructor does not encode platform
BaseParams: the selected CLI still rejects FRFCFS and has not installed the
explicit capacity handoff. This is functional-pass progress toward integration,
not complete selected platform support.

Eight downstream host stages now accept validated idReuse one/two without
changing hardware. Immutable selected DDR3 host-buffer, deinterleaver, crossbar
and output-buffer cones are literally identical; the four fixed targets match
after documented module-name/comment/whitespace normalization. Existing native
read-egress storage and write acknowledgement widths already support reuse two.
A reproducible read-only source/cone audit records these contracts separately
from native behavioral testing.

All four exact golden bindings pass the new host-counter/AW FIFO differential:
15 fields before/after edges, 30,239 Rocket cycles, 30,469 MegaBoom/Saturn cycles
(shared literal cone, separate receipts) and 30,239 Shuttle cycles. Directed
traffic reaches and holds capacity twenty beyond sixteen, with wrap, stalls,
resets and concurrent pushes/pops. Total distinct-cone replay is 90,947 cycles.
The finite component comparison excludes complete functional memory composition,
host/MMIO ABI and platform behavior. Its report binds current sources, fixtures,
tools, immutable pre-MIDAS resources and SFC RTL; all four goldens remain intact.

See `sim/goldengate-circt/test/FASEDCapacity.md` for changed files, exact commands,
checks and assumptions. Final scoped evidence is
`/scratch/jfx/goldengate-circt-f2-build/iteration6-functional-capacity-verified/result.json`
and `iteration6-host-id-reuse-verified.json` in that build directory. The Scala
oracle is unchanged. No long CHIA verification jobs were duplicated.

Next connect platform-derived capacity before early control allocation, compose
FRFCFS functional/timing release and its distinct MMIO/header/catalog, then
AutoILA/platform wiring. Keep the selected configuration guard until integration
is complete. Four-target semantic acceptance, separate Jasper LEC/SEC, F2 runtime
and matched compiler-speed results remain missing; adapters intentionally remain
unset. No full-target parity or speed claim is made.

Final iteration-6 checks: the main compiler and sixteen test executables build
successfully with eight jobs. All eighteen distinct focused CTests pass across
the initial run and targeted retest. The initial ingress-order failure exposed
capacity-sixteen RAM/address assumptions in its interpreter; corrected exact
depth and derived address widths pass the larger-bank cases without changing
production queue sizing. Python compilation and root/submodule whitespace
checks pass. Both scoped reports still match all 76 recorded current
source/tool/fixture/oracle/recipe/golden file hashes. All implementation
subagents are joined and no implementation jobs remain. These checks do not
resolve the missing complete four-target candidate inventory or acceptance
nodes described above.

## Implementation checkpoint: FRFCFS bridge and distinct MMIO (2026-10-21)

Iteration 7 retains CHIA's missing complete Rocket candidate inventory blocker
(`iter6-full`). Native compiler components now attach the selected composed
FRFCFS model after functional WriteEgress and close its timing/admission/release
boundaries directly. Explicit base32 and exact selected 10/1 or 20/2
maxFlight/idReuse profiles are required. Detached preflight preserves previously
materialized bank handles and module bodies. The CLI guard remains in place:
these components have not yet been selected in the full compiler path.

A distinct 68-word MMIO bank now implements all selected FRFCFS programming,
status, power, histogram and error words. Thirty-two writable registers retain
full 32-bit host readback and exact narrower model consumers. Host reset and
target-fire/reset ownership remain separate. The selected SFC B-error/R-response
capture quirk and unused transaction-depth word are preserved. Native attachment
connects all bank/model/functional relaxed-mode and host-response observations,
validates exact register identities/permissions, and reuses the common MCRFile
transport. Early allocation selects 512 bytes for FRFCFS versus 128 for LBP,
rejecting malformed or mixed profiles without mutating outputs.

All three exact DDR3 golden bindings pass the fresh register differential:
25,017 cycles, 236 fields before/after edges, 107 resets and 100,816 word writes,
including every strobe and differing R/B errors. The receipts share one literal
SFC parent cone; target/recipe/input identities remain separate. Independently
supplied model status, zero overflow histogram bins, ready assumptions and
illegal read-only-write exclusions are explicit. This is register-component
evidence, not complete timing/functional composition or whole-target semantics.
Rocket remains the distinct LBP target and is not substituted into this replay.

The compiler and focused executables build with eight jobs. All seven distinct
focused CTests pass across the initial run and targeted retest: MMIO, bridge,
integration, composed timing model, address decode, clock/control and F2 config.
The bridge covers four wrapper/capacity profiles and 4,096 wiring/reset/release
cycles each; integration checks both early catalogs, all bank routes, 68-lane
transport and atomic negative cases. An initialized integration fixture also
passes firtool flow/initialization and Verilator lint. Initial failures and their
corrections remain in the logs; the passing attachment retest supersedes them.

Exact changed files, commands, assumptions and checks are documented in
`sim/goldengate-circt/test/FASEDFRFCFSBridge.md` and `FASEDFRFCFSMMIO.md`.
Fresh component evidence is
`/scratch/jfx/goldengate-circt-f2-build/iteration7-frfcfs-mmio-final/result.json`.
All 69 report file identities remain current. All four immutable receipt,
manifest and full-RTL identities match the prior checkpoint, and archived
pre-MIDAS and SFC artifact hash checks pass; see `iteration7-golden-identities.json`
in that build directory. Scala oracle sources are unchanged. Python compilation
and root/submodule whitespace checks pass. All implementation agents are joined
and no implementation jobs remain; no long CHIA verification job was duplicated.

Next install the explicit platform capacity before early allocation in the CLI,
select the FRFCFS functional/timing/MMIO branch, emit its exact driver header,
and finish AutoILA/platform integration. Complete four-target RTL/collateral
inventories are still absent, so real acceptance adapters intentionally remain
unset. Four-target semantics, separate Jasper LEC and SEC, F2 artifacts/runtime
and matched compiler-speed results remain incomplete. The development build is
unoptimized and no speed claim is made.


## Iteration 8 checkpoint: selected CLI and FASED driver ABI

The real baseline CLI now selects `FRFCFS16GBQuadRankLLC4MB_BaseF2Config`.
It installs explicit base capacity 32 before early register allocation and
functional construction, materializes the 68-word FRFCFS bank, and attaches
native DDR3/LLC timing and MMIO after WriteEgress. Common MCR/control transport,
host memory and platform assembly follow that branch. The LBP branch retains
capacity 16 and its six fragments. Duplicate memory fragments and FRFCFS/U250
combinations reject; the F2 enabled-Print diagnostic remains unsupported.

FASED header emission now validates and emits the exact selected 68-read/
32-write, 512-byte driver map, in addition to LBP's 21-read/5-write map. Live
bank/model/control paths, clocks/resets, response observations, narrow selectors,
constructor widths and selected 10/1 or 20/2 edge profiles are checked before
emission. The normalized constructor ABI passes independently against all four
immutable exact target headers, including Rocket's base-128 LBP map and the
three DDR3 base-zero maps. Only C++ string syntax, integer suffixes, whitespace
and line continuations are normalized; ordered names, addresses, permissions,
widget index, CSV name and memory-size exponent remain exact.

Six focused CTests pass: FASED header, actual functional pipeline, F2 CLI config,
AutoILA wiring, XDC emission and F1/F2 shim. The pipeline starts with a declared
constant-output target/channel scaffold, then runs actual TokenEngine through
functional ingress/egress, native FRFCFS, bank attachment and control mapping
for both selected profiles. It checks 10/20 AW entries, 128/256 read-buffer
entries, stable early bank handles, closed configuration/readiness inputs and
68-word/512-byte allocation. Both profiles also pass fresh firtool lowering and
Verilator lint. The initial direct fixture export replayed historical raw
BridgeIO annotations and failed; export now removes only the cloned archive,
matching production emission, retaining attached annotations and all hardware.
The failed logs and passing retests remain archived. A test-only NamedAttrList
API compile error was corrected; the final compiler and test builds pass.

The read-only AutoILA/XDC audit finds zero debug selections and the exact SFC
skip message for all three DDR3 inputs; no ILA artifacts are emitted by those
SFC runs. Existing native enabled/no-probe behavior and F2 hierarchy handling
cover those contracts. This audit is not an emitted candidate XDC comparison.
All four immutable receipt/manifest/RTL identities and archived artifact and
pre-MIDAS records validate and equal iteration 7. Scala oracle sources remain
unchanged; root/submodule whitespace checks pass. All implementation agents
are joined and no long CHIA verification job was duplicated.

Changes and exact commands are documented in
`sim/goldengate-circt/test/F2FRFCFSPipeline.md` and `F2FRFCFSHeader.md`.
Evidence lives under `/scratch/jfx/goldengate-circt-f2-build/`:
`iteration8-ctest.log`, `iteration8-header-four-goldens.log`,
`iteration8-pipeline-syntax-final.json`, `iteration8-autoila-xdc-audit.json`,
`iteration8-golden-identities.json` and `iteration8-implementation-result.json`.

Next provide CHIA-owned candidate generation/inventories from all four archived
pre-MIDAS input pairs and resolve actual whole-target compiler failures, then
install the complete syntax/normalized semantic adapter. Candidate inventories
and semantic/Jasper/F2/speed adapters remain unset, so the repeated semantic
inventory block remains explicit. No full target parity, LEC/SEC, bitstream/
runtime or speed result is claimed. The development build is unoptimized.

## Iteration 9 checkpoint: CHIA-owned native candidate preparation

The repeated missing-CIRCT-inventory block prevented verification from exercising
iteration 8's compiler change. `verification_nodes.py` now builds the configured
Release compiler, generates missing/stale candidates and validates complete
receipts before freezing artifact identity. `verification.yaml` configures this
real preparation command; its semantic comparison, Jasper, F2 and speed adapters
remain null. The build uses eight concurrent C++ jobs for this manager's RAM;
each native transformation inherits the worker's full available CPU affinity.

`GenerateF2CIRCTCandidates.py` replays exactly the four immutable pre-MIDAS input
pairs with the complete selected F2 platform strings. It tracks source membership,
binary/config/input/resource content, appends blackboxes like the production make
rule, and inventories every generated file. It requires all primary outputs and
annotation boundaries, retaining raw RTL. Only defines/XDC sidecars may be empty
among required files. Failed attempts retain diagnostics and partial inventories;
only complete successful attempts publish an atomic current receipt. Changed
sources/binary/inputs invalidate reuse; altered output membership/content rejects.
Generation continues across target failures to return all available diagnostics.
Preparation and receipt reuse never count as semantic or speed acceptance.

Ten synthetic candidate-contract tests, 31 verification-node tests and five loop
graph tests pass under the configured Conda Python. The Release build command's
shell syntax and CMake configuration pass; no compiler build or real target job
was launched here. A read-only check validates prerequisites for all four exact
recipes against the real immutable SFC archives, including complete output/input/
resource hashes. Their receipt, manifest and RTL identities still match iteration
8. No new SFC/CIRCT semantic comparison was executed. The prior four-target scoped
FASED header ABI evidence remains archived; it is not whole-target parity.

Evidence: `/scratch/jfx/goldengate-circt-f2-build/iteration9-implementation-result.json`,
`iteration9-candidate-prerequisites.json`, `iteration9-golden-identities.json`,
`iteration9-candidate-generator-test.log`, `iteration9-verification-nodes-test.log`,
`iteration9-loop-graph-test.log` and `iteration9-release-configure.json`.
The preparation contract is documented in
`sim/goldengate-circt/test/Support/GenerateF2CIRCTCandidates.md`.

Next CHIA builds the Release compiler and runs all four matched-input native
compilations. Use their actual failures to select the next compiler fix, then
install the full syntax/normalized semantic adapter. Full semantic parity,
separate LEC/SEC, F2 bitstream/runtime and faster matched compiler timing remain
unestablished. Scala oracle and immutable goldens are preserved; all implementation
agents are joined before this call returns.

## Iteration 10 checkpoint: optimized register catalog construction

CHIA iteration 9 built Release successfully, then all four exact native target
compilations exited with SIGSEGV at the common ClockBridge construction boundary.
The focused Release ClockBridge test reproduced a crash through StringAttr
hashing. `SingleClockBridge.cpp`, `ResetPulseBridge.cpp` and `SimulationMaster.cpp`
enumerated temporary initializer-list-backed ArrayRefs whose storage expired
before loop execution under C++17. Static constant name arrays now give all
three catalogs valid storage. Their tests assert exact ordered names in addition
to existing offsets/permissions and behavior checks. No pass or functionality
was removed, and no platform/recipe fragment changed.

The compiler and eight focused tests build/pass with Release `-O3 -DNDEBUG`:
single-clock bridge, reset-pulse bridge, simulation master, their three header
consumers, F2 CLI configuration and debug-snapshot policy. A scoped audit compares
the asserted metadata to all twelve corresponding register tables in the four
immutable SFC headers. Names, relative four-byte offsets and struct indices
match. Absolute per-target addresses are recorded, not generalized to synthetic
component allocations. Permissions are supported by Scala helpers and component
behavior checks; the headers themselves do not encode them. All four archived
receipt/manifest/full-RTL identities match iteration 9, and complete archived
inputs/resources/artifacts revalidate. Scala oracle sources are unchanged.

See `sim/goldengate-circt/test/ReleaseRegisterCatalogs.md` for commands and scope.
Evidence is `/scratch/jfx/goldengate-circt-f2-build/iteration10-implementation-result.json`,
`iteration10-clock-before-gdb.log`, `iteration10-release-ctest.log`,
`iteration10-header-ctest.log`, `iteration10-register-contract-audit.json` and
`iteration10-golden-identities.json`. Root/submodule whitespace checks pass;
unrelated shared edits and immutable goldens are preserved. All implementation
agents are joined before return; no long verification job was duplicated.

Next CHIA retries all four archived-input compilations with the corrected native
compiler. Their actual results select the next compiler fix. No full-target
semantic comparison, separate LEC/SEC, F2 bitstream/runtime or matched compiler
speed result is established, and the corresponding acceptance adapters remain
intentionally unset.

## Iteration 11 checkpoint: retained FRFCFS vector observations

CHIA iteration 10 successfully emits the Rocket candidate. The three exact DDR3
targets reach FPGA shell assembly and reject an unrecognized diagnostic output.
The native post-MMIO FRFCFS component pipeline retains four passive vector
observations: scheduler entries, nested rank/bank status, LLC MSHRs and metadata.
`FPGATopShell.cpp` now validates those vector leaves recursively. Bundle flips,
known-width UInt leaves, allowed diagnostic names and output direction remain
checked; public AXI vector fields still reject. No state, MMIO route, public
connection, inner module body or selected recipe changed.

The Release compiler and four focused CTests build/pass. Regression coverage
includes both public platform contracts, nested hidden inputs and clocks/resets,
unknown widths, empty extra public vectors, exact forwarding and 33 atomic
rejections. Both actual FRFCFS component profiles (10/1 and 20/2) provide four
post-MMIO diagnostic types that pass individually through verified F2 boundary
scaffolds. A reconstructed pre-fix shell implementation rejects the first type.
This is component evidence, not a complete selected-target replay.

A scoped static comparison matches all 121 public scalar ports of each of the
four immutable SFC FPGATops to the native F2 contract by name, width and direction.
Only aggregate-name flattening, implicit scalar widths and order/comments are
normalized. Complete archived prerequisites revalidate; all four receipt,
manifest and full-RTL identities equal iteration 10. This does not compare the
emitted candidate designs or establish behavioral/annotation/MMIO/XDC parity.

See `sim/goldengate-circt/test/F2ShellDiagnostics.md`. Evidence is under
`/scratch/jfx/goldengate-circt-f2-build/`: `iteration11-implementation-result.json`,
`iteration11-release-ctest.log`, `iteration11-before-reproduction.json`,
`iteration11-frfcfs-shell-result.json` and `iteration11-shell-golden-audit.json`.
Scala oracle and immutable goldens are unchanged; unrelated edits are preserved.
All agents are joined before return and no long CHIA verification is duplicated.

Next CHIA retries the four archived-input native compilations. Full normalized
semantic comparison, independent Jasper LEC and SEC, F2 bitstream/runtime and
faster matched FireSim compiler timing remain unestablished. Acceptance adapters
remain intentionally unset.

## Iteration 12 checkpoint: honor optional SRAM extraction configuration

CHIA iteration 11 successfully emitted complete RTL/collateral candidates for
all four exact F2 targets. Its semantic node then blocked on the unconfigured
comparison adapter. Read-only comparisons of those archived candidates expose
a compiler configuration mismatch: baseline compilation unconditionally ran
LabelSRAMModels, while Scala gates it on GenerateMultiCycleRamModels, default
false in every supported selected platform profile. Rocket gained one register
file model, ten channels and twenty model-port annotations; MegaBoom gained two
models, 94 channels and 188 model-port annotations. These differences are not
accepted normalizations.

The baseline CLI now honors that disabled default, retaining memory selections
and inline memory behavior. Explicit optional SRAM diagnostic modes remain
available, and unsupported platform fragments still reject before import. The
Release compiler builds; four focused CTests pass. A new actual-pipeline probe
checks depth-31 UInt64 read/write geometry, masks, inline identity and annotation
retention for both selected F2 compiler profiles and U250, then verifies both
explicit extraction commands. Its deliberate later FAME rejection limits the
test to this configuration boundary. The pre-fix probe extracts the memory and
fails at an introduced model connection; that evidence is retained.

Scoped archived comparisons match all four public interfaces (121 FPGATop and
174 F1Shim ports each), 155 shim routes each, eight bridge constructors each
and 43/71/51/51 bridge-extraction channels. Shuttle/Saturn model-port boundaries
match all six compared annotation classes. Each header matches eleven driver
constructors and 88 fixed-layout ABI assertions. Defines and synthesis XDC
match; implementation XDC matches after stanza ordering and a verified live
hierarchy substitution. This is evidence about the previous emitted candidate,
not fresh full-target validation of the compiler change or behavioral parity.

`CompareF2Semantics.py` now supplies CHIA a real partial static comparison
adapter. It validates exact recipes, paired archived inputs, complete receipts
and output inventories before comparing measured contracts. Its report always
sets passed/syntax/semantic false, with concrete differences and unresolved
syntax, stateful/memory/token/MMIO and remaining collateral obligations. It
cannot release downstream nodes. Jasper LEC/SEC, F2 and speed adapters remain
unset. No full verification job was duplicated in this implementation call.

See `sim/goldengate-circt/test/BaselineSRAMConfig.md` and
`test/Support/CompareF2Semantics.md`. Evidence is under
`/scratch/jfx/goldengate-circt-f2-build/iteration12-*`, including the focused
CTest log, annotation/interface/collateral audits, adapter smoke and immutable
golden validation. All four receipt/manifest/full-RTL identities equal iteration
11; complete archived prerequisites revalidate. Scala oracle sources and
unrelated edits are preserved. All implementation agents join before return.

Next CHIA regenerates the affected candidates and runs the configured static
adapter; use its actual differences to drive the next compiler fix and extend
the missing semantic checks. Four-target correctness, independent LEC and SEC,
F2 artifact/runtime success and faster matched firesim_compiler timing remain
incomplete. No compiler-speed claim is made.

## Iteration 13 checkpoint: fresh SRAM evidence and paired syntax checks

CHIA iteration 12 regenerated all four exact F2 candidates with the corrected
optional-SRAM configuration. Fresh read-only comparisons now match all six
inferred model/channel annotation classes for Rocket, MegaBoomV3, plain Shuttle
and Saturn Shuttle. Bridge extraction, F1Shim/FPGATop public ports, driver
constructors/fixed-layout assertions, TargetConfig, defines and synthesis XDC
also match on all four. This validates the previous configuration fix at those
boundaries; whole-design equivalence remains incomplete. No additional compiler
transformation source changed in this iteration.

The existing partial adapter now runs actual paired complete-RTL Verilator lint
and generated-driver C++ syntax checks under CHIA. Both RTLs use identical
platform defines, their verified archived resource bodies and the same functional
BUFGCE simulation library. Both driver probes exercise real framework includes,
layout checks, TargetConfig and constructor phases. Commands, versions, hashes,
logs and timeout outcomes are retained. Seventeen focused tests pass, including
small real tool success/failure fixtures, missing-module rejection and timeout
termination. Thirty-one verification-node tests pass. Full-target syntax jobs
remain CHIA-owned and were not duplicated here. Syntax can pass independently;
semantic/passed remain false while the documented behavioral obligations remain.
Driver include dependencies are recorded in results; comprehensive invalidation
for external include-tree changes remains a prerequisite for eventual acceptance.

Fresh actual-RTL scoped checks match 3/2/2/1 clock-gate enable equations,
45/72/52/51 host-reset flag initializations and 42/70/50/50 finishing-cycle domain
expressions. A Rocket ResetPulse bridge plus local MMIO transport differential
passes 50,000 randomized cycles with independent channel backpressure and
repeated host resets (zero initial unreset state, single-beat offsets 0/4).
The corrected Rocket inline register file passes 8,192 cycles / 32,768 read
comparisons against extracted immutable SFC memory logic and an independent
state model, including 4,146 writes and 1,233 read/write collision cycles.
All 31 words are initialized by legal writes; valid addresses, constant read
enables and write mask match the original wiring. Upstream CPU/FAME behavior,
uninitialized/out-of-range memory behavior and full handshake dependency cones
remain outside these component checks. No mismatch was found in these scopes.

Evidence is under `/scratch/jfx/goldengate-circt-f2-build/iteration13-*`:
`semantic-static-all4.json`, `clock-state-audit.json`, `reset-bridge-probe/`,
`register-file-probe/`, `paired-syntax/`, adapter/node test logs and immutable
receipt validation. All 88/87/87/87 archived prerequisite records revalidate;
receipt/manifest/RTL identities equal iteration 12. Scala oracle sources and
immutable SFC artifacts are unchanged. Unrelated shared edits are preserved;
all implementation agents are joined before return.

Next CHIA runs the configured full paired syntax commands and retains concrete
compiler/driver diagnostics for the next implementation fix. Stateful target,
bridge and memory equivalence, remaining annotations/collateral, resolved
implementation XDC, independent Jasper LEC/SEC, F2 bitstream/runtime success and
faster matched firesim_compiler timing remain incomplete. No timing claim is made.

## Iteration 14 checkpoint: emit missing SimulationMapping collateral

CHIA iteration 13 generated all four exact F2 candidates. Its Rocket adapter
passed actual paired complete RTL lint and generated-driver syntax checks;
semantic acceptance remained false. Inspection found a concrete compiler output
mismatch on every archived native candidate: missing `.peer2peer.const.yaml`
and `.partition.const.h`, both emitted by Scala SimulationMapping. No runtime
failure on the selected unpartitioned recipes is inferred from this absence.

Native CPU header preparation now emits the address map from the same ordered,
live validated stream allocations as the driver constructor. The metasim header
emitter publishes the empty partition header after validating the supported
physical boundary. Duplicate sidecars reject before mutation. AutoILA preserves
the captured map across temporary driver-header analysis, and Print composition
forwards it transactionally. Direct Make wiring copies and requires both files;
the candidate generator requires them in each complete output inventory.
The existing semantic adapter compares their supported normalized contracts and
still keeps semantic/passed false pending the remaining behavioral obligations.

The Release compiler builds. Four focused CTests pass (CPU stream header,
metasim header, Print binding and nested CPU allocation); nineteen adapter tests
and ten candidate-contract tests pass. Real native output emission on small
stream and F2 physical fixtures matches both sidecar contracts against all four
immutable goldens: TracerV base 0 / capacity 6144 / width 64 bytes, and no
partition constants. This is component evidence, not fresh complete candidate
or AutoILA validation. Integration caught and corrected a function-signature
edit and a null-attribute test lookup; failed logs remain alongside final passes.

Read-only Rocket emitted-RTL ClockBridge plus local MCR and SimulationMaster
plus local MCR differentials each pass 50,000 randomized cycles. Scope is valid
register offsets, zero-length transactions, independent handshake/backpressure,
repeated host reset and deterministic zero initialization of unreset state.
ClockBridge records 12,524 reads / 8,902 writes / 25,068 tokens; SimulationMaster
records 12,615 reads / 8,849 writes. These bounded component checks do not prove
whole-target behavior. Memory inspection found a conditional nonzero-random
initialization correlation difference in ClockBridge snapshots; the normal
FireSim simulation flow forces RANDOM=32'b0, so no active bug is established
there. Arbitrary initialization correspondence remains an explicit obligation.

Evidence is under `/scratch/jfx/goldengate-circt-f2-build/iteration14-*`, notably
`sidecars-before.json`, `sidecars-fixture-comparison.json`, `ctest-passed.log`,
`clock-bridge-probe/result.json`, `master-bridge-probe/result.json`,
`memory-audit.json`, `golden-validation.json` and `implementation-result.json`.
All 88/87/87/87 immutable prerequisite records revalidate and their identities
match iteration 13. Scala oracle sources, SFC goldens and unrelated shared edits
are preserved. All implementation agents joined; no full verification job was
duplicated in this call.

Next CHIA regenerates the four exact candidates, validates new collateral through
the actual normal/AutoILA compiler paths and runs the configured paired adapter.
Full stateful/memory/token/MMIO behavior, remaining annotations and resolved XDC,
independent Jasper LEC/SEC, successful F2 bitstream/runtime evidence and faster
matched firesim_compiler timing remain incomplete. No speed claim is made.

## Iteration 15 checkpoint: ClockBridge snapshot initialization

CHIA iteration 14 generated all four exact F2 candidates. Rocket's paired RTL
and driver syntax checks passed, as did the newly emitted stream map and empty
partition header comparisons. Semantic acceptance remained false. Read-only
inspection established a concrete conditional mismatch in both ClockBridge
snapshot registers on every target: SFC repeats one 32-bit RANDOM word in each
64-bit shadow register, while CIRCT's packed initializer permits unequal halves.
The normal FireSim flow defines RANDOM=32'b0, masking this difference; no normal
workload failure is inferred.

The producer now explicitly marks only its two unreset UInt64 snapshots. The
backend captures their identities before lowering, validates the corresponding
SV declarations and guarded packed initializers, then captures a separate fresh
32-bit RANDOM word per snapshot and duplicates that word into both halves.
Unknown shapes reject before mutation/publication. Unmarked registers and live
latch/hold updates remain intact. This restores the snapshots' allowed initial
states; it does not reproduce the global deterministic PRNG call sequence or
establish arbitrary whole-design initialization correspondence.

The Release compiler and focused backend build pass. Backend tests cover
nonaligned packing, unmarked state, malformed shapes, atomic rejection,
idempotence and preservation of existing output on failure. The SingleClockBridge
CTest passes. A small emitted FIRRTL fixture passes initialization and 32 live
latch/hold cycles with random seeds 1/7/42/20261021 and with RANDOM=32'b0.
The genuine unmarked standard-lowering control fails the same initialization
check with seed 1, demonstrating that the check detects the original discrepancy.
These are component checks; no selected complete target was regenerated here.

The partial comparison adapter now resolves each implementation-XDC clock path
through actual emitted RTL to BUFGCE/O, rejecting unknown Tcl, ambiguous or
missing objects and changed clock parameters. All eight constraints (3/2/2/1)
match across the four archived pairs after only resolved parent spelling and
snippet order normalization. All other supported static contracts also match,
including bridge/model annotations, interface/driver ABI and both new sidecars.
Twenty-three adapter tests pass. Synthesis/link pin persistence, the external F2
shell prefix and actual Vivado timing remain explicit obligations; semantic and
passed remain false.

A read-only Rocket UART serial/queue/register-bank/local-MCR differential passes
200,000 cycles, including 50,098 tokens, six transmitted bytes and four received
bytes. Scope is deterministic zero initialization and valid payloads. Random
local bus stress does not hold inputs stable while stalled and therefore does
not establish legal AXI integration or full-target equivalence. The TracerV audit
was source inspection only.

Evidence is under `/scratch/jfx/goldengate-circt-f2-build/iteration15-*`, especially
`snapshot-implementation.json`, `snapshot-smoke/result.json`,
`snapshot-smoke/negative-result.json`, `static-all4.json`, `uart-probe/result.json`,
`golden-validation.json` and `implementation-result.json`. All 88/87/87/87
immutable prerequisite records revalidate with unchanged receipt/manifest/RTL
identities. Scala oracle sources, immutable SFC goldens and unrelated shared
edits are preserved. All implementation workers completed before return.

Next CHIA regenerates the four exact candidates with the scoped initialization
fix and runs the paired adapter. Full target/bridge/memory/token/MMIO/reset
semantics, remaining annotation contracts, independent Jasper LEC and SEC,
successful F2 bitstream/runtime evidence and faster matched firesim_compiler
timing remain incomplete. No long verification jobs or timing claims were
duplicated in this call.

## Iteration 16 checkpoint: padded memory initialization

CHIA iteration 15 generated all four exact F2 candidates. Rocket's paired syntax
and supported static comparisons passed, but semantic acceptance remained false.
A read-only audit found a concrete initialization mismatch: CIRCT's 65-bit Rocket
FPU register file uses a 96-bit temporary plus low-65-bit extraction, bypassing
the existing memory initialization normalizer. Each row receives new random
chunks, whereas SFC samples one replicated 32-bit RANDOM word for all rows.
The three DDR3 targets have the same issue in their 512-row, 48-bit PTW L2 TLB
arrays. Shape inspection finds 20/24/16/16 such padded module definitions; that
count does not establish a separate SFC correspondence for every array.
Normal FireSim RANDOM=32'b0 masks this conditional mismatch.

The backend now recognizes a temporary padded to the next 32-bit boundary,
validates its low-word extraction and optional bounded low-zero chunk index,
and preserves the existing shared seed across chunks and rows. Complete shape,
bounds, guard and consumer checks precede each rewrite. Unsupported patterns
remain unchanged. Existing exact-width patterns, loop bounds, live memory
ports and source IR remain intact. This restores the scoped SFC initial-state
constraint; global deterministic PRNG sequence/cross-object correspondence and
all other memory behavior remain separate obligations.

The Release compiler and focused backend tests pass. New tests cover padded
33/35/65/531-bit words, malformed extraction, excess padding, incomplete fills
and real FIRRTL 32x65 memory lowering. With explicit memory initialization
macros enabled, the emitted fixture passes random seeds 1/7/42/20261021 and
RANDOM=32'b0: each checks replicated chunks, all 32 initial rows and 256 live
write/read/hold cycles. The verbatim archived Rocket register-file module fails
the same initial-state constraint with seed 1, demonstrating detection of the
original discrepancy. No complete target was regenerated in this call.

Scoped emitted-component differentials also pass: DDR3 bank state has 50,000
cycles; each of the four TracerV engine/trigger/local-MCR pairs has 200,000
cycles; the actual 6144x512 stream queue/RAM pair has 100,000 cycles including
fill/full/drain/wrap/reset. These use deterministic zero initialization. MMIO
source stress changes inputs while stalled and therefore does not establish
legal complete AXI integration. No defect was found in these component checks.

Read-only comparisons on the four iteration-15 archives still match all
supported static contracts, including bridge/model annotations, public and
driver interfaces, stream/partition sidecars and resolved implementation-XDC
constraints. All 88/87/87/87 immutable prerequisite records revalidate with
unchanged receipt/manifest/RTL identities. Scala oracle sources and unrelated
shared edits are preserved. All three workers completed before return.
Evidence is under `/scratch/jfx/goldengate-circt-f2-build/iteration16-*`, especially
`implementation-result.json`, `memory-implementation.json`,
`memory-smoke/positive-result.json`, `memory-smoke/negative-result.json`,
`padded-memory-audit.json`, `static-all4.json`, `golden-validation.json`,
`fased-audit/result.json` and `tracerv-audit.json`.

Next CHIA regenerates all four candidates and runs the paired adapter. Full
stateful target/bridge/memory/token/MMIO/reset semantics, remaining annotation
contracts, post-synthesis XDC persistence/timing, separate Jasper LEC and SEC,
successful F2 bitstream/runtime evidence and faster matched firesim_compiler
timing remain incomplete. No full verification jobs were duplicated and no
speed claim is made.

## Iteration 17 checkpoint: outlined memory initialization dependencies

CHIA iteration 16 generated all four exact F2 candidates. Rocket's paired
syntax and supported static contracts passed; complete semantic acceptance
remained false. Inspection of those actual native archives found 5/5/5/4
outlined RAM modules before the first definition of ENABLE_INITIAL_MEM_.
CIRCT attached its standard initialization fragments to the parent but omitted
their references on these earlier memory modules. FireSim's normal simulation
defines enable memory randomization and set RANDOM=32'b0 without defining that
helper, so preprocessing silently removes the affected initializers.

The native backend now attaches the existing memory and general initialization
fragment references to the outlined memories before single-file export. It
validates generated memory guards, standard guarded defaults, symbol resolution
and reference order before any mutation. Unknown shapes reject atomically.
Existing custom fragment order, user macro overrides, module order, blackbox
text and source IR are preserved. Synthesis and disabled-memory-init guards
retain their behavior. This fixes initializer activation; full target behavior
and global random-sequence correspondence remain separate obligations.

The Release compiler builds successfully. Focused backend tests cover actual
32x65 FIRRTL lowering, fragment ordering,
idempotence, preserved custom fragments and atomic rejection of malformed
guards/defaults/references. The emitted fixture passes six simulation runs:
random seeds 1/7/42/20261021, RANDOM=32'b0 and a nonzero sentinel. Each checks
replicated chunks, all 32 initial rows and 256 live write/read/hold cycles using
production randomization switches without helper overrides. The original
emitted fixture fails the sentinel check because its initializer is absent.
Five preprocessing modes also pass, including synthesis suppression, disabled
memory initialization and preserved RANDOM override. These are backend fixture
checks; no complete target was regenerated in this call.

Read-only supported static comparisons on the four CHIA iteration-16 archives
match bridge/model annotations, public interfaces, driver ABI, defines,
stream/partition collateral and resolved synthesis/implementation constraints.
All 88/87/87/87 immutable prerequisite records revalidate with unchanged
receipt/manifest/RTL identities. The prior padded-memory fix is present in all
76 identified module definitions, a structural check rather than individual
array equivalence. Scala oracle sources and unrelated shared edits are preserved.

Scoped fresh emitted BlockDev component compositions pass 4,024 cycles for each
exact target, including stable stalled MMIO, full data-queue target reset and a
complete sector write/read with checked response data. Fresh Rocket and
MegaBoomV3 host AXI component compositions each pass 120,000 cycles with stable
stalled payloads, ordered bursts and response IDs; a changed response-router ID
fails as expected. BlockDev uses zero initialization; host AXI does not observe
RAM contents before a valid write. These checks exclude complete platform,
address translation, downstream buffering and FASED token integration. They do
not establish full-target equivalence.

All three workers completed before return. Evidence is under
`/scratch/jfx/goldengate-circt-f2-build/iteration17-*`, especially
`implementation-result.json`, `init-header-audit.json`,
`init-header-preprocess.json`, `init-smoke/positive-result.json`,
`init-smoke/negative-result.json`, `static-all4.json`, `golden-validation.json`,
`blockdev-audit-notes.json` and `host-axi-audit.json`.

Next CHIA regenerates all four candidates and runs the paired adapter. Full
stateful target/bridge/memory/token/MMIO/reset semantics, remaining annotation
contracts, post-synthesis XDC persistence/timing, separate Jasper LEC and SEC,
successful F2 bitstream/runtime evidence and faster matched firesim_compiler
timing remain incomplete. No full verification jobs were duplicated and no
speed claim is made.

## Iteration 18 checkpoint: synchronous memory fragment validation

CHIA iteration 17 rebuilt successfully but all four exact candidates failed
RTL emission with `unrecognized outlined memory initialization guard`. The
iteration-17 helper was too narrow: real synchronous memories add guarded
random temporaries and a third register-initialization block alongside the
memory initializer. The first such archived module in every target is the
same combined-port `cc_dir_1024x23`, with `_RW0_raddr_d0`, `_RW0_ren_d0` and
`_RW0_rmode_d0` pipeline state. Archived and failed manifests bind to identical
immutable pre-MIDAS FIRRTL, annotations and resource inventories. Inspection
finds 8/23/9/9 synchronous module definitions; these counts are shape evidence,
not independent memory equivalence results.

The backend now recognizes the optional paired RANDOMIZE_REG_INIT guards:
one declarations-only guard containing one or more generated temporaries and
one procedural guard after the memory initializer. It preserves their contents
and order. Unknown guards, extra side effects in declarations, unpaired blocks
and all previously rejected malformed fragments still reject before mutation.
Existing asynchronous memory behavior and source IR remain intact. This fixes
the compiler rejection introduced in iteration 17 without disabling fragment
attachment or memory/register initialization.

The Release compiler and focused backend suite pass. Fifteen helper cases cover
valid asynchronous/synchronous forms, custom fragment preservation, idempotence
and atomic rejection. Actual FIRRTL backend fixtures cover all six combinations
of read latency 0/1/2 and write latency 1/2 at 32x65, plus the combined-port
1024x23 geometry found in the selected targets. The combined fixture retains
all three guarded pipeline initializers. Small firtool reproductions additionally
cover multiple readers and a depth-one optimized memory.

Two native emitted synchronous fixtures (read/write latency 1/1 and 2/2) each
pass random seeds 1/7/42/20261021, zero RANDOM and a nonzero sentinel: twelve
runs total. Each checks all 32 initial rows and 512 latency-aware live cycles
under production initialization defines without helper overrides. Arbitrary
initial write-pipeline state is flushed and all rows are overwritten before
live scoreboarding. These tests do not establish full-target or arbitrary
global PRNG correspondence. Five preprocessing cases separately check memory
only, registers only, both, neither and synthesis suppression, retaining the
user RANDOM override.

All 88/87/87/87 immutable SFC prerequisite records revalidate unchanged. The
previous four archived supported-static comparisons are reused only after
revalidating 41 input/output path records and all complete-marker manifest
identities. Their supported annotations/interfaces/driver ABI/sidecar/XDC
matches remain valid for those archives; complete semantic and passed remain
false. No current complete candidate or new paired full semantic result is
claimed. Scala oracle sources, immutable goldens and unrelated shared edits
are preserved. The read-only analysis worker completed with no outstanding jobs.

Evidence is under `/scratch/jfx/goldengate-circt-f2-build/iteration18-*`, especially
`implementation-result.json`, `archive-guard-audit.json`,
`generated-memory-shapes.json`, `backend-test.log`, `memory-smoke/result.json`,
`preprocess.json`, `golden-validation.json` and `static-reuse.json`.

Next CHIA regenerates the four exact candidates and reruns paired verification.
Full stateful semantic parity, remaining annotation and post-synthesis XDC
obligations, separate Jasper LEC and SEC, F2 build/runtime evidence and faster
matched firesim_compiler timing remain incomplete. No duplicate long
verification jobs were launched and no speed claim is made.

## Iteration 19 checkpoint: initialization prolog activation order

CHIA iteration 18 generated all four exact F2 candidates. Rocket's paired
syntax and supported static contracts pass, but full semantic acceptance
remains false. A read-only preprocessor audit found a concrete conditional
initialization defect in every native archive: the general header selects an
empty INIT_RANDOM_PROLOG_ before the memory header activates RANDOMIZE.
With MEM-only randomization and a custom INIT_RANDOM hook, native raw/final
outputs execute zero hooks; SFC raw/final outputs retain 284/384/321/395 hooks
for Rocket/MegaBoomV3/Shuttle/Saturn Shuttle respectively. These counts prove
suppression, not equal initializer correspondence. A real synchronous backend
fixture exposes the converse REG-only case when its memory header comes first.

The backend now establishes both guarded MEM/REG activation rules at the start
of the existing general initialization fragment, before prolog selection. It
clones the validated memory activation guard and changes only the second
condition to RANDOMIZE_REG_INIT. Existing fragments, initializer bodies, module
order, user defaults/overrides and source IR remain intact. Validation precedes
mutation; partially prepared, unguarded or late activation definitions reject
atomically. Repeated calls remain unchanged. This addresses MEM/REG activation;
other initialization flags and global PRNG correspondence are not proven here.

The Release compiler and focused backend suite pass. Eighteen helper cases
cover preparation, idempotence, custom references and atomic rejection. Actual
backend fixtures retain all six 32x65 read/write latency combinations and the
1024x23 combined-port pipeline guards. Seven stateful hook probes pass across
MEM-only/REG-only/both/neither, both complete header orders and a custom prolog
override. Enabled memory rows and pipeline register initialization retain a
nonzero RANDOM override. Two prior-backend controls fail the hook assertion as
expected. Seven preprocessing modes additionally verify synthesis suppression,
custom delay and prolog overrides. These are bounded backend checks, not
full-target equivalence results.

Fresh read-only supported-static comparisons on the four CHIA iteration-18
archives match interfaces, driver ABI, supported bridge/model annotations,
stream/partition collateral and resolved XDC contracts; semantic and passed
remain false. All 88/87/87/87 immutable SFC prerequisite records revalidate
with unchanged receipt/manifest/RTL identities. Independent annotation and
clock/reset audits found no further established compiler defect; remaining
annotation contracts are not waived. Scala oracle sources, immutable goldens
and unrelated shared edits are preserved. Both workers completed with no
outstanding jobs.

Evidence is under `/scratch/jfx/goldengate-circt-f2-build/iteration19-*`, especially
`implementation-result.json`, `prolog-archive-audit.json`,
`prolog-smoke/result.json`, `preprocess.json`, `backend-test.log`,
`static-all4.json`, `golden-validation.json`, `annotations-normalization.json`
and `schedule-audit.json`.

Next CHIA regenerates the four exact candidates and reruns paired verification.
Full stateful semantics, remaining annotation/post-synthesis XDC obligations,
separate Jasper LEC and SEC, F2 build/runtime evidence and faster matched
firesim_compiler timing remain incomplete. No duplicate long verification jobs
were launched and no speed claim is made.

## Iteration 20 checkpoint: annotation serialization allocations

CHIA iteration 19's native compiler exited successfully on all four targets.
MegaBoomV3 emitted/finalized 198 artifacts, all matching its failed manifest's
inventory, but the provenance guard correctly rejected a concurrent change to
`verification_nodes.py`. The native sources, binary, selected/sample recipes
and immutable golden inputs did not change during that generation. The current
`verification.yaml` also differs from those runs, so their receipts are stale.
The failed attempt remains failed; do not republish its output as accepted.

The compiler now borrows StringAttr values and dictionary-key storage when
building its temporary JSON tree, avoiding redundant owning-string copies.
MLIR attributes are immutable and context-owned; serialization and JSON-tree
destruction complete synchronously before the circuit/context can be destroyed
or mutated. LLVM's borrowed constructors retain the same UTF-8 validation and
fallback as their owning counterparts. Every annotation boundary, verifier,
transformation, output file and filtering rule remains in place. The archived
four-target emissions contain approximately 40/59/42/63 MB of value-string bytes
and 737k/850k/678k/809k dictionary keys respectively. These are allocation
opportunity counts, not wall-time measurements or a compiler speed result.

The Release compiler and focused annotation/XDC test pass. New regression cases
exercise nested null/bool/integer/float values, Unicode and escaped keys,
embedded NULs, a 1 MiB escaped body, FAME filtering, unchanged source attributes
and a subsequent changed annotation boundary. Independent before/after probes
use CIRCT's JSON importer and the actual old/new native emitters on sixteen
archived boundaries: SFC bridge-extraction/wrap-top and native bridge-extraction/
final-XDC annotations for each exact target. All parsed JSON values, list
membership/order and output bytes remain identical. MegaBoom's failed archive
is used only as read-only test data. These checks validate this serialization
change, not complete target semantics or pre-build acceptance.

All 88/87/87/87 immutable SFC prerequisite records revalidate unchanged against
iteration 19. Scala oracle sources and unrelated shared edits are preserved.
Both analysis workers completed without source edits or outstanding jobs.
Evidence is under `/scratch/jfx/goldengate-circt-f2-build/iteration20-*`, especially
`implementation-result.json`, `identity-audit.json`, `performance-audit.json`,
`backend-test.log`, `annotation-roundtrip.json`, `build-probe-commands.json`
and `golden-validation.json`.

Next CHIA regenerates all four candidates against stable prerequisites and
collects every pre-build syntax/static-semantic verdict. The plan's corrected
pre-build gate can pass independently of Jasper and Vivado; downstream stateful
proof, post-synthesis XDC/timing, F2 runtime and matched firesim_compiler timing
remain mandatory for completion. No long verification jobs were duplicated,
no adapters were changed here, and no speed claim is made.

## Recovery checkpoint: streaming annotation output and compiler-time replay

The supplied iteration-20 graph passed all four pre-build syntax/static-semantic
comparisons. Jasper and F2 failed before tool execution because the output
selector counted golden RTL in the candidate provenance inventory. The existing
`downstream_common.py` subtree selector fixes that failure; a focused regression
and read-only Jasper preflight pass. No new LEC/SEC, bitstream or runtime result
is inferred. BaseF2 and the exact selected FRFCFS profiles are already implemented.

Native annotation emission now validates the selected immutable MLIR attributes
and writes them through LLVM's streaming JSON writer, avoiding a second complete
JSON container tree at every compiler boundary. All output files, filtering,
validation, field/array ordering, two-space formatting and trailing newlines
remain. LLVM still formats every scalar. Invalid UTF-8 dictionary keys take the
original converter path to preserve release normalization/collision behavior;
debug UTF-8 assertions remain before opening the destination. Closing and
checking the output also makes write failures explicit compiler failures.
There is no cache or cross-boundary state. MLIR threading was already enabled;
no unsafe concurrent whole-circuit mutation was introduced.

The Release compiler builds. Focused debug and optimized annotation/XDC tests
pass, including nested/empty values, numeric limits, large/escaped/Unicode/NUL
strings, FAME filtering, changed boundaries, eight malformed-payload rejections
that preserve an existing file, unwritable paths and `/dev/full`. The optimized
test also checks invalid-UTF-8 key collisions. Sixteen old/new native-emitter
comparisons pass on the exact four targets' archived SFC bridge-extraction/
wrap-top and native bridge-extraction/final-XDC boundaries: parsed JSON values,
array order and output bytes agree. This validates serialization, not a new
full-target transformation or equivalence result. All 88/87/87/87 immutable SFC
prerequisite records revalidate; receipt/manifest/full-RTL identities equal the
previous checkpoint. Scala oracle sources and shared edits are preserved.

`run_speed.py` now provides CHIA's real `timing_scope: firesim_compiler` adapter.
It replays the pinned SFC launch and current native compiler on the same frozen
pre-MIDAS FIRRTL/annotations with equal CPU affinity, fresh output directories
and complete resource append. An initial fresh-process pair is separate from
three alternating repeated pairs; every SFC invocation includes fresh JVM
startup/JIT and compiler-invoked bridge elaboration. Filesystem warming is
recorded without claiming cache eviction or a persistent warmed JVM. Per-launch
RSS, sampled thread counts, exact commands and timings are retained. Complete
same-compiler output reproduction and paired RTL/driver syntax run outside the
timers; any mismatch fails closed. Only output-directory metadata references
may normalize; RTL/header/XDC/FIRRTL differences cannot. Real repetitions remain
CHIA-owned and have not run in this implementation call.
Ten focused adapter tests and 35 verification-node tests pass. The four actual
archived compiler command pairs reconstruct without launching transformations.

Evidence is under
`/scratch/jfx/goldengate-circt-f2-build/iteration21-streaming-annotations/`,
especially `implementation-result.json`, `archive-roundtrip.json`,
`golden-validation.json`, `final-focused-debug.log`, `final-focused-release.log`
and `final-release-build.log`. Adapter tests and command-profile reconstruction
evidence are retained under the sibling `iteration21-speed-adapter/` directory.
All implementation workers finish before return; no long verification jobs
were duplicated. No compiler-speed improvement or acceptance pass is claimed.

Next CHIA regenerates all four candidates against the changed compiler/config,
reruns the paired pre-build gate, and fans out the existing Jasper/F2 adapters.
Separate actual LEC and SEC, successful F2 artifacts/workload markers, and faster
matched compiler medians for all four remain mandatory and incomplete. The speed
node runs after the downstream join to avoid local Vivado contention.

## Recovery iteration 2: asynchronous read compatibility and retained Jasper diagnostics (2026-10-22)

The preceding CHIA graph is
`automation/chia_firesim_circt/logs/verification/20261021T204512Z-pid2577644-iter1-full/graph.json`.
For candidate `57d1df95d91d5c514a708a7d8d9e90f948c1ed1b15f4c44fa4fa59313d12ab96`,
all four paired pre-build static comparisons passed. Three alternating repeated
complete compiler pairs, after a separately recorded initial pair, also passed
reproduction/syntax checks and the `firesim_compiler` median gate:

| Target | SFC median seconds | CIRCT median seconds | CIRCT / SFC |
| --- | ---: | ---: | ---: |
| Rocket | 49.706262 | 26.011154 | 0.523297 |
| MegaBoomV3 | 527.661448 | 141.990294 | 0.269094 |
| Shuttle | 56.163638 | 31.119252 | 0.554082 |
| Saturn Shuttle | 92.451284 | 48.088821 | 0.520153 |

All runs used 16 CPUs with matched affinity; every SFC launch used a fresh JVM,
including startup/JIT and compiler-invoked bridge elaboration. These are actual
previous-candidate results, not acceptance of this changed compiler. Exact
commands, repetitions, initial-versus-repeated conditions, thread observations,
bridge costs and per-launch RSS remain in the four speed adapter reports.

The remote Rocket `lec`-labelled job ran for 12,623 seconds before its 100 GiB
memory guard observed 101 GiB and killed Jasper. Its interface check failed:
8 implementation undrivens, 421 implementation X assignments, out-of-range
indices on both sides and roughly 194k reset-X elements per side. The retained
Jasper log contains 410 one-cycle counterexample records. Authoritative final
signoff/status/target reports and exported counterexample traces are missing.
This is incomplete, with unresolved counterexamples, not a successful proof.
The state-matching invocation uses the SEC application; neither compare-point
reports nor its name establish the required separate LEC and SEC signoffs.

`run_jasper.py` now collects regular files from Jasper's result archive after
validating the entire member inventory. Six legitimate internal log/cache
aliases are inventoried and skipped without creating or dereferencing links.
Traversal, escaping links, special files and conflicting paths still reject;
proof criteria are unchanged. Launcher status and `RESULT.txt` are retained
before signoff parsing. The existing failed archive was recovered into a new
diagnostic directory, without modifying its original reports or rerunning
Jasper. Seven collector tests and eight existing downstream/signoff tests pass.
See `/scratch/jfx/goldengate-circt-f2-build/iteration22-jasper-archive/result.json`.

The smallest compiler change from this investigation matches SFC's asynchronous
memory read emission. `SimulatorRTL.cpp` normalizes only zero-latency read-only
ports with explicit subfield users in the private backend clone. Original
enable drivers remain on a dead sink; the memory input becomes constant one,
so the outlined module's disabled-read X arm is unreachable. Whole-port
aggregate users retain the existing backend behavior, avoiding competing
drivers. Synchronous readers, read/write ports, write enables and source
annotations are unchanged. The immutable Rocket SFC golden has six corresponding
variable-enabled native readers in three inclusive-cache ListBuffer modules.
For example, `ListBuffer_PutBufferAEntry_q40_e40` assigns
`tail_MPORT_4_en = io_pop_valid` at golden RTL line 68235 but reads the array
unconditionally at line 68238; its next-pointer reader does likewise at
68246/68249. The other modules are `ListBuffer_PutBufferCEntry_q2_e16` and
`ListBuffer_QueuedRequest_q21_e33`. Exact read-only cone inspection is retained
in `exact-sfc-async-cones.json` and `archived-async-read-instances.json`.
The global firtool ignore-read-enable option is deliberately unused because it
also changes synchronous pipelines, including a latency-two combined-port
case in the installed version. This fix does not establish that the previous
counterexamples are solved, nor does it resolve all reset/state correspondence.

Focused backend tests cover variable enables across read latencies 0/1/2 and
write latencies 1/2, aggregate fallback, combined-port initialization and source
preservation. A native emitted-RTL simulation checks 2,048 direct-array oracle
reads, including 503 disabled cycles and 150 same-address writes. Evidence is
under `/scratch/jfx/goldengate-circt-f2-build/iteration22-memory-read-enable/`.
Old/new backend fixtures differ only in the two asynchronous enable connections
and their location comments; all four synchronous-reader fixtures, the
aggregate fallback and the combined-port fixture reproduce byte-identically.
That reproduction check scopes the untouched lowering; it is not a substitute
for normalized semantic comparison of complete target outputs.
Immutable SFC receipts validate 88/87/87/87 records for the four selected targets,
with unchanged receipt/manifest/RTL identities; no goldens were regenerated.

The previous F2 branch built its native driver but stopped at localhost SSH
before Vivado. `CHIA_OPERATOR_HANDOFF_READ_BEFORE_FIXING_SSH.md` records the
operator's successful key/rsync check. Preserve that repaired environment and
let CHIA retry. No F2 bitstream, AGFI or target workload success is established.
Next CHIA regenerates the four candidates and replays all current gates;
Jasper's separate complete proofs, F2 artifact/runtime evidence and current
candidate speed results remain required. No long verification jobs were
launched by this implementation call.

## Recovery iteration 3: synchronous RW data capture (2026-10-22)

The supplied iteration-2 graph, under
`automation/chia_firesim_circt/logs/verification/20261021T204512Z-pid2577644-iter2-full/`,
passed all four pre-build static comparisons for candidate
`b50ca303c508dc198091fed4293c7faadbcdaf08883d4e8d091e3d1be42b515a`.
It does not establish full equivalence. Rocket's retained Jasper run again
failed interface checks and reached 101 GiB against its old 100 GiB guard.
Its 410 one-cycle counterexample records have no saved traces or final signoff.
Unqualified invalid payload/state correspondence remains unresolved; those
records do not demonstrate failure of the preceding asynchronous-read fix.

The next concrete oracle difference is Directory's synchronous RW output.
The actual archived input declares undefined RUW. Immutable Rocket SFC RTL
lines 70868–70873 capture `cc_dir_0[addr]` under enable regardless of write mode,
and otherwise hold the output register. Native RTL previously pipelined address,
enable and mode, returning X when disabled or writing. Valid target reads are
already qualified; this is an SFC-choice compatibility refinement, not a
demonstrated corruption of valid target transactions or resolution of top CEX.

`SimulatorRTL.cpp` now rewrites only single-port ground UInt memories with
read/write latencies 1/1, one mask bit and undefined/old RUW in the private
backend clone. An asynchronous reader feeds a data register enabled by the
original port enable; the writer keeps the same clock, address, data, mode and
mask. Thus writes capture pre-write data, masked writes still capture data,
and disabled cycles hold it. Original leaf drivers/observations remain on a
duplex proxy. Initialization/prefix metadata is retained. New RUW, longer
latencies, other port/type/mask shapes, aggregate uses, annotations and symbols
retain their original lowering. Original source IR and its archive are unchanged.

The Release compiler and focused native backend suite pass. Nine combined-port
1024x23 fixtures cover the two refinements and seven excluded variants. A
before/after native-backend probe reproduces all seven excluded variants and
all seven existing separate-reader fixtures byte-identically; only the two
eligible RW fixtures change. This is preservation evidence for tiny same-compiler
fixtures, not cross-compiler byte-equality acceptance. Eight emitted-RTL runs
(undefined/old RUW, four seeds) match the memory update cone extracted from the
immutable SFC golden over 32,768 random cycles and 40 directed cycles, after
overwriting all rows. Cases include enabled reads/writes, zero write masks and
disabled holds after storage/address changes. The previous lowering fails the
same old-data assertion as a negative control. Arbitrary initial-state/global
PRNG correspondence and full-target equivalence remain unproven.
All 88/87/87/87 immutable SFC prerequisite records revalidate with unchanged
receipt/manifest/full-RTL identities. Evidence and exact commands are under
`/scratch/jfx/goldengate-circt-f2-build/iteration23-sync-readwrite/`, including
`implementation-result.json`, `oracle-extraction.json`, `simulation.json`,
`baseline-control.json`, `backend-comparison.json` and `golden-validation.json`.

Both Jasper YAML invocations now explicitly use `--max-mem-gb 128 --jobs 1`,
and the adapter defaults to 128. Per-run evidence records remote physical and
available RAM, swap and CPU count, plus the existing flow's rounded sampled
process-tree peak RSS or an explicit unknown. This five-second watchdog is
measured in GiB, not a reservation; 128 GiB exceeds the host's roughly 122.7 GiB
physical RAM. One ProofGrid job does not cap OS threads. Memory aborts remain
incomplete. The current state-matching invocation still uses the SEC application;
its compare-point reports do not establish the separate required LEC proof.

The prior F2 branch actually built Rocket AGFI `agfi-0ab8a52ce81acce80`, with
`firesim/rocket/firesim/build-receipt.json` in that graph. Infrasetup then failed
because driver packaging looked in generated RTL instead of `sim/output/f2`.
The operator already repaired the resolver and validated all nine packaging
inputs; those edits are preserved. No workload success is established, and the
previous candidate's AGFI cannot verify this changed compiler.

Speed passed Rocket, then failed MegaBoom's final accepted-identity check.
All eight retained MegaBoom output directories reproduce accepted outputs and
all four prior paired syntax checks passed. An operator F2 adapter edit occurred
during timing; the original final snapshot is unavailable, so no claim that it
was the only changed field is made. `run_speed.py` now retains start/end snapshots
and exact changed leaf paths while still rejecting any drift. Its RSS test now
accounts for inherited parent RSS. All 99 automation tests pass, including 12
speed and 20 Jasper/archive/downstream tests. Diagnostic evidence is under
`iteration23-speed-diagnostics/`. No failed timing is credited as a speed pass.

All three workers finished, with no background proof/build jobs. Scala oracle
sources, immutable SFC goldens and unrelated shared edits are preserved. No long
verification job was duplicated. Next CHIA regenerates the exact four candidates
and all paired comparisons, then dispatches Jasper/F2 and finally matched timing.
Separate complete LEC and SEC, current-candidate F2 target markers/exits, and
faster complete compiler medians on all four remain mandatory and incomplete.

## Recovery iteration 4: reach inferred RW ports (2026-10-22)

The supplied iteration-3 graph is
`automation/chia_firesim_circt/logs/verification/20261021T204512Z-pid2577644-iter3-full/graph.json`,
candidate `08da1c138f57560fae6f4712fe3aa270cfe01da2dac8916fc98a397d00b09cd5`.
All four pre-build comparisons, actual F2 scalar smoke runs and matched compiler
timings passed. These are previous-candidate results, not acceptance of this
source change. The read-only artifact/runtime/timing audit is retained at
`/scratch/jfx/goldengate-circt-f2-build/iteration24-evidence/result.json`.

| Exact target | Previous F2 AGFI | SFC / CIRCT compiler median seconds |
| --- | --- | --- |
| RocketConfig | agfi-00e7f7dba498e21e3 | 49.394871 / 26.157456 |
| MegaBoomV3Config | agfi-084c9b3b7e65f0ba9 | 539.174567 / 141.539031 |
| ShuttleConfig | agfi-02d5a3571cfb6286a | 57.118781 / 31.591608 |
| FireSimGENV256D128ShuttleConfig | agfi-0286fff1f532d4e7b | 92.786434 / 48.654277 |

Each F2 UART confirms the matching AGFI and F2 PCI identity, checksum
`0x4d81038d93425513`, target PASS, driver PASS and command exit zero. Frozen
candidate and delivered RTL/XDC/defines hashes match; build farms were localhost,
run farms were F2, metasim was disabled and cleanup completed. This terminating
scalar smoke establishes no Linux/vector workload coverage. The audit retains
exact build receipts, runtime paths, tar/DCP hashes and commands. Timing scope
is `firesim_compiler`, with 16 affinity CPUs, three alternating repetitions and
separate initial fresh-process runs. Every SFC run includes a new JVM, startup,
JIT and compiler-invoked bridge elaboration; no warmed-JVM or cold-OS-cache claim
is made. Full-output reproduction and syntax checks passed. Per-launch maximum
SFC/CIRCT RSS KiB was 3025648/594052, 18413196/2164536, 3359772/679492 and
5567368/1104608 respectively. Native peak observed OS threads was 33 despite
16-CPU affinity; no claim that affinity limits thread count is made.

Jasper now finishes without a memory-guard abort: Rocket LEC sampled 103 GiB
against the 128 GiB guard. It still fails. Boundary targets have 35 proven,
19 undetermined and 30 counterexamples; auto-cutpoint and X/undriven signoff
are incomplete, overall signoff invalid. The saved PCIS B-ID counterexample
compares different unmatched RAM contents while both queues are empty. All
30 boundary CEX names are payloads, but valid/ready equality remains unproven.
This does not establish a valid-transaction compiler error, and no payload
qualification, initialization assumption or proof failure was waived.

The preceding RW fix did not reach the real Directory. Iterations 2 and 3
emitted the same Rocket RTL SHA256
`c18fea5cb1d712022285fce1c53416b369c76f1dfff8ab7d088f1718844891fc`.
Archived FIRRTL declares `smem cc_dir : UInt<23>[8][1024] undefined`.
Golden Gate's LowerTypes leaves eight ground UInt23 memories, each with a
separate reader and writer. Firtool creates the eligible combined `rw` ports
later in InferReadWrite, after the previous compatibility helper ran.

`SimulatorRTL.cpp` now also normalizes eligible newly inferred RW ports at the
CHIRRTLToLowFIRRTL/LowFIRRTLToHW boundary. Every standard compiler stage remains.
Leaf proxies preserve original drivers/observations without introducing a
duplex bundle after LowerTypes. A private attached annotation protects excluded
source RW ports through standard memory recreation/unmasking and is removed
before HW lowering. New data registers have no preset random slot: standard
SeqToSV appends them beyond existing slots. RandomizeRegisterInit is not rerun,
so surviving original register initialization offsets remain unchanged.

Release compilation and the complete focused backend suite pass, including
inferred ports, nonoverlapping initialization slots and a wide-mask fallback
that firtool subsequently unmasks. All 24 existing tiny backend fixtures
reproduce unchanged; this is same-compiler preservation, not SFC parity.
Four inferred-memory simulations match the exact immutable SFC one-bank cone
over 16,384 random and 20 directed cycles after row initialization; the old
compiler fails the same check. An isolated three-module Directory closure
also passes four seeds, 48,032 checked cycles, 384,256 internal bank-output
comparisons and 29,949 read-qualified common result comparisons. It exercises
12,757 writes and 18,083 disabled-read cycles. The old Directory fails the
same bank-output comparison at cycle 1181. All 28 surviving original random
slot assignments remain unchanged; eight new data registers are appended.

The isolated native FIRRTL closure explicitly concretizes the three abstract
Reset input ports to synchronous UInt1, matching the inspected emitted design;
its memory/control text otherwise matches the archived candidate. The SFC
modules and dependencies are extracted from immutable RTL and renamed only in
copies. The test assumes reset and a complete 1024-row wipe, holds writes under
backpressure and avoids illegal multiple hits. It uses two-state simulation
without random-initialization macros. It establishes no initial-state, FAME,
full-target or formal equivalence. Final Directory RTL reproduces the tested
bytes exactly. Exact commands, inputs, hashes, comparisons and negative controls
are retained under
`/scratch/jfx/goldengate-circt-f2-build/iteration24-inferred-rw/`.
All 88/87/87/87 immutable SFC prerequisite records revalidate with unchanged
receipt/manifest/RTL identities. All 101 automation tests pass.

The installed Jasper 2026.06 SEC guide explicitly defines state_matching as
combinational cone equivalence with double-sided state cutpoints. Thus using
the SEC application is not itself evidence that LEC is unavailable. The
separate LEC invocation now additionally retains authoritative detailed
auto-cutpoint signoff through a report-only hook in its staged flow copy and
requires a nonempty inventory of actual proven cutpoint properties. Helper
equality/compare-point reports alone remain insufficient; unknown report
schemas fail closed pending inspection of actual output. The scope correctly
states that --no-init-map suppresses matcher-added mappings, while Jasper may
still add automatic reset-X mappings. No proof settings, state relations or
external flow sources were changed, and no proof was run by this call.

Scala oracle sources, immutable goldens, repaired SSH/driver setup and unrelated
shared edits are preserved. CHIA must regenerate all four current candidates,
repeat paired semantics, fan out separate complete LEC/SEC and F2 execution,
then repeat complete matched compiler timing. Prior successful F2/speed results
cannot accept this candidate; full correctness and speed completion remain open.

## Recovery iteration 5: independent synchronous readers (2026-10-22)

Iteration-4 graph `20261021T204512Z-pid2577644-iter4-full/graph.json`
accepted four static comparisons for identity
`085f7465754f54c3dc90db4bf771e32a75dbc2e5d549a6cbf1fe7c644e29bc83`.
The operator's Linux-policy update then changed `verification.yaml` and
`verification_nodes.py` after that acceptance. These two prerequisite changes
invalidate all four candidate receipts; the subsequent MegaBoom F2 and speed
blocks are correct. The downstream audit reconstructs this policy drift
separately from this iteration's later compiler mutation. Preserve the Linux
update and strict receipt checks; ordinary CHIA preparation must regenerate
the four current receipts. No adapter workaround or stale-identity reuse is
appropriate. Audit evidence is retained at
`/scratch/jfx/goldengate-circt-f2-build/iteration25-downstream-diagnostics/`.

Rocket's iteration-4 F2 process had loaded the old scalar workload before the
policy update. AGFI `agfi-080a9a36ddd810217` reached target/driver PASS and exit
zero, but its UART has no Linux banner, userspace, shutdown-request or powerdown
markers. It does not satisfy current Linux acceptance. No successful Linux
FPGA run has been established. Base images, the new private-rootfs preparation
and repaired manager setup remain preserved.

Rocket LEC still has 35 proven, 19 undetermined and 30 boundary counterexamples;
actual auto-cutpoint evidence has 21 proven and 35 undetermined, with no actual
cutpoint CEX. All 464 failing helper assertions are bound-one comparisons whose
two states have reset-X classification; 350 use exact canonical matches.
Only 56 actual state cutpoints exist, versus 6,072 helper assertions. Individual
helper traces were not exported, so these counts do not establish a cause for
every failure. Helpers include unsafe correspondence guesses such as different
pipeline stages; none was promoted to an assumption or initial-state equality.
Complete separate LEC and SEC remain missing. No proof settings, valid/payload
qualification, initialization assumptions or external flow sources changed.

The real `GGFASEDReadBuffer16x8` uses independent read/write addresses in a
128x65 memory and therefore remains separate after InferReadWrite. Scala
`midas/widgets/Lib.scala` MultiQueue uses SyncReadMem; immutable SFC RTL captures
the pre-edge data. CIRCT's default address/en pipeline observes the updated
array after a same-address write and returns X when a read is disabled. The
native backend now refines eligible independent one-cycle UInt readers with
enabled data registers and asynchronous array reads. Undefined/Old RUW matches
SFC's data capture/hold choice. Writer ports, clocks, masks, memory initialization
and existing register slots remain unchanged. New RUW, longer latencies,
annotated/symbolic memories and unsupported aggregate port uses retain their
backend policy. The rewrite runs after InferReadWrite, retains every standard
compiler stage and does not change the public source IR.

Release compilation and the complete backend suite pass. The 32x65 fixtures
cover Undefined/Old data capture and New/longer-latency fallback. Twenty-six
existing outputs retain identical code after excluding only trailing exporter
source-location comments; only the independent read-one/write-one fixture
changes. This is preservation evidence, not cross-compiler semantic proof.
The actual archived SFC MultiQueue and native queue control pass four seeds,
64,592 checked cycles and 42,102 valid-qualified payload comparisons with an
explicit same-shape native-emitted memory wrapper substitution. All 193 observed
read/write collisions have invalid dequeue output. The old backend fails raw
data equality at such an invalid collision (seed 1, cycle 406); no corruption
of valid transactions is claimed. Simulation assumes synchronous reset, full
memory prefill, legal burst/queue scheduling and two-state execution without
random initialization. This is neither a full-target compile nor formal proof.
Exact extracted RTL, adapters, commands, hashes and negative controls are under
`/scratch/jfx/goldengate-circt-f2-build/iteration25-sync-read/`.

A separate derived-policy memory bench passes 16,400 checks across four seeds,
including 8,182 disabled reads, 4,103 collisions and 4,022 disabled-read writes.
The old backend fails its first directed enabled collision; SFC's pre-edge data
capture/hold policy supplies this oracle. Final emitted 128x65 wrapper bytes
exactly reproduce the tested output. This bench adds enable-control coverage
that the actual queue's constant-one reader does not exercise; it is not an
additional full-target comparison or initialization proof.

`run_jasper.py` now recognizes the actual retained Jasper 2026.06 detailed
auto-cutpoint JSON schema: status-keyed arrays, including bounded and waived
entries. Complete acceptance requires a nonempty unique proven inventory and
every other status array empty. Missing, unknown, malformed and duplicate
entries fail closed. The real 21-proven/35-undetermined report remains failed
and byte-unchanged; synthetic parser controls do not count as proof results.
All 109 automation tests pass. All 88/87/87/87 immutable SFC prerequisite records
revalidate against unchanged receipt/manifest/RTL identities; no golden was
regenerated. Scala oracle sources and unrelated worktree edits remain intact.

CHIA next regenerates the exact four F2 candidates, compares current outputs
against immutable SFC transforms, then dispatches complete separate LEC/SEC and
real Linux boot/poweroff on F2, followed by matched complete compiler timing.
No long verification job was duplicated by this implementation call. All four
current acceptance nodes and the overall loop remain incomplete.

## Recovery iteration 6: faithful runtime cycle limits (2026-10-23)

Iteration-5 graph `20261021T204512Z-pid2577644-iter5-full/graph.json`
passed all four pre-build comparisons for identity
`ad5543aac3b7dc27d52ed1b2b120b44b4011f8307f0c56f4412a6d0b90681cde`.
Rocket's new AGFI `agfi-01e28e7d795500ed6` reached Linux kernel initialization,
but the driver timed out at 19,452,035,058 displayed target cycles in 217.9
seconds, exiting 1 without userspace/poweroff markers. The requested
`+max-cycles=100000000000` was parsed with 32-bit `atoi`, producing
1,215,752,192 on this manager. This was neither exhaustion of the intended
100-billion scheduler-cycle allowance nor the separate 7200-second manager
wall timeout. Preserve the failed runtime evidence.

The actual C++ driver scheduler now uses checked decimal `uint64_t` parsing,
rejecting empty, signed, whitespace, trailing-junk, embedded-NUL and out-of-range
arguments. It also bounds the next credit before addition to avoid wrapping
near `UINT64_MAX`. Existing zero/default/last-duplicate behavior remains.
A standalone regression links the production scheduler source and passes 231
checks under C++20, C++17, NDEBUG and UBSan. It advances to 100 billion cycles
with ten scheduled callbacks beyond 32 bits. Final-credit tests seed the private
current cycle near the 64-bit limit, without changing the production API.
Both the old parser and old credit arithmetic fail their respective controls.

The real Rocket F2 driver was rebuilt through its normal make rule with full
Conda/manager initialization and the retained, hash-validated iteration-5
CIRCT headers. The log contains the scheduler compile and final driver link.
Its SHA256 changed from
`34e3b6413d57e8a9057620f569f8eeae483086a653a91966ad3e64fed1112059` to
`0d3453d195e10cf1b4659f94797d30afa037d011aed90cd7c42873abaaa7b11e`.
This was a driver-only build; no target compilation, Vivado, deployment or
FPGA workload was launched by this call. CHIA must still deploy the rebuilt
driver and establish Linux boot/poweroff on every selected target.

The iteration-5 semantic/speed snapshots differ in exactly one entry: the
new generated `deploy/workloads/chia-f2-rocket-81adbd4020e7/firemarshal.sh`
was counted as untracked implementation source. Runtime preparation now keeps
its private rootfs, script and manifest under the per-run verification evidence
directory; FireSim resolves their relative workload paths normally. Source
identity policy is unchanged. A regression exercises the real snapshot before
and after runtime preparation, verifies rootfs path resolution and confirms
that an actual new C++ source still invalidates identity. The old preparation
path fails this regression. All 51 focused adapter/node/private-image tests pass.

Jasper remains incomplete: boundary signoff is 35 proven, 19 undetermined and
30 CEX; actual auto-cutpoints are 21 proven and 35 undetermined. Six helper CEX
became undetermined, with zero newly proven helper equalities. The 256 missing
UNKNOWN helper rows are the read-buffer data/last memory slices: the external
text matcher recognizes direct memory-output slices but loses the layout through
the new capture register. This is a correspondence limitation, not removal or
proof of those states. Reset-X classification alone justifies no initial-state
relation. No new native compiler defect is demonstrated by the retained traces;
no speculative compiler, proof-setting or initialization change was made.
Future mapping needs checked field packing/state lineage and actual transition
and initialization proof obligations, with individual failing helper traces.

Exact scheduler checks, negative controls, driver build argv/log/hashes and
runtime diagnostics are under
`/scratch/jfx/goldengate-circt-f2-build/iteration26-max-cycles/`; Jasper audit
is under `iteration26-jasper-diagnostics/`. All 88/87/87/87 immutable SFC
prerequisite records revalidate against unchanged iteration-20 receipt,
manifest and RTL hashes. No fresh golden transformation or full-target semantic
comparison was performed by this call; CHIA owns the fresh four-target gate.
Scala oracle sources, native compiler sources, immutable goldens and unrelated
shared edits remain preserved. Next obtain a fresh semantic identity, separate
complete LEC/SEC, four real F2 Linux passes and matched compiler timings.
The driver and evidence fixes do not establish compiler parity or a speed win.

## Recovery iteration 7: compiler-origin memory correspondence (2026-10-23)

The supplied iteration-6 graph is
`automation/chia_firesim_circt/logs/verification/20261021T204512Z-pid2577644-iter6-full/graph.json`,
identity `93933e7373a6a8021e130f503d829ec5af07b999086887e84cceb3b1d34d87a4`.
All four pre-build comparisons, actual F2 Linux boot/userspace/poweroff runs and
matched complete compiler timings passed. Independent read-only auditing rehashed
accepted/replayed outputs, toolchains, exact inputs/recipes and delivered FPGA
artifacts, checked ordered target markers and exit zero, and checked 64 RTL/driver
syntax arms. Detailed artifact/runtime paths, hashes and commands remain under
`/scratch/jfx/goldengate-circt-f2-build/iteration27-evidence/`.

| Exact target | Previous F2 AGFI | SFC / CIRCT compiler median seconds |
| --- | --- | --- |
| RocketConfig | agfi-043244c1193209ef6 | 49.699393 / 26.164804 |
| MegaBoomV3Config | agfi-01a6c96101a2951a6 | 539.051516 / 142.105592 |
| ShuttleConfig | agfi-05560a57d6c64f79d | 57.056627 / 31.722073 |
| FireSimGENV256D128ShuttleConfig | agfi-0a6cd894eb27ee795 | 92.719814 / 48.801550 |

These are previous-candidate results. Timing uses 16 CPUs, three alternating
repetitions with a separately recorded initial fresh-process pair, and
`timing_scope: firesim_compiler`. Every SFC sample starts a fresh JVM and includes
startup/JIT and compiler-invoked bridge elaboration; resource append is inside
the interval. Neither OS cold-cache nor persistent warmed-JVM performance is
claimed. The audit retains thread counts, bridge costs and peak RSS.

Jasper still fails: Rocket boundary signoff has 35 proven, 19 undetermined and
30 CEX; actual auto-cutpoints have 21 proven and 35 undetermined. Helper reports
have 56 equivalent, 468 non-equivalent and 5292 unknown rows, with 5816 helper
mappings and zero matcher-added initialization mappings. Its four-hour proof
ended at the time limit with 103 GiB sampled aggregate RSS. No complete separate
LEC or SEC result exists. The missing 256 read-buffer field rows reflect the
external matcher's lost packing through the new data capture; they establish
neither state removal nor initial equality. Existing boundary-only trace export
also starved individual helper counterexamples.

The native compiler now emits `FireSim-generated.memory-origins.json`. A private
mark attached by the actual FASED read-buffer transform is checked against live
writer concatenation, reader slices, geometry and clocks before standard lowering.
The emitter records the capture actually introduced by the existing synchronous
reader normalization, resolves final storage/register identities and top-rooted
hierarchy, and binds the exact compiler-emitted RTL prefix by SHA256/size. The
mark is removed on the lowering clone; source IR and standard passes remain.
The sidecar describes native packing/capture identities only, with no SFC state
name, equality assertion or initialization relation.

Candidate preparation requires/inventories this collateral, and normal CIRCT
make wiring delivers it adjacent to the copied simulator RTL. A GNU make dry-run
of that actual recipe passes without invoking the compiler or FPGA tools. The real Jasper
adapter stages it only from the validated candidate output inventory. A strict
emitted-SV recognizer independently checks packing, addresses, clocks, capture,
RAM structure and instance paths against the hash-bound compiler record before
proposing additional helper assertions. Existing layouts remain intact and
conflicts reject. The staged flow also retains up to eight individual helper-CEX
VCDs and export errors separately from the original boundary traces. External
flow sources, proof settings, `--no-init-map`, raw payload comparisons and strict
LEC/SEC signoff requirements remain unchanged. No proof credit follows from this
metadata or diagnostic hook.

Development and configured Release compiler builds pass. The backend suite checks
128x65 and 256x65 metadata, source preservation, output-path determinism, identical
RTL with/without collateral, and malformed-origin/reader rejection before output
replacement. All 28 comparable prior backend fixtures remain byte-identical; the
old final Directory closure and this run's Read65 closure differ as inputs and
are excluded from that comparison. Actual unmodified native read-buffer helpers
of both sizes pass the production staging/original-scanner join with appended
resource RTL. The 96 distinct focused Python tests pass, including all four
retained native/SFC layout inventories and strict negative controls. All
88/87/87/87 immutable SFC prerequisite records revalidate with unchanged
iteration-20 receipt/manifest/RTL identities. No new SFC transformation or
full-target semantic comparison was performed. Focused evidence is under
`/scratch/jfx/goldengate-circt-f2-build/iteration27-memory-origins/`.

The unchanged full native read-buffer suite also passes all 12 variants,
480,000 state/edge samples and 104 atomic-rejection checks. Its original
unoptimized CTest run was interrupted without pass credit; the replacement
compiles the identical test and production transform translation units at `-O2`
into external evidence objects, retains assertions (no added `NDEBUG`), and
exits zero in 115.54 seconds. Host-hierarchy CTest separately passes. Exact
original/replacement argv, source hashes and outcomes are retained in
`interrupted-ctest.json` and `optimized-fased-result.json`. All delegated
workers completed and no implementation-owned jobs remain.

CHIA must regenerate the exact four current candidates and their paired static
comparisons, then dispatch separate complete LEC/SEC and F2 Linux execution in
parallel, followed by matched complete compiler timing. Prior Linux/speed passes
cannot accept these changed compiler/collateral sources. This call launched no
long verification-node jobs, preserved Scala oracle/goldens/unrelated edits,
and leaves full correctness and faster-current-candidate completion incomplete.


## Recovery iteration 8: retain origins through AutoILA LowerTypes (2026-10-23)

The iteration-7 verification graph
`20261021T204512Z-pid2577644-iter7-full/graph.json` stops at candidate
preparation: Rocket completes, but MegaBoomV3, Shuttle and Saturn Shuttle each
exit 1 at the new native read-buffer origin check. All four use the same
compiler binary. Failed attempts publish neither simulator RTL nor origin
collateral and preserve their prior complete receipts. No current four-target
semantic identity or downstream verification result exists.

The origin guard added in iteration 7 missed a production representation.
The three failing recipes select `WithAutoILA`, which runs the complete-circuit
`lowerTypesWithRetainedTargets(...FpgaDebugOnly)` before RTL emission, including
when the probe count is zero. That stage flattens the read-buffer enqueue and
dequeue bundle ports. Rocket's `BaseF2Config` skips this stage. The collector
previously required aggregate `SubfieldOp` endpoints, rejecting valid ground
module arguments. This is not a missing BaseF2 implementation, unsupported
memory depth or reason to alter the selected recipes.

`SimulatorRTL.cpp` now accepts those live module arguments only with the exact
`enq_bits_data`/`enq_bits_last` and `deq_bits_data`/`deq_bits_last` identities,
64/1-bit types, owning module and input/output directions. The existing
aggregate recognition and all memory geometry, packing, slice, enable, clock,
latency, capture and hierarchy guards remain. No pass, AutoILA stage, RTL
behavior, metadata schema, proof setting or initialization relation changes.

`SimulatorRTLTest.cpp` exercises both sizes through the exact production
AutoILA LowerTypes call. Aggregate/flattened emission preserves source IR and
produces identical RTL and JSON on the unit fixture. Flat wrong-field,
wrong-destination, direction, 63+2-bit packing and disabled-reader cases must
reject specifically at the origin guard, preserving existing outputs/source.
The negative reader test now uses the existing constant attribute's builtin
integer type so the test itself remains well typed.

Actual native helpers generated by `addFASEDReadBuffer`, then passed through
that same LowerTypes call, reproduce the old rejection at both 128x65 and
256x65. The fixed emitter accepts both, retaining the exact lowered input IR.
Same-in-memory marked/unmarked lowered helpers emit byte-identical RTL; all
28 comparable prior backend fixtures remain byte-identical. Both resulting
sidecars pass the production Jasper staging/original-scanner join with appended
resource RTL. All 36 relevant adapter/layout/archive tests
pass. Development and Release CLI builds pass. Immutable SFC validation checks
88/87/87/87 prerequisite records and unchanged iteration-20 receipt, manifest
and RTL hashes; all 154 snapshotted main Scala oracle files remain unchanged.
No SFC transformation or new full-target semantic comparison was run here.
Exact commands, old negative controls, emitted fixtures, source hashes, builds
and checks are under
`/scratch/jfx/goldengate-circt-f2-build/iteration28-origins-validation/`.

CHIA next regenerates the four exact F2 candidates and reruns their syntax and
static-semantic comparisons. After all pass, it dispatches separate complete
Jasper LEC/SEC and actual F2 Linux execution in parallel, then matched complete
`firesim_compiler` timings. Prior four-target Linux/speed passes do not accept
this source candidate. No long verification node was duplicated by this call;
complete proofs, current-candidate correctness and speed remain incomplete.

### 2026-10-23 — required-feature implementation, first concurrent slices

Four compiler streams advanced with separate owners, integrated centrally.
`FAME5ThreadedState` banks scalar registers by active thread, preserving clock,
feedback and selected-thread synchronous reset priority. It validates all state
and retained register targets before mutation. The focused
`--thread-fame5-registers` mode emits verified FIRRTL, annotations and simulator
RTL; production grouping/threading remains guarded. Against the unchanged SFC
`MuxingMultiThreader.onStmt`, two/three/four-thread cases match 4,096 edges each
in both expression evaluation and exported Verilator RTL scoreboards. Both final
development and Release compilers reproduce those checked native RTL artifacts.

Enabled AutoCounter printf synthesis now composes selection, reset suppression,
counter/Identity state, printf and trigger annotations, then consumes the old
AutoCounter selections. `--enable-autocounter-printf` exercises this through
`--compile-baseline --stop-after-print-host-binding`; complete platform assembly
is still rejected. Same-input unchanged SFC counter/reset/printf/annotation
contracts match. The minimal CLI fixture checks two events and one PrintBridge,
then intentionally reaches the no-data-channel FAME diagnostic. This is a
successful handoff check, not a successful full simulator compile. Stateful tests
cover 1/8/64/65/129-bit events, rollover, clock/reset behavior and atomic rejection.

FAME channel-clock analysis now shares validated definition-local groups across
different model instances and represents the SFC non-hub virtual-clock case,
including channels without associated clocks. A two-domain/two-worker fixture
matches the unchanged SFC transform's four associated/unassociated clock/reset
observations. Generalized model assembly and end-to-end scheduling remain work.

NIC token-queue insertion now validates all widgets atomically and constructs
independent per-widget queues, gates and controls. The single-widget ABI is
preserved; multiple widgets use indexed names. Binding metadata is retargeted
with BridgeIO annotations. Tests exercise 12,000 single-widget cycles and 12,000
cycles per widget with independent stalls; aliased channels must reject without
mutation. Emitted queue RTL still matches immutable historical SFC Queue_17/18
and an independent 40,000-cycle scoreboard (including reset/full/empty/dual
operations). This historical U250 component check supplies no F2 runtime credit.

Final development and Release builds pass. All seven focused CTests pass:
enabled AutoCounter CLI, F2 option validation, FAME5 register state, AutoCounter
values, AutoCounter reset gates, FAME channel-clock domains and NIC token queues.
Immutable four-target SFC prerequisite validation passes 88/87/87/87 records;
receipt/manifest/RTL identities match the preserved iteration-20 snapshot and all
154 snapshotted main Scala oracle files remain unchanged. No new four-target
transformation, timing, Jasper or F2 job was duplicated. Exact commands, source
and artifact hashes, logs and supporting comparison receipts are indexed in
`/scratch/jfx/goldengate-circt-f2-build/iteration29-full-features/implementation-result.json`.

Read-only formal diagnosis of Rocket LEC helper 03 finds unreset sampled
`bdev_req_len` differing at time zero, before any rising clock, without an initial
state correspondence map. The saved trace does not demonstrate a next-state
mismatch and does not discharge any proof. Initial correspondence and observable
equivalence still need justification and separate complete LEC/SEC results.

Next implementation work is production FAME5 grouping, target-memory banking
and scheduler/channel muxing; enabled AutoCounter CSV/MMIO and F2 Print assembly;
generalized model/domain scheduling; and indexed NIC packet/stream/MMIO/header
integration plus both loopback modes. Once enabled representatives compile,
wire their real verification adapters and hash-bound feature receipts. All four
features remain incomplete: supporting checks cannot fill feature acceptance
receipts. CHIA must regenerate current candidates and run its separate semantic,
Jasper, F2, compiler-speed and feature nodes. All feature workers finished and
were joined before returning. `LOOP_COMPLETE: no`.

### 2026-10-24 — required-feature implementation, memory/clock/Print/NIC composition

The latest completed CHIA graph is
`20261023T211611Z-pid2768860-iter1-full/graph.json`, candidate
`55fe2697a4680704c80ced54ba4aabfd9581bdeb2a9d2c1cef7adde4953bfdf3`.
All four exact baseline semantic, F2 Linux and `firesim_compiler` speed nodes
passed for that candidate. Rocket LEC failed and required-feature evidence was
blocked. Those previous results do not accept the new source candidate.

`FAME5ThreadedState` now banks combinational-read, one-cycle-write target
memories with the unchanged SFC power-of-two mapping `{address, thread}`.
It preserves aggregate masks, separate R/W and RW ports, clocks/enables and
read-under-write attributes. Complete preflight rejects unsupported synchronous
reads, initialization/retained targets and non-power-of-two thread counts before
mutation. The new `--thread-fame5-memories` diagnostic exports verified native
RTL. Unchanged SFC `MuxingMultiThreader` plus `ImplementThreadedMems` and CIRCT
run the same depth-three masked R/W/RW inputs through independent RTL
scoreboards: 8,192 cycles per case, 49,152 observations for two threads and
98,304 for four threads, with identical respective checksums
17289719672077809564 and 4990882079842146862. This does not implement production
grouping, scheduling or synchronous-read response buffering.

The new atomic virtual-clock constructor validates shared instances before
adding enable/gating and deleting the consumed scalar clock. The actual CLI
uses this shared API. Two stateful repeated workers with independent stalls and
resets match unchanged SFC in 384 nine-signal observations across associated
and unassociated clock cases; four clock-analysis reset rows also match.
Generalized production queue/simulator composition remains guarded.

Print allocation, decoder/control/master/response paths and headers now use
validated live 25/32-bit host addresses rather than fixed U250 widths. A real
unchanged SFC PrintBridgeModule elaboration under `midas.F2Config` confirms the
32-bit control, six-register/32-byte region, format/argument/clock and
512-bit/depth-6144 stream ABI. Native tests cover both widget orders, 2,048
handshake/data cases, high-bit 25/31 non-alias error routing, typed-width atomic
rejection and allocated-header corruption checks. SFC widget and native decoder
RTL lower and lint successfully. The obsolete blanket F2 diagnostic rejection
is removed. Enabled AutoCounter printf now passes the actual BaseF2 handoff
check and its normalized SFC counter/reset/printf/annotation comparison; that
minimal fixture intentionally exits at the no-data-channel FAME diagnostic.
Complete enabled Print assembly and the CSV AutoCounter widget remain gaps.

NIC packet adapters and CPU stream FIFOs now compose independent widget state
and indexed stream/count identities, preserve the existing single-widget ABI,
and retarget retained per-widget bindings with annotations. Two-widget emitted
RTL matches independent compositions of immutable historical SFC
BigTokenToNICTokenAdapter, NICTokenToBigTokenAdapter and Queue_52/54 for 48,000
cycles, supplemented by accepted-packet scoreboards. Widget zero saturates while
widget one continues independently; reset and prolonged backpressure are
exercised. Single-widget adapter and bidirectional CPU FIFO regressions each
match SFC for 40,000 cycles. These are historical U250 primitive comparisons,
not F2 feature evidence or coverage of SimpleNIC's actual loopback modes.
Indexed MMIO/catalog/header assembly and both loopback APIs remain required.

Development and Release builds pass. All 18 main focused CTests and 16 additional
clock/model CLI integration CTests pass. Final Release emits both checked memory
RTL artifacts; both compilers reproduce all six previously checked register-state
RTL cases. Immutable SFC prerequisites validate 88/87/87/87 records with unchanged
iteration-20 receipt/manifest/RTL identities, and all 154 snapshotted main Scala
oracle files remain unchanged. No long CHIA verification jobs were duplicated.
Exact commands, source/artifact hashes, component comparisons, test logs and
scope limitations are indexed in
`/scratch/jfx/goldengate-circt-f2-build/iteration30-feature-integration/implementation-result.json`.

Read-only diagnosis of the latest Rocket LEC archive records 30 boundary and
490 helper counterexamples, 5,543 undetermined assertions and incomplete signoff.
Helper 03 still differs in unreset sampled `bdev_req_len` at time zero before a
rising clock. No initial correspondence map exists in that run. This observation
neither waives boundary counterexamples nor supplies any LEC/SEC result; complete
separate proofs remain required.

Next implement production FAME5 grouping/scheduler and synchronous-read state,
complete enabled AutoCounter CSV/Print platform assembly, general model/domain
composition, and indexed NIC MMIO/catalog/header plus loopback. Real feature
execution adapters and hash-bound case receipts remain blocked until those
enabled representatives compile. CHIA must verify the current candidate through
its separate semantic, Jasper, F2, speed and feature nodes. All three feature
workers finished and were joined; no worker jobs remain. `LOOP_COMPLETE: no`.

### 2026-10-24 — iteration 3: shared FAME5 models, CSV host, model controls and NIC MMIO

The latest completed CHIA graph is
`20261023T211611Z-pid2768860-iter2-full/graph.json`, candidate
`74d9c745acdd2f0ecaebae952ea20ed1f89715e20d81a1c2add322c12c99d0cb`.
All four exact baseline semantic, F2 Linux and `firesim_compiler` speed nodes
passed. Rocket LEC failed and features remained blocked. Those results apply
to that previous candidate; CHIA must verify the current compiler sources.

`FAME5Models` now consumes actual retained threading selections, replaces a
validated leaf-model group with shared hardware, banks register and supported
combinational-read memory state, and muxes/demuxes channels with round-robin
selectors. Clone/verify/commit keeps unsupported topology failures atomic.
The actual `--multi-thread-fame5-models` CLI matches unchanged SFC
MultiThreadFAME5Models, MuxingMultiThreader and ImplementThreadedMems on the same
FIRRTL/annotations for two and four threads: 8,192 cycles each, 16,384/32,768
observations, matching checksums 129382100592920784/64021397117480295 and
normalized annotations. These simulations initialize the unreset local selector
to zero and reset across whole rotations; they do not prove selector phase.
Hierarchy, synchronous-read response state, retained-target renames and full
production enablement remain incomplete.

`AutoCounterBridge` materializes real typed CSV constructor metadata into
Accumulate/Identity 64-bit counters, delayed sampling, trigger/reset behavior,
depth-two sample buffering and exact widget MMIO through local F2 AXI32/id12.
The actual `--materialize-autocounter-hosts` CLI exports independently rooted
host RTL. Against unchanged SFC AutoCounterBridgeModule it matches 2,669 cycles,
351 MMIO reads and 39 samples, including rollover, periods 0/3/17, queue drops
and AXI stalls. Supplemental standalone-constructor serialization hints retain
the actual operation singletons; ambiguous untyped operations are rejected.
Target CSV synthesis/token binding, global allocation/driver headers and
complete enabled Print assembly remain required.

`FAMEModelControls` validates and rewrites ordered per-definition control plans
atomically; the actual CLI now calls this common API. Two definitions, three
instances and two rational domains match 576 emitted-equation observations
against unchanged SFC FAMETransform, including both clock association modes,
independent stalls/reset and differing finish decisions. Nine malformed-plan
cases reject without mutation. Production generalized queue/simulator assembly,
broader memory/fanout cases and clock phase proof remain incomplete.

NIC decoded MMIO now instantiates independent six-register banks in constructor
order and retains indexed widget bindings. Actual live NIC/TracerV queues now
form a multi-widget catalog in complete BridgeIO annotation order, including
interleaved producer classes and indexed sink counts. Six producer-order
permutations and malformed-topology atomic rejection pass. Two native MMIO
instances match immutable historical SFC register cones and independent state
scoreboards for 24,097 cycles, with 9,514 independent writes, 6,001 reset-time
writes and 787 zero-strobe writes. This historical U250 primitive comparison
supplies no F2 or loopback acceptance. Indexed global AXI/header assembly and
both actual loopback modes remain required.

Final Development and Release builds and all 44 focused CTests pass. Release
also emits both checked-in grouped-FAME5 and CSV-host CLI fixtures. Immutable
four-target SFC receipts validate 88/87/87/87 prerequisite records with unchanged
receipt/manifest/RTL identities, and all 154 snapshotted main Scala oracle files
remain unchanged. Exact changed files, source/binary hashes, commands, comparison
receipts, test logs, assumptions and remaining gaps are indexed in
`/scratch/jfx/goldengate-circt-f2-build/iteration31-production-features/implementation-result.json`.

Read-only diagnosis of the latest Rocket LEC run records 30 boundary and 462
helper counterexamples, 52 boundary and 5,519 helper undetermined assertions,
invalid/incomplete signoff and launcher timeout 124. No new proof, waiver or
initial correspondence assumption was introduced; separate complete LEC and
SEC remain missing. Component tests do not fill enabled-feature acceptance
receipts. Next integrate production enabled representatives and their real
semantic/formal/F2 execution adapters while resolving formal failures. All
three feature workers completed and were joined; no worker jobs remain. No
long CHIA verification jobs were duplicated. `LOOP_COMPLETE: no`.

### 2026-10-25 — iteration 4: synchronous response state, token binding and graph assembly

The latest completed CHIA graph is
`20261023T211611Z-pid2768860-iter3-full/graph.json`, candidate
`13ff40ac023126af9ee7b0d3e84b098285594f753ed259c09ebabf05bc94b0d6`.
All four exact baseline semantic, F2 Linux and `firesim_compiler` speed nodes
passed for that previous candidate. Rocket LEC failed and features remained
blocked. These results do not accept the current implementation.

FAME5 synchronous target memories now bank responses and delayed thread tags
according to unchanged SFC ImplementThreadedMems for two/four threads. Clock
aliases resolve through acyclic nodes and uniquely driven wires. Actual emitted
RTL comparisons exposed and fixed a shared backend mixed R/W/RW lowering bug:
same-address old-data collisions previously observed new data. The original
counterexample is retained. Final Release comparisons against unchanged SFC on
identical FIRRTL/annotations pass 16,384 cycles each, with 19,010/19,013 checked
responses and checksums 8113004069396412269/6894306669083484066. Both normalized
22-port interfaces and retained annotations match. The synchronous API requires
scalar UInt data, supported power-of-two threading and a common input target
clock; unsupported signed data rejects atomically. Startup alignment and
unreset helper-state assumptions remain explicit, not proven.

AutoCounter constructors now bind to actual post-FAME Decoupled target tokens
through atomic joins and per-field readiness, with independent indexed local
control ports. The actual `--bind-autocounter-hosts` CLI matches unchanged SFC
widgets and HostPortIOConnectChannels2Port for 4,584 cycles, 686 MMIO reads,
39/37 samples and 2,569/1,268 accepted tokens. Ten normalized constructor/channel
annotations match; the final compiler re-emits the identical tested RTL.
Production target CSV synthesis, global allocation/driver headers and complete
enabled Print platform assembly remain required.

The common FAME model graph API now composes multiple definitions/instances,
per-definition controls, real pipe/fanout/ready-valid transport and an active
simulator wrapper. Production dispatch uses it for multiple selected definitions
after InferModelPorts, preserving the existing selected-SRAM path. The bounded
two-domain, two-definition, three-instance fixture matches unchanged SFC
FAMETransform/SimWrapper on 576 model observations, 61 complete connection cones,
seven queues, 20 ordered annotations and two clock-gate/XDC identity/MFMR
contracts. Both emitted RTL boundaries pass syntax checks. A discovered stale
hierarchical annotation bypass now rejects before mutation. Broader memory and
ready-valid graph coverage, clock phase/reset proof and complete enabled
representative assembly remain incomplete.

Multiple normal NIC widgets now receive independent indexed AXI adapters,
constructor-ordered allocation/catalog entries and actual per-widget MMIO/source/
sink driver bindings. Six NIC/TracerV producer interleavings and malformed late
bindings are checked. Each path of the actual two-widget wrapper matches the
immutable historical U250 SFC MCR/register cone for 103,828 cycles while its peer
holds responses; this supplies only primitive supporting evidence. Final rebuilt
MLIR is identical and emitted RTL differs only in the recorded source-location
path. Neither loopback API nor enabled F2 Linux packet acceptance is established.

Final Development and Release builds, all 52 focused CTests and the shared RTL
backend regression pass. Immutable four-target SFC receipts validate 88/87/87/87
records with unchanged receipt/manifest/RTL hashes. All 163 snapshotted main
Scala oracle files remain unchanged. Commands, changed files, source/binary
hashes, comparisons, assumptions and logs are indexed in
`/scratch/jfx/goldengate-circt-f2-build/iteration32-enabled-bindings/implementation-result.json`.

Read-only diagnosis of the latest Rocket LEC reports 30 boundary and 445 helper
counterexamples, 52 boundary and 5,536 helper undetermined assertions, invalid/
incomplete signoff and launcher timeout 124. No proof, waiver or new state
assumption was introduced. Separate complete LEC and SEC remain required.
Next connect these compiler components into production enabled representatives,
including NIC loopback, then enable their real semantic/formal/F2 case adapters
as prerequisites become available. Component comparisons create no feature
acceptance receipts. CHIA must verify the current candidate through every node.
No duplicate long verification jobs were launched. `LOOP_COMPLETE: no`.

### 2026-10-25 — iteration 5: grouped memory gates, enabled CSV and NIC loopback

The latest completed CHIA graph is
`20261023T211611Z-pid2768860-iter4-full/graph.json`, candidate
`67df1279a4ba46573179008775a1276a5c0261fb3a38badda66dfb76e3a7604a`.
All four exact baseline semantic, F2 Linux and `firesim_compiler` speed nodes
passed for that prior candidate. Rocket LEC failed and features remained blocked.
Those results do not accept these compiler changes.

Grouped FAME5 workers now compose synchronous response banking through validated
canonical `AbstractClockGate` infrastructure with constant enabled CE. Dynamic
CE and unsupported gates reject atomically. Actual final Release output matches
unchanged complete SFC transforms for two/four threads over 16,384 cycles each:
6,121/6,114 defined responses, checksums 86339382448976747/7120853224376438303,
and normalized 24/46-port interfaces/selection metadata. Only SFC's generated
memory-wrapper inline directive is normalized. Undefined payload perturbation
passes; written-data, mask and ready corruption fail in both RTL arms. Unreset
selector alignment, warmup and supported gate shape remain explicit assumptions;
dynamic target-clock staging and production enablement remain incomplete.

Enabled AutoCounter CSV target synthesis now selects/reset-gates events, exports
each hierarchy instance, resolves clock roots and creates typed per-domain CSV
constructors plus trigger/reset/channel metadata through the actual CLI.
Unchanged SFC matches 122,880 defined observations across 8,192 samples, 25 ports
and 18 normalized annotations. Scala/native constructor event permutations are
recorded explicitly; positional MMIO/header ABI equivalence is not established.
Final Release re-emits identical tested RTL/annotations. Production composition
and complete enabled Print remain required.

Compile-time NIC loopback now has independent per-widget 74-bit keep-preserving
queues and SFC ten-seed-token/forward scheduling. Actual final Release CLI RTL
matches unchanged SFC queues, generator and HostPort binder for 12,000 cycles:
6,262/3,875 forwarded tokens, 30 seed tokens per widget and independent stalls.
All handshakes, done, live packet data/keep/last/order and defined MAC/rate values
match. Distinct uninitialized RAM seeds retain identical defined traces. Invalid
seed payloads and the three explicitly SFC-undriven pauser fields are excluded
only in this supporting boundary. Loopback MMIO/header/driver assembly, runtime
loopback and enabled F2 packet representatives remain incomplete.

The Jasper adapter now stages an explicit AXI producer-valid contract, including
stalled payloads, while keeping controls/unknown outputs strict. Automatic reset-X
and undriven mappings are disabled; qualification failure and unknown initial
memory relations fail closed. Separate actual tiny LEC/SEC checks cover invalid
unwritten differences and eight corruption controls (18 checks total). The positive
fixture's behavioral obligations are proven under an exhaustively checked shared
symbolic-word relation; raw X/overall signoff remains Incomplete and is retained.
That fixture construction is never supplied to production. Complete baseline
and enabled-feature LEC/SEC remain missing. SEC uses documented Basic below four
jobs and Proof at four or more; bounded results remain incomplete.

Development/Release builds, 55 focused CTests, the shared RTL backend regression,
35 Jasper tests and 11 adapter tests pass. Immutable four-target SFC receipts
validate 88/87/87/87 records with unchanged iteration-20 identities. All 194
snapshotted midas Scala files and the actual SimpleNICBridge.scala remain
unchanged. Exact commands, changed files, hashes, comparisons and limits are in
`/scratch/jfx/goldengate-circt-f2-build/iteration33-ram-contract-enabled-csv/implementation-result.json`.

Next complete production feature composition, dynamic gate/clock staging,
constructor-order ABI and enabled Print/NIC driver wiring, then configure real
feature semantic/formal/F2 execution as those representatives become available.
The features audit remains blocking; component receipts do not replace enabled
Linux exercise or complete proofs. CHIA must verify this candidate through all
nodes. No duplicate long baseline jobs were launched; all workers and owned
jobs are joined. `LOOP_COMPLETE: no`.

### 2026-10-25 — iteration 6: ordered CSV hosts, shared grouped models and loopback control

The latest completed CHIA graph is
`20261023T211611Z-pid2768860-iter5-full/graph.json`, candidate
`37eac30a59bf41be96da3c83398f8fef83a43f2bde461ce8f765beaab964db4b`.
Its four fixed baseline semantic, F2 Linux and `firesim_compiler` speed nodes
passed. Rocket LEC failed and required features remained blocked. These prior
results do not accept the implementation below.

Enabled CSV constructor ordering now reproduces the Scala 2.13.10 reference
hashes and both BridgeTopWiring immutable-map traversals. Eight matched SFC
cases cover small/large maps, complete hash collisions and repeated modules;
event arrays match in their original order. Actual Release target RTL matches
122,880 defined observations over 8,192 samples. The new
`--synthesize-autocounter-csv-hosts` path composes source selection/exports,
ordered constructors and local F2 AXI host engines. Their exact 17/15-register
maps and live sample traces match unchanged SFC over 3,449/3,059 cycles, with
507/429 reads and 39 samples per host. Independently seeded unreset sample
slots are compared only when valid. Production FAME token binding, global
clock/trigger/reset/MMIO/header wiring and complete enabled Print remain open.

Repeated model definitions now preserve grouped channel leaf indices and
independent instance rewrites. The actual shared grouped-model CLI boundary
matches unchanged SFC for 576 semantic rows, complete top wiring/interfaces,
target state signatures, 22 ordered annotations and two clock/XDC metadata
contracts. Independently emitted RTL matches for 8,192 host cycles with real
registered SFC clock gates, rational tokens, stalls and host resets. Aggregate
Pipe transport still rejects atomically, matching the current SFC SimWrapper
limitation. Ready-valid transport, memory models and the enabled F2 platform
representative remain required.

`--add-nic-loopback-control` now composes independent loopback token paths with
one-word done banks and local F2 AXI control. Actual Release output matches
unchanged Widget.genROReg/MCRFile helpers for 100,004 cycles: 18,053/4,597 writes,
25,346/5,768 reads, independent response stalls, zero strobes and resets during
pending transactions. This is explicitly a status/MMIO boundary comparison.
The complete unchanged SFC loopback widget cannot lower with firtool because
three pauser outputs are unassigned; that syntax failure is retained. Production
selection, disabled-stream allocation, driver/header assembly and all required
NIC Linux packet representatives remain incomplete.

Dynamic FAME5 investigation retains a target-state phase counterexample at
cycle 70 of complete unchanged SFC grouping. Its target-edge/local-selector
relation is unresolved. A narrowly checked virtual-clock-controller classifier
provides a precise atomic rejection; dynamic grouping remains disabled. Both
actual two/four-thread dynamic CLI inputs reject without emitting RTL. Fresh
supported constant-clock regressions still match SFC for 16,384 cycles each,
including 6,121/6,114 defined memory responses and corruption controls. Neither
raw SFC/native agreement nor the classifier establishes dynamic FAME5 parity.

Development/Release builds, 58 focused CTests and the shared RTL backend
regression pass. The immutable SFC receipts validate 88/87/87/87 records with
unchanged iteration-20 identities. All 195 snapshotted Scala oracle files remain
unchanged. Final Release SHA256 is
`38537cbdd05b49bc3895a7e4e7325ad51826c5fb0704c0096a9c9b437457a340`.
Exact commands, source/product hashes and scoped comparison receipts are in
`/scratch/jfx/goldengate-circt-f2-build/iteration34-loopback-mmio-ordered-events/implementation-result.json`.

Read-only diagnosis of the latest Rocket LEC retains six proven and 48
undetermined boundary obligations, 30 unreachable qualified preconditions,
invalid overall signoff and timeout 124. Its 516 helper counterexamples remain
unclassified; SEC was not reached. No production memory relation, waiver or new
proof was introduced. Keep strict controls/written payloads and unspecified
unwritten RAM; no clearing or runtime policy changes were added.

Next establish the dynamic target-edge/thread/input phase relation and compose
the supported components into production enabled representatives, including
AutoCounter/Print tokens and NIC driver/stream allocation. Configure real case
execution adapters when these prerequisites exist. Separate complete LEC and
SEC plus enabled-feature F2 Linux exercise/poweroff receipts remain mandatory;
component evidence creates no feature acceptance receipts. No duplicate long
verification jobs were launched. All workers and owned jobs are joined.
`LOOP_COMPLETE: no`.

### 2026-10-26 — recovery checkpoint 36: dynamic RAM, CSV ABI and internal RV graphs

Reconciled the interrupted iteration-35 work described in
`CHIA_OPERATOR_RECOVERY_20261026.md` without reverting shared edits or changing
immutable goldens. The prior complete CHIA graph remains
`20261023T211611Z-pid2768860-iter6-full/graph.json`, candidate
`d4c6d31494870f9ee18784a68dce5566f51603de0b383f787dfb84c7042f1477`.
Its four baseline semantic, Linux/F2 and compiler-speed passes do not accept
this candidate. Rocket LEC remains incomplete and separate SEC was not reached;
all mandatory feature acceptance receipts remain missing.

Explicit Xilinx FAME5 grouping now admits canonical dynamic virtual controllers
with asynchronous-read target RAM. Real AMD BUFGCE/glbl simulation compares
unchanged complete SFC grouping/specialization with independent per-thread
register/memory ledgers: two/four threads, four cases, 16,384 cycles per side.
Register checksums are 12116495378382872502/7711840682964475012; memory checksums
are 16412324831840668899/9489000063359163849 with 3,514/3,509 writes and
3,515/3,518 suppressed writes. Controls, defined state and written data are
strict; invalid/unwritten payload differences pass, and actual wrong-mask
corruption fails. Final Release reemits the tested RTL and annotations. The
unreset helper-selector startup relation is explicit; dynamic synchronous RAM
responses still reject atomically. Recursive grouping and production threaded
Linux enablement remain incomplete.

CSV hosts now expose constructor-ordered MMIO catalog validation and real
`autocounter_t` header emission from explicit platform allocations. The actual
source-to-FAME-to-bound-host compiler path validates its implemented catalog
before committing. Fresh final Release RTL matches unchanged SFC over 7,295
cycles, 55 retained annotations and 17/15-word banks. Both generated headers
compile against the unchanged driver ABI and match 38 semantic constructor rows
across two widgets; event-address corruption is detected. Global control
dispatch, full platform/header publication, a dynamic trigger representative
and complete enabled Print remain open.

Channel analysis now validates complete two-ended ready-valid handshake pairs,
UInt<1> leaves, exact field identities and one shared reverse channel. The model
graph validates pairs before mutation. Actual final Release complete FAME
boundaries match unchanged SFC for 12,288 cycles, 22 retained annotations and two
gate/name/MFMR contracts. The emitted internal queue separately matches the
unchanged production ReadyValidChannel primitive for 24,000 cycles; independent
startup seeds and invalid/live/control corruption checks pass. The unchanged
SFC SimWrapper itself fails its internal-channel lookup. These two boundary
comparisons therefore do not establish full transported SimWrapper parity.
Model memories, broader clock topology and the production representative remain
required.

Recovered loopback CPU queues/catalogs now have stable producer stimuli and
explicit controls. Final Release reemits the exact RTL compared with unchanged
SFC for 22,000 cycles: two independent 3,072-entry incoming queues, 9,216 accepted
words per widget, repeated resets and full backpressure. Invalid outgoing
payload perturbation passes; five occupancy/ready/valid/widget-binding faults
fail. Actual unchanged SFC loopback genHeader fails the unchanged simplenic
shared-driver ABI: its singleton done struct has offset 0/size 8, while the driver
expects offset 40/size 48 and writes normal-mode configuration fields. This
incompatibility and the earlier three undriven SFC pauser fields remain explicit
production gaps. No silent register padding, oracle fix or NIC acceptance claim
was introduced.

The Rocket report contains four actual automatic-cutpoint counterexamples with
no retained traces. A separate eight-trace export budget now retains their VCDs
and export/inventory errors independently of helper/boundary budgets. This is
reporting only: no proof commands, assumptions, reset/X waivers, memory clearing
or runtime policy changes. Counterexample causes remain unclassified until
CHIA obtains the traces; no new long proof or FPGA job was launched here.

Development/Release builds, 64 focused CTests, the shared RTL backend regression
and 23 formal-adapter tests pass. Immutable four-target receipts/inputs/outputs
and iteration-20 identities validate unchanged; all 195 Scala oracle files in
the pre-interruption snapshot remain unchanged. Final Release SHA256 is
`c2e5792441c9ac4aedcea43e17d818485bfb3c8a8e82ae1ce9504b7e67321e75`.
Exact commands, changed files, source/product hashes, assumptions and scoped
comparison receipts are in
`/scratch/jfx/goldengate-circt-f2-build/iteration36-recovery-production-features/implementation-result.json`.

Next compose CSV global control/header allocation and enabled Print in the
production platform, extend FAME5 synchronous-memory/hierarchy support and NIC
compatible driver contracts, and inspect CHIA's actual cutpoint traces. Configure
real feature execution adapters once these production prerequisites exist.
Separate complete LEC AND SEC and enabled-feature F2 Linux exercise/poweroff
remain mandatory; supporting receipts do not populate feature acceptance.
All feature owners and owned subprocesses are joined. `LOOP_COMPLETE: no`.


### 2026-10-26 — checkpoint 37: synchronous threaded RAM, CSV global control and memory models

The latest completed CHIA graph is
`20261026T083504Z-pid1709906-iter1-full/graph.json`, candidate
`9d87584a3b58e305bcbdc91282292636f161caa45525c4bcc7751650e636e73e`.
Its four fixed baseline semantic, F2 Linux/poweroff and `firesim_compiler` speed
nodes passed. These prior results do not accept the changed candidate. Rocket
LEC timed out with four actual cutpoint counterexamples and undetermined
obligations; separate SEC was not reached. Required features remain blocked.

Explicit Xilinx FAME5 grouping now supports canonical dynamic virtual-controller
synchronous target RAM, preserving response-edge banking and thread tags. Full
unchanged SFC grouping/specialization and real BUFGCE/glbl simulations match for
two/four threads over 16,384 cycles each: 7,013/6,995 qualified responses,
3,514/3,509 writes, 3,515/3,518 masked writes and 1,757/1,752 old-data collisions.
All four concrete Boolean helper startups match after whole-rotation host reset;
no target/response memory or tags are cleared. Actual response-tag and mask
faults fail. Final Release reproduces the tested synchronous and earlier
asynchronous/register artifacts. Arbitrary-X startup proof, recursive grouping,
more general dynamic domains and production threaded Linux remain open.

`assembleAutoCounterControl` now validates a complete CSV-only bank catalog and
explicit allocation atomically, assembles one global AXI network and emits
allocation-bound driver constructors. `compileAutoCounterCSVToControl` composes
source selection, CSV, clock/reset/trigger bindings, FAME, hosts and global
control. New CLI paths publish the network and header; local AXI ports and
wrapper-only output monitor sinks are internalized without changing inner state.
Actual unchanged SFC comparisons pass 4,313 direct-network and 7,333 source
pipeline cycles, preserving 10/55 annotations and exact 17/15-word pipeline
banks. Actual shared driver headers match 38/47 semantic rows across two widgets;
address corruption and a compiled AXI route crosswire are detected. Mixed-widget
platform integration, complete enabled Print and a dynamic trigger representative
remain incomplete. Rational clock tokens are explicit comparison inputs.

The generalized model graph now internalizes exported clock aliases for unique
virtual model definitions. The new dual-domain memory/combinational case matches
unchanged SFC for complete interfaces, wiring, aliases, memory descriptors,
30 annotations and two gate/name/MFMR/XDC contracts. Actual Scala VerilogCompiler
boundary RTL and native RTL match for 16,384 cycles with independently seeded
unreset memory and an independent persistent ledger: 11,243 writes, 2,794 masked
writes and 1,227 old-read-under-write collisions. Both full wrappers separately
pass syntax checks. This establishes model-boundary behavior, not transported
SimWrapper RTL parity; repeated exported aliases, broader topology and the
known internal ready-valid SFC SimWrapper lookup defect remain open.

The prior two-widget loopback stream output reemits unchanged with final Release.
Its earlier 22,000-cycle component comparison remains scoped supporting evidence.
The unchanged SFC singleton loopback header still disagrees with its shared
driver (done offset 0/size 8 versus offset 40/size 48); three undriven pauser
fields and all required NIC Linux packet representatives remain open.

Retained Rocket LEC has 21 proven, four counterexample and 31 undetermined actual
cutpoints. The last session command was boundary/helper counterexample export;
no actual cutpoint VCD was retained before wall timeout. The reporting hook now
exports actual automatic cutpoints first with an independent eight-trace budget
and flushed per-property progress/errors. No proof commands, assumptions,
initial-state relations, qualification rules or runtime policy changed. The four
counterexamples remain unclassified; no new long proof or FPGA job was launched.

Development and Release builds, 68 focused CTests, the shared simulator RTL
backend regression, 28 Jasper adapter/archive tests and syntax checks for eight
changed Python sources pass. A Release-only unit-target invocation failed because
that build disables tests; the development target and final CTests passed.
A test-only null attribute guard and initially uninitialized AXI test stimulus
were corrected; retained failing runs are identified in the receipts.
Immutable four-target receipts/inputs/outputs validate 114/112/112/112 records
against unchanged iteration-20 identities; all 195 snapshotted Scala oracle
sources remain unchanged. Final Release SHA256 is
`f5b9257d4f1f5409a2cef481172d31b3cc57c045df591ba94a9b74bed04a6a09`.
The 31 changed native files, two formal reporting/test files, exact commands,
product hashes, scoped comparisons and assumptions are recorded in
`/scratch/jfx/goldengate-circt-f2-build/iteration37-production-control-memory/implementation-result.json`.

Next integrate CSV plus Print into the production platform, complete production
threading/model/clock and NIC driver contracts, and inspect the actual cutpoint
traces from CHIA. Configure real feature execution adapters when these production
prerequisites exist. Complete separate LEC AND SEC and enabled F2 Linux feature
exercise/poweroff remain required; component receipts do not populate the feature
acceptance tree. All owners and owned jobs are joined. `LOOP_COMPLETE: no`.

### 2026-10-27 — checkpoint 38: recursive grouping and mixed instrumentation composition

The latest completed CHIA graph is
`20261026T083504Z-pid1709906-iter2-full/graph.json`, candidate
`0b25b6e4910ef65919667ecaf4d74480d39fb09b0a338839135fc4d60f4d30ae`.
All four fixed baseline semantic, F2 Linux/poweroff and `firesim_compiler` speed
nodes passed for that identity. These results do not accept the new candidate.
Rocket LEC remained incomplete with four counterexamples, 31 undetermined
cutpoints and timeout; separate SEC was not reached. Features remain blocked.
No duplicate long CHIA proof, FPGA build or baseline verification was launched.

FAME5 now recursively clones reachable internal model hierarchies in checked
postorder, retains child instances/name kinds and canonical host wiring, and
banks each descendant's scalar state and asynchronous target RAM independently.
Unsafe clock/reset wiring, external/recursive/bind/layer semantics and retained
removed-descendant targets reject atomically. Actual unchanged SFC full grouping
and specialization match for two/four threads over 16,384 cycles each: 32,752/
32,732 qualified written responses, 18,054/18,060 sibling writes, 6,022 suppressed
masked writes, 2,340 stalls and 242/243 clears. Independent ledgers and actual
mask/data-crosswire faults check isolation. Already-present canonical host ports
also match; final Release reproduces ten tested old/new cases. Arbitrary-X helper
startup, dynamic/synchronous-memory descendants and threaded Linux remain open.

`compileInstrumentationControl` now composes all selected post-FAME CSV and
Print constructors into one complete global MMIO catalog, shared control
interconnect, buffered outgoing CPU AXI transport and allocation-bound driver
collateral. The transform commits atomically; the explicit CLI emits RTL,
retained annotations, both driver and Print decoder headers and the peer map.
The actual unchanged Scala CSV/Print/CPUManagedStreamEngine/HasWidgets oracle
matches 48 driver semantic rows and 16 normalized channel/clock/reset annotation
rows. Its strengthened 5,347-cycle comparison covers 2,621 Print accepts, 27
complete DMA beats, 723 control reads, 39/39 CSV samples and 2,800/1,379 independent
target accepts, including ROI start/end, flush, reset, MMIO/count reads, IDs,
backpressure and decode errors. A compiled signed-argument packing fault is
rejected. This selected boundary still requires source pipeline/full platform,
dynamic trigger and checked CSV/Print Linux integration for acceptance.

Generalized models now internalize exported clock aliases across every retained
instance of a shared definition atomically. A three-definition/five-instance
case (two independent memory and two combinational instances) matches complete
interfaces/wiring, memory descriptors and 37 retained annotations against SFC.
Actual boundary RTL matches for 16,384 cycles with rational 1/2 and 1/3 tokens,
independent stalls/reset and persistent old-read-under-write ledgers. Invalid
payload differences are ignored; actual memory, ready and cross-instance binding
corruptions fail. Both full wrappers pass syntax. Transported runtime parity,
broader shapes and the unchanged internal ready-valid SimWrapper lookup defect
remain open. Final model and prior two-NIC loopback RTL/annotations reproduce
exactly; this does not upgrade NIC component evidence to feature acceptance.
The singleton compile-time loopback SFC header/shared-driver ABI conflict and
undriven pauser fields remain unresolved.

All four retained actual Jasper cutpoint VCDs are now inspected and hash-bound.
The mapped registers agree at the first sample, but unreset temporary clones of
TSI enqBitsLast, BlockDev wBeatCounters_0 and ResetPulse pulseLength differ before
any observed positive clock edge. These observations do not prove transition
equivalence or justify waiving the counterexamples. A checked abstract initial
state relation and strict negative controls are still needed. No assumption,
waiver, initialization or proof-policy change was made here. Preserve the
operator-installed unlimited proof/wall/adapter policy and 128 GiB RSS guard;
the archived run retains its original four-hour limits.

Development/Release builds, 71 unique focused CTests after a corrected negative
test, the shared simulator RTL backend regression and changed Python syntax
checks pass. Retained intermediate failures include C++ handle constness, a
missing unit import library and a test helper dereferencing a missing annotation
field; their fixes and affected reruns are recorded. Four immutable SFC golden
receipts/inputs/outputs validate 114/112/112/112 records against iteration-20
identities. All 194 snapshotted MIDAS Scala files and the separately checked
SimpleNIC Scala oracle remain unchanged. Final Release SHA256 is
`a8796fd5eeba7b6728ceeb7f198b320be8a73416cfe02a682c8723f84ce51f7c`.
Changed files, exact commands, initial/final compiler bindings, assumptions,
formal observations and scope limits are retained in
`/scratch/jfx/goldengate-circt-f2-build/iteration38-feature-composition/implementation-result.json`.

Next integrate the enabled instrumentation/model/threading boundaries into
bounded Linux-capable F2 representatives, resolve the NIC driver contract, and
establish a checked Jasper initial-state relation without hiding live control or
payload failures. Configure real feature execution adapters as those production
prerequisites become available. Separate complete LEC AND SEC and enabled F2
Linux feature exercise/poweroff remain mandatory; no supporting receipt is
promoted into the feature acceptance tree. `LOOP_COMPLETE: no`.

### 2026-10-30 — checkpoint 39: enabled source composition and generalized memory/model boundaries

The supplied recovery graph records iteration-3 candidate
`0237f54d8a0da07feaa282be409d65568a5ff96f66d7c6085d14b4b3187c9f4b`:
all four baseline semantic and F2 Linux/poweroff nodes passed; Jasper was stopped
without signoff after its internal engine limit and extended parent wait. Speed
and features did not execute in that iteration. Those results do not accept this
candidate. No duplicate long CHIA verification, FPGA build or workload was run.
The active 24-hour proof policy and immutable stopped project remain unchanged.

FAME5 memory banking now supports non-power-of-two thread counts using unchanged
SFC's independent per-accessor unreset host-cycle base counters, while preserving
the existing power-of-two implementation. Six complete nested asynchronous/RL1
SFC/native comparisons pass for two/three/four threads, 16,384 cycles each, with
independent written-memory ledgers, mask/isolation controls, all four Boolean
response-helper startups, invalid-payload invariance and actual emitted
mask/crosswire/frozen-base faults. Final Release exactly reproduces 16 new and
preserved cases. These are scoped checks: an unaligned midtraffic host reset
breaks logical thread association identically in SFC and native. Both original
failures are retained, and no production alignment guarantee was found. Do not
promote aligned-reset evidence into arbitrary-reset or threaded Linux acceptance.

The new atomic `compileInstrumentationSourceToControl` API and explicit
`--synthesize-instrumentation-source-control` CLI compose real enabled CSV and
Print selection, dynamic trigger/reset/clock wiring, post-extraction lowering,
FAME, shared global MMIO, CPU transport and driver/decoder/peer collateral.
Literal Print enable/argument const types now cross the mutable channel boundary
without changing width or signedness. Three actual unchanged Scala source/FAME/
host comparisons pass for 8,228 cycles each: 62 retained annotation rows, 54
actual driver semantic rows, 66/68/66 DMA beats and 39/39 CSV samples. Actual
Print corruption and disabled dynamic-trigger controls fail; invalid payload
perturbation passes. Six CLI success/atomic-rejection cases pass. Complete F2
platform bridge composition is still missing, and a direct top-input Print
argument remains a real SFC-supported/native-rejected model-passthrough case.

Generalized model boundaries now admit shared scalar virtual-clock models
without an explicit clock hub, matching SFC FAMETransform; full simulator
transport still rejects hubless graphs atomically, matching SimWrapper's scope.
Actual unchanged SFC/native hubless boundary RTL matches for 16,384 cycles with
30,658 independent state-ledger checks and fault controls. A separate valid
complete transported graph matches for 32,768 cycles with rational 1/2 and 1/3
clock tokens, independent stalls, fanout and latency-one transport; three actual
clock-lane/ready/initial-token faults fail. All 20 retained annotations and two
gate/MFMR contracts agree. These explicitly scoped RTL checks do not prove
arbitrary initial states, clock-generator phase or Linux integration.

Compile-time NIC loopback now emits a distinct native lifecycle driver and its
header, with direct driver build include/dependency wiring. The actual shared
widget registry/bridge driver compiles two independent registrations and runs
2,000 ticks with no MMIO or CPU-stream access; an unexpected MMIO write fails.
Tested loopback RTL/annotations and prior stream/MMIO comparisons remain
hash-identical. The unchanged SFC singleton header/shared-driver ABI mismatch
and three undriven pauser outputs are retained reference defects, not claimed
parity. Full normal/multiple/runtime-loopback enabled Linux coverage is open.

All 34 actual stopped-run cutpoints were joined to saved state inventories;
the four first-cycle scalar pairs are unreset on both sides. A private short
Jasper diagnostic on the actual frozen TSI channel closure reproduces the
independent-state counterexample at cycle one. Sharing only enqBitsLast's initial
value leaves the strict result undetermined at bound 86 with reset-X interface
obligations outstanding; actual transition corruption fails at cycle two. This
establishes neither a complete SEC result nor a separate LEC result. No relation,
waiver, helper assumption, memory clearing or production proof-policy change was
adopted. All diagnostic projects and reports are retained and their jobs joined.

Development/Release builds, 75 unique focused CTests after the corrected
virtual-clock fixture, the simulator RTL backend regression, driver Makefile
selection/dependency checks, seven changed Python syntax checks and diff checks
pass. Four immutable SFC golden sets validate 114/112/112/112 records against
iteration-20 identities; all 195 snapshotted MIDAS/SimpleNIC oracle files remain
unchanged. Final Release SHA256 is
`5de2366f1367c759a606119c7f4a05f01378b0be5aeab2a65138d8fe8f6a2c08`.
Exact changed-file manifests, commands, scoped comparisons, negative controls,
retained failures, final compiler bindings and preservation checks are indexed by
`/scratch/jfx/goldengate-circt-f2-build/iteration39-production-features/implementation-result.json`.
All owners and owned jobs have finished.

Next fix direct model passthrough and compose the enabled boundaries with the
full F2 platform/driver pipeline for bounded Linux representatives. Establish
checked queue/state initial relations for all actual Jasper failures without
hiding defined controls or live payloads. Configure real feature execution
adapters when these full-platform prerequisites exist; unset adapters and absent
hash-bound case receipts intentionally keep the feature guard blocked. All four
baseline current-candidate checks, separate complete LEC AND SEC, matched
`firesim_compiler` speed and enabled F2 Linux feature exercise/poweroff remain
required. `LOOP_COMPLETE: no`.

### 2026-11-01 — checkpoint 40: signed threaded RAM correctness and direct Print integration

The latest completed CHIA graph remains
`20261030T132059Z-pid1846599-iter1-full/graph.json`, candidate
`cbacfd81fb4a54a6dfcf5f8d0110fe4d0d923b8c623376ebf295af9d7b54e936`.
Its four baseline semantic, F2 Linux/poweroff and compiler-speed passes do not
accept this changed candidate. No duplicate long CHIA proof, FPGA build or
baseline verification was launched by this implementation turn.

FAME5 now admits scalar signed synchronous target RAM. The same-input nested
dynamic comparison exposed a second, defined-behavior compiler defect: the RTL
backend's UInt-only synchronous-read normalization skipped signed RAM, producing
post-write data instead of old data at cycle 54. Integer-type normalization now
also handles signed independent readers and explicit/inferred readwriters while
preserving existing new-data-RUW behavior. Unchanged SFC full grouping/Xilinx
specialization and native nested Branch/Leaf cases match for two/four threads,
16,384 cycles each, including negative signed writes, masks, collisions, held
responses, all four Boolean helper startups and invalid-payload invariance.
Actual compiled wrong-mask and sign-extension faults fail. All twelve positive
and fault RTL/annotation products reemit identically with final Release. The
unaligned host-reset phase defect remains on both SFC/native; no reset policy,
target RAM clearing or production threading acceptance was introduced.

FAMEModelGraph now promotes exclusive scalar bridge-to-bridge passthroughs into
complete Decoupled channels, retaining payload annotations/symbols and rejecting
unsupported wiring atomically. This fixes the previously rejected direct
top-input Print argument through the real source selection/FAME/shared-control/
CPU transport composition. ChannelExcision's two fanout branches have an explicit
ordinal bijection: the model and direct Print roles remain distinct. Unchanged
SFC/native source pipelines match for 8,228 cycles, 62 retained annotation rows,
54 actual driver ABI rows, 66 DMA beats and 39/39 CSV samples. Four live compiled
faults fail; invalid-only payload perturbation passes. Seven CLI cases and the
graph unit pass; eighteen products from three success cases reemit identically
with final Release. Complete original-platform bridge and Linux integration is
still missing. Exact production insertion/allocation/driver hooks are recorded
in `iteration40-production-integration/instrumentation/production-hook-analysis.md`
under the build evidence root below; removing the current early return alone
does not establish that composition.

The new atomic NIC loopback composition API/CLI joins one/two selected widgets
to a single global MMIO allocation, allocated disabled directional CPU streams,
independently buffered F2 CPU AXI transport, dedicated lifecycle driver and
allocation-derived peer/header collateral. CPUStreamPort validates the selected
NIC catalog instead of requiring the TracerV address profile. Dead pauser payload
use is checked conservatively through plain internal wiring; live logic, output
uses and region-bearing blocks reject. Direct/nested one/two-widget tests and
eight atomic rejection cases pass. The unchanged whole SFC loopback network still
fails initialization on its three unused pauser outputs/widget. A separately
labeled diagnostic invalidates only those proven-dead outputs, preserving the
original failure and source/hash. Its actual two-widget HasWidgets/CPU/buffer
network matches native for 60,000 cycles after one synchronous host-reset edge:
22,091 MMIO reads, 18,432 CPU writes, checked independent packets, stalls and
three resets without clearing RAM. Actual driver/peer/stream ABI checks and an
unexpected-MMIO negative control pass. This normalized diagnostic is explicitly
not an unnormalized SFC syntax pass or full NIC feature acceptance.

Latest retained Rocket LEC diagnostics contain 3,261 unique raw engine witnesses
and four actual first-cycle automatic cutpoints, all unreset scalar pairs in the
frozen state inventories. No complete signoff or new initial relation follows
from that inventory. The adapter now retains hash-bound raw witness diagnostics
and reports timeout explicitly instead of hiding it behind a missing-summary
exception. Jasper dispatch retains per-target/per-mode failed receipts and runs
independent SEC after a failed LEC, including a remotely timed-out proof whose
process cleanup is confirmed. An unconfirmed timeout stops further dispatch to
preserve the one-job resource policy. Separate complete LEC AND SEC remain open;
the 24-hour/128-GiB policy and all strict defined-control/live-data obligations
are unchanged. No new long proof, waiver or memory initialization was adopted.

Development/Release builds, 77 focused CTests, the final NIC guard rerun, the
extended actual simulator-RTL backend regression and 121 Python adapter/contract/
dispatch/graph tests pass. Intermediate C++ API/dependency/test-fixture failures,
the actual signed collision failure, the diagnostic NIC pre-reset cycle-zero
difference and original SFC pauser syntax failure are retained. Four immutable
SFC golden sets validate 114/112/112/112 records against iteration-20 identities;
all 195 snapshotted Scala oracle sources remain unchanged. Final Release SHA256:
`852a5a14e5be4ef68c32e34291742877dae3cd683b9d6dddd0c6582ed774b43d`.
Exact changed-file manifests, commands, artifact bindings, assumptions and scoped
results are indexed by
`/scratch/jfx/goldengate-circt-f2-build/iteration40-production-integration/implementation-result.json`.

Next connect the validated Print/CSV, threading/model and NIC boundaries through
the actual original-platform F2 assembly and driver/workload paths, then enable
real bounded feature adapters. Establish checked queue/state initial relations
and obtain separate complete LEC/SEC without repeating an unchanged failed
correspondence setup. All four current-candidate baseline checks, matched
compiler-speed gates and enabled-feature semantic/LEC/SEC/F2 Linux exercise plus
clean poweroff receipts remain mandatory. The feature guard remains blocked;
supporting component receipts do not populate its acceptance tree.
`LOOP_COMPLETE: no`.

### 2026-11-02 — checkpoint 41: full F2 Print/NIC composition and production threading handoff

The supplied November 1 candidate `12b318b04aa0d7f3f3dc3d14456ee8f92fc4474c5b6d745406588a1f9e65645f`
passed four baseline semantic and F2 Linux nodes; its Jasper run was interrupted
and speed/features were skipped. Those results do not accept this changed
candidate. This implementation turn launched no duplicate CHIA proof, FPGA build
or Linux run. Exactly four baseline recipes and the seven required feature
representatives remain unchanged; no supporting receipt was installed as feature
acceptance.

The production compiler now accepts SynthPrints, AutoCounter printf and model
threading through explicit flags and their existing Scala configuration
fragments. Print hosts join the actual active FAME transport, original widgets,
global MMIO catalog, directional CPU streams and generated complete driver.
Passive synthesized clock exports are admitted only through exact retained
channel-clock identities and uniquely driven hierarchical forwarding. A real
private periodic-Print Rocket input completes unchanged frozen SFC and native
`WithPrintfSynthesis_BaseF2Config` compilation. Normalized comparison passes 46
channel/port rows, nine clocked bridge constructors, seven fanouts, two platform
interfaces, 95 existing ABI assertions, actual executed Print constructor ABI,
streams, partition collateral and XDC; complete RTL and real full driver headers
pass syntax on both sides. An initial comparison at unlike clock-enrichment
stages is retained, then corrected by comparing all bridge fields at the common
post-WrapTop stage. This is static/syntax support, not behavioral equivalence or
Linux acceptance.

NIC's real CPU platform adapter now validates independent directional
allocations, admitting multiple widgets and mixed TracerV/Print/NIC streams
without hardcoded source/sink window equality. A bounded WithNIC-derived Rocket
input compiles through the full original `BaseF2Config` platform on both SFC and
native. Nine bridges, 58 channels/ports, 18 fanouts, twelve driver classes, 95
static ABI assertions, both interfaces, streams and XDC match; complete paired
RTL/driver syntax passes. Compile-time `--nic-mode=loopback` now composes through
original-platform CPU/MMIO/control/header assembly. Its dead-pauser check follows
pure data and held state in exact instance context, with buffered positive and
live output/reset/clock/enable/ready/assertion/memory negative controls. Actual
native loopback emits complete RTL/collateral, passes complete RTL/header syntax,
and links the actual emitted constructor to the real widget/registry/inline
driver implementation for 1,000 lifecycle ticks; a deliberate MMIO access fails.
The singleton done word remains read/write, matching SFC's actual attach policy
despite the genROReg name. Original SFC loopback undriven-pauser and singleton
header/driver defects remain retained; native syntax/lifecycle success is not an
unnormalized SFC loopback parity result, packet exercise or Linux acceptance.

Production threading now carries selectors through WrapTop/ExtractModel,
restores instance metadata, groups after FAME channel/fanout preparation and
retains physical allocation/startup obligations in normal compiler metadata.
The new atomic native dedup handoff respects NoDedup/DedupGroup and retargets
known metadata using proved port ordinals and whole SSA/operand-graph identity,
including renamed operations/instances and supported lowered aggregate metadata.
Unknown changed-target schemas reject. Adversarial same-name/different-expression
metadata follows the corresponding canonical operation instead of conflating
names. A genuinely quad-Rocket Linux-capable input completes frozen SFC F2
compilation with four shared threads. Native now promotes twenty models,
deduplicates 1,511 definitions and passes graph coverage, then explicitly rejects
`PlusArgTimeout_3/plusarg_reader:plusarg_reader_3` external sharing before RTL.
That production blocker remains open, as do multiple repeated groups and the
SFC-like unreset local-selector startup/reset phase obligation. No threaded full
platform or generalized multi-model Linux/formal acceptance is claimed.

CSV AutoCounter original-platform integration remains guarded. A new bounded
full AutoCounter printf oracle attempt reaches SFC synthesis but fails actual
TriggerWiring (wire trigger versus the pass's node-only sink handling); the
original error is retained without removing trigger sources or editing Scala.
The new deterministic driver-output checker validates CSV metadata, activity,
rollover, samples and Print formatting/order against independent expectations;
four checker mutation tests pass. It is not connected to a fabricated enabled
Linux case. The feature adapters/evidence remain incomplete by design.

Read-only stopped-SEC diagnosis records 56 one-cycle internal-helper failures,
6,072 unqualified helper maps and zero explicit initial mappings. No waveform
values were exported, and absence of a named top-output counterexample does not
establish nonobservability. Unreset state/invalid payload and undriven queue
classification still need proved closure. No waiver, initial-state assumption,
DRAM clearing or proof-policy change was introduced; separate complete LEC AND
SEC remain missing.

Development/Release builds and thirty unique focused CTests pass, including the
final 17/17 integration run; intermediate fixture, missing-test-binary and
compiler failures remain retained with successful corrected reruns. Nine actual
Makefile selection checks and four Python checker tests pass. Final Release
SHA256 is `1f0e3e2a1154805999e836b3c01806fe8b0ac75e0a0a28c21bf2d2d3bb590c42`.
Its reemission reproduces 186 Print, 206 normal-NIC and 203 loopback products
identically, binding the earlier normalized static/syntax checks to this binary;
this artifact identity check is not byte-equality SFC parity or a speed claim.
Four immutable baseline golden sets retain 114/112/112/112 validated records;
all 195 snapshotted Scala oracle files are unchanged. Exact owned files, commands,
artifact identities, retained failures and scoped results are indexed by
`/scratch/jfx/goldengate-circt-f2-build/iteration41-full-platform/implementation-result.json`.
All feature owners and their subprocesses are joined before handoff.

Next port justified PlusArgReader/external sharing and multiple-group/startup
semantics, integrate real CSV hosts with the original widget catalog/platform,
and configure bounded Linux counter/Print/packet/multidomain exercises as their
prerequisites become available. Resolve the formal initial-state/queue relation
without hiding defined controls or live data. All four current-candidate baseline
semantic, separate LEC/SEC, matched firesim_compiler speed and enabled F2 Linux
feature exercise/poweroff gates remain required. `LOOP_COMPLETE: no`.
