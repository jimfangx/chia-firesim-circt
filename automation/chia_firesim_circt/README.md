Fresh clones: read [REPRODUCING.md](../../REPRODUCING.md) first. The historical
operator YAML files contain paths from the original verification host; configure
local compiler, artifact, toolchain and formal-flow locations before using them.
They are not portable verification receipts or prerequisites for source builds.

# FireSim CIRCT CHIA loop

## Integrated seven-case Linux verification

Use `loop.py --verify feature-linux-repair --verification-first` after integrating
a candidate. This mode checks the four fixed baseline designs, then runs exactly
seven feature-enabled F2 Linux workloads. It joins workers and farms before any
implementation turn, repairs failures automatically, and succeeds only when all
seven current-case feature checks, userspace, poweroff and cleanup pass together.
Formal, compiler-speed and baseline FPGA nodes remain disabled in this mode;
full-port acceptance remains separate. The active iteration is published in
`logs/feature-linux-repair-active.json`. Shared build/worker locks serialize local
Vivado and bound cloud execution; DRAM zero-out remains disabled.

Before manager commands, initialize Conda and use `source ./sourceme-manager.sh`
in `sims/firesim` with SSH setup enabled. On F2 the `--skip-ssh-setup` option must
not be used. A source change requires a fresh seven-case verification identity.
The default verification YAML selects the integrated immutable compiler pin;
rebuild and update its pin after future native compiler edits.

Multiclock monitor manifests use schema2 and derive reset acquisition from the
generated ResetPulse default and phase-zero ClockBridge declarations. Target
identity and accumulated events have separate exact reset prefixes; a slower
domain may legally mask its initial count-zero Print. The checker validates the
declared contract, all later event ordering, identity and rollover; it does not
infer a tolerance from observed output. Schema1 remains available only for
legacy single-clock observations. The SFC-derived oracle fixtures cover integer
and fractional ratios, odd/even reset lengths, and independent stalls.

The November 4 F2 transport repair uses explicit source acknowledgements rather
than consuming queue heads on PCI reads. Its optional sixth stream parameter is
enabled only for native F2 sources; five-argument SFC/U250 constructors retain
legacy behavior. Each 64-byte host read completes before a fenced source-count
write1 ACK and readback. The compiler validates the actual route and emits
`FireSim-generated.f2-cpu-stream-protocol.json`; the baseline comparison binds
that metadata, preserves the original five arguments, and reports raw ABI
differences and the protocol extension explicitly. This is not formal or FPGA
runtime signoff. See the current port plan and operator handoff for failure
evidence and active repair jobs.

Explicit ACKs require a backend that advertises non-destructive-read support;
the real F2 backend does. The current emulator's ID0 DMA path is destructive,
and F2xsim lacks the required payload path, so those backends reject this new
profile before any I/O. Their legacy five-argument stream path is retained.

For a shared transport repair, set `feature_linux.stop_on_case_failure: true`.
The default remains `false` for independent diagnostic coverage. When enabled,
the first failed case prevents queued dispatch; active jobs still finish and
join, and failed farms require verified cleanup. Unrun cases remain incomplete.

The active charter is [FIRESIM_CIRCT_PORT_PLAN.md](../../FIRESIM_CIRCT_PORT_PLAN.md).
The goal is correct and faster FireSim transformation through CIRCT for exactly
four baseline designs: Rocket, MegaBoomV3, plain Shuttle, and Saturn Shuttle.
Full completion also requires FAME5, enabled AutoCounter/Print, multi-clock and
generalized models, and NIC integration. QSFP/partition transport is deferred.

`selected_config_build_recipes.yaml` contains those four F2 recipes. Three are
copied from `sims/firesim-staging/sample_config_build_recipes.yaml`; plain Shuttle
is explicitly derived from the MegaBoom recipe because the sample lacks it.
The old thirteen-target U250 archive is preserved, but is no longer an acceptance
matrix. LargeBoom and U250 outputs do not substitute for MegaBoomV3 and F2.

