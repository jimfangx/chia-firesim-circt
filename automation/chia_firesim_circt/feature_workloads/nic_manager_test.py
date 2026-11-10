#!/usr/bin/env python3
"""Run the production manager hook with local transport and the real NIC peer.

Physical manager classes are test doubles; actual delivery/hash checking,
launcher, PID ownership, shared-memory traffic, stop/join and retained results
are executed. No FireSim/FPGA acceptance follows from this focused test.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import mmap
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import feature_nic_manager
import nic_validate


class Output(str):
    def __new__(cls, stdout, returncode):
        obj = super().__new__(cls, stdout); obj.return_code = returncode; return obj


class LocalTransport:
    env = SimpleNamespace(host_string='local-focused-test')
    def __init__(self): self.directory = None; self.warn = False
    @contextmanager
    def settings(self, **kwargs):
        old = self.warn; self.warn = kwargs.get('warn_only', self.warn)
        try: yield
        finally: self.warn = old
    @contextmanager
    def cd(self, path):
        old = self.directory; self.directory = path
        try: yield
        finally: self.directory = old
    def run(self, command, **kwargs):
        result = subprocess.run(['bash', '-c', command], cwd=self.directory, capture_output=True, text=True, timeout=kwargs.get('timeout', 30))
        if result.returncode and not self.warn: raise RuntimeError(result.stderr)
        return Output(result.stdout, result.returncode)
    def put(self, source, destination): shutil.copy2(source, destination)
    def get(self, source, destination): shutil.copy2(source, destination)


class Runtime:
    def get_boot_simulation_command(self, all_macs, all_linklatencies, all_netbws, all_shmemportnames, hostdebug_config, extra_plusargs, all_bootbinaries, all_rootfses):
        return locals()


class Server:
    def __init__(self, host, results): self.host = host; self.results = results
    def get_host_instance(self): return self.host
    def get_local_job_results_dir_path(self): return str(self.results)
    def copy_back_job_results_from_run(self, slot):
        self.results.mkdir(parents=True, exist_ok=True)
        source=Path(self.host.instance_deploy_manager.get_remote_sim_dir_for_slot(slot))/'uartlog'
        if source.is_file(): shutil.copy2(source,self.results/'uartlog')
        (self.results / 'real-manager-copy-called').touch()
        if getattr(self,'fail_copy',False): raise OSError('controlled original result-copy failure')


class Manager:
    def __init__(self, directory):
        self.directory = directory
        (directory / 'sim_slot_0').mkdir(parents=True)
        self.parent_node = SimpleNamespace(instance_deploy_manager=self)
        self.server = Server(self.parent_node, directory / 'results')
        self.parent_node.sim_slots = [self.server]
    def get_remote_sim_dir_for_slot(self, slot): return str(self.directory / ('sim_slot_' + str(slot))) + '/'
    def instance_assigned_simulations(self): return True
    def start_sim_slot(self, slot):
        self.boot = Runtime().get_boot_simulation_command([], [1], [1], ['default'], SimpleNamespace(zero_out_dram=False), '+slotid=0', ['kernel'], ['rootfs'])
    def kill_sim_slot(self, slot): pass


def exercise(port, packets=8):
    mappings = {}
    try:
        for direction in ('nts', 'stn'):
            for round_ in (0, 1):
                with (Path('/dev/shm') / ('port_' + direction + port + '_' + str(round_))).open('r+b') as stream:
                    mappings[direction, round_] = mmap.mmap(stream.fileno(), 129)
        outgoing = nic_validate.flits_for(0, packets)
        received, round_, token_sequence = 0, 0, 0
        while outgoing or received < packets:
            tx, rx = mappings['nts', round_], mappings['stn', round_]
            tx[:128] = nic_validate.tokens(outgoing, 2, token_sequence); tx[128] = 1
            token_sequence += 2
            deadline = time.monotonic() + 5
            while not rx[128]:
                if time.monotonic() > deadline: raise RuntimeError('peer traffic timeout')
                time.sleep(.0001)
            for beat in range(2):
                control = int.from_bytes(rx[beat * 64:beat * 64 + 8], 'little')
                for slot in range(7):
                    bits = control >> (43 + slot * 3) & 7
                    if bits & 1 and bits & 4: received += 1
            rx[128] = 0; round_ ^= 1
        return token_sequence
    finally:
        for mapping in mappings.values(): mapping.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--peer', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    port = 'chia_manager_test_' + uuid.uuid4().hex
    config = dict(peer_binary=str(args.peer.resolve()), peer_sha256=hashlib.sha256(args.peer.read_bytes()).hexdigest(),
                  bindings=[dict(widget=0, port=port, mode='normal'), dict(widget=1, port=port + '_1', mode='runtime_loopback')],
                  packets=8, link_latency=14, timeout_seconds=30)
    transport = LocalTransport()
    assert feature_nic_manager.install(dict(feature_nic=config), api=transport, runtime_class=Runtime, manager_class=Manager, server_class=Server)
    manager = Manager(args.output_dir / 'complete')
    try:
        manager.start_sim_slot(0)
        assert manager.boot['all_shmemportnames'] == [port, port + '_1']
        assert manager.boot['all_linklatencies'] == [14, 14]
        assert manager.boot['all_netbws'] == [200, 200]
        assert manager.boot['all_bootbinaries'] == ['kernel'] and manager.boot['all_rootfses'] == ['rootfs']
        assert manager.boot['extra_plusargs'] == '+slotid=0 +nic-loopback1'
        sent_beats = exercise(port)
        manager.server.copy_back_job_results_from_run(0)
        report, = manager.server.results.glob('*/peer-lifecycle.json')
        payload = json.loads(report.read_text())
        assert payload['passed'] and payload['joined'] and payload['returncode'] == 0
        summaries = [json.loads(line.split(' ', 1)[1])
                     for line in (report.parent / 'peer.stdout').read_text().splitlines()
                     if line.startswith('CIRCT_NIC_PEER_RESULT ')]
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary['widget'] == 0 and summary['token_sequence_observed']
        assert summary['token_first'] == 0 and summary['token_last'] == sent_beats - 1
        assert summary['token_count'] == sent_beats and summary['rounds'] * 2 == sent_beats
        assert (manager.server.results / 'real-manager-copy-called').exists()
        # Abort a fresh instance before traffic. It must stop/join the peer and
        # preserve a failure receipt; cancellation cannot be reported passed.
        aborted = Manager(args.output_dir / 'aborted')
        aborted.start_sim_slot(0)
        aborted.kill_sim_slot(0)
        report, = aborted.server.results.glob('*/peer-lifecycle.json')
        payload = json.loads(report.read_text())
        assert not payload['passed'] and payload['joined'] and payload['returncode'] == 1
        incomplete = Manager(args.output_dir / 'incomplete')
        incomplete.start_sim_slot(0)
        uart=Path(incomplete.get_remote_sim_dir_for_slot(0))/'uartlog'
        uart.write_text('CONTROL_UART: simulated driver failure retained after zero peer traffic\n')
        try:
            incomplete.server.copy_back_job_results_from_run(0)
        except ValueError as error:
            assert 'missed traffic' in str(error)
        else:
            raise AssertionError('manager accepted missing normal-mode traffic')
        assert (incomplete.server.results / 'real-manager-copy-called').exists()
        assert (incomplete.server.results / 'uartlog').read_bytes()==uart.read_bytes()
        report, = incomplete.server.results.glob('*/peer-lifecycle.json')
        assert json.loads(report.read_text())['joined']
        copy_failed=Manager(args.output_dir/'copy-failed')
        copy_failed.start_sim_slot(0)
        uart=Path(copy_failed.get_remote_sim_dir_for_slot(0))/'uartlog'
        uart.write_text('CONTROL_UART: retained before simultaneous collection and peer errors\n')
        copy_failed.server.fail_copy=True
        try:
            copy_failed.server.copy_back_job_results_from_run(0)
        except RuntimeError as error:
            assert 'missed traffic' in str(error) and 'controlled original result-copy failure' in str(error)
        else:
            raise AssertionError('manager accepted simultaneous peer and collection failures')
        assert (copy_failed.server.results/'uartlog').read_bytes()==uart.read_bytes()
        report,=copy_failed.server.results.glob('*/peer-lifecycle.json')
        assert json.loads(report.read_text())['joined']
        print(json.dumps(dict(passed=True, scope='actual NIC manager hook lifecycle with local transport; no FPGA acceptance',
                              copied_actual_peer=True, hash_checked=True, runtime_args_bound=True,
                              successful_completion_joined=True, aborted_peer_joined=True, retained_failed_abort=True,
                              collected_token_conservation_checked=True,
                              missing_traffic_rejected=True,failed_peer_uart_collected_before_reraise=True,
                              simultaneous_collection_and_peer_errors_preserved=True), indent=2))
    finally:
        manager.kill_sim_slot(0)


if __name__ == '__main__':
    main()
