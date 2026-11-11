# Reproducing the FireSim CIRCT port

The default branch is the source distribution for this port. Clone the parent
fork; its gitlinks pin the FireSim and FireMarshal forks and all other source
dependencies. Do not substitute the moving heads of those dependencies.

## Clone and set up

The validated host is Ubuntu 24.04 on x86_64. Install Miniforge/Conda, the host
dependencies needed by Chipyard and FireSim, and the AWS FPGA tools when building
bitstreams. Vivado, an AWS account with F2 access, SSH credentials and Cadence
Jasper licenses are external prerequisites, not redistributable source inputs.

```sh
git clone https://github.com/jimfangx/chia-firesim-circt.git
cd chia-firesim-circt
export CY_DIR="$PWD"
conda create -y -p "$CY_DIR/.conda-env" --file reproducibility/conda-linux-64.explicit
conda activate "$CY_DIR/.conda-env"
export RISCV="$CONDA_PREFIX/riscv-tools"
export PATH="$RISCV/bin:$PATH"
./build-setup.sh --skip-conda --skip-clean
source ./env.sh
```

The explicit Conda lock records the actual package versions and package hashes
used on the validated host. It requires those public package artifacts to remain
available; it does not pin proprietary FPGA/formal installations or every pip
package used by the optional CHIA orchestration. The ordinary Chipyard dependency
specifications remain available under `conda-reqs/`.

The submodule initializer applies
`patches/icenet/multiple-nic-and-loopback.patch` before target elaboration. This
patch is part of the parent fork and checks its exact pinned IceNet base. It
preserves the previously local two-interface NIC source changes without relying
on unpublished commits. If initializing submodules manually, run:

```sh
./scripts/apply-firesim-circt-patches.sh
```

That operation is idempotent. IceNet's resulting dirty status is the documented
source overlay, not missing source from this distribution.

## Build the compiler and transformed RTL

`conda-reqs/circt.json` pins the CIRCT SDK to `firtool-1.75.0`; the full static
distribution includes LLVM/MLIR/CIRCT development libraries. A firtool executable
alone is insufficient. `build-setup.sh` installs this SDK into `$RISCV`.

```sh
export GOLDENGATE_CIRCT_PREFIX="$RISCV"
export CMAKE_BUILD_PARALLEL_LEVEL="$(getconf _NPROCESSORS_ONLN)"
./scripts/build-goldengate-circt.sh

cd "$CY_DIR/sims/firesim"
source ./sourceme-manager.sh
cd sim
make -j"$(getconf _NPROCESSORS_ONLN)" compile \
  PLATFORM=f2 TARGET_PROJECT=firesim \
  TARGET_PROJECT_MAKEFRAG="$CY_DIR/generators/firechip/chip/src/main/makefrag/firesim" \
  DESIGN=FireSim TARGET_CONFIG=FireSimCIRCTInstrumentedRocketConfig \
  PLATFORM_CONFIG=WithAutoCounter_WithPrintfSynthesis_BaseF2Config
```

On F2, source **`./sourceme-manager.sh` without `--skip-ssh-setup`**. Complete SSH
initialization and make the intended FireSim key available before manager runs.
The production make rules build target and compiler JARs from pinned sources;
the old host's private prebuilt JARs and `/scratch/jfx` paths are not needed to
compile this example. The native compiler is built from checked-in C++ sources.
Normal FireSim builds require CIRCT, including `firesim buildbitstream`, driver
builds and direct RTL builds. The manager explicitly selects CIRCT, and the make
rules reject SFC selection. Separate golden-reference tools invoke Scala SFC
directly for comparisons.

Production elaboration uses Chipyard's native Chisel/CIRCT CHIRRTL output
(`<name>.fir`) directly. It does not request `--emit-legacy-sfc` or run the
FIRRTL2 compatibility emitter. Chisel elaboration still runs in Scala; the
FireSim transformations and RTL generation run in `goldengate-circt`. Native
CHIRRTL embeds the annotation array also written to `<name>.anno.json`. The
importer consumes matching mirrored arrays once, preserving intentional repeats
within each array. Separate golden-reference tools may explicitly request the
legacy `.sfc.fir` format for SFC comparisons.