## Verification graph

The November 3 user directive resumes full FPGA and Jasper verification; it
supersedes the formal-disabled feature-development phase below. To verify saved
work before another implementation call, submit `loop.py --verify full
--verification-first --iterations 9999 --model gpt-6.1-sol`. Implementation calls
now default to four hours; override with `--implementation-timeout-seconds N`.
Jasper's separate 24-hour proof limit is unchanged. See
[the restart handoff](../../CHIA_OPERATOR_VERIFICATION_20261103.md) for the pending
enabled-feature verification wiring; the feature audit itself only reads receipts.

```text
main Codex implementation call (waits for all delegated agents)
  -> semantic CHIA task: four golden syntax/semantic comparisons
      -> FireSim CHIA task: local F2 builds + manager-launched F2 runs --+
      -> Jasper CHIA task: LEC and SEC for all four -------------------+-> join
      -> enabled-feature Linux CHIA task: seven cases, checked on F2 --+
      -> speed CHIA task: matched SFC/CIRCT FireSim compiler wall times
      -> features CHIA task: audit enabled feature-case acceptance evidence
  -> completion check or next implementation turn with evidence
```

FireSim, Jasper and enabled-feature Linux dispatch before any is awaited. Timing runs after all
finish, avoiding local Vivado contention. All verification finishes before the
next implementation call may mutate source. A checkout-wide `.loop.lock`
prevents two cooperating loop drivers from editing one candidate concurrently.

The first node is a pre-build gate. All four selected candidates must pass the
declared static-semantic comparisons and complete paired SFC/CIRCT RTL and driver
syntax checks. Reports identify `verification_stage: prebuild`; `semantic: true`
does not claim full behavioral equivalence. Formal proof, post-synthesis timing
and FPGA execution are recorded under their respective `deferred_obligations`
and checked by Jasper/FireSim after fan-out. They are not prerequisites for
starting those nodes. Every target result is retained, even when another fails.

Use as many subagents as you see fit; there is no task-specific limit on delegation.
The main implementation agent must coordinate edits, integrate results and wait
for all subagents before returning. CHIA considers the main
LLM call complete when it exits. Long verification jobs belong in the verification
nodes, not background implementation agents.

Codex resumes by session ID using the worker's existing local session store.
The loop never snapshots or restores the shared `~/.codex` SQLite databases,
WAL/SHM files or rollout transcripts. A worker-side `.codex-exec.lock` also
rejects duplicate implementation calls after a driver failure. Ray task retries
are disabled for implementation calls, and a failed Codex response stops the
loop before verification or another implementation turn. Timeout cleanup kills
the CLI's process group, including descendant tool processes.

`SessionWriterConflict` means another writer owns the same Codex session; it
is not a billing error. Keep that session closed in other CLI/app clients while
the loop uses it. Do not delete writer locks or overwrite database files. After
the owner has exited, submit a new loop job: it starts a fresh session and reads
the current plan, sources and saved evidence. Existing compiler work and goldens
remain on disk. Follow progress with `chia job logs -f JOB_ID`.

A completion declaration is accepted only when all six nodes pass with the same
content identity and Codex emits `LOOP_COMPLETE: yes`. Four baseline passes alone
cannot finish the port. Missing adapters, stale
inputs, absent artifacts, incomplete proofs, failed workloads, and any target
that fails the speed goal keep the loop incomplete. Iteration exhaustion exits
with an error. `boot` aliases `full`; `none`, `preflight`, `compile`, and `metasim`
are diagnostic modes and cannot complete the new acceptance goal. The historical
Rocket metasim dispatcher/cache remains available for focused regression work.

## Enabled-feature FPGA Linux tests

The `feature_linux` node executes exactly the seven representatives in
`feature_scope.yaml` and `feature_cases.yaml`. It prepares actual enabled RTL and
drivers, freezes each case, submits at most two jobs, and joins every worker.
Vivado builds share the host build lock. Runtime-only NIC modes can reuse matching
hardware while their workloads and observations remain separate.

