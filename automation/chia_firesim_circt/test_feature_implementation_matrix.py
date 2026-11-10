"""Temporary-fixture publication controls; no compiler, manager or FPGA calls."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import feature_implementation_matrix as matrix
from downstream_common import record, write


class MatrixPublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name); self.repo=root/'repo'; self.repo.mkdir()
        self.automation=self.repo/'automation/chia_firesim_circt'; self.automation.mkdir(parents=True)
        source=Path(__file__).parent
        for name in ('feature_scope.yaml','feature_cases.yaml'):
            shutil.copy2(source/name,self.automation/name)
        (self.automation/'feature_f2.py').write_text('# fixture execution adapter\n')
        workloads=self.automation/'feature_workloads'; workloads.mkdir()
        for name in matrix.CHECKERS.values(): (workloads/name).write_text('# fixture checker\n')
        self.compiler=root/'compiler'; self.compiler.write_text('fixture compiler')
        self.jar=root/'firechip.jar'; self.jar.write_text('fixture generator')
        self.output=root/'evidence'; self.registry=matrix.execution.cases(self.repo)
        self.calls=[]; self.drift=False
        for name,side_effect in [('compile_case',self.compile),('build_driver',self.driver),('command',self.command)]:
            handle=patch.object(matrix.execution,name,side_effect=side_effect); handle.start(); self.addCleanup(handle.stop)
        handle=patch.object(matrix.execution,'enabled_contract'); handle.start(); self.addCleanup(handle.stop)

    def receipt(self,repo,directory,case_id,purpose,argv):
        directory.mkdir(parents=True,exist_ok=True)
        for name in ('stdout.log','stderr.log'): (directory/name).write_text('fixture tool output\n')
        path=directory/'command.json'
        write(path,dict(case_id=case_id,purpose=purpose,source_identity=matrix.execution.source_identity(repo),
            argv=list(map(str,argv)),returncode=0,timed_out=False,
            stdout=record(directory/'stdout.log'),stderr=record(directory/'stderr.log')))
        return path

    def compile(self,repo,directory,case_id,config,compiler,jar):
        self.calls.append(('compile',case_id))
        generated=directory/'generated'; generated.mkdir()
        (generated/'FireSim-generated.sv').write_text('fixture emitted RTL')
        inputs=directory/'inputs'; inputs.mkdir(); (inputs/'input.fir').write_text('fixture FIRRTL')
        command=self.receipt(repo,directory/'production_compile',case_id,'production_compile',[compiler,'fixture'])
        write(directory/'compile.json',dict(case_id=case_id,config=config,compiler=record(compiler),generator=record(jar),
            inputs=[record(inputs/'input.fir')],products=[record(generated/'FireSim-generated.sv')],
            resources=[],command=record(command)))
        if self.drift:
            (self.automation/'feature_workloads/thread_check.py').write_text('# changed source during batch\n')

    def driver(self,repo,directory,case_id,config):
        self.calls.append(('driver',case_id))
        path=directory/'driver'; path.mkdir(); binary=path/'FireSim-f2'; binary.write_text('fixture executable'); binary.chmod(0o755)
        command=self.receipt(repo,directory/'driver_build',case_id,'driver_build',['make','f2'])
        write(directory/'driver.json',dict(driver=record(binary),command=record(command)))

    def command(self,repo,directory,case_id,purpose,argv):
        self.calls.append(('checker',case_id))
        case=directory.parent; validation=case/'workload-validation'; validation.mkdir()
        preparation=validation/'linux-workload-inputs.json'
        write(preparation,dict(schema_version=1,private_rootfs=dict(path='fixture-private-rootfs',sha256='fixture')))
        write(validation/'result.json',dict(case_id=case_id,passed=True,guest_manifests=[record(preparation)],commands=[]))
        return self.receipt(repo,directory,case_id,purpose,argv)

    def batch(self,**kwargs):
        return matrix.run_matrix(self.repo,self.output,self.compiler,self.jar,**kwargs)

    def test_full_matrix_passes_real_validator_and_reuses_current_stages(self):
        result=self.batch()
        self.assertTrue(result['readiness_published'])
        self.assertEqual(len(result['cases']),7)
        self.assertEqual(len(self.calls),21)
        original=matrix.execution.source_identity
        self.assertTrue(self.batch()['readiness_published'])
        self.assertEqual(len(self.calls),21)
        self.assertIs(matrix.execution.source_identity,original)
        report=self.output/'readiness.json'
        self.assertTrue(matrix.readiness.validate_readiness(report,self.repo,0)['implementation_ready'])
        artifact=Path(result['cases'][0]['artifacts']['rtl']['path']); artifact.write_text('corrupted')
        with self.assertRaises(ValueError): matrix.readiness.validate_readiness(report,self.repo,0)

    def test_subset_worker_does_not_publish_and_has_its_own_receipt(self):
        result=self.batch(selected=[next(iter(self.registry))],publish=False,result_name='worker-result.json')
        self.assertTrue(result['passed']); self.assertFalse(result['readiness_published'])
        self.assertEqual(len(result['remaining']),6)
        self.assertTrue((self.output/'worker-result.json').is_file())
        self.assertFalse((self.output/'matrix-result.json').exists())
        self.assertFalse((self.output/'readiness.json').exists())

    def test_source_drift_never_publishes_and_restores_command_hasher(self):
        self.drift=True; original=matrix.execution.source_identity
        with self.assertRaisesRegex(ValueError,'Sources changed'):
            self.batch(selected=[next(iter(self.registry))])
        self.assertIs(matrix.execution.source_identity,original)
        self.assertFalse((self.output/'readiness.json').exists())
        self.assertFalse(json.loads((self.output/'matrix-result.json').read_text())['passed'])

    def test_stale_receipts_and_partial_builds_are_preserved(self):
        case=next(iter(self.registry)); self.batch(selected=[case],publish=False)
        command=self.output/case/'production_compile/command.json'; before=command.read_bytes()
        (self.automation/'feature_workloads/thread_check.py').write_text('# later implementation\n')
        with self.assertRaisesRegex(ValueError,'current-source'):
            self.batch(selected=[case],publish=False)
        self.assertEqual(command.read_bytes(),before)
        other=list(self.registry)[1]; (self.output/other/'generated').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'Partial compile'):
            self.batch(selected=[other],publish=False)


if __name__=='__main__': unittest.main()
