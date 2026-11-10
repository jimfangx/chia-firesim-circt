#!/bin/sh
# Installed into a private br-base rootfs as /firemarshal.sh. The execution
# adapter supplies /etc/circt-nic-case: MODE WIDGET INTERFACE PACKETS, one row
# per physical NIC. Each worker executes concurrently; every exit is joined.
set -eu
stage=setup
workers=''
cleanup_workers() {
  # Every PID was created by this shell and has not yet been reaped. Never use
  # a global process-name kill or signal workers belonging to another test.
  for worker in $workers; do
    pid=${worker#*:}
    kill -TERM "$pid" 2>/dev/null || :
    kill -KILL "$pid" 2>/dev/null || :
  done
  for worker in $workers; do
    widget=${worker%%:*}
    pid=${worker#*:}
    if wait "$pid"; then rc=0; else rc=$?; fi
    printf 'CIRCT_NIC_WORKER {"widget":%s,"event":"joined","returncode":%s}\n' "$widget" "$rc"
  done
  workers=''
}
fail_poweroff() {
  failure_rc=$1
  trap - 0 1 2 15
  [ "$failure_rc" -ne 0 ] || failure_rc=1
  printf 'CIRCT_NIC_FAILURE: packet workload stage=%s returncode=%s\n' "$stage" "$failure_rc" >&2
  printf 'CIRCT_LINUX_BOOT: FAIL NIC workload stage=%s returncode=%s\n' "$stage" "$failure_rc" >&2
  cleanup_workers
  sync || :
  poweroff -f || :
  exit "$failure_rc"
}
trap 'fail_poweroff "$?"' 0
trap 'fail_poweroff 129' 1
trap 'fail_poweroff 130' 2
trap 'fail_poweroff 143' 15
[ "$(uname -s)" = Linux ]
[ -r /proc/meminfo ]
printf 'CIRCT_LINUX_BOOT: PASS\n'
case_file=${CIRCT_NIC_CASE:-/etc/circt-nic-case}
[ -s "$case_file" ]
# br-base retains its kernel; install its exact matching IceNIC module in the
# private image when it is modular. Fail if neither built-in nor loadable.
if ! ls /sys/class/net/eth* >/dev/null 2>&1; then
  [ -r /usr/lib/icenet.ko ]
  insmod /usr/lib/icenet.ko
fi
stage=packet_launch
while read -r mode widget interface packets; do
  [ -n "$mode" ] || continue
  circt-nic-packets "$interface" "$widget" "$mode" "$packets" &
  workers="${workers:+$workers }$widget:$!"
  printf 'CIRCT_NIC_WORKER {"widget":%s,"event":"launched"}\n' "$widget"
done < "$case_file"
stage=packet_join
while [ -n "$workers" ]; do
  worker=${workers%% *}
  widget=${worker%%:*}
  pid=${worker#*:}
  if wait "$pid"; then rc=0; else rc=$?; fi
  case "$workers" in
    *' '*) workers=${workers#* } ;;
    *) workers='' ;;
  esac
  printf 'CIRCT_NIC_WORKER {"widget":%s,"event":"joined","returncode":%s}\n' "$widget" "$rc"
  [ "$rc" -eq 0 ] || exit "$rc"
done
stage=shutdown
printf 'CIRCT_LINUX_POWEROFF: REQUESTED\n'
sync
poweroff -f
trap - 0 1 2 15
printf 'CIRCT_LINUX_POWEROFF: FAIL poweroff returned\n' >&2
exit 1