The FAME5 workload checks all four pinned logical CPUs and independent initialized
and written state. Instrumentation checks actual AutoCounter CSV, ordered Print
events and rational domain progress. NIC workloads exchange 128 checked packets
per widget, require distinct interfaces and joined guest/host workers, and cover
normal, two-NIC, compile-time and per-widget runtime loopback. Every case requires
Linux userspace, feature checks, clean poweroff, zero driver exit and farm cleanup.
DRAM zero-out stays disabled. Native/fixture controls never award an FPGA pass.

Guest programs use a separately inventoried RV64GC compiler/sysroot through
`guest_toolchain.py`. The final linked static ELF must pass ISA/ABI checks;
`-march` alone cannot constrain instructions in a prebuilt C library. The local
toolchain manifest lives under
`/scratch/jfx/chia-feature-linux-integration-20261103/guest-toolchain/` and binds
the compiler, binutils, headers and libraries. Its hashes affect runtime reuse,
while unchanged hardware can retain its bitstream. Guest failures emit explicit
failure markers and power off. Failed/stopped Ray workers require a separate
owned-farm cleanup receipt; uncertain cleanup stops further cases and source edits.

Linux-only receipts do not establish matched enabled-case SFC semantics or Jasper
signoff; the separate strict feature audit still requires them. Design-time reset,
arbitrary stalls and memory geometry also need supporting RTL/formal evidence.
See `feature_workloads/runtime_coverage.md` for the exact observation limits.

`run_feature_linux_job.py --repo CHECKOUT --config YAML --evidence-dir DIRECTORY`
runs fresh four-target semantics and this Linux node as a separate CHIA job. Use
an isolated checkout while another candidate is under verification, with private
candidate/compiler outputs. Point `CHIA_F2_BUILD_LOCK` at the manager's shared
build lock and `CHIA_FEATURE_WORKER_LOCK` at its shared feature-worker lock.

## Feature implementation phase

The user requested finishing implementation before resuming formal proofs. Use
`--verify feature-implementation` to keep this priority across turns. Each turn
joins the four baseline semantic comparisons; a failed comparison returns to
implementation for repair. A passing candidate is frozen before an independent
background F2 job is submitted. The next implementation starts immediately after
snapshot/dispatch, without joining FPGA execution. Source/config/RTL/driver inputs
for background jobs must be immutable; their logs and results stay distinct from
the changing checkout. Jasper and compiler benchmarks are not dispatched in this
phase. The default `full` graph and its final acceptance rules are unchanged.

The implementation agent may run targeted unchanged-SFC semantic/RTL checks,
driver builds and workload/checker validation. Feature-specific FPGA work must
also use isolated background execution, with resource coordination and cleanup;
do not wait for FPGA jobs or repeat four baseline builds inside the implementation
call. Join implementation subagents and their short checks before returning.
Independently tracked background F2 jobs continue against their snapshots, and
their retained results feed subsequent turns.

Per-turn responses, bounded feedback and `phase-result.json` are retained under
`logs/feature-implementation/<run-id>/`. Feature implementation continues until
Codex emits `FEATURE_IMPLEMENTATION_COMPLETE: yes` and a fresh `readiness.json`
at the exact path in that turn's prompt passes the implementation-readiness
validator. `LOOP_COMPLETE: yes` cannot stop or award acceptance in this phase.
Successful phase completion means implementation readiness, **not** full
verification; it never restarts formal proofs automatically.

The readiness schema is deliberately separate from feature acceptance receipts:

- `schema_version: 1`, `source_identity` from
  `feature_implementation_readiness.source_identity(REPO)`, `remaining: []`,
  and `compiler: {path, sha256}` identifying the built compiler.
- `features`: exactly the four required feature IDs, each with `implemented: true`,
  `production_integrated: true`, and `remaining: []`.
