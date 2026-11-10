# Active user scope update — 2026-10-23

The user explicitly requested mandatory FAME5, enabled AutoCounter/Print
integration, multi-clock/generalized models, and NIC support, with QSFP deferred.
The port plan, loop prompt, README, START.yaml and verification.yaml now reflect
that scope. Preserve the four baseline recipes and use bounded enabled feature
representatives; do not expand to an all-config matrix.

Prefer concurrent feature owners when dependencies allow. Coordinate shared
passes centrally and join every implementation subagent before returning.
Each representative requires matched SFC/CIRCT semantic/syntax checks, separate
Jasper LEC and SEC, and feature-exercising F2 Linux boot with clean poweroff.
Baseline-only boot or feature checkboxes cannot satisfy enabled acceptance.

`feature_scope.yaml` declares required capabilities, Linux-capable representative
cases and supporting microfixtures. `feature_acceptance.py` is a separate,
read-only completion audit. It rejects missing evidence, stale inputs, missing
golden SFC/CIRCT/config bindings, failed/unproved Jasper inventories, Linux
failures or absent feature observations. Case receipt schema is documented in
that module. Feature execution adapters/cases still need implementation; this
audit deliberately starts blocked. Do not remove or weaken it to complete the
original four-target milestone. The five-node graph now requires features too.

Evidence manifests belong under
`logs/feature-evidence/<current-identity>/<feature>/*.json`, outside the hashed
scope registry. Never append report hashes to the registry itself. Reuse actual
unchanged FPGA hardware and runtime inputs with recorded provenance; repeated
Vivado work caused solely by unrelated source edits must be corrected as part
of the orchestration work.

Validation: the full 135-test automation suite passed; the feature guard's 13
tests passed again after its final fresh-helper-loading adjustment. Tests launch
no real proofs/builds/FPGA runs. All supervisor implementation subagents joined.

Driver activation is queued safely: administrative Ray job
`firesim-circt-scope-reload-20261023` holds an implementation-call barrier and
waits for the old iteration-8 graph (all verification branches returned), then
stops the old driver and submits `firesim-circt-full-scope-20261023` with that
graph as bounded feedback. No active proof/build is interrupted. The reload
dry-run verified join-before-stop and barrier release before new submission.

## Next implementation turn: parallelize the remaining integration gaps

User reaffirmed parallel subagent implementation on 2026-10-24. Start from
`/scratch/jfx/goldengate-circt-f2-build/iteration31-production-features/implementation-result.json`;
preserve its working components and focused SFC comparisons. Use as many
subagents as useful within the available concurrency slots, scheduling waves
when necessary. The coordinator should integrate rather than serialize all
independent work behind Rocket formal signoff.

- FAME5 owner: production hierarchy/group enablement, synchronous-read memory
  response state, retained renames, and reset/selector phase correctness.
- AutoCounter/Print owner: accepted-token/CSV target binding, enabled Print
  assembly, and feature-specific counter/print Linux checks.
- Multi-clock/model owner: production queue/transport assembly, model dependency
  fanout, memory-model coverage, and clock phase/reset behavior.
- NIC owner: independent multi-widget integration, compile-time and per-widget
  runtime loopback, and checked Linux packet exchange.
- Formal/acceptance support, when slots permit: diagnose retained Rocket
  counterexamples, connect enabled-case execution/receipts, and address the
  fail-fast orchestration that currently prevents separate SEC and other-target
  diagnostics after Rocket LEC fails. Preserve strict overall proof acceptance.

Before edits, declare ownership of files and shared APIs. The main agent owns
integration in tools/goldengate.cpp, CMake/build wiring, global MMIO/stream/header
allocation and verification dispatch; workers propose coordinated changes to
those files instead of editing them concurrently. Agree on model/state/clock
interfaces first, then implement independent consumers concurrently. Share one
bounded CPU budget across builds rather than starting several all-core builds.

Do not mutate the current verification candidate or launch duplicate long
Jasper/FPGA jobs while CHIA branches are active. After they join, implement the
next candidate, join every worker, integrate and check it, and return control to
CHIA for semantic -> parallel Jasper/F2 -> join -> compiler timing -> feature
acceptance. Each enabled representative still needs semantic comparison,
separate complete LEC/SEC, and checked Linux exercise/poweroff. Supporting unit
tests do not replace those gates.

## User clarification: preserve justified CIRCT correctness fixes (2026-10-25)

Do not change correct CIRCT behavior simply to reproduce an SFC bug. First
establish intended semantics with a focused reproducer and classify each
discrepancy as a CIRCT bug, an SFC bug, a justified protocol/uninitialized-state
difference, or unresolved. Preserve justified corrections and record deliberate
semantic deviations explicitly; do not claim strict equivalence for a changed
contract. Keep immutable goldens and strict overall acceptance intact.

Read `automation/chia_firesim_circt/logs/jasper-difference-audit-20261025.md`.
The retained Rocket B-response-ID witness is time-zero empty-queue payload:
SFC 0xffff versus CIRCT 0xfffd, with unmatched uninitialized RAM. It is not
evidence that CIRCT fixed an SFC bug, and does not classify all 30 failures.
The recent FAME5 old-data collision correction fixed CIRCT to match SFC;
preserve it. Diagnose state correspondence and transaction observability
before proposing source changes or protocol-qualified proof contracts. Do not
launch duplicate proofs while the CHIA Jasper node is running.

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