Enable the optional multi-cycle RAM models with `WithMultiCycleRamModels` (or
its `MCRams` alias) in `PLATFORM_CONFIG`, or
`GOLDENGATE_CIRCT_MULTICYCLE_RAM_MODELS=enabled` in make. Direct compiler calls
accept `--enable-multicycle-ram-models`. The default remains disabled. The
enabled path extracts memories selected by `FirrtlMemModelAnnotation`, carries
them through the generalized FAME model graph, and materializes SFC-compatible
async host models. Address/data widths and read/write counts come from the
typed memory-port channels; host depth is derived from address width, matching
SFC. Each selected wrapper instance has independent storage. Unsupported port
ABIs, including combined read/write ports, fail explicitly. Storage remains
uninitialized; this option does not enable DRAM zeroing.

Commands may share an input token channel: native delivery tracking prevents
early acknowledgement or repeat consumption while the RAM model services the
commands serially. Distinct response fields may also share an output channel;
native joins their handshakes until the complete packet is available. The
transport producer holds a valid token/payload until its handshake. Host reset
flushes protocol state while retaining memory contents. Multiple producers of
the same response field are rejected. The portable CTests
`goldengate-multicycle-ram-sharing` and
`goldengate-multicycle-ram-response-join` require firtool and Verilator, and retain
hashed SFC fixtures with regeneration sources. They cover model behavior, not
whole-design formal equivalence or an FPGA/Linux run.

Feature-specific full-design Jasper inputs can be selected with
`automation/chia_firesim_circt/run_jasper.py --input-manifest <manifest.json>
--profile <exact PLATFORM_CONFIG>`. The schema and validation are in
`automation/chia_firesim_circt/jasper_feature_inputs.py`. The manifest binds
both complete RTL files and native memory-origin collateral to one hashed
elaboration inventory and exact enabled profile. Set `CHIA_INPUT_IDENTITY` to
the manifest's `input_design_identity` and `CHIA_REPORT_PATH` to a fresh result
path. Run `--mode lec` and `--mode sec` separately. `--stage-only` validates and
stages locally without starting Jasper; a staged result is explicitly unproven.
Explicit feature inputs never fall back to archived baseline outputs. Preserve
the default 24-hour/128-GiB proof limits and coordinate with existing remote
proofs; this standalone adapter does not provide a host-global queue.

Synthesized target assertions are opt-in. Add `WithSynthAsserts` to the platform
configuration (for example, `WithSynthAsserts_BaseF2Config`), or pass
`GOLDENGATE_CIRCT_SYNTH_ASSERTS=enabled` to make. Direct native compiler invocations
accept `--enable-synth-asserts`. The compiler exports native FIRRTL assertions and
legacy failing `stop`/`printf` pairs through the instance hierarchy, grouping
them by target clock. The assertion bridge reports the failure ID, message and
target cycle using the existing `synthesized_assertions_t` driver. Its token
queue stalls at an enabled failure; the driver can resume it, and
`+disable-asserts` disables runtime checking. Global reset conditions mask
failures; instance exclusions suppress the selected instance paths. Successful
`stop(0)` operations are preserved. Assertions with undriven clocks are omitted
as in SFC; ambiguous clock roots are rejected.

The native regression tests cover assertion export and host bridge behavior.
The earlier seven F2 Linux representatives do not establish synthesized
assertion coverage; enabled assertion hardware still needs its own matched SFC,
F2/runtime and formal signoff.

The seven bounded feature representatives and exact compiler flags live in
`automation/chia_firesim_circt/feature_cases.yaml`:

* four-thread Rocket with `MTModels`, AutoCounter and Print;
* instrumented Rocket with AutoCounter and Print;
* two-domain/model Rocket with instrumentation;
* normal single NIC and normal two NICs;
* compile-time NIC loopback (`--nic-mode=loopback`);
* mixed normal/per-widget runtime loopback (same hardware as two NICs).

`scripts/build-goldengate-circt.sh` accepts `GOLDENGATE_CIRCT_PREFIX` and
`GOLDENGATE_CIRCT_BUILD_DIR` for other locations. Its default output is
`.local/goldengate-circt-build/`; it imposes no CPU-affinity or memory limits.

