#!/usr/bin/env python3
"""Local adapter/lifecycle controls; never dispatch Ray, FireSim or AWS."""
import copy
import csv
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import yaml
import feature_f2 as adapter
import f2_manager
from downstream_common import record, write
from feature_workloads.instrumentation_check import MARKER, PERIOD


class FeatureF2Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        tools = patch.object(adapter, 'guest_tools', return_value=[])
        tools.start(); self.addCleanup(tools.stop)
        self.repo=Path(self.tmp.name)/'repo'; self.repo.mkdir()
        self.deploy=self.repo/'sims/firesim/deploy'; self.deploy.mkdir(parents=True)
        (self.deploy/'workloads').mkdir(); (self.deploy/'runtools').mkdir()
        self.topology=self.deploy/'runtools/user_topology.py'; self.topology.write_text('# unchanged source\n')
        (self.deploy/'config_runtime.yaml').write_text(yaml.safe_dump(dict(run_farm={},metasimulation={},
            host_debug=dict(zero_out_dram=True),target_config={},workload={})))
        (self.deploy/'config_hwdb.yaml').write_text('fixture_hw: {agfi: agfi-0123}\n')
        self.work=Path(self.tmp.name)/'candidate'; self.work.mkdir()
        self.accepted=self.work/'accepted'; self.accepted.mkdir()
        self.config=dict(workload='instrumentation',feature='autocounter_print',target_config='Example',platform_config='ExampleF2')
        self.metadata(self.accepted)
        self.manifest=dict(repo=str(self.repo),source_repo=str(self.repo),accepted=str(self.accepted),
            config=self.config,case_id='enabled_case',source_files=[record(self.topology)],
            accepted_files=[],shared_immutable_images=[],hardware_key='hardware',source_identity='fixture-identity')

    def metadata(self,directory,domains=((0,1,1),)):
        rows=[dict(widgetClass='midas.widgets.AutoCounterBridgeModule',clockInfo=dict(name='domain'+str(i),
            multiplier=m,divisor=d),widgetConstructorKey=dict(eventMetadata=[dict(label='top_feature_domain%d_%s'%(i,suffix))
            for suffix in ('active','cycles','wrap')])) for i,m,d in domains]
        rows.extend([
            dict(widgetClass='midas.widgets.ResetPulseBridgeModule',
                 clockInfo=dict(name='base',multiplier=1,divisor=1),
                 widgetConstructorKey=dict(activeHigh=True,defaultPulseLength=50,maxPulseLength=1023)),
            dict(widgetClass='midas.widgets.ClockBridgeModule',widgetConstructorKey=dict(
                **{'class':'firesim.lib.bridges.ClockParameters'},clocks=[
                    dict(name='domain'+str(i),multiplier=m,divisor=d) for i,m,d in domains]))])
        write(directory/'post-wrap-top-all.json',rows)
        return rows

    def roots(self):
        directory=self.work/'fixture-root'; directory.mkdir(exist_ok=True)
        for name in ('kernel','rootfs','manifest'): (directory/name).write_text(name)
        (directory/'template').write_text(json.dumps(dict(common_outputs=[],workloads=[dict(name='boot')])) )
        return [dict(template=directory/'template',bootbinary=directory/'kernel',rootfs=directory/'rootfs',manifest=directory/'manifest')]

    def test_emitted_event_metadata_and_rational_sample_period(self):
        rows=self.metadata(self.accepted,((0,1,5),(1,2,3)))
        # Real emitted schema is eventMetadata; unrelated selected library
        # counters do not create an invented feature-monitor domain.
        rows.append(dict(widgetClass='midas.widgets.AutoCounterBridgeModule',clockInfo=dict(name='other',multiplier=1,divisor=7),
                         widgetConstructorKey=dict(eventMetadata=[dict(label='library_event')])))
        write(self.accepted/'post-wrap-top-all.json',rows)
        result=adapter.monitor_manifest(self.accepted)
        self.assertEqual([d['reset_cycles'] for d in result['domains']],[10,33])
        self.assertEqual([d['masked_source_tokens'] for d in result['domains']],[11,34])
        self.assertEqual(result['reset_base_cycles'],50)
        self.assertEqual(result['clock_phase'],'all-countdowns-zero')
        self.assertEqual(result['schema_version'],2)
        self.assertEqual(result['sample_period_base']%15,0)
        self.assertGreaterEqual(result['sample_period_base'],PERIOD+7)
        rows[0]['widgetConstructorKey']['events']=rows[0]['widgetConstructorKey'].pop('eventMetadata')
        write(self.accepted/'post-wrap-top-all.json',rows)
        with self.assertRaises(KeyError): adapter.monitor_manifest(self.accepted)

    def test_monitor_reset_contract_comes_from_generated_bridge_metadata(self):
        rows=self.metadata(self.accepted,((0,1,2),))
        resets=next(row for row in rows if row['widgetClass']=='midas.widgets.ResetPulseBridgeModule')
        resets['widgetConstructorKey']['defaultPulseLength']=49
        write(self.accepted/'post-wrap-top-all.json',rows)
        result=adapter.monitor_manifest(self.accepted)
        self.assertEqual(result['reset_base_cycles'],49)
        self.assertEqual(result['domains'][0]['reset_cycles'],25)
        self.assertEqual(result['domains'][0]['masked_source_tokens'],25)
        self.assertEqual(result['domains'][0]['first_print_cycle'],0)
        for changed in [
                [row for row in rows if row is not resets],
                rows+[copy.deepcopy(resets)],
                [dict(row,widgetConstructorKey=dict(row['widgetConstructorKey'],activeHigh=False))
                 if row is resets else row for row in rows],
                [dict(row,widgetConstructorKey=dict(row['widgetConstructorKey'],defaultPulseLength=0))
                 if row is resets else row for row in rows]]:
            write(self.accepted/'post-wrap-top-all.json',changed)
            with self.subTest(rows=changed),self.assertRaises(ValueError):
                adapter.monitor_manifest(self.accepted)

    def test_runtime_keeps_frozen_sources_and_one_linux_instance(self):
        before=record(self.topology)
        with patch.object(adapter.features,'guest_assets',return_value=self.roots()):
            name,path=adapter.runtime(self.repo,self.work,self.manifest,dict(recipe_name='hardware'))
        self.assertEqual(record(self.topology),before)
        config=yaml.safe_load(path.read_text())
        self.assertFalse(config['host_debug']['zero_out_dram'])
        self.assertEqual(config['target_config']['topology'],'no_net_config')
        self.assertEqual(config['target_config']['no_net_num_nodes'],1)
        self.assertIn('+autocounter-readrate='+str(PERIOD+7),config['target_config']['plusarg_passthrough'])
        self.assertEqual(config['run_farm']['recipe_arg_overrides']['run_farm_hosts_to_use'],[{'f2.6xlarge':1}])
        self.manifest['config']=dict(self.config,supernode=True)
        with patch.object(adapter.features,'guest_assets') as guest:
            with self.assertRaisesRegex(ValueError,'supernode'): adapter.runtime(self.repo,self.work,self.manifest,{})
            guest.assert_not_called()

    def transcript(self):
        return ('Linux version fixture\nCIRCT_LINUX_BOOT: PASS\n'+MARKER+'\n'
                'CIRCT_LINUX_POWEROFF: REQUESTED\nreboot: Power down\n'
                '*** PASSED *** after 999 cycles\nScript done COMMAND_EXIT_CODE="0"\n')

    def test_actual_output_checker_signatures_and_corruption(self):
        result=self.deploy/'results-workload/tag-fixture'; result.mkdir(parents=True)
        (result/'uartlog').write_text(self.transcript())
        labels=['local_cycle',*[f'top_feature_domain0_{s}' for s in ('active','cycles','wrap')]]
        rows=[['version','1'],['Clock Domain Name',' domain0',' Base Multiplier',' 1',' Base Divisor',' 1'],
            ['label',*labels],['description','Clock cycles elapsed in the local domain.','Cycles outside local reset.',
            'Target domain cycles since local reset release.','Low four bits of target domain cycles.'],
            ['type','Accumulate','Accumulate','Identity','Identity'],['event width','1','1','64','4'],
            ['accumulator width','64','64','64','64']]
        for i in range(1,4):
            c=i*(PERIOD+7); rows.append([c,c,c-50,c-50,(c-50)%16])
        out=io.StringIO(); csv.writer(out).writerows(rows)
        (result/'AUTOCOUNTER0.csv').write_text(out.getvalue())
        prints='# Clock Domain: domain0, Relative Frequency: 1/1 of Base Clock\n'+''.join(
            'CYCLE:%13d [CIRCT_FEATURE] domain=0 cycles=%d wrap=0\n'%(c+50,c) for c in (0,PERIOD))
        (result/'PRINT0').write_text(prints)
        self.assertTrue(adapter.check_outputs(self.repo,self.work,self.manifest,'tag-fixture')['passed'])
        import shutil
        shutil.rmtree(self.work/'runtime-output')
        (result/'PRINT0').write_text(prints.replace('wrap=0','wrap=1'))
        with self.assertRaisesRegex(ValueError,'corrupt'): adapter.check_outputs(self.repo,self.work,self.manifest,'tag-fixture')

    def worker_fixture(self):
        required=['FireSim-generated'+s for s in ('.sv','.const.h','.const.vh','.partition.const.h',
            '.peer2peer.const.yaml','.defines.vh','.synthesis.xdc','.implementation.xdc')]
        required+=['post-bridge-extraction-all.json']
        for name in required: (self.accepted/name).write_text('[]' if name.endswith('.json') else 'fixture')
        driver=self.work/'accepted-driver'; driver.write_text('fixture driver')
        self.manifest.update(accepted_files=[record(p) for p in self.accepted.iterdir()],driver=record(driver))
        write(self.work/'snapshot.json',self.manifest); write(self.work/'job.json',dict(phase='queued'))

    def test_observation_changes_during_checker_are_rejected(self):
        result=self.deploy/'results-workload/tag-changing'; result.mkdir(parents=True)
        (result/'uartlog').write_text(self.transcript())
        manifest=dict(self.manifest,config=dict(workload='threads',feature='fame5',threads=4))
        def corrupt(*args):
            (self.work/'runtime-output/uartlog').write_text('changed observation')
            return dict(passed=True)
        with patch('feature_workloads.thread_check.check',side_effect=corrupt):
            with self.assertRaisesRegex(ValueError,'changed during checking'):
                adapter.check_outputs(self.repo,self.work,manifest,'tag-changing')

    def test_real_prepare_and_manager_api_cleanup_on_success_and_failure(self):
        # prepare() is real: it captures all production collateral and emits
        # the actual BuildConfig quintuplet/build YAML consumed by the manager.
        for failure in (None,'runworkload','launchrunfarm','checker','run_and_cleanup'):
            with self.subTest(failure=failure):
                if (self.work/'execution').exists():
                    import shutil
                    shutil.rmtree(self.work/'execution')
                self.worker_fixture(); actions=[]
                def manager(repo,directory,action,recipes,extra=(),timeout=21600):
                    frozen=json.loads((directory/'frozen-inputs.json').read_text())
                    self.assertEqual(frozen['feature_resources']['memory_bytes'],20*1024**3)
                    self.assertLessEqual(len(frozen['feature_resources']['cpu_ids']),4)
                    self.assertTrue(all(Path(row['path']).is_file() for row in frozen['files']))
                    actions.append(action)
                    if action==failure or (failure=='run_and_cleanup' and action=='runworkload'):
                        raise ValueError('primary '+action)
                    if failure=='run_and_cleanup' and action=='terminaterunfarm': raise ValueError('cleanup failed')
                    if action=='buildbitstream':
                        path=self.deploy/'built-hwdb-entries/circt_enabled_case'; path.parent.mkdir(exist_ok=True)
                        path.write_text(yaml.safe_dump(dict(circt_enabled_case=dict(agfi='agfi-0123'))))
                    log=directory/(action+'.log'); log.write_text('fixture manager')
                    write(directory/(action+'-command.json'),dict(argv=['fixture',action],returncode=0,timed_out=False,
                        stdout=record(log),stderr=record(log)))
                    return log
                def checker(repo,directory,manifest,tag):
                    if failure=='checker': raise ValueError('primary checker')
                    retained=directory/'runtime-output'; retained.mkdir()
                    uart=retained/'uartlog'; uart.write_text(self.transcript())
                    log=directory/'checker.log'; log.write_text('fixture checker')
                    command=directory/'checker-command.json'
                    write(command,dict(argv=['fixture','checker'],returncode=0,timed_out=False,
                        stdout=record(log),stderr=record(log)))
                    result=dict(passed=True,case_id=manifest['case_id'],input_identity=manifest['source_identity'],
                        command=record(command),observations=[record(uart)],capabilities=['checked_counter_and_print_runtime_output'])
                    write(directory/'feature-check.json',result)
                    return result
                with patch.object(adapter,'run_manager',side_effect=manager), \
                     patch.object(adapter.features,'guest_assets',return_value=self.roots()), \
                     patch.object(adapter,'run_checker',side_effect=checker), \
                     patch.object(adapter.signal,'signal'):
                    rc=adapter.run_snapshot(self.work/'snapshot.json')
                result=json.loads((self.work/'result.json').read_text())
                self.assertEqual(actions[-1],'terminaterunfarm',result)
                self.assertEqual(result['runfarm_cleanup'],failure!='run_and_cleanup')
                self.assertEqual(rc,int(failure is not None))
                self.assertEqual(record(self.topology),self.manifest['source_files'][0])
                if failure: self.assertIn('primary '+('runworkload' if failure=='run_and_cleanup' else failure),result['error'])
                if failure=='run_and_cleanup': self.assertIn('cleanup failed',result['cleanup_error'])
                if failure is None:
                    self.assertTrue(result['linux_booted']); self.assertFalse(result['full_acceptance'])
                    self.assertEqual(result['input_identity'],'fixture-identity')
                    adapter.bg.validate(list(result['receipts'].values())+result['feature_checks']+[result['command']])
                    # A successful exit cannot repair a changed build log.
                    Path(result['build_log']).write_text('altered after build')
                    with self.assertRaisesRegex(ValueError,'changed'):
                        adapter.linux_receipts(self.work,self.manifest,result)

    def test_local_tool_limits_execute_and_reject_unbounded_requests(self):
        manifest=dict(feature_resources=dict(cpu_ids=[min(os.sched_getaffinity(0))],memory_bytes=20*1024**3,timeout_seconds=3))
        tool=self.work/'make'; tool.write_text('#!/bin/bash\nprintf "%s\\n" "$MAKEFLAGS"\nulimit -v\n'); tool.chmod(0o755)
        environment=dict(os.environ,PATH=str(self.work)+':'+os.environ['PATH'])
        command=f2_manager.bounded_tool_command('make fixture',manifest)
        completed=subprocess.run(command,shell=True,text=True,capture_output=True,env=environment,timeout=10)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertEqual(completed.stdout.splitlines(),['-j1',str(20*1024**2)])
        self.assertEqual(f2_manager.bounded_tool_command('echo remote-driver',manifest),'echo remote-driver')
        altered=copy.deepcopy(manifest); altered['feature_resources']['memory_bytes']=21*1024**3
        with self.assertRaises(ValueError): f2_manager.bounded_tool_command('make',altered)

    def test_manager_timeout_reaps_its_local_process_group(self):
        # Source a private stub rather than the real manager; only a real local
        # shell child is launched. No SSH setup or FireSim command is executed.
        (self.repo/'sims/firesim/sourceme-manager.sh').write_text('true\n')
        marker=self.work/'child.pid'
        child='import os,pathlib,time; pathlib.Path('+repr(str(marker))+').write_text(str(os.getpid())); time.sleep(60)'
        argv=['python3','-c',child]
        with patch.object(adapter.run_f2,'manager_command',return_value=argv):
            with self.assertRaises(subprocess.TimeoutExpired):
                adapter.run_manager(self.repo,self.work,'local_timeout',self.work/'recipes',timeout=1)
        for name in ('logs','results-build','results-workload'):
            self.assertTrue((self.deploy/name).is_dir())
        pid=int(marker.read_text())
        with self.assertRaises(ProcessLookupError): os.kill(pid,0)
        receipt=json.loads((self.work/'local_timeout-command.json').read_text())
        self.assertTrue(receipt['timed_out']); self.assertNotEqual(receipt['returncode'],0)
        adapter.bg.validate([receipt['stdout'],receipt['stderr']])

    def test_production_command_timeout_keeps_receipt_and_joins_child(self):
        marker=self.work/'production-child.pid'
        code='import os,pathlib,time; pathlib.Path('+repr(str(marker))+').write_text(str(os.getpid())); time.sleep(60)'
        directory=self.work/'production-timeout'
        with patch.object(adapter.features,'source_identity',return_value='fixture identity'):
            with self.assertRaisesRegex(ValueError,'local_timeout failed'):
                adapter.features.command(self.repo,directory,'fixture-case','local_timeout',
                    ['python3','-c',code],timeout=1)
        receipt=json.loads((directory/'command.json').read_text())
        self.assertTrue(receipt['timed_out']); self.assertIsNone(receipt['returncode'])
        adapter.bg.validate([receipt['stdout'],receipt['stderr']])
        with self.assertRaises(ProcessLookupError): os.kill(int(marker.read_text()),0)

    def test_manager_nonzero_exit_retains_actual_argv_status_and_output(self):
        (self.repo/'sims/firesim/sourceme-manager.sh').write_text('true\n')
        argv=['python3','-c','import sys; print("real local diagnostic"); sys.exit(3)']
        with patch.object(adapter.run_f2,'manager_command',return_value=argv):
            with self.assertRaisesRegex(ValueError,'exited 3'):
                adapter.run_manager(self.repo,self.work,'local_failure',self.work/'recipes')
        receipt=json.loads((self.work/'local_failure-command.json').read_text())
        self.assertEqual(receipt['returncode'],3); self.assertFalse(receipt['timed_out'])
        self.assertEqual(receipt['argv'][-3:],argv)
        self.assertIn('real local diagnostic',Path(receipt['stdout']['path']).read_text())
        adapter.bg.validate([receipt['stdout'],receipt['stderr']])

    def test_context_rejects_wrong_feature_and_changed_artifacts(self):
        artifact=self.work/'bound-config'; artifact.write_text('actual configuration')
        context=dict(input_identity='baseline-origin',enabled_features=['autocounter_print'],
                     artifacts=[dict(role='config',**record(artifact))])
        adapter.validate_context(context,self.config,'enabled_case')
        broken=dict(context,enabled_features=['nic'])
        with self.assertRaises(ValueError): adapter.validate_context(broken,self.config,'enabled_case')
        artifact.write_text('new configuration')
        with self.assertRaisesRegex(ValueError,'changed'):
            adapter.validate_context(context,self.config,'enabled_case')

    def test_checker_command_binds_observations_and_rejects_unobserved_capabilities(self):
        def command(repo,directory,case_id,purpose,argv):
            self.assertIn('--check-outputs',list(map(str,argv)))
            directory.mkdir()
            log=directory/'stdout.log'; log.write_text('actual checker receipt fixture')
            path=directory/'command.json'
            write(path,dict(argv=list(map(str,argv)),returncode=0,timed_out=False,
                stdout=record(log),stderr=record(log)))
            retained=directory.parent/'runtime-output'; retained.mkdir(exist_ok=True)
            (retained/'uartlog').write_text(self.transcript())
            write(directory.parent/'feature-check.json',dict(passed=True))
            return path
        self.manifest['context']=dict(input_identity='baseline-origin',enabled_features=['autocounter_print'])
        with patch.object(adapter.features,'command',side_effect=command):
            verdict=adapter.run_checker(self.repo,self.work,self.manifest,'tag')
        self.assertEqual(verdict['input_identity'],'baseline-origin')
        self.assertEqual(verdict['case_id'],'enabled_case')
        adapter.bg.validate([verdict['command'],*verdict['observations']])
        self.assertNotIn('trigger_reset_backpressure',verdict['capabilities'])
        import shutil
        shutil.rmtree(self.work/'checker-command')
        self.manifest['context']['capabilities']=['trigger_reset_backpressure']
        with patch.object(adapter.features,'command',side_effect=command):
            with self.assertRaisesRegex(ValueError,'exceeds observed'):
                adapter.run_checker(self.repo,self.work,self.manifest,'tag')

    def test_submission_does_not_relabel_inflight_other_identity(self):
        root=self.repo/'automation/chia_firesim_circt/logs/background-features'; root.mkdir(parents=True)
        old=root/'old/job.json'; write(old,dict(job_id='old',phase='queued',runtime_key='same',input_identity='old'))
        calls=[]
        client=types.SimpleNamespace(get_job_status=lambda job:'RUNNING',submit_job=lambda **kw:calls.append(kw))
        module=types.ModuleType('ray.job_submission'); module.JobSubmissionClient=lambda address:client
        manifest=dict(runtime_key='same',hardware_key='hw',repo=str(self.repo))
        context=dict(input_identity='current',enabled_features=['autocounter_print'])
        with patch.dict(sys.modules,{'ray.job_submission':module}),patch.object(adapter,'freeze',return_value=manifest), \
             patch.dict(os.environ,{'CHIA_F2_BUILD_LOCK':'/shared/build.lock','CHIA_FEATURE_WORKER_LOCK':'/shared/worker.lock'}):
            submitted=adapter.submit(self.repo,'enabled_case',self.work,self.work/'new-identity',context=context)
        self.assertNotEqual(submitted['job_id'],'old'); self.assertEqual(len(calls),1)
        self.assertEqual(submitted['input_identity'],'current')
        self.assertEqual(calls[0]['runtime_env']['env_vars']['CHIA_F2_BUILD_LOCK'],'/shared/build.lock')
        self.assertEqual(calls[0]['runtime_env']['env_vars']['CHIA_FEATURE_WORKER_LOCK'],'/shared/worker.lock')
        self.assertEqual(calls[0]['runtime_env']['env_vars']['RAY_JOB_STOP_WAIT_TIME_S'],'720')

    def test_freeze_copies_ignored_nic_module_and_deduplicates_locations(self):
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        subprocess.run(['git','add','.'],cwd=self.repo,check=True)
        subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                        'commit','-qm','fixture'],cwd=self.repo,check=True)
        source_paths=[self.topology]
        for relative in ('env.sh','sims/firesim/env.sh'):
            path=self.repo/relative; path.write_text('ROOT='+str(self.repo)); source_paths.append(path)
        for relative in ('software/firemarshal/images/firechip/br-base/br-base-bin',
                         'software/firemarshal/images/firechip/br-base/br-base.img'):
            path=self.repo/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('fixture image')
        (self.repo/'.conda-env').mkdir()
        module=self.repo/'software/firemarshal/boards/firechip/drivers/icenet-driver/icenet.ko'
        module.parent.mkdir(parents=True); module.write_bytes(b'actual captured module fixture')
        implementation=self.work/'implementation'; generated=implementation/'generated'; generated.mkdir(parents=True)
        (generated/'FireSim-generated.sv').write_text('fixture RTL')
        (generated/'FireSim-generated.const.h').write_text('new simplenic_t(')
        fir=implementation/'input.fir'; fir.write_text('fixture source')
        driver=implementation/'driver'; driver.write_text('fixture executable')
        write(implementation/'compile.json',dict(products=[record(path) for path in generated.iterdir()],inputs=[record(fir)]))
        write(implementation/'driver.json',dict(driver=record(driver)))
        config=dict(self.config,workload='nic',feature='nic',nic_modes=['normal'])
        with patch.object(adapter.features,'cases',return_value={'enabled_case':config}), \
             patch.object(adapter.features,'source_identity',return_value='fixture source identity'), \
             patch.object(adapter.bg,'source_files',return_value=([self.repo],source_paths)):
            one=adapter.freeze(self.repo,'enabled_case',implementation,self.work/'snapshot-one')
            two=adapter.freeze(self.repo,'enabled_case',implementation,self.work/'snapshot-two')
            config['nic_modes']=['runtime_loopback']
            three=adapter.freeze(self.repo,'enabled_case',implementation,self.work/'snapshot-runtime')
            libc=self.work/'libc.a'; libc.write_text('scalar runtime one')
            with patch.object(adapter, 'guest_tools', return_value=[record(libc)]):
                four=adapter.freeze(self.repo,'enabled_case',implementation,self.work/'snapshot-tools-one')
            libc.write_text('scalar runtime two')
            with patch.object(adapter, 'guest_tools', return_value=[record(libc)]):
                five=adapter.freeze(self.repo,'enabled_case',implementation,self.work/'snapshot-tools-two')
        self.assertEqual(one['runtime_key'],two['runtime_key'])
        self.assertEqual(one['hardware_key'],three['hardware_key'])
        self.assertNotEqual(one['runtime_key'],three['runtime_key'])
        self.assertEqual(four['hardware_key'],five['hardware_key'])
        self.assertNotEqual(four['runtime_key'],five['runtime_key'])
        with self.assertRaisesRegex(ValueError, 'Frozen background'):
            adapter.bg.validate(four['guest_toolchain'])
        copied=Path(one['repo'])/module.relative_to(self.repo)
        self.assertEqual(copied.read_bytes(),module.read_bytes())
        self.assertNotEqual(copied.stat().st_ino,module.stat().st_ino)
        self.assertEqual((Path(one['repo'])/'sims/firesim/deploy/config_hwdb.yaml').read_bytes(),
                         (self.deploy/'config_hwdb.yaml').read_bytes())
        module.write_bytes(b'next implementation module')
        adapter.bg.validate(one['source_files']+one['accepted_files']+[one['driver']])

    def test_submission_deduplicates_and_bounds_active_queue_without_cloud(self):
        root=self.repo/'automation/chia_firesim_circt/logs/background-features'; root.mkdir(parents=True)
        old=root/'old/job.json'; write(old,dict(job_id='old',phase='queued',runtime_key='same'))
        client=types.SimpleNamespace(get_job_status=lambda job: 'RUNNING',submit_job=lambda **kw: self.fail('cloud dispatch'))
        module=types.ModuleType('ray.job_submission'); module.JobSubmissionClient=lambda address:client
        manifest=dict(runtime_key='same',hardware_key='hw',repo=str(self.repo))
        with patch.dict(sys.modules,{'ray.job_submission':module}),patch.object(adapter,'freeze',return_value=manifest):
            reused=adapter.submit(self.repo,'case',self.work,self.work/'reuse')
            self.assertEqual(reused['job_id'],'old')
            write(root/'second/job.json',dict(job_id='second',phase='running',runtime_key='different'))
            manifest['runtime_key']='third'
            with self.assertRaisesRegex(ValueError,'bounded at two'):
                adapter.submit(self.repo,'case',self.work,self.work/'bounded')


if __name__=='__main__': unittest.main()
