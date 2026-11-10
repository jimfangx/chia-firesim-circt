#!/usr/bin/env bash
# Build from checked-in compiler sources and the installed, pinned CIRCT SDK.
set -euo pipefail
port_repo=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
port_sdk=${GOLDENGATE_CIRCT_PREFIX:-${RISCV:-$port_repo/.conda-env/riscv-tools}}
port_build=${GOLDENGATE_CIRCT_BUILD_DIR:-$port_repo/.local/goldengate-circt-build}
port_jobs=${CMAKE_BUILD_PARALLEL_LEVEL:-$(getconf _NPROCESSORS_ONLN)}
test -f "$port_sdk/lib/cmake/circt/CIRCTConfig.cmake" || {
  echo 'Install the CIRCT SDK with build-setup.sh, or set GOLDENGATE_CIRCT_PREFIX.' >&2; exit 1;
}
cmake -S "$port_repo/sims/firesim/sim/goldengate-circt" -B "$port_build" -G Ninja \
  -DCIRCT_DIR="$port_sdk/lib/cmake/circt" -DCMAKE_BUILD_TYPE=Release \
  -DZLIB_ROOT="$(dirname "$port_sdk")"
cmake --build "$port_build" --target goldengate-circt --parallel "$port_jobs"
printf 'Built compiler: %s/goldengate-circt\n' "$port_build"