### Production artifacts and regression tests

Production make and the default compiler helper configure `BUILD_TESTING=OFF`
and build only `goldengate-circt`. Sources such as
`test/Transforms/NICMultiMMIOTest.cpp` are regression tests: they are not linked
into the compiler or FPGA driver and are not required on a deployment host.
Keep these tests with the implementation in Git for CI and development.

Build and run the separate test configuration explicitly:

```sh
cd "$CY_DIR"
./scripts/build-goldengate-circt.sh --with-tests
ctest --test-dir "$CY_DIR/.local/goldengate-circt-tests" --output-on-failure
```

This uses `.local/goldengate-circt-tests/` with `BUILD_TESTING=ON`; it does not
populate the production build with test executables. Deploy the built compiler
and the applicable FireSim driver/runtime dependencies, not the source checkout
or its test-build directory.

## Rebuild Linux and the feature guests

```sh
cd "$CY_DIR/software/firemarshal"
./marshal -v build br-base.json
./marshal -v install br-base.json
```

The feature adapters create private overlays from those base images, run feature
checks inside Linux, and request poweroff. Generated root filesystems, boot
images, AGFIs, local manager YAML and run logs are intentionally not committed.
Rebuild them on the new machine. DRAM zeroing remains disabled.

The feature guest programs require a hash-bound RV64GC cross compiler and
sysroot. Recreate the tested toolchain without installing system packages:

```sh
cd "$CY_DIR"
python3 scripts/prepare-feature-guest-toolchain.py
export CHIA_GUEST_TOOLCHAIN_PREFIX="$CY_DIR/.local/guest-toolchain/usr/bin/riscv64-linux-gnu-"
```

This downloads the exact Ubuntu package versions in
`reproducibility/guest-toolchain-packages.json`, verifies SHA-256, extracts them
privately and generates `toolchain-manifest.json` for the existing guest checks.
If the distribution mirror has retired a version, obtain the same hash-verified
packages from an archive and pass `--packages-dir`; newer substitutes are not
silently accepted. Final linked guest ELFs are still checked for RV64GC/LP64D.

## Verification and retained scope

All seven integrated representatives passed real F2 Linux feature checks,
poweroff and run-farm cleanup on November 9, 2026, alongside exactly four
baseline static comparisons (Rocket, MegaBoomV3, plain Shuttle and Saturn
Shuttle). Runtime checks are documented in
`automation/chia_firesim_circt/feature_workloads/runtime_coverage.md`.

The November 10 compiler optimizations reproduced the saved outputs for all four
baselines and seven feature configurations: 2,229 files matched exactly, and 11
file lists differed only by their output-directory prefix. The normal manager
path also regenerated matching Rocket RTL and compiler collateral, linked the
F2 driver and staged the FPGA files. See
`reproducibility/compiler-update-20261110.json` for compiler/commit identities and
the bounded checks. These checks reused the existing hardware results; they did
not launch new bitstream builds, FPGA runs or formal proofs. The earlier
`validated-source-manifest.json` retains its original hardware-validated source
and compiler identity as historical evidence.

These results do not establish full behavioral equivalence or complete port
signoff. Jasper LEC/SEC, matched enabled-feature SFC comparisons and current
compiler-speed measurements retain their own obligations. Preserve failures;
do not treat unwritten memory as zero or waive defined control/payload failures.

The CHIA YAML and historical port plan retain paths and receipts from the old
machine as diagnostic history. To use the optional automation on another host,
configure local locations for the native compiler, generated JARs, immutable SFC
archives, candidate output, guest toolchain and Jasper flow. Historical SFC
goldens are separate artifacts with pinned provenance; rebuilding Scala source
is not a claim to reproduce the old JAR bytes or its archived receipt. Source
builds and normal manager operation do not require restarting CHIA.

For a new source build, use a new output/evidence directory and record its own
compiler/input/output hashes. Old host paths and old runtime receipts must not
be edited to appear to verify a new build. Native CTest targets are declared in
`sims/firesim/sim/goldengate-circt/CMakeLists.txt`; driver tests are under
`sims/firesim/sim/midas/src/test/cc`, and automation tests under
`automation/chia_firesim_circt/`.
