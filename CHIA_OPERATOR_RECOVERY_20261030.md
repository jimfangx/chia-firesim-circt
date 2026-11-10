# Restart after stalled unlimited Jasper attempt, 2026-10-30

The user requested a 24-hour Jasper limit, restart, and preservation of this
run's results for bug fixing. This supersedes the earlier unlimited-time policy.
Read this handoff before the next implementation turn. Preserve compiler work,
immutable SFC goldens, and the existing four baseline recipes.

## Stopped attempt and retained results

The previous driver was `firesim-circt-recovery-20261026-0840`. Its active graph
was `20261026T083504Z-pid1709906-iter3-full`. All four semantic comparisons and
F2 Linux boot/poweroff runs passed. Rocket LEC ran over 80 hours; the Ht engine
hit an internal limit at cycle 16 on October 29 and exited. The parent Jasper
session then waited without new log output for over 28 hours. Separate SEC and
the current iteration's compiler-speed/feature audit did not execute.

Local retained evidence:

- `automation/chia_firesim_circt/logs/jasper-recovery-20261030/`
- `pre-stop-proof.tar.gz`: complete proof directory before any stop signal,
  including internal engine state, SQLite database/WAL/SHM, logs and mappings.
- `preservation-result.json`: archive checksum and selected evidence hashes.
- `counterexample-inventory.json`: all 5,974 distinct engine witness properties,
  bounds and elapsed times extracted from the original log.
- `pre-stop-selected/proof/`: directly readable log, config, state map and
  original interface/mapping reports. Original RTL/input archive remains in the
  old iteration's `jasper/rocket/lec/bundle` and `formal-inputs.tar`.
- `stopped-proof.tar.gz` and termination metadata capture post-interrupt state,
  when available. See `recovery-graph.json` for bounded diagnostic feedback.

Remote project and duplicate archive storage are preserved on `eda` under
`/scratch/jfx/chia-formal/8dfc6b44ef84485ea7d58fba050d5cd8-rocket-lec/`.
Operator snapshots are under `operator-recovery-20261030/`. No golden, candidate
RTL or existing result receipt was rewritten to claim success.

SIGINT to this run's exclusive Jasper process group initiated proof shutdown
and emitted a ProofGrid summary, but no final status/signoff reports appeared
after several minutes. The remaining processes were terminated under the user's
restart authorization. Raw retained engine witnesses are not complete signoff,
nor are they 5,974 independently established compiler bugs.

## Use these results before another long proof

There are 34 actual automatic-cutpoint witnesses in the inventory. They include
the four earlier one-cycle failures (TSI enqBitsLast twice, BlockDev wBeatCounters,
ResetPulse pulseLength), AXI ready-valid channel fired state at four cycles,
ClockBridge/TSI cycle-token state at five cycles, TSI and BlockDev channel fired
state at six cycles, bridge cycle/counter state at seven cycles, and ResetPulse
doneInit at eight cycles. Other helper witnesses extend through sixteen cycles,
including UART counters, FASED read-buffer pointers and target async queues.

Classify causes using the exact frozen spec/imp, reset sequence and state map:
incorrect correspondence, unspecified initial state, incorrect assumptions,
unobservable payload, or a reproducible defined-behavior compiler defect. Keep
valid/ready/control, initialized/written data, clock/reset/mask/latency semantics
and full actual cutpoint signoff strict. Do not simply rerun the unchanged
mapping for another day or count helper names as established functional bugs.
Extract focused reproducers and useful traces from preserved engine data where
possible; record any recovery limits instead of inventing completed reports.

Unwritten RAM need not be bit-exact. Keep DRAM zero-out disabled; no clearing
workaround. Retain justified CIRCT fixes when an SFC defect is actually shown,
without silently broadening acceptance or mutating the immutable reference.

## New time policy and continued work

Both LEC and SEC use a 24h proof budget, a remote wall cap of 87,000 seconds
(24h plus ten minutes for reporting), and a CHIA adapter cap of 87,600 seconds
(another ten minutes for collection/cleanup). Preserve 128 GiB RSS protection,
one proof job, strict signoff and bounded SSH setup/transfer. Remote wall-time
expiry must clean up only that project, including detached engine sessions;
keep partial reports and return a failed/incomplete result, never a proof pass.

Prioritize formal correspondence/reproducers alongside remaining feature work.
Use parallel owners with explicit shared-file ownership, integrate centrally,
and join all workers before returning to CHIA. FAME5, instrumentation/Print,
generalized multi-clock models and NIC production representatives still need
their enabled semantic, separate LEC/SEC and feature-exercising F2 Linux/poweroff
receipts. QSFP remains deferred. Compiler speed remains SFC-versus-CIRCT whole
FireSim compiler time, excluding workload runtime.
