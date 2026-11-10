#!/bin/sh
set -eu
stage=setup
fail_poweroff() {
  rc=$1
  trap - 0 1 2 15
  [ "$rc" -ne 0 ] || rc=1
  printf 'CIRCT_THREAD_RESULT: FAIL stage=%s returncode=%s\n' "$stage" "$rc" >&2
  printf 'CIRCT_LINUX_BOOT: FAIL thread workload stage=%s returncode=%s\n' "$stage" "$rc" >&2
  sync || :
  poweroff -f || :
  exit "$rc"
}
trap 'fail_poweroff "$?"' 0
trap 'fail_poweroff 129' 1
trap 'fail_poweroff 130' 2
trap 'fail_poweroff 143' 15
[ "$(uname -s)" = Linux ]
printf 'CIRCT_LINUX_BOOT: PASS\n'
stage=thread_execution
circt-thread-state 4
stage=shutdown
printf 'CIRCT_LINUX_POWEROFF: REQUESTED\n'
sync
poweroff -f
# A real successful shutdown never returns to the init hook. Keep the existing
# failure marker if poweroff returns; suppress recursive failure shutdown calls.
trap - 0 1 2 15
printf 'CIRCT_LINUX_POWEROFF: FAIL poweroff returned\n' >&2
exit 1
