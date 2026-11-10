# User request for the main implementation loop — 2026-10-22

The user requests increasing Jasper's memory limit to 128 GB. At the next
implementation boundary, configure BOTH LEC and SEC invocations explicitly with
`--max-mem-gb 128 --jobs 1`, and align the adapter default, YAML comments and
documentation. Keep full proof requirements and fail-closed results unchanged.
Do not change source/configuration during the current verification or start a
duplicate proof. The current iteration-2 Rocket LEC was already dispatched with
the old 100 limit; that invocation will retain its original arguments.

Implementation facts independently checked in the existing local flow:

- `/scratch/jfx/circt_compare/sec_flow/run_sec.py` accepts `--max-mem-gb`.
  Its watchdog sums the Jasper project's process RSS, divides by `2**30`, polls
  every five seconds, and kills the proof process tree if the threshold is
  exceeded. The flag is therefore measured in GiB despite its GB name. It is a
  sampled watchdog, not a kernel-enforced allocation/reservation limit.
- `--jobs 1` sets the ProofGrid job count. It reduces concurrent proof-engine
  model copies; it is not an RSS limit or a guarantee of one OS thread.
- `run_jasper.py` currently defaults to `--jobs 1 --max-mem-gb 100`.
- `eda` currently has 131,732,537,344 bytes of physical RAM (about 122.7 GiB),
  128 GiB swap, and 32 logical CPUs. A 128 GiB RSS threshold exceeds physical
  RAM; raising this watchdog does not create RAM or guarantee proof capacity.
  Record available memory and peak RSS with the new run. If the enlarged proof
  does not fit, report the need for a larger host or a sound partitioned proof;
  do not silently weaken equivalence coverage or interpret a memory abort as pass.

Apply through the ordinary CHIA verification path after the active branches join.
