"""Runtime checker controls; fixtures/native execution never claim an FPGA pass."""
import copy
import csv
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from feature_workloads import thread_check, instrumentation_check as monitor, nic_check


class ThreadRuntimeChecks(unittest.TestCase):
    def transcript(self):
        rows=[dict(thread=i,cpu=i,epochs=5,words=4093,rounds=1024,digest=thread_check.digest(i)) for i in range(4)]
        return ('CIRCT_LINUX_BOOT: PASS\n'+''.join('CIRCT_THREAD_STATE '+json.dumps(row)+'\n' for row in rows)
                +'CIRCT_THREAD_RESULT: PASS threads=4\nCIRCT_LINUX_POWEROFF: REQUESTED\nreboot: Power down\n')

    def test_all_pinned_threads_and_written_memory(self):
        verdict=thread_check.check(self.transcript(),4)
        self.assertEqual(len(set(verdict['digests'])),4)
        self.assertIn('per_thread_initialized_memory',verdict['observed_checks'])

    def test_missing_duplicate_corrupt_and_post_shutdown_state_fail(self):
        good=self.transcript(); lines=good.splitlines()
        broken=[good.replace('"cpu": 1','"cpu": 0'),good.replace('"cpu": 0','"cpu": false'),
                good.replace('"epochs": 5','"epochs": 4',1),good.replace(thread_check.digest(2),'0'*16),
                '\n'.join(line for line in lines if '"thread": 3' not in line),
                good.replace('CIRCT_THREAD_RESULT: PASS threads=4','CIRCT_THREAD_RESULT: PASS threads=4\nCIRCT_THREAD_RESULT: PASS threads=4'),
                good+'CIRCT_LINUX_POWEROFF: FAIL returned\n',
                good.replace('CIRCT_LINUX_BOOT: PASS','boot incomplete')]
        for text in broken:
            with self.subTest(text=text[-150:]),self.assertRaises(ValueError):thread_check.check(text,4)

    def test_native_written_state_matches_independent_python_oracle(self):
        cc=shutil.which('cc') or '/scratch/jfx/fsim-circt/.conda-env/bin/cc'
        source=Path(__file__).parent/'feature_workloads/thread_state.c'
        with tempfile.TemporaryDirectory() as directory:
            binary=Path(directory)/'threads'
            subprocess.run([cc,'-O2','-Wall','-Wextra','-Werror','-pthread',str(source),'-o',str(binary)],check=True,capture_output=True)
            process=subprocess.run([str(binary),'4'],check=True,capture_output=True,text=True,timeout=30)
            self.assertTrue(thread_check.check(process.stdout,4,shutdown=False)['passed'])