- `cases`: exactly the seven representatives in `feature_scope.yaml`, each with
  `case_id`, `feature`, and `artifacts` binding nonempty `config`, `rtl`, `driver`,
  `workload`, `checker` and `execution_adapter` files as `{path, sha256}`.
- Each case's `commands` lists `{path, sha256}` bindings to actual JSON command
  receipts. Receipts must cover `purpose: production_compile`, `driver_build`
  and `workload_checker`, with matching `case_id`, current `source_identity`, `argv` as a string list,
  `returncode: 0`, `timed_out: false`, and hash-bound `stdout`/`stderr` files.

The report must be written during the current implementation turn. All source
and file bindings are rechecked before acceptance of the milestone. Missing or
stale artifacts, remaining integration gaps and failed commands keep this phase
running. Do not fabricate completed formal/FPGA acceptance to populate readiness.
Actual Linux execution and formal proof remain required in the later full graph.

### Producing the bounded implementation matrix

After source changes and the compiler/generator builds are complete, run the
production matrix in a fresh evidence directory. It compiles every declared
representative, links its actual F2 driver, prepares a private Linux rootfs and
executes the native workload/checker controls. It does not dispatch FPGA work.

```bash
python3 automation/chia_firesim_circt/feature_implementation_matrix.py \
  --repo /scratch/jfx/fsim-circt \
  --compiler /scratch/jfx/goldengate-circt-f2-release-build/goldengate-circt \
  --generator-jar /absolute/path/to/current/firechip.jar \
  --output-dir /absolute/path/to/bounded \
  --readiness /absolute/path/from/current/implementation/prompt/readiness.json
```

Repeat `--case CASE_ID` to build a subset. Independent workers must own disjoint
case directories and use `--no-publish --result-name WORKER-result.json`; selected
success then exits zero without publishing readiness. Finish with one central
invocation over the same output directory, omitting `--no-publish`, to validate
and publish the complete matrix. Successful current-source stages are reused;
`--generator-jar` can be omitted when all requested cases already have valid
compile manifests. Failed, partial or stale evidence is preserved and requires
a fresh case/output directory for another attempt.

The runner computes `source_identity(REPO)` before and after the batch. Only
nested command stamps in that process use the cached initial value; workload
validator subprocesses and the final readiness validator hash sources afresh.
Source/compiler/generator drift aborts publication. Every case binds the exact
production config/recipe, emitted RTL, executable driver, private Linux
preparation manifest, checker source and isolated execution adapter. Real
command receipts cover production compile, driver build and workload/checker
validation. A complete candidate must pass `validate_readiness` before the
requested report is written, and the published report is validated again.

Without `--no-publish`, exit status zero means validated implementation readiness,
two means selected work passed but the full matrix remains incomplete, and one
means a failure. `matrix-result.json` (or `--result-name`) retains the precise
completed cases and outstanding evidence. FPGA submission remains a separate
explicit `feature_f2.py` operation against an immutable snapshot; its jobs never
delay this implementation command. Formal and FPGA acceptance are never inferred
from this matrix.

## Required feature implementation

`feature_scope.yaml` declares four required streams and bounded feature-enabled
representatives. Keep the original four baseline recipes; do not enumerate all
sample configurations. Parallel subagents may own FAME5, AutoCounter/Print,
multi-clock/generalized-model and NIC work, with coordinated shared-file edits
and one main agent joining and integrating all results before returning.

Every representative requires matched SFC/CIRCT semantic/syntax checks, separate
complete Jasper LEC and SEC, and F2 Linux boot, feature-specific exercise and
clean poweroff. Tiny supporting fixtures and ordinary Linux boot with the
feature disabled do not establish acceptance. NIC checks include real traffic,
distinct multiple-widget bindings and the existing loopback modes; QSFP remains
deferred. Compiler timing remains a separate SFC/CIRCT transformation metric.

