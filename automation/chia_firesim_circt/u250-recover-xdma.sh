#!/usr/bin/env bash
# Recover the PCI function removed by a failed U250 flash. No FPGA programming.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
    printf 'Run with sudo bash %s\n' "$0" >&2
    exit 2
fi

u250_lab_bin=/tools/Xilinx/Vivado_Lab/2023.1/bin
u250_pci=/sys/bus/pci/devices/0000:01:00.0

# This path rescans PCIe and enables MMIO; it does not depend on the old JTAG UID.
/usr/local/bin/firesim-fpga-util.py \
    --bdf 01:00.0 --fpga-db /scratch/firesim-db.json --reconnect-bdf \
    --vivado-bin "$u250_lab_bin/vivado_lab" \
    --hw-server-bin "$u250_lab_bin/hw_server"
udevadm settle --timeout=10
if [[ ! -d "$u250_pci" ]]; then
    printf 'PCI function 0000:01:00.0 is still absent after rescan.\n' >&2
    lspci -nn -d 10ee: || true
    exit 1
fi
lspci -nn -s 0000:01:00.0

if [[ ! -d /sys/module/xdma ]]; then
    /usr/local/bin/firesim-load-xdma-module
fi
udevadm settle --timeout=10
u250_driver=$(readlink -f "$u250_pci/driver" || true)
if [[ ${u250_driver##*/} != xdma ]]; then
    printf 'PCI function is present but XDMA did not bind. Inspect kernel messages for xdma/01:00.0 before retrying enumeration.\n' >&2
    exit 1
fi

shopt -s nullglob
u250_nodes=(/dev/xdma*)
if (( ${#u250_nodes[@]} == 0 )); then
    printf 'XDMA bound but /dev/xdma* nodes are absent. Inspect udev/kernel messages before retrying enumeration.\n' >&2
    exit 1
fi
/usr/local/bin/firesim-chmod-xdma-perm
printf 'PCI and XDMA recovery completed. Retry firesim enumeratefpgas to verify the current JTAG-to-BDF mapping.\n'