class InstrumentationRuntimeChecks(unittest.TestCase):
    def fixture(self):
        period=monitor.PERIOD+8
        manifest=dict(schema_version=2,reset_base_cycles=50,clock_phase='all-countdowns-zero',
                      domains=[],sample_period_base=period,min_samples=3,min_prints=2)
        csvs=[]; prints=[]
        for ident,div in [(0,1),(1,2)]:
            name='domain'+str(ident); reset=50//div
            masked=50 if div==1 else 26
            first_print=0 if div==1 else monitor.PERIOD
            manifest['domains'].append(dict(id=ident,name=name,multiplier=1,divisor=div,
                reset_cycles=reset,masked_source_tokens=masked,first_print_cycle=first_print))
            labels=['local_cycle',*[f'top_feature_domain{ident}_{suffix}' for suffix in ('active','cycles','wrap')]]
            rows=[['version','1'],['Clock Domain Name',' '+name,' Base Multiplier',' 1',' Base Divisor',' '+str(div)],
                  ['label',*labels],['description','Clock cycles elapsed in the local domain.','Cycles outside local reset.',
                  'Target domain cycles since local reset release.','Low four bits of target domain cycles.'],
                  ['type','Accumulate','Accumulate','Identity','Identity'],['event width','1','1','64','4'],
                  ['accumulator width','64','64','64','64']]
            for sample in range(1,4):
                local=sample*period//div;count=local-reset
                rows.append([sample*period,local,local-masked,count,count%16])
            out=io.StringIO();csv.writer(out).writerows(rows);csvs.append(out.getvalue())
            prints.append(f'# Clock Domain: {name}, Relative Frequency: 1/{div} of Base Clock\n'+''.join(
                f'CYCLE:{count+reset:13d} [CIRCT_FEATURE] domain={ident} cycles={count} wrap=0\n'
                for count in (first_print,first_print+monitor.PERIOD)))
        uart=('CIRCT_LINUX_BOOT: PASS\n'+monitor.MARKER+'\n'
              'CIRCT_LINUX_POWEROFF: REQUESTED\nreboot: Power down\n')
        return manifest,csvs,prints,uart

    def test_two_actual_domain_contracts(self):
        verdict=monitor.check(*self.fixture(),require_shutdown=True)
        self.assertEqual(len(verdict['domains']),2)
        self.assertIn('rational_domain_cycle_progress',verdict['observed_checks'])

    def test_missing_domain_corrupt_reset_ratio_and_shutdown_fail(self):
        m,c,p,u=self.fixture()
        cases=[(m,c[:1],p,u),(m,c,p[:1],u),(m,c,p,u.replace('reboot: Power down','still running')),
               (m,c,p,u.replace('CIRCT_LINUX_BOOT: PASS','')),(m,c,p,u+monitor.MARKER),
               (m,c,p,u+'CHIA_FEATURE_INSTRUMENTATION: FAIL\n'),
               (m,c,[p[0],p[1].replace('domain=1','domain=0')],u)]
        for key,value in [('reset_cycles',24),('divisor',3)]:
            changed=copy.deepcopy(m);changed['domains'][1][key]=value;cases.append((changed,c,p,u))
        changed=copy.deepcopy(m);changed['min_prints']=0;cases.append((changed,c,p,u))
        for args in cases:
            with self.subTest(args=str(args)[-100:]),self.assertRaises(ValueError):
                monitor.check(*args,require_shutdown=True)

    def test_reset_acquisition_matches_captured_sfc_oracle(self):
        # These expected values were captured from actual SFC/native FAME
        # boundary execution, including fractional ratios and both stall modes.
        path=Path(__file__).parent/'feature_workloads/testdata/reset_acquisition_sfc.json'
        fixture=json.loads(path.read_text())
        self.assertEqual(len(fixture['cases']),24)
        for case in fixture['cases']:
            with self.subTest(case=case):
                self.assertEqual(monitor.reset_acquisition(case['reset_base_cycles'],
                    case['multiplier'],case['divisor']),case['expected'])

    def test_reset_phase_does_not_hide_counter_or_print_corruption(self):
        m,c,p,u=self.fixture()
        rows=list(csv.reader(io.StringIO(c[1])))
        variants=[]
        for row, column, delta in [(7,2,1),(7,2,-1),(8,2,1),(7,3,-1),(7,4,1)]:
            bad=copy.deepcopy(rows);bad[row][column]=str(int(bad[row][column])+delta)
            out=io.StringIO();csv.writer(out).writerows(bad);variants.append((m,[c[0],out.getvalue()],p,u))
        # The only omitted event is the declared reset-masked count-zero Print.
        bad_print=p[1].replace('cycles=1048576','cycles=2097152',1)
        variants.append((m,c,[p[0],bad_print],u))
        zero='CYCLE:           25 [CIRCT_FEATURE] domain=1 cycles=0 wrap=0\n'
        lines=p[1].splitlines(keepends=True)
        variants.append((m,c,[p[0],lines[0]+zero+''.join(lines[1:])],u))
        for key,value in [('masked_source_tokens',25),('first_print_cycle',0)]:
            bad=copy.deepcopy(m);bad['domains'][1][key]=value;variants.append((bad,c,p,u))
        for key,value in [('reset_base_cycles',49),('clock_phase','shifted'),('schema_version',1)]:
            bad=copy.deepcopy(m);bad[key]=value;variants.append((bad,c,p,u))
        for args in variants:
            with self.subTest(args=str(args)[-120:]),self.assertRaises(ValueError):
                monitor.check(*args,require_shutdown=True)

    def test_unsupported_or_invalid_reset_contract_fails_closed(self):
        for args in [(0,1,2),(50,0,2),(50,1,0),(50,2,1),(True,1,2),(50,1.0,2)]:
            with self.subTest(args=args),self.assertRaises(ValueError):
                monitor.reset_acquisition(*args)


