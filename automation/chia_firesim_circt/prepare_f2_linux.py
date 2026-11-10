#!/usr/bin/env python3
"""Prepare a private br-base Linux disk using FireMarshal's boot-command hook.

The installed br-base-uniform template and base boot/rootfs remain unchanged.
Buildroot's existing S99run invokes /firemarshal.sh after userspace startup;
only a private rootfs copy receives this run's check-and-poweroff script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

BOOT_MARKER = "CIRCT_LINUX_BOOT: PASS"
POWEROFF_MARKER = "CIRCT_LINUX_POWEROFF: REQUESTED"
GUEST_SCRIPT = '''#!/bin/sh
# FireMarshal Buildroot /etc/init.d/S99run executes this after the normal services.
fail() {
    printf 'CIRCT_LINUX_BOOT: FAIL %s\\n' "$*" >&2
    exit 1
}
[ "$(uname -s)" = Linux ] || fail 'kernel is not Linux'
[ "$(cat /proc/sys/kernel/ostype)" = Linux ] || fail 'procfs is unavailable'
[ -r /etc/os-release ] || fail 'rootfs os-release is unavailable'
[ -x /sbin/poweroff ] || fail 'poweroff is unavailable'
grep -q '^MemTotal:' /proc/meminfo || fail 'kernel memory information unavailable'
[ -d /sys/devices/system/cpu/cpu0 ] || fail 'sysfs CPU information unavailable'
cat /etc/os-release || fail 'cannot read rootfs os-release'
uname -a || fail 'cannot read kernel identification'
printf 'CIRCT_LINUX_BOOT: PASS\\n'
printf 'CIRCT_LINUX_POWEROFF: REQUESTED\\n'
sync || fail 'sync failed'
poweroff -f
printf 'CIRCT_LINUX_POWEROFF: FAIL poweroff returned\\n' >&2
exit 1
'''


def record(path):
    path = Path(path).resolve()
    digest = hashlib.sha256()
    with path.open("rb") as incoming:
        for block in iter(lambda: incoming.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def debugfs(image, command, *, write=False):
    executable = shutil.which("debugfs") or "/usr/sbin/debugfs"
    argv = [executable, *( ["-w"] if write else []), "-R", command, str(image)]
    completed = subprocess.run(argv, check=True, text=True, capture_output=True, timeout=60)
    # debugfs often returns zero for command errors, so every write is verified
    # by a subsequent content/mode read rather than trusting the process alone.
    return completed.stdout


def quote_debugfs(path):
    text = str(path)
    if any(character in text for character in ("\n", "\r", "\x00")):
        raise ValueError("debugfs path contains a control character")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def executable_stat(output):
    mode = re.search(r"Mode:\s+([0-7]+)\b", output)
    return bool(mode and int(mode.group(1), 8) & 0o100)


def prepare_linux_workload(repo, directory, template=None, *, guest_script=None, overlays=()):
    """Return Paths and a hash manifest for a kernel plus private Linux rootfs.

    The caller clones the returned template JSON and resolves bootbinary/rootfs
    relative to its own tagged deploy/workloads/<benchmark> asset directory.
    This function never builds, mounts, or modifies the base images/template.
    """
    repo, directory = Path(repo).resolve(), Path(directory).resolve()
    template = Path(template).resolve() if template else repo / "sims/firesim/deploy/workloads/br-base-uniform.json"
    config = json.loads(template.read_text())
    if config.get("benchmark_name") != "br-base-uniform":
        raise ValueError("Linux verification requires the installed br-base-uniform template")
    base = template.parent / config["benchmark_name"]
    kernel = (base / config["common_bootbinary"]).resolve()
    image = (base / config["common_rootfs"]).resolve()
    if not kernel.is_file() or kernel.stat().st_size == 0 or not image.is_file() or image.stat().st_size == 0:
        raise ValueError("missing br-base Linux images; run ./marshal -v build br-base.json && ./marshal -v install br-base.json")
    with kernel.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            raise ValueError("br-base bootbinary must be the real Linux/OpenSBI ELF")
    originals = {"bootbinary": record(kernel), "rootfs": record(image), "template": record(template)}
    hook = debugfs(image, "cat /etc/init.d/S99run")
    if "/firemarshal.sh" not in hook or not executable_stat(debugfs(image, "stat /etc/init.d/S99run")):
        raise ValueError("base rootfs lacks the executable FireMarshal S99run boot-command hook")
    directory.mkdir(parents=True, exist_ok=True)
    private = directory / "br-base-poweroff.img"
    script = directory / "firemarshal.sh"
    manifest = directory / "linux-workload-inputs.json"
    if any(path.exists() for path in (private, script, manifest)):
        raise ValueError("refusing to reuse a private Linux workload directory")
    contents = GUEST_SCRIPT if guest_script is None else Path(guest_script).read_text()
    if not contents.startswith("#!/bin/sh\n"):
        raise ValueError("Linux guest hook must be a POSIX shell script")
    script.write_text(contents)
    subprocess.run(["sh", "-n", str(script)], check=True, timeout=30)
    script.chmod(0o755)
    subprocess.run(["cp", "--reflink=auto", "--sparse=always", str(image), str(private)], check=True, timeout=180)
    debugfs(private, "rm /firemarshal.sh", write=True)
    debugfs(private, "write " + quote_debugfs(script) + " /firemarshal.sh", write=True)
    debugfs(private, "set_inode_field /firemarshal.sh mode 0100755", write=True)
    debugfs(private, "set_inode_field /firemarshal.sh uid 0", write=True)
    debugfs(private, "set_inode_field /firemarshal.sh gid 0", write=True)
    if debugfs(private, "cat /firemarshal.sh") != contents:
        raise ValueError("private rootfs guest script readback differs")
    if not re.search(r"Mode:\s+0?755\b", debugfs(private, "stat /firemarshal.sh")):
        raise ValueError("private rootfs guest script is not executable")
    if debugfs(private, "cat /etc/init.d/S99run") != hook:
        raise ValueError("private rootfs changed the FireMarshal init hook")
    installed = []
    destinations = {"/firemarshal.sh"}
    for source, destination, mode in overlays:
        source = Path(source).resolve()
        target = Path(destination)
        if (not target.is_absolute() or ".." in target.parts or str(target) in destinations
                or not source.is_file() or source.stat().st_size == 0
                or mode not in (0o644, 0o755)):
            raise ValueError("invalid or duplicate Linux feature overlay")
        destinations.add(str(target))
        binding = record(source)
        for parent in reversed(target.parents):
            if str(parent) != "/":
                debugfs(private, "mkdir " + quote_debugfs(parent), write=True)
        debugfs(private, "rm " + quote_debugfs(target), write=True)
        debugfs(private, "write " + quote_debugfs(source) + " " + quote_debugfs(target), write=True)
        debugfs(private, "set_inode_field " + quote_debugfs(target) + f" mode 010{mode:04o}", write=True)
        # Binary-safe readback: debugfs cat's text decoding cannot validate ELF.
        readback = directory / ("overlay-" + str(len(installed)) + ".readback")
        debugfs(private, "dump " + quote_debugfs(target) + " " + quote_debugfs(readback))
        if not readback.is_file() or record(readback)["sha256"] != binding["sha256"] or record(source) != binding:
            raise ValueError("Linux feature overlay readback differs: " + str(target))
        stat = debugfs(private, "stat " + quote_debugfs(target))
        parsed = re.search(r"Mode:\s+([0-7]+)\b", stat)
        if not parsed or int(parsed.group(1), 8) != mode:
            raise ValueError("Linux feature overlay mode differs: " + str(target))
        installed.append({"source": binding, "destination": str(target), "mode": mode})
    fsck_log = directory / "linux-rootfs-fsck.log"
    with fsck_log.open("w") as stream:
        subprocess.run([shutil.which("e2fsck") or "/usr/sbin/e2fsck", "-f", "-n", str(private)],
                       check=True, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
    current = {"bootbinary": record(kernel), "rootfs": record(image), "template": record(template)}
    if current != originals:
        raise ValueError("base Linux images or template changed during preparation")
    payload = {"schema_version": 1, "base": originals, "private_rootfs": record(private),
               "guest_script": record(script), "overlays": installed, "filesystem_check": record(fsck_log),
               "boot_marker": BOOT_MARKER, "shutdown_marker": POWEROFF_MARKER,
               "rootfs_hook": "/etc/init.d/S99run -> /firemarshal.sh",
               "preparation": "private reflink/copy of br-base rootfs; debugfs command hook replacement/readback",
               "verification_claim": "none: image preparation only; Linux boot/shutdown requires actual FPGA UART evidence"}
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return {"bootbinary": kernel, "rootfs": private, "template": template, "manifest": manifest,
            "guest_script": script, "boot_marker": BOOT_MARKER, "shutdown_marker": POWEROFF_MARKER}


def build_base(repo, log):
    """Run the documented base build/install with a clean manager environment."""
    repo, log = Path(repo).resolve(), Path(log).resolve()
    log.parent.mkdir(parents=True, exist_ok=True)
    shell = ('set -e\nlinux_repo=$1\nset --\n'
             'source "${CONDA_EXE%/bin/conda}/etc/profile.d/conda.sh"\n'
             'cd "$linux_repo/sims/firesim"\nsource ./sourceme-manager.sh\n'
             'cd "$linux_repo/software/firemarshal"\n'
             './marshal -v build br-base.json\n./marshal -v install br-base.json\n')
    with log.open("w") as stream:
        subprocess.run(["bash", "-lc", shell, "chia-linux-base", str(repo)],
                       check=True, stdout=stream, stderr=subprocess.STDOUT, timeout=21600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--build-base", action="store_true", help="first run documented marshal base build/install")
    args = parser.parse_args()
    if args.build_base:
        build_base(args.repo, args.output_dir / "marshal-base.log")
    result = prepare_linux_workload(args.repo, args.output_dir, args.template)
    print(json.dumps({key: str(value) for key, value in result.items()}, indent=2))


if __name__ == "__main__":
    main()
