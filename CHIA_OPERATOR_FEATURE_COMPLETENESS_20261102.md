# Implementation priority: reach feature completeness

The user requested feature completeness on 2026-11-02. Treat this as the next
implementation milestone, alongside continued formal diagnosis. Preserve the
final semantic/Linux/LEC/SEC/speed acceptance standard; this milestone is not
permission to declare the port complete without verification.

Operator update: the user explicitly requested interrupting the current
verification iteration and proceeding directly to implementation. The old driver
and queued charter updater were stopped; the charter is now applied. Start with
production feature integration, using the interrupted graph only as diagnostic
feedback. Read `automation/chia_firesim_circt/logs/feature-completeness-20261102/forced-join/`
for the interruption receipt and preserved SEC evidence. This is a one-iteration
skip: resume normal CHIA verification after the implementation agent returns.
Do not launch the same unchanged long baseline proof from this implementation
call. Formal diagnostics can be analyzed independently while feature owners
finish the missing production paths.

## What the next implementation turn must deliver

Connect the existing feature transforms to the normal FireSim compiler and
actual F2 platform, rather than accumulating additional standalone CLI paths.
The selected FireSim recipes must be able to select enabled features through
their real compiler/bridge configuration. Emit the complete simulator RTL,
clock/reset/constraint collateral, shared MMIO/stream allocations and usable
generated driver/header/peer metadata. Reject genuinely unsupported inputs
explicitly; do not silently disable features or hide partial compilation.

Assign concurrent owners with explicit shared-file/API ownership:

- FAME5: production grouping and scheduling before transport, retained thread
  metadata, reset/startup alignment, and a threaded Linux-capable representative
  that exercises every logical thread and independent state/memory.
- Instrumentation: integrate source/FAME AutoCounter and Print with all normal
  platform bridges, shared MMIO, clock/reset/trigger and CPU transport; provide
  a Linux workload/checker for nonempty correct CSV samples and expected prints.
  Direct-input Print passthrough is already implemented at checkpoint 40.
- Generalized models/clocks: integrate model/domain boundaries through the full
  platform clock and channel transport; provide a multiple-model/domain Linux
  representative with measured domain activity, ratios and independent stalls.
- NIC: integrate target/clock/memory/F2 shell and driver/workload paths for
  normal and multiple widgets plus compile-time and per-widget runtime loopback.
  Resolve the original SFC pauser/driver defects with a documented intended
  contract, retaining unchanged oracle failures and sound comparisons.
- Acceptance support: connect real enabled-case execution to the existing
  semantic, Jasper and F2 nodes; create actual hash-bound case manifests.
  The read-only feature audit is already implemented and must remain strict.

Use as many subagents as useful within available slots; schedule waves when
needed. The main implementation agent owns shared CLI/platform/build integration
and resolves interfaces centrally. Join every worker and subprocess before
handing the candidate back to CHIA. Do not mutate the verification candidate or
launch duplicate long proof/FPGA jobs from an implementation turn.

## Definition of the feature milestone

For each required stream, retain a runnable production configuration, complete
generated artifacts, driver build, automated workload and feature checker, plus
meaningful semantic/RTL/ABI supporting tests. Select the bounded representatives
already specified in feature_scope.yaml (seven cases total); preserve exactly
four baseline targets and do not enumerate all sample recipes. Reuse builds for
runtime-only NIC mode changes when actual hardware inputs match.

Distinguish implementation complete, production integrated, Linux exercised and
formally verified in the status report. Missing formal results may remain an
explicit blocker after the implementation milestone; missing execution adapters,
driver assembly or workload logic are unfinished implementation, not merely
missing verification. Prioritize closing those gaps over more isolated helpers.

Formal diagnosis should have an independent owner when resources permit and
must use retained counterexamples and focused reproducers. Do not require a
successful unchanged full-top proof before implementing independent features.
Defined behavior, initialized/written data and live control/payload remain
strict. Unspecified unwritten RAM need not match bit-exactly; zero_out_dram
stays false. Preserve justified CIRCT fixes and immutable SFC goldens.

Final completion still requires the fixed-four semantic comparisons, separate
LEC/SEC, F2 Linux exercise/poweroff, compiler-speed checks, and every required
enabled-feature acceptance receipt for the current candidate. QSFP stays
deferred. Current Jasper policy remains 24h and 128 GiB RSS.
