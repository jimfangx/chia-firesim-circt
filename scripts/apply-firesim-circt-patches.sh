#!/usr/bin/env bash
# The parent fork carries the exact IceNet changes used by the NIC representatives.
set -euo pipefail
port_repo=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
port_icenet="$port_repo/generators/icenet"
port_patch="$port_repo/patches/icenet/multiple-nic-and-loopback.patch"
port_expected=61d2925b9d57df189ab8d801eb81185040251249
test -e "$port_icenet/.git" || { echo 'Initialize generators/icenet first.' >&2; exit 1; }
test "$(git -C "$port_icenet" rev-parse HEAD)" = "$port_expected" || {
  echo 'IceNet revision differs from the pinned patch base; refusing to modify it.' >&2; exit 1;
}
if git -C "$port_icenet" apply --reverse --check "$port_patch" 2>/dev/null; then
  echo 'FireSim CIRCT IceNet patch already applied.'
else
  git -C "$port_icenet" apply --check "$port_patch"
  git -C "$port_icenet" apply "$port_patch"
  echo 'Applied FireSim CIRCT IceNet patch.'
fi
