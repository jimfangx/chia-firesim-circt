"""Build feature Linux guests with a hash-bound RV64GC toolchain/sysroot.

-march only constrains newly compiled code. Prebuilt libc/libgcc may introduce
unsupported instructions, so every final static ELF is checked after linking.
The extracted toolchain is shared read-only by snapshots, like the Conda tools;
its compiler, binutils, headers and libraries are bound before/after guest builds.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess

from downstream_common import record, write

DEFAULT_ROOT = Path('/scratch/jfx/chia-feature-linux-integration-20261103/guest-toolchain')
# The split extensions below are subsets of I/M/A/C in newer ISA metadata.
GC_EXTENSIONS = {'i', 'm', 'a', 'f', 'd', 'c', 'zicsr', 'zifencei', 'zmmul',
                 'zaamo', 'zalrsc', 'zca', 'zcd'}


def _configuration(repo):
    prefix = os.environ.get('CHIA_GUEST_TOOLCHAIN_PREFIX')
    manifest = DEFAULT_ROOT/'toolchain-manifest.json'
    if prefix:
        compiler = Path(prefix+'gcc').resolve()
        candidates = [ancestor/'toolchain-manifest.json' for ancestor in compiler.parents]
        manifest = next((path for path in candidates if path.is_file()), None)
        if manifest is None:
            raise ValueError('CHIA_GUEST_TOOLCHAIN_PREFIX requires a hash-bound toolchain-manifest.json')
    data = json.loads(manifest.read_text())
    if data.get('schema_version') != 1 or not isinstance(data.get('files'), list) or not data['files']:
        raise ValueError('Guest toolchain requires a nonempty bound file inventory')
    if prefix and Path(data['compiler']).resolve() != Path(prefix+'gcc').resolve():
        raise ValueError('Guest toolchain prefix differs from its compiler manifest')
    paths = {item['path'] for item in data['files']}
    for key in ('compiler', 'readelf'):
        if str(Path(data[key]).resolve()) not in paths or not os.access(data[key], os.X_OK):
            raise ValueError('Guest toolchain executable is not inventoried: '+key)
    if not Path(data['sysroot']).is_dir() or not Path(data['host_library_path']).is_dir():
        raise ValueError('Guest toolchain sysroot/host library directory is missing')
    return manifest, data


def bindings(repo):
    manifest, data = _configuration(repo)
    before = manifest.read_bytes()
    for item in data['files']:
        if record(item['path']) != item:
            raise ValueError('Guest toolchain changed: '+item['path'])
    if manifest.read_bytes() != before:
        raise ValueError('Guest toolchain manifest changed during validation')
    return [record(manifest), *data['files']]


def compiler_argv(repo):
    _, data = _configuration(repo)
    # These private binutils libraries are absent from the host system. Set the
    # search path for this one compiler invocation and its children only.
    return ['env', 'LD_LIBRARY_PATH='+data['host_library_path'], data['compiler'],
            '--sysroot='+data['sysroot'], '-march=rv64gc', '-mabi=lp64d']


def validate_architecture(architecture):
    if not architecture.startswith('rv64'):
        raise ValueError('Guest executable must declare RV64GC architecture')
    extensions = architecture[4:].split('_')
    unknown = [extension for extension in extensions if not any(
        re.fullmatch(re.escape(allowed)+r'(?:[0-9]+p[0-9]+)?', extension)
        for allowed in GC_EXTENSIONS)]
    if unknown or not any(re.fullmatch(r'i(?:[0-9]+p[0-9]+)?', extension) for extension in extensions):
        raise ValueError('Guest executable requires unsupported ISA extensions: '+','.join(unknown or extensions))
    return extensions


def validate_binary(repo, binary):
    _, data = _configuration(repo)
    argv = ['env', 'LD_LIBRARY_PATH='+data['host_library_path'], data['readelf'], '-h', '-A', '-l', str(binary)]
    process = subprocess.run(argv, check=True, capture_output=True, text=True)
    text = process.stdout
    architectures = re.findall(r'Tag_RISCV_arch:\s*"([^"]+)"', text)
    if (len(architectures) != 1 or re.search(r'Class:\s+ELF64\b', text) is None
            or re.search(r'Machine:\s+RISC-V\b', text) is None or 'double-float ABI' not in text
            or re.search(r'^\s*INTERP\b', text, re.MULTILINE)):
        raise ValueError('Guest binary must be a static RV64 LP64D ELF with one ISA attribute')
    extensions = validate_architecture(architectures[0])
    return dict(passed=True, binary=record(binary), architecture=architectures[0],
                extensions=extensions, static=True, abi='lp64d', argv=argv,
                scope='Final linked ELF ISA/ABI preflight including prebuilt libc/libgcc; no FPGA execution claim')


def build(repo, directory, case_id, source, binary, *, pthread=False, command=None):
    if command is None:
        from feature_execution import command
    before = bindings(repo)
    argv = [*compiler_argv(repo), '-O2', '-Wall', '-Wextra', '-Werror', '-static',
            *(['-pthread'] if pthread else []), source, '-o', binary]
    command(repo, directory, case_id, 'guest_build', argv)
    result = validate_binary(repo, binary)
    if before != bindings(repo):
        raise ValueError('Guest toolchain changed while building')
    result['toolchain'] = before
    write(Path(directory)/'elf-isa.json', result)
    return result
