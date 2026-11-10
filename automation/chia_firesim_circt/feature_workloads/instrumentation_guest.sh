#!/bin/sh
# Executed by the private br-base FireMarshal hook before sync/poweroff.
set -eu
[ "$(uname -s)" = Linux ]
[ -r /proc/meminfo ]
# Real guest arithmetic and written/read memory traffic keep the domain observer
# active during userspace and give the host checker an independent workload end.
i=1
sum=0
while [ "$i" -le 4096 ]; do
    sum=$((sum + i))
    i=$((i + 1))
done
[ "$sum" -eq 8390656 ]
file=/tmp/chia-instrumentation-memory
trap 'rm -f "$file"' EXIT HUP INT TERM
dd if=/dev/zero of="$file" bs=4096 count=16 2>/dev/null
[ "$(wc -c < "$file")" -eq 65536 ]
printf 'CHIA_FEATURE_INSTRUMENTATION: PASS sum=%s bytes=65536\n' "$sum"
