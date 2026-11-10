# User directive: finish implementation; pause formal verification

The user explicitly directed: kill the formal proof and finish the rest of the
implementation so the port is feature complete, as the first priority. This
supersedes the repeated implementation -> full baseline verification cadence
for the current development phase. Formal acceptance is still required later;
do not restart long Jasper jobs between feature-implementation turns.

Latest cadence update: after each completed implementation call, CHIA runs the
four baseline semantic checks. Repair failures immediately. On semantic success,
freeze and launch an isolated FPGA build/run in the background and start the
next implementation turn without waiting. The background job belongs to the
frozen candidate and must not observe subsequent source/driver/platform changes.
Keep its identity-bound diagnostics and feed failures back. Serialize Vivado,
deduplicate identical actual inputs, bound pending jobs and retain all history.
Do not change final acceptance or resume formal before feature completeness.

The old driver `firesim-circt-feature-complete-20261102T114233Z` was stopped.
Its current four-target semantic and F2 Linux boot/poweroff checks passed;
Rocket LEC was interrupted without signoff. Exact frozen input files, mappings,
logs, engine snapshot and raw witnesses are retained under the old iteration
`20261102T114235Z-pid348909-iter1-full` and
`automation/chia_firesim_circt/logs/feature-first-20261102/`.
Missing/skipped formal, current compiler-speed and enabled-feature checks remain
incomplete. These historical baseline passes do not validate subsequent edits.

## Work to completion, not another isolated component checkpoint

Read the latest completed implementation report:
`/scratch/jfx/goldengate-circt-f2-build/iteration41-full-platform/implementation-result.json`.
That turn implemented full F2 Print and selected NIC composition. Preserve its
working paths and matched supporting comparisons rather than reimplementing
those helpers. Finish the remaining production paths and their usable build,
driver, workload and checker tooling. The milestone requires actual enabled
representatives of every required feature, not just declared support flags.

1. FAME5: handle production `PlusArgTimeout_3/plusarg_reader:plusarg_reader_3`
   external-module sharing with justified semantics, multiple repeated model
   groups, and startup/reset selection. Generate full production threaded RTL
   and usable thread/clock/channel metadata, drivers and a Linux representative
   that exercises all logical threads and independent state/memory. Do not
   silently remove PlusArg/timeout functionality or thread-unsafe blackboxes.
2. AutoCounter/Print: finish actual original-platform CSV widgets and shared
   allocation/catalog/header/driver integration with enabled Print. Preserve
   real triggers, resets, backpressure and event order. Implement a combined
   CSV+Print Linux workload and check expected prints and counter samples.
   Keep the retained SFC TriggerWiring failure explicit and use justified SFC
   comparisons of supported equivalent boundaries; do not alter the oracle.
3. Models/clocks: integrate generalized model and domain boundaries through
   complete F2 clock/transport/platform/driver paths. Provide a multi-model,
   rational multi-clock Linux representative with activity/ratio/stall checks.
4. NIC: finish normal and two-widget production composition, compile-time
   loopback and per-widget runtime loopback with complete target/clock/memory,
   F2 shell, streams, headers and usable drivers. Resolve the retained SFC
   pauser/driver defect scope with documented intended behavior. Supply packet
   workloads/checkers that verify real independent traffic rather than idle
   widgets. Reuse one bitstream for runtime-only changes when input hashes match.
5. Enabled execution: implement runnable adapters for every representative in
   feature_scope.yaml, with all config/RTL/driver/workload/checker/adapter files
   and successful compilation/validation command receipts. The feature audit
   already exists; another checklist does not substitute for these paths.

Use as many concurrent subagents as useful with explicit file ownership.
Coordinate common compiler/platform/clock/state/MMIO APIs before edits. The main
agent integrates centrally, resolves conflicts, completes meaningful compiler,
RTL, driver and workload/checker tests, and joins every worker/subprocess before
returning. Use all available cores within a shared memory/CPU budget. Keep
working across turns until implementation readiness is supported by real
artifacts and evidence; return incomplete with precise blockers when necessary.

## Development-phase verification and completion

The `feature-implementation` loop phase runs the four baseline semantic checks
between coding turns and dispatches immutable FPGA work without waiting. It
deliberately omits Jasper and compiler benchmarks during implementation. Run
relevant matched-input SFC/native semantics, syntax, RTL, driver and
execution-tool checks as part of implementation. Submit enabled-feature FPGA
builds/runs through isolated background execution adapters when needed to debug
integration; retain job ownership/cleanup receipts without waiting in the coding
turn. Consume their results in subsequent turns. Do not launch long formal
jobs. Coordinate full compiler builds so independent owners do not start
competing all-core rebuilds.

The phase's implementation-readiness report binds all four features and seven
declared cases to actual files and successful commands; it is a separate
milestone, not full verification. Keep feature implementation, production
integration, Linux exercise and formal signoff distinct in every status report.
Missing drivers, runtime/checker logic, execution adapters, production enablement
or remaining unsupported required shapes mean implementation is not ready.

Final port completion still needs current exact-four baseline semantic checks,
separate LEC/SEC, matched SFC/CIRCT compiler-speed evidence and feature-exercising
F2 Linux/poweroff receipts. Resume that final acceptance phase once the user
requests it after implementation readiness; do not automatically restart formal
verification while this feature-first development directive remains active.

Preserve immutable goldens and justified CIRCT corrections. Unwritten RAM need
not be bit-exact; leave zero_out_dram false and retain strict defined-state,
control, live-data, reset/mask/latency semantics. The eventual Jasper budget
remains 24h/128GiB. QSFP remains deferred. Keep exactly four baseline targets and
the seven bounded enabled-feature representatives; no sample-config enumeration.
