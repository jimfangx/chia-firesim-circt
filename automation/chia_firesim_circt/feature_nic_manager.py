"""Install bounded feature NIC peers around the real FireSim manager lifecycle.

Only a frozen manifest containing feature_nic enables these hooks. FPGA loading,
driver execution, Linux, monitoring and result collection stay with FireSim.
Peers run on the actual simulator host, use unique per-widget shared memory and
are stopped/joined before job results are accepted. No verification is invented.
"""
from __future__ import annotations
import atexit
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
import time


MODES = {'normal', 'runtime_loopback', 'compile_time_loopback'}


def validate_config(manifest):
    config = manifest.get('feature_nic')
    if config is None:
        return None
    config = dict(config)
    bindings = config.get('bindings')
    if not isinstance(bindings, list) or not bindings:
        raise ValueError('feature_nic requires explicit widget bindings')
    if [row.get('widget') for row in bindings] != list(range(len(bindings))):
        raise ValueError('feature_nic widget bindings must be contiguous and ordered')
    ports = set()
    for row in bindings:
        if row.get('mode') not in MODES or not isinstance(row.get('port'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', row['port']) or row['port'] in ports:
            raise ValueError('feature_nic requires distinct safe ports and supported modes')
        ports.add(row['port'])
    for field in ('packets', 'link_latency', 'timeout_seconds'):
        if type(config.get(field)) is not int or config[field] <= 0:
            raise ValueError('feature_nic requires a positive integer ' + field)
    if config['packets'] % 8 or config['link_latency'] % 7 or config['link_latency'] // 7 > 3072:
        raise ValueError('feature_nic packets/latency violate the NIC transport geometry')
    if any(row['mode'] == 'normal' for row in bindings):
        path = Path(config['peer_binary'])
        if not path.is_absolute() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != config.get('peer_sha256'):
            raise ValueError('feature_nic must bind the frozen actual host-peer binary')
    return config


def bind_boot_arguments(arguments, config):
    """Change only NIC runtime options; retain all workload/rootfs/clock options."""
    bindings = config['bindings']
    options = shlex.split(arguments.get('extra_plusargs', ''))
    if any(re.match(r'\+(?:nic-loopback|shmemportname|linklatency)\d', arg) or arg == '+zero-out-dram' for arg in options):
        raise ValueError('conflicting NIC runtime passthrough options in feature case')
    if arguments['hostdebug_config'].zero_out_dram:
        raise ValueError('feature NIC runs require zero_out_dram false')
    arguments['all_shmemportnames'] = [row['port'] for row in bindings]
    arguments['all_linklatencies'] = [config['link_latency']] * len(bindings)
    arguments['all_netbws'] = [200] * len(bindings)
    # One host target can have several widgets without several Linux kernels.
    # MAC arrays are NIC-indexed, while bootbinary/rootfs arrays stay unchanged.
    arguments['all_macs'] = ['02:00:00:00:%02x:%02x' % ((row['widget'] >> 8) & 255, row['widget'] & 255) for row in bindings]
    options.extend('+nic-loopback' + str(row['widget']) for row in bindings if row['mode'] == 'runtime_loopback')
    arguments['extra_plusargs'] = shlex.join(options)
    return arguments


READY = r'''
import pathlib,sys,time
p=pathlib.Path(sys.argv[1])
for _ in range(100):
    if (p/'peer.pid').exists() and (p/'peer.stdout').exists() and 'CIRCT_NIC_PEER_READY' in (p/'peer.stdout').read_text():
        print((p/'peer.pid').read_text().strip());sys.exit(0)
    if (p/'peer.exit').exists():
        raise SystemExit('NIC peer exited before ready: '+(p/'peer.stderr').read_text())
    time.sleep(.1)
raise SystemExit('NIC peer startup timeout')
'''
DETACH = r'''
import pathlib,subprocess,sys
p=pathlib.Path(sys.argv[1]).resolve()
with (p/'launcher.log').open('ab',buffering=0) as output:
    child=subprocess.Popen(['/bin/bash',str(p/'launch.sh')],cwd=str(p),
        stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
        start_new_session=True,close_fds=True)
(p/'launcher.pid').write_text(str(child.pid)+'\n')
print(child.pid)
'''


def launch_peer(api, remote):
    """Detach without backgrounding Fabric's surrounding cd/SSH command.

    A trailing shell ampersand under api.cd backgrounds the compound cd &&
    launch list. Its shell can keep the SSH channel open until the peer exits.
    The foreground Python helper instead closes all inherited channel handles
    in its detached child and returns immediately. READY is checked separately.
    """
    return api.run('python3 -c ' + shlex.quote(DETACH) + ' ' + shlex.quote(remote),
                   pty=False, timeout=10)


STOP = r'''
import os,pathlib,signal,sys,time
p=pathlib.Path(sys.argv[1]);(p/'stop').touch()
for _ in range(100):
    if (p/'peer.exit').exists():break
    time.sleep(.1)
else:
    # Only signal the recorded process if its command line still contains this
    # run's unique stop path; never kill a reused PID or an unrelated peer.
    pid=int((p/'peer.pid').read_text())
    try:
        command=pathlib.Path('/proc',str(pid),'cmdline').read_bytes()
        if str(p/'stop').encode() not in command:raise SystemExit('NIC peer PID ownership mismatch')
        os.kill(pid,signal.SIGTERM)
    except (ProcessLookupError,FileNotFoundError):pass
    for _ in range(50):
        if (p/'peer.exit').exists():break
        time.sleep(.1)
    else:
        try:
            command=pathlib.Path('/proc',str(pid),'cmdline').read_bytes()
            if str(p/'stop').encode() not in command:raise SystemExit('NIC peer PID ownership mismatch before forced kill')
            os.kill(pid,signal.SIGKILL)
        except (ProcessLookupError,FileNotFoundError):pass
        for _ in range(50):
            if (p/'peer.exit').exists():break
            time.sleep(.1)
        else:raise SystemExit('NIC peer wrapper did not join its child')
print((p/'peer.exit').read_text().strip())
'''


def collect_abort(api, manager, slot, server, config, *, kill_error=None, peer_error=None):
    """Copy raw owned-slot evidence without marking the Linux job complete.

    Missing files and failed copies are diagnostics, never replacement success
    evidence. Do not mount a live guest rootfs or call FireSim's completion hook.
    """
    remote=manager.get_remote_sim_dir_for_slot(slot).rstrip('/')
    local=Path(server.get_local_job_results_dir_path())/'abort-diagnostics'/str(time.time_ns())
    local.mkdir(parents=True,exist_ok=False)
    names=['uartlog','simoutput','heartbeat.csv','sim-run.sh']
    names.extend('niclog'+str(row['widget']) for row in config['bindings'])
    files=[]; failures=[]
    for name in names:
        source=remote+'/'+name
        try:
            with api.settings(warn_only=True):
                exists=api.run('test -f '+shlex.quote(source),timeout=10)
            if getattr(exists,'return_code',0)!=0:
                failures.append(dict(path=source,error='missing'))
                continue
            api.get(source,str(local/name))
            path=local/name
            files.append(dict(path=str(path),bytes=path.stat().st_size,
                              sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        except BaseException as error:
            failures.append(dict(path=source,error=type(error).__name__+': '+str(error)))
    receipt=dict(schema_version=1,scope='aborted NIC runtime raw evidence only',passed=False,
                 remote_directory=remote,slot=slot,files=files,collection_failures=failures,
                 kill_error=kill_error,peer_error=peer_error)
    (local/'collection.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    return receipt


def install(manifest, *, api=None, runtime_class=None, manager_class=None, server_class=None):
    """Patch this manager process; a missing feature_nic manifest is a no-op.

    Optional dependencies permit lifecycle tests with a real local shell backend.
    The production path imports actual Fabric and FireSim classes.
    """
    config = validate_config(manifest)
    if config is None:
        return False
    if api is None:
        import fabric.api as api
    if runtime_class is None:
        from runtools.runtime_config import RuntimeHWConfig as runtime_class
    if manager_class is None:
        from runtools.run_farm_deploy_managers import InstanceDeployManager as manager_class
    if server_class is None:
        from runtools.firesim_topology_elements import FireSimServerNode as server_class
    normal = [row for row in config['bindings'] if row['mode'] == 'normal']
    token = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
    directory_name = 'circt-nic-peer-' + token
    active = {}
    original_boot = runtime_class.get_boot_simulation_command
    signature = inspect.signature(original_boot)

    def boot(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        bind_boot_arguments(bound.arguments, config)
        return original_boot(*bound.args, **bound.kwargs)
    runtime_class.get_boot_simulation_command = boot

    def remote_dir(manager, slot):
        return manager.get_remote_sim_dir_for_slot(slot).rstrip('/') + '/' + directory_name

    def cleanup(manager, slot, server, *, required):
        if not normal:
            return
        remote = remote_dir(manager, slot)
        local = Path(server.get_local_job_results_dir_path()) / directory_name
        local.mkdir(parents=True, exist_ok=True)
        # If kill/abort runs before start, there is no peer to own or stop.
        with api.settings(warn_only=True):
            exists = api.run('test -f ' + shlex.quote(remote + '/peer.pid'))
        if getattr(exists, 'return_code', 0) != 0:
            if required:
                raise ValueError('feature NIC result has no recorded real host peer')
            return
        errors=[]
        joined=False
        try:
            api.run('python3 -c ' + shlex.quote(STOP) + ' ' + shlex.quote(remote), timeout=25)
            joined=True
        except BaseException as error:
            errors.append('stop/join: '+type(error).__name__+': '+str(error))
        # Graceful exit normally unlinks these in Mapping destructors. A forced
        # kill cannot run destructors; after the launcher has joined, reclaim
        # only this ready peer's explicitly owned per-widget names. A startup
        # collision never reaches READY and must preserve the foreign objects.
        reclaim = ('import pathlib,sys; p=pathlib.Path(sys.argv[1]); '
                   'ready=\"CIRCT_NIC_PEER_READY\" in (p/\"peer.stdout\").read_text(); '
                   'names=' + repr(['/dev/shm/port_' + direction + row['port'] + '_' + str(round_)
                                    for row in normal for direction in ('nts', 'stn') for round_ in (0, 1)]) + '; '
                   '[(pathlib.Path(name).unlink(missing_ok=True)) for name in names if ready]')
        if joined:
            try:
                api.run('python3 -c ' + shlex.quote(reclaim) + ' ' + shlex.quote(remote), timeout=10)
            except BaseException as error:
                errors.append('shared memory reclaim: '+type(error).__name__+': '+str(error))
        for name in ('peer.stdout', 'peer.stderr', 'peer.pid', 'peer.exit', 'launcher.log', 'bindings.json'):
            try:
                api.get(remote + '/' + name, str(local / name))
            except BaseException as error:
                errors.append(name+': '+type(error).__name__+': '+str(error))
        code = None
        try:
            code = int((local / 'peer.exit').read_text().strip())
        except (OSError,ValueError) as error:
            errors.append('peer exit: '+str(error))
        rows = []
        for line in ((local / 'peer.stdout').read_text().splitlines() if (local / 'peer.stdout').exists() else []):
            if line.startswith('CIRCT_NIC_PEER_RESULT '):
                try: rows.append(json.loads(line.split(' ', 1)[1]))
                except ValueError as error: errors.append('peer summary: '+str(error))
        passed = (joined and not errors and code == 0 and {row.get('widget') for row in rows} == {row['widget'] for row in normal}
                  and len(rows) == len(normal)
                  and all(row.get('passed') is True and row.get('requests') == config['packets']
                          and row.get('replies') == config['packets'] and row.get('rounds', 0) > 0 for row in rows))
        receipt = dict(schema_version=1, passed=passed, returncode=code, joined=joined, errors=errors,
                       remote_directory=remote, bindings=config['bindings'], peer_sha256=config.get('peer_sha256'),
                       files=[dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in sorted(local.iterdir()) if path.is_file() and path.name != 'peer-lifecycle.json'])
        (local / 'peer-lifecycle.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        if joined: active.pop((str(getattr(api.env, 'host_string', '')), remote), None)
        if errors:
            raise ValueError('NIC peer cleanup/collection failed: '+str(local)+'; '+'; '.join(errors))
        if required and not passed:
            raise ValueError('real host NIC peer failed, missed traffic, or did not join: ' + str(local))

    original_start = manager_class.start_sim_slot
    def start(self, slot):
        if normal and self.instance_assigned_simulations():
            binary = Path(config['peer_binary'])
            if hashlib.sha256(binary.read_bytes()).hexdigest() != config['peer_sha256']:
                raise ValueError('frozen NIC peer binary changed')
            remote = remote_dir(self, slot)
            server = self.parent_node.sim_slots[slot]
            api.run('mkdir ' + shlex.quote(remote))
            with tempfile.TemporaryDirectory(prefix='chia-nic-peer-') as temporary:
                temporary = Path(temporary)
                bindings = temporary / 'bindings.json'
                bindings.write_text(json.dumps(config, indent=2, sort_keys=True) + '\n')
                launcher = temporary / 'launch.sh'
                argv = ['./nic_peer', str(config['link_latency']), str(config['packets']), str(config['timeout_seconds']), remote + '/stop',
                        *[str(row['widget']) + ':' + row['port'] for row in normal]]
                launcher.write_text('#!/bin/bash\nset +e\n' + shlex.join(argv) + ' >peer.stdout 2>peer.stderr &\n'
                                    'child=$!\nprintf "%s\\n" "$child" >peer.pid\nwait "$child"\nresult=$?\nprintf "%s\\n" "$result" >peer.exit\nexit "$result"\n')
                api.put(str(binary), remote + '/nic_peer')
                api.put(str(launcher), remote + '/launch.sh')
                api.put(str(bindings), remote + '/bindings.json')
            observed = str(api.run('sha256sum ' + shlex.quote(remote + '/nic_peer'))).split()[0]
            if observed != config['peer_sha256']:
                raise ValueError('NIC host peer delivery hash mismatch')
            api.run('chmod 755 ' + shlex.quote(remote + '/nic_peer') + ' '
                    + shlex.quote(remote + '/launch.sh'), timeout=10)
            active[str(getattr(api.env, 'host_string', '')), remote] = (self, slot, server)
            try:
                launch_peer(api, remote)
                api.run('python3 -c ' + shlex.quote(READY) + ' ' + shlex.quote(remote), timeout=15)
            except BaseException:
                cleanup(self, slot, server, required=False)
                raise
        try:
            return original_start(self, slot)
        except BaseException:
            if normal:
                cleanup(self, slot, self.parent_node.sim_slots[slot], required=False)
            raise
    manager_class.start_sim_slot = start

    original_copy = server_class.copy_back_job_results_from_run
    def copy(self, slot):
        manager = self.get_host_instance().instance_deploy_manager
        peer_error = None
        try:
            cleanup(manager, slot, self, required=True)
        except BaseException as error:
            peer_error = error
        # Even a failed peer can accompany a useful UART/driver failure. Copy
        # the simulator's outputs before the farm is terminated, then preserve
        # the peer failure. Successful collection cannot turn it into a pass.
        try:
            result = original_copy(self, slot)
        except BaseException as error:
            if peer_error is not None:
                raise RuntimeError('NIC peer validation failed: ' + str(peer_error)
                                   + '; FireSim result collection also failed: ' + str(error)) from error
            raise
        if peer_error is not None:
            raise peer_error
        return result
    server_class.copy_back_job_results_from_run = copy

    original_kill = manager_class.kill_sim_slot
    def kill(self, slot):
        kill_error=None; peer_error=None
        try:
            result=original_kill(self, slot)
        except BaseException as error:
            kill_error=error
        if self.instance_assigned_simulations():
            server=self.parent_node.sim_slots[slot]
            try:
                cleanup(self, slot, self.parent_node.sim_slots[slot], required=False)
            except BaseException as error:
                peer_error=error
            if os.environ.get('CHIA_FEATURE_ABORT_DIAGNOSTICS')=='1':
                collect_abort(api,self,slot,server,config,
                              kill_error=None if kill_error is None else str(kill_error),
                              peer_error=None if peer_error is None else str(peer_error))
        if kill_error is not None: raise kill_error
        if peer_error is not None: raise peer_error
        return result
    manager_class.kill_sim_slot = kill

    def shutdown():
        # Fabric uses per-task host contexts; restore the exact host captured
        # at launch before abort cleanup. The peer also has its own wall bound.
        for (host, remote), (manager, slot, server) in list(active.items()):
            try:
                with api.settings(host_string=host):
                    cleanup(manager, slot, server, required=False)
            except BaseException as error:
                print('CIRCT_NIC_PEER_CLEANUP_FAILED: ' + str(error), flush=True)
    atexit.register(shutdown)
    return True
