# Rocket bare-metal regression tests for CIRCT Golden Gate

All C sources and RV64GC/lp64d ELF executables live in this directory. They
reuse FireMarshal's bare-metal startup, printf, and tohost exit support.
`make` and the live Chia configuration still select the original
`circt-metasim-bare` smoke test. The new tests are built separately with:

```sh
./build.sh suite
make validate-spike
make manifests
python3 -m unittest -v test_validator
```

Use `CC=/path/to/riscv64-unknown-elf-gcc` to select another toolchain. The
default finds the installed compiler or the repository's local toolchain.
The new suite uses its own linker script and objects under `.fame-build`.

## Executables and coverage

| Executable | Checks and purpose |
| --- | --- |
| `circt-fame-suite` | All 11 portable groups below; 90,141 checks. |
| `circt-fame-rocket-suite` | The portable suite plus direct UART MMIO and ordered output. |
| `circt-fame-integer` | 64-bit and word arithmetic; all signed/unsigned multiply-high variants; division by zero and overflow; bit-serial multiplication/division oracles; masked shifts; logic; comparisons; x0. |
| `circt-fame-memory` | 128 KiB working set, cacheline/page strides, permuted stores/loads, walking bits, little-endian byte lanes, byte/halfword/word write masks and sign extension, dependent read-after-write, unaligned memcpy with guards. |
| `circt-fame-control` | All six conditional branches, indirect calls/returns, recursive stack guards, switch dispatch, explicit compressed instructions. |
| `circt-fame-atomics` | All nine AMOs in word/doubleword widths with acquire/release; signed/unsigned min/max; LR/SC success and reservation consumption. |
| `circt-fame-csr` | CSR read/write/set/clear and immediate variants; hart/ISA identity; cycle and retired-instruction progress. |
| `circt-fame-traps` | Machine ECALL, EBREAK, illegal writes to a read-only CSR, compressed breakpoint/illegal instruction, register save/restore and MRET recovery. |
| `circt-fame-vm` | Sv39 translated reads/writes via MPRV, PMP access, page boundaries, TLB remapping with SFENCE.VMA, read-only store fault and unmapped load fault with MTVAL checks. |
| `circt-fame-interrupts` | CLINT software and timer interrupts, acknowledgement, bounded delivery checks, WFI wakeup, timer progress. |
| `circt-fame-fp` | Single/double arithmetic, sqrt, fused multiply-add, conversions and rounding, subnormals, signed zero, NaN, exception flags, classification, comparison, and FP memory traffic. |
| `circt-fame-icache` | Repeated execution of patched instructions after FENCE.I and explicit compressed instruction fetch. |
| `circt-fame-workloads` | CRC32 and three known SHA-256 vectors, signed matrix multiplication with a closed-form oracle, dependent pointer chasing, permutation sorting. |
| `circt-fame-uart` | 663 direct UART bytes, exceeding the target's 256-entry TX FIFO, FIFO-full polling, eight ordered epochs and a completion marker. |

The portable suite and all eleven portable standalone programs passed Spike.
Logs, deterministic reference counts/signatures, and ELF hashes are retained
under `spike-results/`. The combined portable signature is
`0x78194504c338c229`. `binary-inventory.json` inventories all 14 new binaries.
Spike supplies ISA validation of the test programs; it does not execute the
transformed CIRCT simulator. The two UART-bearing executables are compiled
but require the actual Rocket UART model to validate their MMIO behavior.

## Target assumptions

These tests target the recorded single-hart FireSimRocketConfig:

- RV64IMAFDC with machine mode, supervisor/Sv39, PMP, counters, and writable DRAM.
- DRAM/load address `0x80000000`; code, data, page tables, TLS, and stack fit
  comfortably in the first MiB. The VM test temporarily opens PMP entry 0 and
  restores it; it expects that entry to be unlocked at startup.
