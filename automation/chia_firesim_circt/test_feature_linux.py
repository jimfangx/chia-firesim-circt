#!/usr/bin/env python3
"""Suite scheduling, provenance and negative controls; never use Ray/AWS/tools."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

import feature_linux as suite
from downstream_common import record, write

REAL_PREPARE = suite.prepare_case


class FeatureLinuxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)/'repo'; self.repo.mkdir()
        self.output = Path(self.tmp.name)/'evidence'
        source = Path(suite.__file__).parent
        dest = self.repo/'automation/chia_firesim_circt'; dest.mkdir(parents=True)
        for name in ('feature_scope.yaml', 'feature_cases.yaml'):
            shutil.copy2(source/name, dest/name)
        self.cases = suite.execution.cases(self.repo)
        self.semantic = dict(ok=True, status='passed', identity='fixture-baseline', details=[])
        for target in suite.nodes.TARGETS:
            self.semantic['details'].append(dict(target=target, ok=True, status='passed', result=dict(
                passed=True, target=target, input_identity='fixture-baseline', verification_stage='prebuild',
                syntax=True, semantic=True, static_semantics=True, unresolved_obligations=[],
                details=dict(paired_syntax=dict(passed=True, pairs={side:dict(rtl=dict(passed=True),
                    driver=dict(passed=True)) for side in ('sfc', 'circt')})))))
        self.config = dict(feature_linux=dict(implementation_matrix=None, poll_seconds=.001))
        self.jobs = {}; self.submitted = []; self.completed = []; self.max_active = 0
        self.client = type('Client', (), {})()
        self.client.get_job_status = self.status
        self.client.stop_job = lambda job: self.jobs[job].update(stopped=True)
        self.failed_cases = set(); self.corrupt = None
        self.patches = [patch.object(suite.nodes, '_snapshot', return_value=('fixture-baseline', {})),
            patch.object(suite.readiness, 'source_identity', return_value='fixture-sources'),
            patch.object(suite, 'prepare_case', side_effect=self.prepare),
            patch.object(suite, '_client', return_value=self.client),
            patch.object(suite.feature_f2, 'submit', side_effect=self.submit),
            patch.object(suite.time, 'sleep')]
        for item in self.patches: item.start(); self.addCleanup(item.stop)

    def prepare(self, repo, directory, case_id, config, options, source, index):
        implementation = directory/'implementation'; generated = implementation/'generated'
        generated.mkdir(parents=True)
        (generated/'FireSim-generated.sv').write_text('fixture actual-source identity '+case_id)
        if config['workload'] in ('threads', 'instrumentation'):
            header = 'new autocounter_t( make_print_bridge_'
        else:
            header = ('new simplenic_loopback_t(' if '--nic-mode=loopback' in config.get('compiler_flags', [])
                      else 'new simplenic_t(')*len(config['nic_modes'])
        (generated/'FireSim-generated.const.h').write_text(header)
        write(generated/'fame-model-graph.json', dict(threaded_groups=1, shared_threads=4,
            clock_domains=2, definitions=2))
        binary=implementation/'driver'; binary.write_text('fixture driver')
        compiler=implementation/'compiler'; compiler.write_text('fixture compiler')
        generator=implementation/'generator.jar'; generator.write_text('fixture generator')
        write(implementation/'compile.json', dict(compiler=record(compiler), generator=record(generator)))
        write(implementation/'driver.json', dict(driver=record(binary)))
        return implementation

    def command(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        (directory/'stdout').write_text('fixture local control\n'); (directory/'stderr').write_text('')
        write(directory/'command.json', dict(argv=['fixture-local-control'], returncode=0,
            timed_out=False, stdout=record(directory/'stdout'), stderr=record(directory/'stderr')))
        return record(directory/'command.json')

    def submit(self, repo, case_id, implementation, directory, *, context):
        directory.mkdir(parents=True)
        command=self.command(directory/'command')
        rtl=implementation/'generated/FireSim-generated.sv'
        runtime=directory/'runtime.yaml'
        runtime.write_text(yaml.safe_dump(dict(host_debug=dict(zero_out_dram=False),
            metasimulation=dict(metasimulation_enabled=False),run_farm=dict(recipe_arg_overrides=dict(
                run_farm_hosts_to_use=[{'f2.6xlarge':1}])))))
        workload=directory/'workload-inputs.json'; write(workload, dict(runtime=record(runtime)))
        frozen=directory/'frozen-inputs.json'; write(frozen, dict(candidate_rtl=record(rtl)))
        uart=directory/'uartlog'; uart.write_text('Linux version fixture\nCIRCT_LINUX_BOOT: PASS\n'
            'CIRCT_LINUX_POWEROFF: REQUESTED\nreboot: Power down\n'
            '*** PASSED *** after 999 cycles\nScript done COMMAND_EXIT_CODE="0"\n')
        check=directory/'feature-check.json'
        write(check, dict(passed=True, case_id=case_id, input_identity=context['input_identity'],
            capabilities=['fixture_runtime_observation'], command=command, observations=[record(uart)]))
        receipts={name:record(directory/'command/stdout') for name in suite.RECEIPTS}
        receipts.update(workload_evidence=record(uart), workload_inputs=record(workload), frozen_inputs=record(frozen))
        snapshot=directory/'snapshot.json'
        write(snapshot, dict(case_id=case_id, config=self.cases[case_id], accepted=str(implementation/'generated'),
            source_files=[],shared_immutable_images=[],accepted_files=[record(rtl)],
            driver=record(implementation/'driver')))
        result=dict(passed=True, case_id=case_id, target=case_id, input_identity=context['input_identity'],
            full_acceptance=False,workload_kind='linux_boot_poweroff',agfi='agfi-fixture',
            command=command,receipts=receipts,feature_checks=[record(check)],
            **{flag:True for flag in suite.FLAGS})
        if self.corrupt: self.corrupt(result, directory)
        write(directory/'result.json', result)
        job_id='fixture-'+str(len(self.jobs))
        job=dict(job_id=job_id,snapshot=str(snapshot))
        self.jobs[job_id]=dict(case_id=case_id, complete=False, stopped=False)
        self.submitted.append(case_id)
        self.max_active=max(self.max_active,sum(not item['complete'] for item in self.jobs.values()))
        return job

    def status(self, job_id):
        job=self.jobs[job_id]
        if not job['complete']:
            job['complete']=True; self.completed.append(job['case_id'])
        return 'STOPPED' if job['stopped'] else ('FAILED' if job['case_id'] in self.failed_cases else 'SUCCEEDED')

    def run_suite(self):
        return suite.run_feature_linux(self.config, self.output, self.repo, self.semantic)

    def test_all_seven_real_linux_results_required_without_formal_claim(self):
        result=self.run_suite()
        self.assertTrue(result['ok'], result)
        self.assertEqual(set(self.submitted), set(self.cases))
        self.assertEqual(set(self.completed), set(self.cases))
        self.assertEqual(len(result['details']),7)
        self.assertEqual(self.max_active,1)
        self.assertEqual(result['max_pending'],1)
        self.assertEqual(result['identity'],'fixture-baseline')
        self.assertEqual(result['verification_scope'],'enabled_feature_linux_only')
        self.assertFalse(result['full_acceptance']); self.assertFalse(result['formal_claim'])
        self.assertFalse(result['feature_semantic_claim'])
        self.assertTrue(result['full_matrix'])
        self.assertEqual(result['required_cases'],list(self.cases))
        self.assertEqual(result['selected_cases'],list(self.cases))
        self.assertEqual(result['remaining_cases'],[])

    def test_selected_three_normal_nic_retries_do_not_execute_other_cases(self):
        selected=[case_id for case_id,config in self.cases.items()
                  if config['workload']=='nic' and 'normal' in config['nic_modes']]
        self.assertEqual(len(selected),3)
        self.config['feature_linux']['cases']=selected
        result=self.run_suite()
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.submitted,selected)
        self.assertEqual(self.completed,selected)
        self.assertEqual(result['selected_cases'],selected)
        self.assertEqual(result['required_cases'],list(self.cases))
        self.assertEqual(result['remaining_cases'],[case_id for case_id in self.cases if case_id not in selected])
        self.assertFalse(result['full_matrix']);self.assertFalse(result['full_acceptance'])

    def test_invalid_retry_selection_never_prepares_or_dispatches(self):
        valid=next(iter(self.cases))
        for index,selected in enumerate(([],['unknown'],[valid,valid],valid,None,[1],[{}])):
            with self.subTest(selected=selected):
                self.output=Path(self.tmp.name)/('invalid-selection-'+str(index))
                self.config['feature_linux']['cases']=selected
                result=self.run_suite()
                self.assertFalse(result['ok'])
                self.assertEqual(result['status'],'blocked')
                self.assertIn('nonempty distinct list',result['reason'])
                self.assertEqual(result['required_cases'],list(self.cases))
        self.assertFalse(self.submitted)
        suite.prepare_case.assert_not_called()

    def test_standalone_summary_reports_partial_scope(self):
        import run_feature_linux_job as job
        text=job.linux_summary(dict(selected_cases=['one','two','three'],required_cases=list(self.cases),
            full_matrix=False,details=[dict(ok=True) for _ in range(3)]))
        self.assertIn('3/3 selected',text)
        self.assertIn('partial retry; 3/7 required',text)
        self.assertNotIn('All seven',text)

    def test_failed_case_does_not_skip_other_cases_or_leave_workers(self):
        self.failed_cases={next(iter(self.cases))}
        result=self.run_suite()
        self.assertFalse(result['ok'])
        self.assertEqual(set(self.completed),set(self.cases))
        self.assertEqual(sum(d['ok'] for d in result['details']),6)

    def test_optional_stop_on_failure_joins_first_worker_and_skips_queued_cases(self):
        self.failed_cases = {next(iter(self.cases))}
        self.config['feature_linux']['stop_on_case_failure'] = True
        result = self.run_suite()
        self.assertFalse(result['ok'])
        self.assertTrue(result['workers_joined'])
        self.assertEqual(self.submitted, [next(iter(self.cases))])
        self.assertEqual(self.completed, self.submitted)
        self.assertEqual(sum(d['status'] == 'not_run' for d in result['details']), 6)
        self.assertFalse(result['full_acceptance'])

    def test_stop_on_failure_joins_already_active_second_worker(self):
        self.failed_cases = {next(iter(self.cases))}
        self.config['feature_linux'].update(stop_on_case_failure=True, max_pending=2)
        result = self.run_suite()
        self.assertFalse(result['ok'])
        self.assertTrue(result['workers_joined'])
        self.assertEqual(len(self.submitted), 2)
        self.assertEqual(self.completed, self.submitted)
        self.assertEqual(sum(d['status'] == 'not_run' for d in result['details']), 5)

    def test_stop_on_failure_does_not_skip_passing_cases(self):
        self.config['feature_linux']['stop_on_case_failure'] = True
        result = self.run_suite()
        self.assertTrue(result['ok'], result)
        self.assertEqual(set(self.completed), set(self.cases))

    def test_stop_on_failure_requires_boolean(self):
        for index, value in enumerate((0, 1, 'true', None)):
            self.output = Path(self.tmp.name)/('invalid-stop-'+str(index))
            self.config['feature_linux']['stop_on_case_failure'] = value
            with self.assertRaisesRegex(ValueError, 'must be a boolean'):
                self.run_suite()
        self.assertFalse(self.submitted)

    def test_explicit_two_pending_jobs_is_supported(self):
        self.config['feature_linux']['max_pending']=2
        result=self.run_suite()
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.max_active,2)

    def test_pending_limit_must_be_integer_one_or_two(self):
        for index,value in enumerate((0,3,True,1.0,'2')):
            with self.subTest(value=value):
                self.output=Path(self.tmp.name)/('invalid-'+str(index))
                self.config['feature_linux']['max_pending']=value
                with self.assertRaisesRegex(ValueError,'integer 1 or 2'):
                    self.run_suite()
        self.assertFalse(self.submitted)

    def test_failed_child_error_and_ray_status_message_are_retained(self):
        case_id=next(iter(self.cases));self.failed_cases={case_id}
        original=self.submit
        def submit(*args,**kwargs):
            job=original(*args,**kwargs)
            if args[1]==case_id:
                write(Path(job['snapshot']).parent/'result.json',dict(passed=False,error='FileNotFoundError: firesim manager missing'))
            return job
        self.client.get_job_info=lambda job:dict(message='entrypoint process exited with code1')
        with patch.object(suite.feature_f2,'submit',side_effect=submit):
            result=self.run_suite()
        detail=next(item for item in result['details'] if item['case_id']==case_id)
        self.assertIn('firesim manager missing',detail['reason'])
        self.assertIn('entrypoint process exited',detail['reason'])
        self.assertEqual(detail['worker_diagnostics']['error'],'FileNotFoundError: firesim manager missing')
        self.assertTrue(Path(detail['worker_result']['path']).is_file())
        self.assertEqual(set(self.completed),set(self.cases))

    def test_failed_ray_before_worker_result_retains_scheduler_diagnostic(self):
        self.failed_cases={next(iter(self.cases))}
        original=self.submit
        def submit(*args,**kwargs):
            job=original(*args,**kwargs)
            if args[1] in self.failed_cases:
                (Path(job['snapshot']).parent/'result.json').unlink()
            return job
        self.client.get_job_info=lambda job:dict(message='Failed to start job supervisor after900s; insufficient memory resources')
        with patch.object(suite.feature_f2,'submit',side_effect=submit):
            result=self.run_suite()
        detail=next(item for item in result['details'] if item['case_id'] in self.failed_cases)
        self.assertIn('insufficient memory resources',detail['reason'])
        self.assertNotIn('worker_result',detail)

    def test_unconfirmed_remote_farm_cleanup_stops_before_next_case(self):
        self.failed_cases={next(iter(self.cases))}
        def uncertain(snapshot):
            path=Path(snapshot).parent/'external-cleanup.json'
            write(path,dict(success=False,error='owned farm still present'))
            return dict(success=False,evidence=str(path))
        with patch('feature_farm_cleanup.recover',side_effect=uncertain):
            result=self.run_suite()
        self.assertFalse(result['ok'])
        self.assertFalse(result['workers_joined'])
        self.assertEqual(len(self.submitted),1)
        self.assertEqual(len(result['unjoined_resources']),1)
        self.assertIn('cleanup is unconfirmed',result['reason'])

    def test_baseline_failure_dispatches_no_feature_jobs(self):
        self.semantic['details'][1]['ok']=False
        result=self.run_suite()
        self.assertEqual(result['status'],'blocked')
        self.assertFalse(self.submitted)

    def test_wrong_baseline_identity_dispatches_no_feature_jobs(self):
        self.semantic['identity']='old baseline'
        result=self.run_suite()
        self.assertFalse(result['ok']); self.assertFalse(self.submitted)

    def test_strict_feature_prebuild_mode_blocks_missing_reports(self):
        self.config['feature_linux']['require_feature_semantic']=True
        result=self.run_suite()
        self.assertFalse(result['ok']); self.assertFalse(self.submitted)
        self.assertTrue(all('No matched enabled' in d['reason'] for d in result['details']))

    def semantic_reports(self):
        reports={}
        for case_id,config in self.cases.items():
            directory=Path(self.tmp.name)/'semantic'/case_id;directory.mkdir(parents=True)
            implementation=self.prepare(self.repo,directory,case_id,config,{},'fixture-sources',None)
            descriptor=directory/'config.json';write(descriptor,dict(case_id=case_id,config=config))
            golden=directory/'golden.sv';golden.write_text('fixture matched SFC')
            command=self.command(directory/'checker')
            report=copy.deepcopy(self.semantic['details'][0]['result'])
            report.update(case_id=case_id,target=case_id,enabled_features=[config['feature']],command=command,
                artifacts=[dict(role='config',**record(descriptor)),
                           dict(role='rtl',**record(implementation/'generated/FireSim-generated.sv')),
                           dict(role='golden_rtl',**record(golden))])
            for side in ('sfc','circt'):
                for kind in ('rtl','driver'):
                    report['details']['paired_syntax']['pairs'][side][kind]['command']=command
            path=directory/'result.json';write(path,report);reports[case_id]=str(path)
        self.config['feature_linux'].update(require_feature_semantic=True,semantic_reports=reports)
        return reports

    def test_strict_semantic_receipts_must_bind_actual_enabled_rtl(self):
        reports=self.semantic_reports();case_id=next(iter(self.cases));path=Path(reports[case_id])
        report=json.loads(path.read_text());wrong=path.parent/'wrong-rtl.sv';wrong.write_text('different candidate')
        report['artifacts']=[dict(role='rtl',**record(wrong)) if item['role']=='rtl' else item for item in report['artifacts']]
        write(path,report)
        result=self.run_suite()
        self.assertFalse(result['ok'])
        failed=next(item for item in result['details'] if item['case_id']==case_id)
        self.assertIn('differs from the actual Linux case',failed['reason'])
        self.assertEqual(len(self.submitted),6)

    def test_strict_bound_feature_prebuild_reports_allow_actual_linux_execution(self):
        self.semantic_reports()
        result=self.run_suite()
        self.assertTrue(result['ok'],result)
        self.assertTrue(all(item['feature_semantic']['status']=='passed' for item in result['details']))
        self.assertFalse(result['full_acceptance'])

    def test_build_only_pass_is_rejected(self):
        self.corrupt=lambda result,directory: result.update(linux_booted=False)
        result=self.run_suite()
        self.assertFalse(result['ok']); self.assertTrue(all(not d['ok'] for d in result['details']))

    def test_missing_checker_observations_are_rejected(self):
        def corrupt(result,directory):
            path=directory/'feature-check.json'; data=json.loads(path.read_text()); data['observations']=[]
            write(path,data); result['feature_checks']=[record(path)]
        self.corrupt=corrupt
        result=self.run_suite()
        self.assertFalse(result['ok'])
        self.assertTrue(all('no actual observations' in d['reason'] for d in result['details']))

    def test_source_drift_before_dispatch_blocks_cloud_work(self):
        with patch.object(suite.readiness,'source_identity',side_effect=['initial','changed']):
            result=self.run_suite()
        self.assertFalse(result['ok']); self.assertFalse(self.submitted)

    def test_final_source_drift_rejects_all_passes_after_join(self):
        with patch.object(suite.readiness,'source_identity',side_effect=['initial','initial','changed']):
            result=self.run_suite()
        self.assertFalse(result['ok']); self.assertEqual(set(self.completed),set(self.cases))

    def test_dram_zeroing_is_rejected_even_if_runtime_claims_pass(self):
        def corrupt(result,directory):
            runtime=directory/'runtime.yaml'; cfg=yaml.safe_load(runtime.read_text()); cfg['host_debug']['zero_out_dram']=True
            runtime.write_text(yaml.safe_dump(cfg))
            workload=directory/'workload-inputs.json'; write(workload,dict(runtime=record(runtime)))
            result['receipts']['workload_inputs']=record(workload)
        self.corrupt=corrupt
        result=self.run_suite()
        self.assertFalse(result['ok'])
        self.assertTrue(all('zero-out disabled' in d['reason'] for d in result['details']))

    def test_nonfinite_deadlines_are_rejected(self):
        self.config['feature_linux']['job_timeout_seconds']=float('inf')
        with self.assertRaisesRegex(ValueError,'positive job deadline'):
            self.run_suite()
        self.assertFalse(self.submitted)

    def test_failure_during_submission_joins_previously_started_worker(self):
        self.config['feature_linux']['max_pending']=2
        original=self.submit
        def interrupted(*args,**kwargs):
            if self.submitted: raise KeyboardInterrupt('fixture interruption')
            return original(*args,**kwargs)
        with patch.object(suite.feature_f2,'submit',side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt): self.run_suite()
        self.assertEqual(len(self.submitted),1)
        self.assertTrue(all(job['complete'] and job['stopped'] for job in self.jobs.values()))
        result=json.loads((self.output/'feature-linux/result.json').read_text())
        self.assertTrue(result['workers_joined'])

    def test_cancellation_has_bounded_unconfirmed_join_and_reports_owned_jobs(self):
        self.config['feature_linux'].update(job_timeout_seconds=.1,cleanup_timeout_seconds=.1,max_pending=2)
        clock=iter(i*.1 for i in range(100))
        self.client.get_job_status=lambda job:'RUNNING'
        with patch.object(suite.time,'monotonic',side_effect=lambda:next(clock)):
            result=self.run_suite()
        self.assertFalse(result['ok']); self.assertFalse(result['workers_joined'])
        self.assertEqual(len(result['unjoined_jobs']),2)

    def test_sigterm_joins_owned_worker_and_recovers_farm_before_return(self):
        import signal
        original_handler=signal.getsignal(signal.SIGTERM)
        def status(job):
            if not self.jobs[job]['stopped']:
                os.kill(os.getpid(),signal.SIGTERM)
            return self.status(job)
        self.client.get_job_status=status
        with self.assertRaises(KeyboardInterrupt):
            self.run_suite()
        self.assertEqual(len(self.submitted),1)
        self.assertTrue(all(job['complete'] and job['stopped'] for job in self.jobs.values()))
        result=json.loads((self.output/'feature-linux/result.json').read_text())
        self.assertTrue(result['workers_joined'])
        self.assertIn('external_cleanup',result['details'][0])
        self.assertEqual(signal.getsignal(signal.SIGTERM),original_handler)

    def cached_case(self, source_identity):
        case_id=next(iter(self.cases)); config=self.cases[case_id]
        directory=Path(self.tmp.name)/'cached'; directory.mkdir()
        generated=directory/'generated'; generated.mkdir()
        (generated/'FireSim-generated.sv').write_text('fixture generated RTL')
        (generated/'FireSim-generated.const.h').write_text('new autocounter_t( make_print_bridge_')
        write(generated/'fame-model-graph.json',dict(threaded_groups=1,shared_threads=4))
        inputs=directory/'inputs'; inputs.mkdir()
        name='firechip.chip.FireSim.'+config['target_config']
        fir=inputs/(name+'.sfc.fir'); fir.write_text('fixture exact target FIRRTL')
        annotations=inputs/(name+'.anno.json'); annotations.write_text('[]')
        compiler=directory/'compiler'; compiler.write_text('fixture compiler')
        generator=directory/'generator.jar'; generator.write_text('fixture generator')
        compile_command=self.command(directory/'production_compile')
        command=json.loads(Path(compile_command['path']).read_text())
        command.update(case_id=case_id,purpose='production_compile',source_identity=source_identity)
        write(compile_command['path'],command)
        driver_command=self.command(directory/'driver_build')
        command=json.loads(Path(driver_command['path']).read_text())
        command.update(case_id=case_id,purpose='driver_build',source_identity=source_identity)
        write(driver_command['path'],command)
        binary=directory/'FireSim-f2'; binary.write_text('fixture driver');binary.chmod(0o755)
        write(directory/'compile.json',dict(case_id=case_id,config=config,compiler=record(compiler),generator=record(generator),
            inputs=[record(fir),record(annotations)],resources=[],products=[record(p) for p in generated.iterdir()],
            command=record(compile_command['path'])))
        write(directory/'driver.json',dict(driver=record(binary),command=record(driver_command['path'])))
        descriptor=directory/'implementation-config.json';write(descriptor,dict(config=config))
        index=dict(cases=[dict(case_id=case_id,artifacts=dict(config=record(descriptor)))])
        return directory,case_id,config,dict(compiler=str(compiler),generator_jar=str(generator)),index

    def test_current_bound_compile_and_driver_are_reused(self):
        cached,case_id,config,options,index=self.cached_case('fixture-sources')
        with patch.object(suite.execution,'compile_case') as compile_case,patch.object(suite.execution,'build_driver') as build:
            result=REAL_PREPARE(self.repo,Path(self.tmp.name)/'new',case_id,config,options,'fixture-sources',index)
        self.assertEqual(result,cached); compile_case.assert_not_called();build.assert_not_called()

    def test_stale_source_id_replays_bound_inputs_instead_of_relabeling_old_receipts(self):
        cached,case_id,config,options,index=self.cached_case('old-sources')
        destination=Path(self.tmp.name)/'new';destination.mkdir()
        def compile_case(repo,directory,ident,cfg,compiler,generator,source_inputs):
            self.assertEqual(source_inputs,cached/'inputs')
            raise RuntimeError('fixture reached genuine production compiler replay')
        with patch.object(suite.execution,'compile_case',side_effect=compile_case):
            with self.assertRaisesRegex(RuntimeError,'genuine production compiler replay'):
                REAL_PREPARE(self.repo,destination,case_id,config,options,'fixture-sources',index)
        old=json.loads((cached/'production_compile/command.json').read_text())
        self.assertEqual(old['source_identity'],'old-sources')

    def test_mismatched_target_flags_never_reuse_cached_elaboration(self):
        cached,case_id,config,options,index=self.cached_case('old-sources')
        destination=Path(self.tmp.name)/'new';destination.mkdir()
        config=dict(config,compiler_flags=['--different'])
        def compile_case(repo,directory,ident,cfg,compiler,generator,source_inputs):
            self.assertIsNone(source_inputs)
            raise RuntimeError('fixture fresh target elaboration')
        with patch.object(suite.execution,'compile_case',side_effect=compile_case):
            with self.assertRaisesRegex(RuntimeError,'fresh target elaboration'):
                REAL_PREPARE(self.repo,destination,case_id,config,options,'fixture-sources',index)

    def test_new_native_compiler_replays_exact_bound_inputs_without_old_rtl_reuse(self):
        cached,case_id,config,options,index=self.cached_case('old-sources')
        old_manifest=record(cached/'compile.json')
        compiler=Path(self.tmp.name)/'new-compiler';compiler.write_text('new native compiler bytes')
        options['compiler']=str(compiler)
        destination=Path(self.tmp.name)/'new';destination.mkdir()
        def compile_case(repo,directory,ident,cfg,current_compiler,generator,source_inputs):
            self.assertEqual(current_compiler,compiler)
            self.assertEqual(source_inputs,cached/'inputs')
            raise RuntimeError('fixture actual new compiler replay')
        with patch.object(suite.execution,'compile_case',side_effect=compile_case) as compile_call:
            with self.assertRaisesRegex(RuntimeError,'actual new compiler replay'):
                REAL_PREPARE(self.repo,destination,case_id,config,options,'fixture-sources',index)
        compile_call.assert_called_once()
        self.assertEqual(record(cached/'compile.json'),old_manifest)

    def test_changed_generator_never_reuses_old_target_elaboration(self):
        cached,case_id,config,options,index=self.cached_case('old-sources')
        generator=Path(self.tmp.name)/'new-generator.jar';generator.write_text('different target generator')
        options['generator_jar']=str(generator)
        destination=Path(self.tmp.name)/'new';destination.mkdir()
        def compile_case(repo,directory,ident,cfg,compiler,current_generator,source_inputs):
            self.assertEqual(current_generator,generator)
            self.assertIsNone(source_inputs)
            raise RuntimeError('fixture genuine new target elaboration')
        with patch.object(suite.execution,'compile_case',side_effect=compile_case):
            with self.assertRaisesRegex(RuntimeError,'genuine new target elaboration'):
                REAL_PREPARE(self.repo,destination,case_id,config,options,'fixture-sources',index)


if __name__=='__main__': unittest.main()
