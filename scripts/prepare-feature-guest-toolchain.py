#!/usr/bin/env python3
"""Recreate the hash-bound RV64GC feature toolchain from pinned Ubuntu packages.

Uses apt download and dpkg-deb extraction; installs nothing system-wide.
Existing downloaded packages can be supplied with --packages-dir.
"""
import argparse, hashlib, json, os, subprocess
from pathlib import Path

def record(path):
    path=path.resolve()
    return dict(path=str(path),bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def main():
    repo=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=repo/'.local/guest-toolchain')
    parser.add_argument('--packages-dir',type=Path)
    args=parser.parse_args();root=args.output.resolve()
    if root.exists():raise ValueError('Use a new output directory; existing toolchains are immutable.')
    root.mkdir(parents=True);packages=root/'packages';packages.mkdir()
    lock=json.loads((repo/'reproducibility/guest-toolchain-packages.json').read_text())
    retained=[]
    for entry in lock['packages']:
        if args.packages_dir is None:
            subprocess.run(['apt','download',entry['package']+'='+entry['version']],cwd=packages,check=True)
            candidates=list(packages.glob('*.deb'))
        else:candidates=list(args.packages_dir.resolve().glob('*.deb'))
        matches=[p for p in candidates if p.stat().st_size==entry['bytes'] and record(p)['sha256']==entry['sha256']]
        if len(matches)!=1:raise ValueError('Missing exact pinned package: '+entry['package']+'='+entry['version'])
        source=matches[0]
        if args.packages_dir is not None:
            import shutil
            target=packages/source.name;shutil.copy2(source,target);source=target
        subprocess.run(['dpkg-deb','--extract',str(source),str(root)],check=True)
        retained.append(dict(**record(source),metadata='\n'.join(k+': '+entry[v] for k,v in [('Package','package'),('Version','version'),('Architecture','architecture')])))
    compiler=root/'usr/bin/riscv64-linux-gnu-gcc-13';readelf=root/'usr/bin/riscv64-linux-gnu-readelf'
    if not compiler.is_file() or not readelf.is_file():raise ValueError('Compiler/binutils extraction incomplete')
    files={str(p.resolve()):record(p) for p in (root/'usr').rglob('*') if p.is_file()}
    manifest=dict(schema_version=1,compiler=str(compiler),readelf=str(readelf),
        sysroot=str(root/'usr/riscv64-linux-gnu'),host_library_path=str(root/'usr/lib/x86_64-linux-gnu'),
        packages=retained,files=list(files.values()),scope='Privately extracted pinned Ubuntu Noble RV64GC compiler and sysroot; no system installation')
    (root/'toolchain-manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    print('export CHIA_GUEST_TOOLCHAIN_PREFIX='+str(root/'usr/bin/riscv64-linux-gnu-'))
    return 0

if __name__=='__main__':raise SystemExit(main())