The read-only `feature_acceptance.py` audit checks actual bound reports, command
receipts, formal signoff inventories and Linux logs. Missing feature execution
adapters/evidence keep completion blocked. Store case manifests at
`logs/feature-evidence/<current-identity>/<feature>/*.json`; its module docstring
defines the schema. Keep evidence out of the hashed scope YAML to avoid circular
identity dependencies. The implementation agent must connect feature-enabled
cases to the semantic/Jasper/F2 execution nodes before claiming completion.

FPGA reuse must be bound to actual RTL/build inputs; runtime reuse additionally
binds the driver, workload and validation policy. Preserve provenance and record
reuse instead of rebuilding identical hardware for unrelated source edits.

## Current readiness

The loop graph and fail-closed adapter runner are implemented. The semantic
node prepares immutable SFC archives, builds the native compiler in Release mode,
then replays missing or stale CIRCT candidates from the same archived inputs.
`verification.yaml` resolves both artifact lists from validated complete receipts.
The candidate build uses at most eight jobs for this manager's RAM; native
transformations use the worker's available CPU affinity. Failed compilations
retain per-target logs and partial inventories without publishing complete receipts.
See [the preparation contract](../../sims/firesim/sim/goldengate-circt/test/Support/GenerateF2CIRCTCandidates.md).

The semantic adapter now evaluates paired RTL/driver syntax and the selected
static contracts. It releases downstream work only when all four targets pass
for the same input identity. Formal equivalence and post-synthesis/runtime
checks are deferred to their own nodes, so synthesis has no circular prerequisite.

Real Jasper and F2 commands are configured. `run_jasper.py` dispatches separate
state-matching (`lec`) and sequential (`sec`) invocations on `eda` and requires
complete proof signoff. The installed SEC guide defines `state_matching` as
combinational cone equivalence with state cutpoints, while `proof` checks
sequential equivalence. LEC requires complete auto-cutpoint signoff and a
nonempty inventory of actual proven cutpoint properties. A helper equality or
compare-point report alone does not establish LEC. The adapter adds only a
reporting hook to its staged flow copy; unknown report schemas fail closed.
`--no-init-map` suppresses matcher-added initialization mappings; Jasper may
still create automatic reset-X mappings, which remain part of the proof scope.
LEC and SEC each have a **24-hour proof limit** (`--prove-time 24h`).
The remote wall deadline is 87,000 seconds: 24 hours plus ten minutes to
export proof reports and counterexamples. CHIA's adapter deadline is 87,600
seconds, adding another ten minutes for archive transfer and local cleanup.
This user update (2026-10-30) supersedes the earlier unlimited-time policy.
Explicit `--prove-time 0s` / `--wall-timeout-seconds 0` overrides remain
available for an intentional unlimited run. SSH setup/transfer timeouts and
the 128 GiB RSS watchdog remain enabled. Timeout, memory abort or incomplete
signoff remains failed verification; retain partial reports and stalled-run
archives for implementation diagnosis. Other node time limits are unchanged.
On a remote wall timeout, a hash-bound helper terminates the exact project's
remaining process sessions before fetching the archive. It records launcher
identities before proof, checks PID start times, preserves files and leaves
other projects untouched; failed cleanup remains explicit diagnostic evidence.

Both commands explicitly use `--max-mem-gb 128 --jobs 1`. The memory flag is a
five-second sampled process-tree RSS watchdog in GiB, rather than a reservation;
128 GiB exceeds the host's roughly 122.7 GiB physical RAM. One ProofGrid job does
not restrict OS threads. The adapter records available RAM/swap before each run
and the flow's rounded sampled peak RSS; missing measurements remain unknown.
Memory aborts remain incomplete and require more capacity or a sound partition.
`run_f2.py` builds the frozen accepted candidate locally through the
FireSim manager, then deploys its new AGFI to an F2 run farm and checks a
`br-base-uniform.json` Linux boot/poweroff workload. A completed build keeps its receipt if runtime
verification is still pending. These adapters do not claim successful proofs or
FPGA execution before those tools actually pass. The configured timing adapter
replays complete matched compiler launches and rejects any accepted-identity
change during measurement, retaining both snapshots and changed fields.

