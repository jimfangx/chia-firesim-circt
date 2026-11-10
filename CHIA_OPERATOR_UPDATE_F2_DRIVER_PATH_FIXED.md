# Supervisor fix for the main loop — 2026-10-22

The user requested fixing the iteration-2 F2 deployment error. The supervisor
has implemented the fix in `automation/chia_firesim_circt/f2_manager.py` and added
a regression in `test_downstream_adapters.py`.

Root cause: the adapter overrode `RuntimeHWConfig.get_local_driver_dir` to return
`GENERATED_DIR`. That directory holds frozen compiler RTL/headers, while the
real make rules build the executable in `OUTPUT_DIR` (`sim/output/f2/<quintuplet>`).
The adapter now delegates to FireSim's original driver directory resolver and
retains its exact-quintuplet guard. Frozen header/RTL make arguments are unchanged.

Read-only validation using the production `RuntimeHWConfig` and
`FireSimServerNode.get_tarball_files_paths` on the retained iteration-2 Rocket
build passed: the real driver ELF and all nine packaging input files exist,
shared-library enumeration succeeds, and `OUTPUT_DIR/build/FireSim-generated.const.h`
matches the frozen candidate header hash. Evidence:
`/scratch/jfx/f2-driver-path-validation-20261022.json`.

Validation: all nine downstream adapter tests pass. The full automation suite
ran 93 tests and hit one failure in the existing speed-adapter peak-RSS ratio
test (107264 KiB was not greater than twice 65760 KiB); its isolated ten-test
speed suite passes. That order-sensitive resource-test failure was not changed
as part of this driver-path fix. Check it separately before claiming the full
automation suite passes.

No new cloud instance, build, deployment or duplicate proof was started. The
iteration-2 build receipt and AGFI remain intact. The adapter edit changes
verification identity: preserve earlier results as history and obtain fresh
acceptance through the CHIA graph. Do not restore the incorrect generated-dir
driver lookup or mark FPGA execution passed based on this local check.

Also read `CHIA_OPERATOR_REQUEST_JASPER_128_GIB.md` for the pending user-requested
128 GiB Jasper limit, which is separate from this path fix.