class NICRuntimeChecks(unittest.TestCase):
    def fixture(self,modes):
        packets=8;uart='CIRCT_LINUX_BOOT: PASS\n';peer=''
        for widget in modes:
            uart+='CIRCT_NIC_WORKER '+json.dumps(dict(widget=widget,event='launched'))+'\n'
        for sequence in range(packets):
            for widget,mode in modes.items():
                direction=int(mode=='normal')
                uart+='CIRCT_NIC_PACKET '+json.dumps(nic_check.expected(widget,sequence,direction))+'\n'
                if mode=='normal':
                    for event,d in [('request',0),('reply',1)]:
                        peer+='CIRCT_NIC_HOST '+json.dumps(dict(event=event,**nic_check.expected(widget,sequence,d)))+'\n'
        for widget,mode in modes.items():
            summary=dict(widget=widget,mode=mode,interface='eth'+str(widget),packets=packets,
                         bytes=sum(len(nic_check.frame(widget,s,0)) for s in range(packets)),passed=True)
            uart+='CIRCT_NIC_RESULT '+json.dumps(summary)+'\n'
            if mode=='normal':peer+='CIRCT_NIC_PEER_RESULT '+json.dumps(dict(widget=widget,requests=packets,replies=packets,rounds=2,passed=True))+'\n'
        for widget in modes:uart+='CIRCT_NIC_WORKER '+json.dumps(dict(widget=widget,event='joined',returncode=0))+'\n'
        uart+='CIRCT_LINUX_POWEROFF: REQUESTED\nreboot: Power down\n'
        return [uart],list(modes),modes,packets,peer or None

    def test_normal_multiple_and_mixed_modes(self):
        for modes in ({0:'normal'},{0:'normal',1:'normal'},{0:'loopback'},{0:'normal',1:'loopback'}):
            result=nic_check.check(*self.fixture(modes),require_worker_lifecycle=True)
            self.assertTrue(result['worker_lifecycle_checked'])

    def test_missing_launch_bad_join_alias_interface_and_cross_widget_payload_fail(self):
        args=list(self.fixture({0:'normal',1:'normal'}));text=args[0][0]
        launch='CIRCT_NIC_WORKER '+json.dumps(dict(widget=1,event='launched'))+'\n'
        join='CIRCT_NIC_WORKER '+json.dumps(dict(widget=0,event='joined',returncode=0))+'\n'
        broken=[text.replace(launch,''),text.replace('"returncode": 0','"returncode": 1',1),
                text.replace('"returncode": 0','"returncode": false',1),
                text.replace('"interface": "eth1"','"interface": "eth0"'),
                text.replace('"fnv1a": '+str(nic_check.expected(1,0,1)['fnv1a']),
                             '"fnv1a": '+str(nic_check.expected(0,0,1)['fnv1a']),1),
                text.replace(join,'').replace(launch,join+launch)]
        for uart in broken:
            with self.subTest(text=uart[-150:]),self.assertRaises(ValueError):
                nic_check.check([uart],*args[1:],require_worker_lifecycle=True)
        with self.assertRaises(ValueError):nic_check.check(*args[:-1],None,require_worker_lifecycle=True)


if __name__=='__main__':unittest.main()