CHIA and Ray are now installed in `.conda-env`. Noninteractive SSH/login shells
must explicitly source `/home/ubuntu/miniforge3/etc/profile.d/conda.sh` before
the manager setup script can activate that environment; otherwise `ray` is
missing from PATH even though it is installed. Cluster startup and loop manager
commands include this initialization. Read-only Jasper preflight verifies SSH,
the installed executable and license setup script; it does not run a proof or
check out a license. Jasper and F2 execution starts only after the four-target
pre-build gate passes.

## Configure real adapters

Read `verification.yaml` for the exact command/report schema. Each adapter uses
an argv array and finite timeout; there is no implicit shell. Use an explicit
script or `bash -lc` if environment setup is required. Commands receive target,
recipe, CPU budget, report destination and content identity as environment
variables and argv placeholders. They must write fresh JSON; exit status alone
never passes a node. Full stdout/stderr, commands and results are retained.

- Semantic: bind actual SFC and CIRCT artifacts and all sidecars for exactly four targets; compare syntax and the semantic contracts specified in the plan. Do not use compile-only or metadata-only success.
- Jasper: separate LEC and SEC adapter commands, each producing actual proof status and unresolved-obligation counts. `eda` is `rocky@32.185.44.117`; use `/tools/Cadence/jasper_2026.06p002` and `/scratch/jfx`. Start from `/scratch/circt_compare` or local `/scratch/jfx/circt_compare/sec_flow`, checking its version and assumptions. The example SEC compare-point report is not a separate LEC run.
- FireSim: one adapter invocation per target, local `ExternallyProvisioned` build farm (`localhost`), explicit F2 run YAML/HWDB, manager `buildbitstream`, `launchrunfarm`, `infrasetup`, `runworkload`, and cleanup in `finally`. Verify the newly built CIRCT image and workload results. `~/firesim.pem` is the F2 SSH key.
- Speed: at least three matched FireSim compiler wall-time samples per target, from the same pre-MIDAS FIRRTL/annotations to equivalent transformed simulator RTL/collateral. Set `timing_scope: firesim_compiler`. The median CIRCT/SFC compiler-time ratio must be below one for each target. Include compiler startup and required compiler stages; record JVM startup/warm conditions and compiler-invoked bridge elaboration costs. Simulation/workload runtime, bitstream builds, deployment and proof time are excluded.

List actual compiler binaries, manifests and external configuration inputs in
`identity_files`; hashes must include every input that affects verification.
The runner hashes artifacts and configuration and rejects changes during a node
or between semantic comparison and its dependents. Adapters must consume frozen
candidate inputs; manager RTL regeneration must select CIRCT and preserve the
accepted provenance. Reports must describe proof/comparison scope and exclusions;
the runner cannot determine equivalence from a self-reported boolean alone.

