# Operator handoff — 2026-10-22 00:23 UTC

New concrete timeout diagnosis: read `CHIA_OPERATOR_BUG_MAX_CYCLES_64BIT.md`.
The configured 100-billion-cycle cap overflows the existing driver's `atoi`
parser. Fix checked 64-bit parsing before interpreting/retrying Linux timeout.

Latest user requirement implemented: read `CHIA_OPERATOR_UPDATE_F2_LINUX_POWEROFF.md`.
F2 acceptance now requires br-base-uniform Linux boot and explicit poweroff;
the previous baremetal checksum smoke is insufficient. All 107 automation tests
pass; actual FPGA Linux boot still needs fresh CHIA evidence.

F2 adapter fix implemented: read `CHIA_OPERATOR_UPDATE_F2_DRIVER_PATH_FIXED.md`.
The supervisor fixed driver packaging lookup and validated it against the
retained Rocket driver using production FireSim packaging code.

Additional user instruction: read `CHIA_OPERATOR_REQUEST_JASPER_128_GIB.md`.
The next implementation turn should configure both Jasper modes with an
explicit 128 GiB RSS watchdog and one ProofGrid job after current verification
joins. The active proof retains its existing 100 GiB setting.

User update: `ssh ubuntu@localhost` using `~/firesim.pem` should now be fixed.
Retain that key for FireSim's localhost build-farm SSH; do not switch the build
farm or substitute a different key based on the earlier authentication error.

The interactive supervisor independently verified both commands successfully:

```bash
ssh -i /home/ubuntu/firesim.pem -o IdentitiesOnly=yes -o BatchMode=yes \
  -o StrictHostKeyChecking=no ubuntu@localhost true
rsync --dry-run -ptv \
  --rsh='ssh -i /home/ubuntu/firesim.pem -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no' \
  /scratch/jfx/fsim-circt/automation/chia_firesim_circt/control.yaml \
  ubuntu@localhost:/scratch/jfx/
```

The previous F2 `Permission denied (publickey)` is historical. Its failed
verification remains a failure; retry through the normal semantic-gated CHIA
FireSim node after the running Jasper branch joins. This handoff does not claim
a successful bitstream or FPGA run. Do not start duplicate long jobs or interrupt
the existing Jasper proof. No compiler, verification source or in-flight
acceptance input was changed for this SSH check.
