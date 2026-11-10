# Bounded NIC execution assets

The four `feature_scope.yaml` NIC representatives use the same production
`SimpleNICBridgeModule` interfaces. Runtime loopback reuses the normal bitstream;
compile-time loopback uses the distinct hardware token scheduler and native
`simplenic_loopback_t` constructor. `usePauser=false` is the supported bounded
configuration. Original SFC compile-time pauser/driver defects and their retained
oracle failures are documented in `sim/goldengate-circt/test/Transforms/NICLoopback.md`;
these execution tools do not edit or waive those reference failures.

`FireSimCIRCTTwoNICRocketConfig` composes `icenet.WithTwoIceNIC` with the
normal bounded NIC Rocket configuration. One Linux target has two physical
peripherals at MMIO addresses `0x10016000` and `0x10017000`, separate DMA
masters and interrupt pairs, and two production NIC widgets/streams. This
exercises both interfaces concurrently within one Linux boot. `NICCountKey`
defaults to one, preserving existing single-device configurations.

`nic_packets.c` executes on the real Linux guest through AF_PACKET and the
IceNIC driver. It sends 128 ordered frames in eight-frame bursts, delays receive
work between bursts, and checks every received byte. Each widget has distinct
payloads; a host-connected normal peer generates different reply payloads, while
hardware/runtime loopback returns the original frame. Ethernet lengths are
126/254/510/1022 bytes: the actual IceNIC driver adds and strips two bytes of
NET_IP_ALIGN padding, giving complete 64-bit wire flits. Padding is unspecified
and is excluded; all Ethernet bytes are strict. No network, DRAM or model memory
is cleared by these tests. The host NIC driver now explicitly initializes only
its defined startup seed token batch, preventing uninitialized host malloc or
retained shared-memory bytes from becoming live packet-valid controls.

Build the guest and peer with the supplied production sources:

```
riscv64-unknown-linux-gnu-gcc -O2 -static nic_packets.c -o circt-nic-packets
g++ -O2 -std=c++17 -static nic_peer.cc -o nic_peer
```

Install `circt-nic-packets` as `/usr/bin/circt-nic-packets`, `nic_guest.sh` as the
private br-base image's `/firemarshal.sh`, and the kernel-matched IceNIC module
as `/usr/lib/icenet.ko` if the base kernel lacks a built-in driver. Supply
`/etc/circt-nic-case` rows `MODE WIDGET INTERFACE PACKETS`, e.g. `normal 0 eth0 128`.
For two interfaces, supply two rows; the guest joins concurrent workers before
requesting clean poweroff. The declared two-widget representatives use widget0
on `eth0` and widget1 on `eth1` in the same private rootfs.

The real manager adapter calls `feature_nic_manager.install(manifest)` before
entering FireSim. Its frozen `feature_nic` manifest is:

```json
{
  "peer_binary": "/absolute/frozen/nic_peer",
  "peer_sha256": "actual SHA256 of that file",
  "bindings": [
    {"widget": 0, "port": "unique_case_nic0", "mode": "normal"},
    {"widget": 1, "port": "unique_case_nic1", "mode": "runtime_loopback"}
  ],
  "packets": 128,
  "link_latency": 6405,
  "timeout_seconds": 21600
}
```

Bindings are contiguous widget indices and unique names. Supported modes are
`normal`, `runtime_loopback`, and `compile_time_loopback`. The manager overrides
only NIC-indexed arrays; rootfs/kernel arrays remain normal FireSim inputs.
It emits exact per-widget runtime loopback flags and rejects conflicting flags
or `zero_out_dram=true`. Normal peers are delivered with hash checking to the
actual run host, started before the real simulator, and checked ready. Each
uses the existing simplenic double-buffer shared-memory protocol and owns only
its unique names. On successful completion, failure, or manager cancellation,
the peer is stopped and its launcher joins it; downloaded logs and a
`peer-lifecycle.json` receipt retain exit/traffic/cleanup status. Incomplete
traffic fails result collection. Independent wall limits remain in the peer.

Run `nic_check.py` on actual collected UART and peer logs, e.g.:

```
python3 nic_check.py --uart uartlog --binding 0:normal --binding 1:loopback \
  --packets 128 --peer peer.stdout --output packet-check.json
```

The checker requires all expected frame identities, bytes/hashes, counts,
ordering, widget summaries, and ordered Linux boot/workload/kernel poweroff.
Normal-mode success additionally requires the distinct request/reply host
records and a successful joined peer. Runtime and compile-time modes both use
`loopback` in guest/checker bindings; their production configuration and driver
identity distinguish the hardware APIs.

`nic_validate.py --output-dir NEW_DIRECTORY` builds the actual static RISC-V
workload, portable static host peer, and production shared NIC driver with real
core/stream implementations. It checks independent widget state, exact runtime
flag parsing, idle seed controls, stream/MMIO identities, descriptor cleanup,
64 C/Python protocol comparisons, actual two-widget shared-memory traffic,
corrupted-packet rejection, checker negative controls, and manager process
lifecycle. These are bounded host tooling checks, not guest FPGA execution or
formal acceptance. Use the real manager adapter/background snapshot path for
FPGA execution and retain that separate evidence.
