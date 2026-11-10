#!/usr/bin/env python3
"""FireSim manager entrypoint that consumes the gate's frozen CIRCT artifacts.

Manager build/run actions and AWS lifecycle are unchanged. Only make input
selection is constrained: -o marks the inventoried compiler outputs supplied,
and GENERATED_DIR points at the isolated candidate copy. Driver compilation and
FPGA delivery still use the real FireSim make rules and manager implementation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy
import shlex
import shutil
import sys

from downstream_common import record


def frozen_arguments(manifest):
    return ["GOLDENGATE_COMPILER=circt", "GENERATED_DIR=" + manifest["generated_dir"],
            *[argument for item in manifest["files"] for argument in ("-o", item["path"])]]


def validate(manifest):
    for item in manifest["files"]:
        if record(item["path"]) != item:
            raise ValueError("frozen CIRCT input changed: " + item["path"])


def bounded_tool_command(command, manifest):
    """Bound opted-in local feature builds, including localhost SSH children.

    A Ray CPU reservation alone does not constrain Vivado, since FireSim
    launches it through SSH. Apply the limit inside that shell. Run-farm
    lifecycle and remote FPGA driver commands retain the manager's behavior.
    """
    limits = manifest.get("feature_resources")
    if limits is None:
        return command
    cpus = limits.get("cpu_ids")
    memory = limits.get("memory_bytes")
    seconds = limits.get("timeout_seconds")
    if (not isinstance(cpus, list) or not 1 <= len(cpus) <= 4 or
            any(type(cpu) is not int or cpu < 0 for cpu in cpus) or len(set(cpus)) != len(cpus) or
            type(memory) is not int or not 0 < memory <= 20 * 1024**3 or
            type(seconds) is not int or not 0 < seconds <= 21600):
        raise ValueError("invalid bounded feature tool resources")
    words = shlex.split(command)
    if not words or not (words[0] == "make" or any(Path(word).name == "build-bitstream.sh" for word in words)):
        return command
    # Set MAKEFLAGS as well as affinity: parallel compilation consumes memory
    # even when many compiler processes contend on one permitted CPU.
    script = ("ulimit -v " + str((memory + 1023) // 1024) + " || exit; "
              "export MAKEFLAGS=" + shlex.quote("-j" + str(len(cpus))) + "; exec " +
              shlex.join(["taskset", "--cpu-list", ",".join(map(str, cpus)),
                          "timeout", "--signal=TERM", "--kill-after=10s", str(seconds) + "s",
                          "bash", "-c", command]))
    return shlex.join(["bash", "-c", script])


def bind_runtime_driver_directory(runtime_class, manifest):
    """Keep make's driver output location, checking the accepted configuration.

    GENERATED_DIR selects compiler RTL/headers. FireSim builds the executable
    under OUTPUT_DIR, and its normal resolver already points packaging there.
    Redirecting that resolver to GENERATED_DIR makes infrasetup look for a
    nonexistent executable despite a successful driver build.
    """
    original = runtime_class.get_local_driver_dir

    def driver_directory(self):
        quintuplet = self.get_deployquintuplet_for_config()
        if quintuplet != manifest["quintuplet"]:
            raise ValueError("manager requested a different hardware configuration: " + quintuplet)
        return original(self)

    runtime_class.get_local_driver_dir = driver_directory


def full_ssh_prefix(factory):
    """Preserve upstream Fabric contexts while enabling nested SSH setup."""
    def prefix(command, *args, **kwargs):
        if shlex.split(command) == ["source", "sourceme-manager.sh", "--skip-ssh-setup"]:
            # A sourced script inherits shell positional parameters unless they
            # are cleared; sourceme-manager.sh parses every one as its options.
            command = "set -- && source sourceme-manager.sh"
        return factory(command, *args, **kwargs)
    return prefix


def delivery_directory(repo, manifest):
    return Path(repo) / "sims/firesim/platforms/f2/aws-fpga-firesim-f2/hdk/cl/developer_designs" / (
        "cl_" + manifest["quintuplet"]) / "design"


def archive_previous_delivery(repo, manifest):
    """Force copying supplied RTL even when an earlier delivery is newer.

    GNU make -o deliberately suppresses regeneration and treats its input old.
    Preserve previous compiler deliverables before asking the real make rules
    to copy this attempt. The board template and previous Vivado outputs stay.
    """
    delivery = delivery_directory(repo, manifest)
    old = [delivery / ("FireSim-generated" + suffix) for suffix in (
        ".sv", ".defines.vh", ".synthesis.xdc", ".implementation.xdc")]
    old.extend(delivery.glob("FireSim-generated.*.ipgen.tcl"))
    previous = Path(manifest["generated_dir"]).parent / "previous-fpga-delivery"
    for path in old:
        if path.exists() or path.is_symlink():
            previous.mkdir(exist_ok=True)
            if (previous / path.name).exists():
                raise ValueError("refusing to replace prior-delivery evidence: " + str(previous / path.name))
            shutil.move(str(path), str(previous / path.name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args, manager_args = parser.parse_known_args()
    manifest = json.loads(args.manifest.read_text())
    validate(manifest)
    repo = Path(manifest["repo"])
    deploy = repo / "sims/firesim/deploy"
    sys.path.insert(0, str(deploy))
    from buildtools.buildconfig import BuildConfig
    from buildtools.bitbuilder import BitBuilder
    import buildtools.bitbuilder as bitbuilder
    import runtools.runtime_config as runtime
    if manifest.get("feature_nic") is not None:
        from feature_nic_manager import install
        install(manifest)
    # Upstream prefixes skip SSH setup even when the outer adapter sourced the
    # full environment. Keep full sourcing at both build and runtime layers.
    bitbuilder.prefix = full_ssh_prefix(bitbuilder.prefix)
    runtime.prefix = full_ssh_prefix(runtime.prefix)

    def check_quintuplet(value):
        if value != manifest["quintuplet"]:
            raise ValueError("manager requested a different hardware configuration: " + value)

    original_make = BuildConfig.make_recipe
    def make_recipe(self, recipe, deploy_dir):
        check_quintuplet(self.get_chisel_quintuplet())
        validate(manifest)
        return original_make(self, recipe, deploy_dir) + " " + shlex.join(frozen_arguments(manifest))
    BuildConfig.make_recipe = make_recipe

    original_run = runtime.run
    def runtime_run(command, *positional, **keywords):
        if command.startswith("make PLATFORM="):
            validate(manifest)
            command += " " + shlex.join(frozen_arguments(manifest))
        return original_run(bounded_tool_command(command, manifest), *positional, **keywords)
    runtime.run = runtime_run
    original_build_run = bitbuilder.run
    def build_run(command, *positional, **keywords):
        return original_build_run(bounded_tool_command(command, manifest), *positional, **keywords)
    bitbuilder.run = build_run
    bind_runtime_driver_directory(runtime.RuntimeHWConfig, manifest)

    original_replace = BitBuilder.replace_rtl
    def replace_rtl(self):
        validate(manifest)
        check_quintuplet(self.build_config.get_chisel_quintuplet())
        archive_previous_delivery(repo, manifest)
        original_replace(self)
        check_quintuplet(self.build_config.get_chisel_quintuplet())
        delivery = delivery_directory(repo, manifest)
        for suffix in (".sv", ".defines.vh", ".synthesis.xdc", ".implementation.xdc"):
            name = "FireSim-generated" + suffix
            expected = next(item for item in manifest["files"] if Path(item["path"]).name == name)
            if record(delivery / name)["sha256"] != expected["sha256"]:
                raise ValueError("FPGA delivery differs from the verified CIRCT candidate: " + name)
        for expected in manifest["files"]:
            name = Path(expected["path"]).name
            if name.endswith(".ipgen.tcl") and record(delivery / name)["sha256"] != expected["sha256"]:
                raise ValueError("FPGA IP generation script differs from the verified candidate: " + name)
        validate(manifest)
    BitBuilder.replace_rtl = replace_rtl
    sys.argv = [str(deploy / "firesim"), *manager_args]
    runpy.run_path(str(deploy / "firesim"), run_name="__main__")


if __name__ == "__main__":
    main()
