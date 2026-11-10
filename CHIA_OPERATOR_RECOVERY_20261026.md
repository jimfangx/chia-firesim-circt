# Resume interrupted production-feature implementation

The previous job `firesim-circt-full-scope-20261023` failed at
2026-10-26 06:40:46 UTC during its seventh implementation turn. The streamed
stdout first reports "Selected model is at capacity. Please try a different
model."; stderr subsequently reports missing rollout threads. The backend's
UnknownCodexError exposed the latter rather than the original capacity event.
This is not a new compiler acceptance result. No automatic model change is
authorized by this handoff; preserve the selected live model.

Before recovery, both .loop.lock and .codex-exec.lock were free, Ray had no
running/pending jobs, and no implementation CLI, compiler build, Jasper or F2
adapter remained active. Preserve all shared-worktree changes and immutable
goldens. The new driver starts a fresh Codex session and uses the last complete
graph as bounded diagnostic feedback, without restoring any session database.

## Last complete verification

`automation/chia_firesim_circt/logs/verification/20261023T211611Z-pid2768860-iter6-full/graph.json`
records all four baseline semantic, F2 Linux/poweroff and compiler-speed passes.
Rocket LEC remains incomplete: six boundary targets proven, thirty unreachable
preconditions, forty-eight undetermined; four actual automatic cutpoint
counterexamples and thirty-one undetermined cutpoints. Separate SEC was not
reached. Mandatory enabled-feature acceptance remains blocked with no case
receipts. These passes belong to the pre-interruption candidate.

## Partial implementation to reconcile before new verification

Read the final implementation turn in
`automation/chia_firesim_circt/logs/codex_20261023_211611.log` and the partial
evidence under `/scratch/jfx/goldengate-circt-f2-build/iteration35-production-feature-composition/`.
The last complete implementation receipt is
`/scratch/jfx/goldengate-circt-f2-build/iteration34-loopback-mmio-ordered-events/implementation-result.json`.
There is no successful completion receipt for the interrupted turn.

Work in progress includes NIC loopback CPU stream catalogs/queues and driver
assembly, two enabled CSV AutoCounter hosts bound to a FAME model, internal
ready-valid/model composition, and dynamic FAME5 phase diagnosis. The prior
agent delegated feature work; inspect and reconcile the resulting files rather
than assuming they were centrally integrated or reverting them wholesale.
Use the partial source-before/oracle-before manifests to identify changes.

The transcript records earlier NIC fixture/catalog/MMIO errors, followed by
repairs and a successful reset-qualified 22,000-cycle comparison command. One
earlier random-initial-RAM comparison incorrectly included cycle zero before
the first host reset edge; the last command starts comparison after that edge.
Independently validate the final fixture and negative controls; do not infer
full production parity or waive defined behavior from that scoped run.
Development compiler/NIC test builds completed, but the interrupted turn did
not finish central Development/Release builds and final regression/receipt
checks. Complete those checks before returning to CHIA.

## Keep the current user requirements

Read the active port plan and
`CHIA_OPERATOR_UPDATE_REQUIRED_FULL_PORT_FEATURES.md`. Parallelize independent
feature work with explicit shared-file ownership, integrate centrally, and join
all workers before returning. Fix the production model/state/protocol relation
and actual enabled feature execution; tiny supporting tests are insufficient.
Do not launch duplicate baseline FPGA/proof jobs from the implementation call.

Unwritten/uninitialized RAM contents need not match bit-exactly. Keep
zero_out_dram false: do not clear DRAM to satisfy comparison. Preserve defined
initialization, written data, valid/ready/control, memory semantics and observable
transactions. Retain justified CIRCT fixes rather than mimicking a proven SFC
defect. QSFP stays deferred; exactly four baseline configurations stay fixed.