- CLINT at `0x02000000`, hart 0 MSIP at offset 0, MTIMECMP at `0x4000`, MTIME
  at `0xbff8`. The interrupts test assumes no unrelated interrupts are active.
- SiFive UART at `0x10020000`, 256 TX entries, 1 GHz peripheral clock and
  `UARTKey.div=271` in the immutable handoff. The UART peripheral divisor is
  programmed to 270 (271 clocks per bit), matching that host bridge.
  Override `FAME_UART_BASE`, `FAME_UART_DIVISOR`, `FAME_UART_FIFO_DEPTH`, or
  `FAME_CLINT_BASE` in `SUITE_CFLAGS` for another target.

Absolute addresses, cycle counts, and interrupt arrival times are excluded
from signatures. Page-fault addresses are fixed virtual addresses. Cycle
and timer checks compare ordering/progress only. Tests return nonzero on
failure; unexpected traps report `FATAL` and exit immediately. WFI and an
unresponsive tohost channel can still prevent progress: retain FireSim's
simulation-cycle limit and an external wall-time timeout.

The runtime's `printf` uses FESVR/tohost writes; those messages appear in
the simulator's `uartlog` but do not by themselves test the target UART.
`circt-fame-uart` and `circt-fame-rocket-suite` exercise actual UART MMIO and
require the eight `FAME_UART: DATA` lines to appear intact and in order.

## Running on FireSim

`make manifests` creates one descriptor per executable in `firesim-workloads/`.
Each descriptor points to its ELF and the existing dummy rootfs with absolute
paths. The Chia `regression_gate.py prepare` installer copies these inputs to
`sims/firesim/deploy/workloads/<binary>/` and installs a descriptor using local
filenames, as required by FireSim's workload loader. Select the installed
descriptor in a **separate** runtime YAML, for example:

```yaml
metasimulation:
  metasimulation_enabled: true
  metasimulation_host_simulator: verilator
workload:
  workload_name: circt-fame-suite.json
```

Run the manager-owned builddriver/launchrunfarm/infrasetup/runworkload sequence
already used by Chia. Keep the current live runtime YAML and smoke workload
available. Use the standalone programs to narrow a failure, and raise the
cycle limit if the simulator reaches its limit before completing the suite.

The validator checks exactly one complete suite, the ordered BEGIN/PASS pair
for every selected test, nonzero check counts, matching aggregate counts,
absence of failures, and (when present) the direct UART payload:

```sh
python3 check_uart.py /path/to/uartlog \
  --binary circt-fame-suite --reference spike-results/expected-results.json
```

For differential testing, run the **same ELF** through the SFC and CIRCT
simulators. Retain the SFC result as a reference, then check the candidate:

```sh
python3 check_uart.py /path/to/sfc/uartlog \
  --binary circt-fame-rocket-suite --write-reference sfc-expected.json
python3 check_uart.py /path/to/circt/uartlog \
  --binary circt-fame-rocket-suite --reference sfc-expected.json
```

Also exercise supported host memory latency/bandwidth and bridge backpressure
settings across multiple runs. Instruction and memory dependencies make
missing/duplicated tokens or wrong clock advancement visible through result
checks and signatures. A target C program cannot directly inject arbitrary
FAME input/output token schedules or host reset. These workloads therefore
supplement the plan's independent FAME semantic testbench and do not establish
its full token, reset, multiclock, FAME-5, or annotation correctness criteria.
Printf-synthesis and AutoCounter instrumentation likewise need enabled
compiler fixtures and separate bridge checks; a C printf call uses FESVR.

ISA corner cases follow the official [unprivileged manual](https://docs.riscv.org/reference/isa/_attachments/riscv-unprivileged.pdf)
and [privileged manual](https://docs.riscv.org/reference/isa/_attachments/riscv-privileged.pdf).
Platform addresses, UART clocking and FIFO size come from the immutable
FireSimRocketConfig DTS/annotations and `generators/firechip/chip/src/main/scala/TargetConfigs.scala`.
