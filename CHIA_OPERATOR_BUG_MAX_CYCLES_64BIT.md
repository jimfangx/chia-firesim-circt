# Premature Linux timeout: max-cycles integer overflow — 2026-10-23

The user asked what timeout was configured for iteration-5 Rocket Linux.
The supervisor found a concrete driver parsing bug while checking the logs.

- Generated `runtime.yaml` sets `+max-cycles=100000000000` (100 billion).
- Each FireSim manager runtime action has a separate 7200-second wall timeout.
- Actual Rocket UART reports simulation timeout after 19,452,035,058 displayed
  target cycles and 217.9 seconds wall time, with script exit 1. Linux reached
  kernel initialization, without a userspace-success or poweroff marker.
- `sims/firesim/sim/midas/src/main/cc/core/systematic_scheduler.cc:11` parses
  the argument with `atoi`, which returns a 32-bit int, despite the receiving
  `max_cycles` being `std::optional<uint64_t>`. 100 billion is out of range.
- An independent call to this manager's libc `atoi("100000000000")` returned
  1,215,752,192. Times 16 is 19,452,035,072, consistent with the logged expiry
  after accounting for the different displayed clock/counter initialization.
  The original cap was not faithfully represented; do not diagnose this as
  exhaustion of the intended 100-billion-cycle allowance or a two-hour timeout.

Fix the driver parser with checked 64-bit numeric conversion (including invalid,
negative, trailing-junk and overflow handling) and a meaningful regression for
100,000,000,000, then rebuild the driver before retrying Linux via the CHIA gate.
Do not change immutable goldens or Scala transformation semantics. The RTL/AGFI
need not change solely for an argument-parser fix, although reuse still requires
valid provenance. Preserve failure receipts and do not mark Linux boot passed.
No source change or duplicate run was launched by this diagnostic handoff.
