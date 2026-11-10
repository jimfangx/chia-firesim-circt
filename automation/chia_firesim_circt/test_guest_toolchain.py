"""Final linked ISA controls and private RV64GC toolchain execution; no FPGA use."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import guest_toolchain as toolchain
from feature_workloads.thread_check import check


GC = 'rv64i2p1_m2p0_a2p1_f2p2_d2p2_c2p0_zicsr2p0_zifencei2p0_zmmul1p0'


class GuestToolchainTest(unittest.TestCase):
    def test_rejects_linked_vector_bitmanip_and_wrong_word_size(self):
        for architecture in (GC+'_v1p0',GC+'_zve64d1p0',GC+'_zba1p0',GC+'_zbb1p0',GC+'_zicbom1p0',
                             'rv32i2p1_m2p0_a2p1_f2p2_d2p2_c2p0',GC+'_unknown1p0'):
            with self.subTest(architecture=architecture), self.assertRaises(ValueError):
                toolchain.validate_architecture(architecture)
        self.assertTrue(toolchain.validate_architecture(GC))
        self.assertTrue(toolchain.validate_architecture(GC+'_zaamo1p0_zalrsc1p0_zca1p0_zcd1p0'))

    def test_unknown_prefix_requires_complete_inventory(self):
        with patch.dict(os.environ,{'CHIA_GUEST_TOOLCHAIN_PREFIX':'/nonexistent/cross/bin/riscv64-'}):
            with self.assertRaisesRegex(ValueError,'manifest'):
                toolchain.compiler_argv(Path.cwd())

    def test_changed_library_is_rejected(self):
        from downstream_common import record
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'lib').mkdir();(root/'sysroot').mkdir()
            for name in ('gcc','readelf','libc.a'):
                (root/name).write_text('fixture');(root/name).chmod(0o755)
            data=dict(schema_version=1,compiler=str(root/'gcc'),readelf=str(root/'readelf'),
                sysroot=str(root/'sysroot'),host_library_path=str(root/'lib'),
                files=[record(root/name) for name in ('gcc','readelf','libc.a')])
            (root/'toolchain-manifest.json').write_text(json.dumps(data))
            with patch.object(toolchain,'DEFAULT_ROOT',root),patch.dict(os.environ,{},clear=True):
                self.assertEqual(len(toolchain.bindings(root)),4)
                (root/'libc.a').write_text('new vector libc')
                with self.assertRaisesRegex(ValueError,'changed'):
                    toolchain.bindings(root)

    @unittest.skipUnless((toolchain.DEFAULT_ROOT/'toolchain-manifest.json').is_file(),'Private GC toolchain not provisioned')
    def test_static_pthread_and_nic_binaries_have_gc_isa_and_run_without_vectors(self):
        root=toolchain.DEFAULT_ROOT;repo=Path(__file__).parent
        def command(repo,directory,case_id,purpose,argv):
            directory.mkdir(parents=True,exist_ok=True)
            process=subprocess.run(list(map(str,argv)),capture_output=True,text=True,timeout=30)
            if process.returncode:raise ValueError(process.stderr)
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{},clear=True):
            directory=Path(directory)
            for name,pthread in [('thread_state',True),('nic_packets',False)]:
                binary=directory/name
                result=toolchain.build(repo,directory/(name+'-build'),'toolchain-test',
                    repo/'feature_workloads'/(name+'.c'),binary,pthread=pthread,command=command)
                self.assertEqual(result['architecture'],GC)
                argv=[str(root/'usr/bin/qemu-riscv64'),'-cpu',
                      'rv64,v=false,zba=false,zbb=false,zbc=false,zbs=false,zicbom=false',str(binary)]
                process=subprocess.run(argv+(['4'] if pthread else []),capture_output=True,text=True,timeout=30)
                if pthread:
                    self.assertEqual(process.returncode,0,process.stderr)
                    self.assertTrue(check(process.stdout,4,shutdown=False)['passed'])
                else:
                    self.assertEqual(process.returncode,2,process.stderr)
                    self.assertIn('usage: nic_packets',process.stderr)


if __name__=='__main__':unittest.main()
