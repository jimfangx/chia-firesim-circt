# New user-requested F2 acceptance — 2026-10-22

The user requested replacing the basic baremetal F2 workload with Linux boot
using `br-base-uniform.json`, followed by `poweroff` after successful boot.
The interactive supervisor implemented this in the shared checkout. Preserve
and integrate the change on the next implementation turn; do not restore the
baremetal workload as sufficient F2 acceptance.

Files changed: `automation/chia_firesim_circt/run_f2.py`, new
`prepare_f2_linux.py` and `test_prepare_f2_linux.py`, `verification_nodes.py`,
`verification.yaml`, adapter/node tests, README and the top of the port plan.

- The user's `./marshal -v build br-base.json` and
  `./marshal -v install br-base.json` succeeded from the FireSim/Conda environment
  using cached images; no kernel/rootfs rebuild was needed.
- The existing br-base-uniform JSON is the template. Each tagged workload uses
  its built kernel and a private cloned rootfs. The verified FireMarshal
  `/etc/init.d/S99run -> /firemarshal.sh` hook checks Linux/proc/sys/rootfs,
  emits `CIRCT_LINUX_BOOT: PASS`, then `CIRCT_LINUX_POWEROFF: REQUESTED`,
  `sync; poweroff -f`. Base images/template hashes remain unchanged.
- F2 UART acceptance requires ordered kernel banner, userspace marker,
  shutdown request, `reboot: Power down`, driver PASS and script exit code zero.
  Kernel panic, Oops, explicit failures, missing shutdown or the former smoke
  output cannot pass. Node reports require Linux boot/poweroff booleans and
  a hashed workload-input receipt. All four targets must pass this new workload.
- `linux_max_cycles` is 100,000,000,000, distinct from the unchanged compiler
  speed metric. Runtime/fan-out lifecycle remains FireSim manager-owned.

Validation: all 107 automation tests pass; a real private ext4 image passed
readback/mode/base-immutability/fsck checks; the production FireSim WorkloadConfig
parser resolved the generated uniform workload's Linux kernel and private disk.
Evidence: `/scratch/jfx/f2-linux-preflight-20261022-checked/`, base build log
`/scratch/jfx/f2-linux-preflight-20261022/marshal-base.log`, and full runtime config
`/scratch/jfx/f2-linux-runtime-preflight-20261022/runtime.yaml`.

No new FPGA instance, bitstream, workload run or duplicate proof was launched by
this supervisor change. The already-dispatched verification cycle may retain
its old smoke workload; its previous input identity must not be reused. Obtain
fresh acceptance through the ordinary CHIA graph. Earlier four-target smoke
passes are useful history, not Linux-boot proof. Do not claim Linux has booted
on FPGA until fresh UART evidence satisfies the new gate.