The local F2 farm override and F2 runtime details are in the plan. Follow the
[FireSim F2 setup guide](https://docs.fires.im/en/latest/getting-started-guides/aws-ec2-f2-getting-started/setting-up-the-firesim-repo/)
and its build/run guides. Never use the default AWS build farm when the build
must run locally, and never use a stock image as candidate evidence.

F2 acceptance now requires Linux boot and clean poweroff on all four targets.
`prepare_f2_linux.py` reuses the built kernel from `br-base-uniform.json` and
clones the rootfs for each tagged run. Its existing FireMarshal `S99run` hook
checks Linux userspace, procfs, sysfs and `/etc/os-release`, prints
`CIRCT_LINUX_BOOT: PASS`, then runs `sync; poweroff -f`. Base images/template stay
unchanged. The UART validator requires the kernel banner, ordered guest markers,
`reboot: Power down`, driver PASS and script exit code zero; a baremetal smoke
result cannot satisfy this gate. Runtime-input receipts retain image, script and
template hashes. Workload execution remains excluded from compiler speed timing.

The base is already built on this manager. To rebuild/install it if needed:

```bash
cd /scratch/jfx/fsim-circt/software/firemarshal
./marshal -v build br-base.json
./marshal -v install br-base.json
```

For image-only preparation with no F2 instance or bitstream build:

```bash
.conda-env/bin/python automation/chia_firesim_circt/prepare_f2_linux.py \
  --repo /scratch/jfx/fsim-circt --output-dir /scratch/jfx/linux-poweroff-preflight
```

## Start and observe

Use the manager environment with CHIA, Ray and authenticated Codex installed;
no OpenAI API-key integration is used. Initialize AWS F2 once if needed. Check
localhost SSH for CHIA/FireSim and `ssh -o BatchMode=yes eda true` for Jasper.
On F2, use `source ./sourceme-manager.sh` with SSH setup enabled. Do not pass
`--skip-ssh-setup`: the setup loads `~/firesim.pem` for access to run hosts.

```bash
source /home/ubuntu/miniforge3/etc/profile.d/conda.sh
cd /scratch/jfx/fsim-circt/sims/firesim
source ./sourceme-manager.sh
cd /scratch/jfx/fsim-circt
mkdir -p sims/firesim/deploy/{build-farm,run-farm}
chia up automation/chia_firesim_circt/cluster.yaml
chia status --chia-cluster automation/chia_firesim_circt/cluster.yaml
chia job submit -- python /scratch/jfx/fsim-circt/automation/chia_firesim_circt/loop.py \
  --iterations 12 --verify full \
  --verification-config /scratch/jfx/fsim-circt/automation/chia_firesim_circt/verification.yaml
```

Do not pass Ray's `--working-dir`: this worker shares the same checkout and
uploading the generated repository is unnecessary. Distinct CHIA resources in
`cluster.yaml` identify tasks on one manager; they are not separate FPGA hosts.
The Jasper adapter performs remote proof execution, and FireSim Manager owns
F2 instance provisioning and lifecycle.

`control.yaml` selects the model for the next iteration; `--model` supplies a
fallback. The loop streams Codex CLI JSON and persists its transcript under
`logs/codex_<timestamp>.log`. Verification records are in
`logs/verification/<run-id>/{semantic,jasper,firesim,speed}/`; `graph.json`
contains the joined result and feeds the next implementation turn.

The full `graph.json` stays on disk. Only a bounded summary of node/target
status, errors and evidence paths enters the next prompt, also saved as
`feedback.txt`. Large inventories and tool transcripts are not embedded in
prompts. The worker checks the complete formatted prompt against a local
200,000-character budget before launching Codex.

After a failed driver, restart with the previous graph as diagnostic context:

```bash
chia job submit --no-wait --submission-id firesim-circt-recovery -- \
  /scratch/jfx/fsim-circt/.conda-env/bin/python \
  /scratch/jfx/fsim-circt/automation/chia_firesim_circt/loop.py \
  --iterations 9999 --verify full --feedback-from /absolute/path/to/previous/graph.json
chia job logs -f firesim-circt-recovery
```

Use a fresh submission ID for each restart. `--feedback-from` preserves failure
diagnostics; it does not reuse old acceptance or skip current verification.

```bash
chia job list
chia job logs -f <job-id>
chia job stop <job-id>
chia down automation/chia_firesim_circt/cluster.yaml
```

Allow the FireSim adapter to terminate its tagged run farm before stopping.
Forced termination can bypass cleanup; inspect manager state and clean up that
specific farm explicitly. Stop the old job before replacing its local cluster.
The updated Python driver is loaded on a new submission; changing source does
not replace a driver that is already running.

For local orchestration checks without Ray, SSH, proofs or FPGA work:

```bash
.conda-env/bin/python -m unittest discover -s automation/chia_firesim_circt -p 'test_*.py'
```
