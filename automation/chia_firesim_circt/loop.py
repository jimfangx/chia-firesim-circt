#!/usr/bin/env python3
"""Chia/Codex development loop for the FireSim Scala-FIRRTL-to-CIRCT port.

Run this through a Chia cluster created from cluster.yaml.  Codex performs one
incremental engineering step, while FireSim verification is always delegated to
the existing FireSim manager commands in sims/firesim/deploy.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import runpy
import selectors
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import ray
import yaml
from chia.base.ChiaFunction import ChiaFunction, get
from chia.models.codex import CodexLLM, CodexQueryResult, parse_session_id
from chia.trace.profiler import start_collector


REPO = Path("/scratch/jfx/fsim-circt")
DEPLOY = REPO / "sims/firesim/deploy"
AUTOMATION = REPO / "automation/chia_firesim_circt"
FIREMARSHAL = REPO / "software/firemarshal"
POWEROFF_WORKLOAD_SOURCE = AUTOMATION / "workloads/circt-boot-poweroff.json"
BAREMETAL_WORKLOAD_SOURCE = AUTOMATION / "workloads/circt-metasim-bare.json"
BAREMETAL_WORKLOAD_NAME = "circt-metasim-bare"
BAREMETAL_PASS_MARKER = "CIRCT_METASIM_BARE: PASS"
REGRESSION_ROOT = AUTOMATION / "workloads/circt-metasim-bare"
REGRESSION_GROUPS = ("integer", "memory", "control", "atomics", "csr", "traps", "vm",
                     "interrupts", "fp", "icache", "workloads", "uart")
RECIPE = "circt_u250_firesim_rocket_singlecore"
MANAGER_ENV = "cd /scratch/jfx/fsim-circt/sims/firesim && source ./sourceme-manager.sh --skip-ssh-setup"
METASIM_RUNTIME = DEPLOY / "config_runtime_metasim.yaml"
LIVE_CONTROL = AUTOMATION / "control.yaml"
FIRESIM_LOG_RE = re.compile(r"The full log of this run is:\s*(?P<path>\S+)")
GOAL_COMPLETE_RE = re.compile(r"^LOOP_COMPLETE:\s*yes\s*$", re.IGNORECASE | re.MULTILINE)


class StreamingCodexLLM(CodexLLM):
    """CodexLLM variant that persists and prints CLI JSON events as they arrive.

    Chia's stock backend deliberately collects ``codex exec --json`` output
    with ``subprocess.run`` and creates its transcript after the CLI exits.
    This loop runs for hours, so make the transcript durable at dispatch time
    and append every stdout/stderr line immediately.  The final result still
    uses the stock parser, preserving retries, error classification, and
    resumable-session handling in ``CodexLLM.prompt``.
    """

    def _run_codex(self, user_message: str, tools=None) -> CodexQueryResult:
        fd, output_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        transcript_path = Path(f"{self._log_prefix}.log") if self._log_prefix else None
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        try:
            if transcript_path is not None:
                transcript_path.parent.mkdir(parents=True, exist_ok=True)
                transcript = transcript_path.open("a", encoding="utf-8")
            else:
                transcript = None
            try:
                if transcript is not None:
                    transcript.write("=" * 80 + "\n")
                    transcript.write(
                        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                        f"Prompt #{self._call_counter + 1} (codex, live)\n"
                    )
                    transcript.write("=" * 80 + f"\n\n[User Message]\n{user_message}\n\n")
                    transcript.flush()

                command = self._build_cmd(
                    tools or [],
                    output_last_message_path=output_path,
                    resume_session_id=self._session_id if self._resume_session else None,
                )
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    cwd=self.work_dir or None,
                    env=os.environ.copy(),
                    errors="replace",
                )
                assert process.stdin is not None
                assert process.stdout is not None
                assert process.stderr is not None
                try:
                    process.stdin.write(self._format_prompt(user_message))
                    process.stdin.close()
                except BrokenPipeError:
                    pass

                streams = selectors.DefaultSelector()
                streams.register(process.stdout, selectors.EVENT_READ, "stdout")
                streams.register(process.stderr, selectors.EVENT_READ, "stderr")
                deadline = time.monotonic() + self.timeout_seconds
                timed_out = False
                try:
                    while streams.get_map():
                        if time.monotonic() >= deadline:
                            timed_out = True
                            process.kill()
                        for key, _ in streams.select(timeout=0.5):
                            line = key.fileobj.readline()
                            if not line:
                                streams.unregister(key.fileobj)
                                key.fileobj.close()
                                continue
                            if key.data == "stdout":
                                stdout_parts.append(line)
                                prefix = "[codex:live]"
                            else:
                                stderr_parts.append(line)
                                prefix = "[codex:live:stderr]"
                            # Keep the raw event intact: it contains every tool
                            # invocation/result even if Chia's compact parser
                            # does not recognize that event type yet.
                            print(f"{prefix} {line}", end="", flush=True)
                            if transcript is not None:
                                transcript.write(f"{prefix} {line}")
                                transcript.flush()
                        if timed_out and process.poll() is not None:
                            # The pipe-drain loop will unregister both streams.
                            continue
                finally:
                    streams.close()
                returncode = process.wait()
                stdout = "".join(stdout_parts)
                stderr = "".join(stderr_parts)
                if timed_out:
                    if transcript is not None:
                        transcript.write(f"\n[Timeout after {self.timeout_seconds}s]\n")
                        transcript.flush()
                    raise subprocess.TimeoutExpired(command, self.timeout_seconds, stdout, stderr)

                try:
                    with open(output_path, encoding="utf-8") as output_file:
                        final_text = output_file.read()
                except FileNotFoundError:
                    final_text = ""
                stream, meta, fallback = self._parse_jsonl_stream(stdout, stderr)
                parsed_session_id = parse_session_id(stdout)
                if self._resume_session and parsed_session_id:
                    self._session_id = parsed_session_id
                if self._session_id:
                    meta["session_id"] = self._session_id
                self._last_metadata = meta
                final_text = final_text or fallback
                if transcript is not None:
                    transcript.write(f"\n[Codex exit {returncode}]\n")
                    transcript.flush()
                if returncode != 0:
                    self.logger.warning("codex exited %d: %s", returncode, stderr[:500])
                return CodexQueryResult(
                    final_text,
                    returncode,
                    stderr,
                    stream,
                    session_id=self._session_id,
                )
            finally:
                if transcript is not None:
                    transcript.close()
        finally:
            try:
                os.unlink(output_path)
            except FileNotFoundError:
                pass


@dataclass
class CommandResult:
    name: str
    returncode: int
    stdout: str
    stderr: str
    stdout_log_path: str | None = None
    stderr_log_path: str | None = None
    firesim_log_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def summary(self) -> str:
        output = (self.stdout + "\n" + self.stderr).strip()
        paths = []
        if self.stdout_log_path:
            paths.append(f"Full stdout: {self.stdout_log_path}")
        if self.stderr_log_path:
            paths.append(f"Full stderr: {self.stderr_log_path}")
        if self.firesim_log_path:
            paths.append(f"FireSim manager log: {self.firesim_log_path}")
        return "\n".join([
            f"{self.name}: {'PASS' if self.ok else 'FAIL'}",
            *paths,
            output[-12000:],
        ]).rstrip()


def _manager_command(action: str, runtime_config: Path = DEPLOY / "config_runtime.yaml") -> list[str]:
    common = [
        "./firesim",
        "-c", str(runtime_config),
        "-b", "config_build.yaml",
        "-r", "config_build_recipes.yaml",
        "-a", "config_hwdb.yaml",
    ]
    return common + [action]


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.") or "verification"


def _append_tail(current: str, addition: str, limit: int = 12_000) -> str:
    """Retain enough output for the next Codex turn without bloating Ray results."""
    return (current + addition)[-limit:]


def _run_and_log(
    name: str,
    command: list[str],
    log_dir: Path,
) -> CommandResult:
    """Stream one gate to Ray's job log and retain complete per-stream files."""
    log_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_filename(name)
    stdout_path = log_dir / f"{stem}.stdout.log"
    stderr_path = log_dir / f"{stem}.stderr.log"
    print(f"\n=== FireSim stage {name}: START ===", flush=True)
    print(f"[stage:{name}] command: {shlex.join(command)}", flush=True)
    print(f"[stage:{name}] stdout: {stdout_path}", flush=True)
    print(f"[stage:{name}] stderr: {stderr_path}", flush=True)

    stdout_tail = ""
    stderr_tail = ""
    firesim_log_path: str | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=DEPLOY,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            errors="replace",
        )
    except OSError as error:
        message = f"Unable to start {shlex.join(command)}: {error}\n"
        stderr_path.write_text(message)
        stdout_path.touch()
        print(f"[stage:{name}:stderr] {message}", end="", flush=True)
        print(f"=== FireSim stage {name}: FAIL (could not start) ===", flush=True)
        return CommandResult(name, 127, "", message, str(stdout_path), str(stderr_path))

    assert process.stdout is not None
    assert process.stderr is not None
    with stdout_path.open("w") as stdout_file, stderr_path.open("w") as stderr_file:
        streams = selectors.DefaultSelector()
        streams.register(process.stdout, selectors.EVENT_READ, ("stdout", stdout_file))
        streams.register(process.stderr, selectors.EVENT_READ, ("stderr", stderr_file))
        while streams.get_map():
            for key, _ in streams.select():
                stream_name, log_file = key.data
                line = key.fileobj.readline()
                if not line:
                    streams.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                log_file.write(line)
                log_file.flush()
                print(f"[stage:{name}:{stream_name}] {line}", end="", flush=True)
                if stream_name == "stdout":
                    stdout_tail = _append_tail(stdout_tail, line)
                else:
                    stderr_tail = _append_tail(stderr_tail, line)
                manager_log_match = FIRESIM_LOG_RE.search(stdout_tail + "\n" + stderr_tail)
                if manager_log_match:
                    firesim_log_path = manager_log_match.group("path")
    returncode = process.wait()
    outcome = "PASS" if returncode == 0 else "FAIL"
    print(f"=== FireSim stage {name}: {outcome} (exit {returncode}) ===", flush=True)
    return CommandResult(
        name, returncode, stdout_tail, stderr_tail,
        str(stdout_path), str(stderr_path), firesim_log_path,
    )


def _run_manager(
    action: str,
    log_dir: Path,
    runtime_config: Path = DEPLOY / "config_runtime.yaml",
    *, name: str | None = None, wall_timeout: int | None = None,
) -> CommandResult:
    # This is intentionally the FireSim manager, not an SSH invocation.  The
    # manager owns all build-farm and run-farm communication.
    command = _manager_command(action, runtime_config)
    if wall_timeout is not None:
        # GNU timeout bounds the manager and its process group, including a stalled WFI run.
        command = ["timeout", "--signal=TERM", "--kill-after=30s", f"{wall_timeout}s",
                   "bash", "-lc", f"{MANAGER_ENV} && cd deploy && exec {shlex.join(command)}"]
    return _run_and_log(name or action, command, log_dir)


def _workspace_snapshot() -> str:
    """Return the live worktree summary supplied to each resumed Codex turn."""
    try:
        status = subprocess.run(
            ["git", "status", "--short"], cwd=REPO, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
    except OSError as error:
        return f"Unable to read live worktree status: {error}"
    if status.returncode:
        return f"Unable to read live worktree status (exit {status.returncode}):\n{status.stdout.strip()}"
    return status.stdout.strip() or "(clean tracked worktree; inspect relevant ignored generated/config files too)"


def _live_model(fallback: str | None) -> str | None:
    """Read the per-iteration Codex model override from the shared worktree."""
    try:
        control = yaml.safe_load(LIVE_CONTROL.read_text())
    except FileNotFoundError:
        return fallback
    except (OSError, yaml.YAMLError) as error:
        print(f"Ignoring invalid live control file {LIVE_CONTROL}: {error}", file=sys.stderr)
        return fallback
    if control is None:
        return fallback
    if not isinstance(control, dict):
        print(f"Ignoring invalid live control file {LIVE_CONTROL}: expected a mapping", file=sys.stderr)
        return fallback
    model = control.get("model")
    if model is None:
        return fallback
    if not isinstance(model, str) or not model.strip():
        print(f"Ignoring invalid model in {LIVE_CONTROL}: expected a non-empty string or null", file=sys.stderr)
        return fallback
    return model.strip()


def _run_workload_and_cleanup(
    log_dir: Path,
    runtime_config: Path,
) -> list[CommandResult]:
    """Run the guest's poweroff workload and then release its run-farm slot."""
    workload = _run_manager("runworkload", log_dir, runtime_config)
    cleanup = _run_manager("terminaterunfarm", log_dir, runtime_config)
    return [workload, cleanup]


def _prepare_workload(name: str, workload_source: Path, log_dir: Path) -> CommandResult:
    """Build and install a FireMarshal workload before its FireSim gate."""
    workload = shlex.quote(str(workload_source))
    command = (
        f"{MANAGER_ENV} && cd {shlex.quote(str(FIREMARSHAL))} && "
        f"./marshal build {workload} && ./marshal install --target firesim {workload}"
    )
    return _run_and_log(name, ["bash", "-lc", command], log_dir)


def _run_metasim_regressions(log_dir: Path, run_id: str,
                             force_full: bool = False) -> list[CommandResult]:
    """Smoke, combined suites, and first-version standalones; cleanup every launch."""
    results = []
    preparation = [sys.executable, str(AUTOMATION / "regression_gate.py"), "prepare",
                   "--root", str(REGRESSION_ROOT), "--deploy", str(DEPLOY),
                   "--log-dir", str(log_dir), "--versions", str(AUTOMATION / "logs/regression-versions")]
    result = _run_and_log("prepare_rocket_regressions",
                          ["bash", "-lc", f"{MANAGER_ENV} && {shlex.join(preparation)}"], log_dir)
    results.append(result)
    if not result.ok:
        return results
    inputs = json.loads((log_dir / "regression-inputs.json").read_text())
    names = [BAREMETAL_WORKLOAD_NAME, "circt-fame-suite", "circt-fame-rocket-suite"]
    if inputs["initial_standalones_required"]:
        names.extend("circt-fame-" + group for group in REGRESSION_GROUPS)
    generated = REPO / "sims/firesim/sim/generated-src/xilinx_alveo_u250" / (
        "xilinx_alveo_u250-firesim-FireSim-FireSimRocketConfig-BaseXilinxAlveoU250Config")
    def candidate_identity():
        paths = sorted([*generated.glob("FireSim-generated.*"), generated / "AbstractClockGate.v"])
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    candidate = candidate_identity()
    if not any(path.endswith(".sv") for path in candidate):
        return results + [CommandResult("regression_candidate_identity", 1, "", "Candidate RTL missing")]
    # Refresh the smoke execution every turn; reuse long-suite results only
    # for identical simulator/driver/workload inputs, at most nine times.
    driver_roots = [REPO / "sims/firesim/sim/midas/src/main/cc",
                    REPO / "generators/firechip/chip/src/main/cc",
                    REPO / "generators/firechip/bridgestubs/src/main/cc",
                    REPO / "generators/testchipip/src/main/resources/testchipip/csrc",
                    REPO / "generators/firechip/chip/src/main/makefrag/firesim",
                    REPO / "sims/firesim/sim/make", DEPLOY / "runtools"]
    driver_inputs = {p for root in driver_roots for p in root.rglob("*")
                     if p.is_file() and (p.suffix in {".cc", ".cpp", ".c", ".h", ".hpp", ".mk", ".py"}
                                         or p.name == "Makefile")}
    driver_inputs.update([DEPLOY / "config_runtime.yaml", DEPLOY / "config_hwdb.yaml",
                          DEPLOY / "firesim", REPO / "sims/firesim/sim/Makefile",
                          REPO / "sims/firesim/env.sh", REPO / "sims/firesim/sourceme-manager.sh",
                          Path(__file__), AUTOMATION / "regression_gate.py"])
    tools = [shutil.which(tool) for tool in ["verilator", "verilator_bin", "c++", "g++", "make"]]
    for dependency in [REPO / ".conda-env/riscv-tools/lib/libfesvr.a",
                       *[Path(tool).resolve() for tool in tools if tool]]:
        if dependency.is_file():
            driver_inputs.add(dependency)
    validation_identity = {"candidate": candidate, "input_version": inputs["version"],
                           "environment": {k: os.environ.get(k, "") for k in
                                           ["CXX", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "RISCV",
                                            "LD_LIBRARY_PATH", "VERILATOR_ROOT"]},
                           "driver_inputs": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                             for p in sorted(driver_inputs)}}
    cache_path = AUTOMATION / "logs/metasim-suite-cache.json"
    try:
        cache = json.loads(cache_path.read_text())
    except (FileNotFoundError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    reuse_suites = (not force_full and not inputs["initial_standalones_required"]
                   and cache.get("identity") == validation_identity
                   and (Path(cache.get("verification", "")) / "regression-results.json").is_file()
                   and cache.get("reuses", 0) < 9)
    if reuse_suites:
        names = [BAREMETAL_WORKLOAD_NAME]
        print(f"Unchanged simulator/driver/workloads: fresh smoke; long-suite evidence "
              f"reused from {cache['verification']} ({cache.get('reuses', 0) + 1}/9)", flush=True)
    report = {"candidate_artifacts": candidate, "input_version": inputs["version"], "required_workloads": names,
              "rocket_differential": inputs["rocket_differential"], "workloads": []}
    report["suite_evidence"] = {"reused": reuse_suites,
                                "verification": cache.get("verification") if reuse_suites else str(log_dir),
                                "reason": "identical simulator, driver and workload inputs" if reuse_suites else "fresh full suites"}
    print(f"Required Rocket regression dispatch: {names}", flush=True)
    for index, binary in enumerate(names):
        case_dir = log_dir / binary
        case_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"{_safe_filename(run_id)}-{binary}"
        runtime_config = case_dir / "runtime.yaml"
        runtime = yaml.safe_load((DEPLOY / "config_runtime.yaml").read_text())
        runtime["metasimulation"]["metasimulation_enabled"] = True
        runtime["metasimulation"]["metasimulation_host_simulator"] = "verilator"
        plusargs = runtime["metasimulation"].get("metasimulation_only_plusargs", "")
        plusargs = re.sub(r"(?:^|\s)\+max-cycles=\S+", "", plusargs)
        runtime["metasimulation"]["metasimulation_only_plusargs"] = plusargs + " +max-cycles=1000000000"
        runtime["workload"].update(workload_name=binary + ".json", suffix_tag=suffix,
                                   terminate_on_completion=True)
        runtime_config.write_text(yaml.safe_dump(runtime, sort_keys=False))
        print(f"{binary}: Verilator; max-cycles=1000000000; wall-time=3600s; runtime={runtime_config}", flush=True)
        case_results = []
        launched = False
        try:
            if candidate_identity() != candidate:
                raise ValueError("Candidate RTL/collateral changed during regression dispatch")
            actions = ("builddriver", "launchrunfarm", "infrasetup", "runworkload") if index == 0 else (
                "launchrunfarm", "infrasetup", "runworkload")
            for action in actions:
                # Cleanup even a failed launch, which can leave a partially launched farm.
                launched = launched or action == "launchrunfarm"
                result = _run_manager(action, case_dir, runtime_config,
                                      name=f"{binary}_{action}", wall_timeout=3600)
                case_results.append(result)
                if not result.ok:
                    break
            if case_results and all(r.ok for r in case_results):
                if candidate_identity() != candidate:
                    raise ValueError("Candidate RTL/collateral changed during metasim")
                if binary == BAREMETAL_WORKLOAD_NAME:
                    command = [sys.executable, str(AUTOMATION / "verify_baremetal_uart.py"),
                               "--results-root", str(DEPLOY / "results-workload"),
                               "--workload-name", binary, "--suffix-tag", suffix,
                               "--pass-marker", BAREMETAL_PASS_MARKER]
                else:
                    command = [sys.executable, str(AUTOMATION / "regression_gate.py"), "validate",
                               "--root", str(REGRESSION_ROOT), "--log-dir", str(case_dir),
                               "--results-root", str(DEPLOY / "results-workload"),
                               "--binary", binary, "--suffix-tag", suffix]
                    if binary not in {"circt-fame-rocket-suite", "circt-fame-uart"}:
                        command.extend(["--reference", inputs["portable_reference"]])
                case_results.append(_run_and_log(f"{binary}_validate", command, case_dir))
        except Exception as error:
            case_results.append(CommandResult(f"{binary}_dispatch", 1, "", str(error)))
        finally:
            if launched:
                case_results.append(_run_manager("terminaterunfarm", case_dir, runtime_config,
                                                name=f"{binary}_terminaterunfarm", wall_timeout=300))
        results.extend(case_results)
        case_report = {"binary": binary, "runtime_yaml": str(runtime_config),
                       "suffix_tag": suffix, "gates": [asdict(r) for r in case_results]}
        validation_path = case_dir / "validation-result.json"
        if validation_path.is_file():
            case_report["validation"] = json.loads(validation_path.read_text())
        report["workloads"].append(case_report)
        (log_dir / "regression-results.json").write_text(json.dumps(report, indent=2) + "\n")
        if any(not r.ok for r in case_results):
            cache_path.unlink(missing_ok=True)
            return results
    if inputs["initial_standalones_required"]:
        (Path(inputs["archive"]) / "standalones-passed.json").write_text(
            json.dumps({"verification": str(log_dir), "workloads": names}, indent=2) + "\n")
    if reuse_suites:
        cache["reuses"] = cache.get("reuses", 0) + 1
    else:
        cache = {"identity": validation_identity, "verification": str(log_dir), "reuses": 0}
    cache_temp = cache_path.with_suffix(".json.tmp")
    cache_temp.write_text(json.dumps(cache, indent=2) + "\n")
    cache_temp.replace(cache_path)
    results.append(CommandResult("required_rocket_regressions", 0,
                                  ("Fresh smoke passed; unchanged-input long-suite evidence reused; " if reuse_suites
                                   else "All required Rocket metasim workloads validated; ")
                                  + inputs["rocket_differential"], ""))
    return results


@ChiaFunction(resources={"firesim_manager": 0.01})
def verify_with_firesim(mode: str, run_id: str, run_hardware_gate: bool = True) -> list[dict[str, object]]:
    # Runtime YAMLs are unique, but manager slots and candidate builds are shared.
    # An advisory lock serializes cooperating Chia verification workers.
    with (AUTOMATION / ".verification.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Chia serializes this function from the long-lived driver. Read the
        # verification implementation afresh on each dispatch so later edits
        # take effect without replacing the running driver or Ray worker.
        # run_path compiles the .py source and does not use a cached .pyc.
        source = AUTOMATION / "loop.py"
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        implementation = runpy.run_path(str(source), run_name="firesim_verification")
        if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError("Verification source changed while loading; retry dispatch")
        results = implementation["_verify_with_firesim"](mode, run_id, run_hardware_gate)
        if mode != "none":
            results.insert(0, asdict(CommandResult(
                "verification_source", 0,
                f"Loaded current verification source: {source}\n"
                f"sha256={source_hash}", "",
            )))
        return results


def _verify_with_firesim(
    mode: str,
    run_id: str,
    run_hardware_gate: bool = True,
) -> list[dict[str, object]]:
    """Run the configured FireSim verification gate on the local manager worker."""
    if mode == "none":
        return []
    log_dir = AUTOMATION / "logs" / "verification" / _safe_filename(run_id)
    if mode == "preflight":
        checks = [
            ["bash", "-lc", f"{MANAGER_ENV} && command -v codex && codex --version"],
            ["bash", "-lc", f"{MANAGER_ENV} && cd deploy && firesim --help"],
            [sys.executable, "-c", "import yaml; yaml.safe_load(open('/scratch/jfx/fsim-circt/sims/firesim/deploy/config_runtime.yaml')); yaml.safe_load(open('/scratch/jfx/fsim-circt/sims/firesim/deploy/config_build.yaml')); yaml.safe_load(open('/scratch/jfx/fsim-circt/sims/firesim/deploy/config_build_recipes.yaml')); yaml.safe_load(open('/scratch/jfx/fsim-circt/sims/firesim/deploy/config_hwdb.yaml'))"],
        ]
        results = []
        for index, command in enumerate(checks, start=1):
            # _run_and_log uses DEPLOY as cwd; all preflight commands use
            # absolute paths or explicitly `cd`, so that is intentional.
            results.append(asdict(_run_and_log(f"preflight-{index}", command, log_dir)))
        return results

    # Gate 1: the exact existing FireSim `make replace-rtl` compiler phase.
    compile_result = _run_manager("replacertl", log_dir)
    results = [asdict(compile_result)]
    if not compile_result.ok:
        return results
    compiler_provenance = _run_and_log(
        "verify_circt_replacertl",
        [
            sys.executable, str(AUTOMATION / "verify_circt_replacertl.py"),
            str(compile_result.stdout_log_path),
        ],
        log_dir,
    )
    results.append(asdict(compiler_provenance))
    if not compiler_provenance.ok or mode == "compile":
        return results

    # Gate 2 uses a tiny tohost-terminating RISC-V program rather than Linux.
    # It tests transformed target execution, target memory, UART, and a clean
    # host/target completion path in minutes rather than a Buildroot boot.
    baremetal_workload = _prepare_workload(
        "prepare_metasim_bare_workload", BAREMETAL_WORKLOAD_SOURCE, log_dir,
    )
    results.append(asdict(baremetal_workload))
    if not baremetal_workload.ok:
        return results
    metasim_workload_results = _run_metasim_regressions(
        log_dir, run_id, force_full=mode in {"full", "boot"} and run_hardware_gate)
    results.extend(asdict(result) for result in metasim_workload_results)
    if any(not result.ok for result in metasim_workload_results):
        return results
    if mode == "metasim" or not run_hardware_gate:
        return results

    # Gate 3: FireSim's normal U250 flow.  The "full" profile reaches here
    # only after the compile and Verilator gates above pass. buildbitstream intentionally runs
    # replace-rtl again as FireSim's standard end-to-end build prerequisite.
    # Keep full-mode hardware validation distinct: its Linux workload exercises
    # the actual U250 image and powers itself off after a successful boot.
    poweroff_workload = _prepare_workload(
        "prepare_poweroff_workload", POWEROFF_WORKLOAD_SOURCE, log_dir,
    )
    results.append(asdict(poweroff_workload))
    if not poweroff_workload.ok:
        return results

    build = _run_manager("buildbitstream", log_dir)
    results.append(asdict(build))
    if not build.ok:
        return results

    sync_result = _run_and_log(
        "sync_hwdb_entry",
        [sys.executable, str(AUTOMATION / "sync_hwdb_entry.py"), "--deploy-dir", str(DEPLOY), "--name", RECIPE],
        log_dir,
    )
    results.append(asdict(sync_result))
    if not sync_result.ok:
        return results

    for action in ("launchrunfarm", "infrasetup"):
        result = _run_manager(action, log_dir)
        results.append(asdict(result))
        if not result.ok:
            return results
    hardware_workload_results = _run_workload_and_cleanup(log_dir, DEPLOY / "config_runtime.yaml")
    results.extend(asdict(result) for result in hardware_workload_results)
    return results


def _agent_prompt(previous_verification: str, iteration: int, total: int, model: str | None) -> str:
    return f"""You are the implementation agent for the FireSim Golden Gate Scala-FIRRTL Compiler to CIRCT migration.

You may read, create, modify, and delete files under {REPO}; keep every source change inside that repository. You are running through the authenticated local Codex CLI: do not use OpenAI APIs or API keys. You may create a well-justified git checkpoint and push it to the configured GitHub remote when the worktree has a coherent, tested milestone; never push unrelated pre-existing changes.

This is a live shared worktree. At the beginning of every turn, reread `FIRESIM_CIRCT_PORT_PLAN.md`, paying special attention to any ADDITIONS or CHANGES to the plan, including the new notes at the top. Run `git status --short`, and inspect current compiler sources. Do not assume resumed context is current, reset/revert user changes, or overwrite the immutable SFC fixtures named in the plan.

NON-NEGOTIABLE IMPLEMENTATION SCOPE: work on the actual Golden Gate compiler in `sims/firesim/sim`. The current Scala FIRRTL compiler under `sims/firesim/sim/midas/src/main/scala/midas/` is the executable semantic oracle; inspect and port its behavior, especially `passes/MidasTransforms.scala` and `passes/fame/FAMETransform.scala`. Implement the CIRCT compiler in the plan's `sims/firesim/sim/goldengate-circt/` tree and make only the necessary FireSim build integration under `sims/firesim/sim/` to invoke it. Every implementation turn must make a concrete, evidence-backed change to those core compiler sources or their direct FireSim compiler build wiring.

Strongly prefer core compiler work over `automation/`, FireMarshal, workload JSON, top-level Python tools, runtime YAML, Ray/Chia configuration, logging, provenance guards, and other harness infrastructure. You may make a minimal infrastructure change only when it directly unblocks the current core compiler change or its comparison test. Such a change does not itself count as porting progress: return immediately to the compiler source/build path. Do not substitute a Python checker, log parser, build wrapper, or a claim that SFC was selected for actual CIRCT compiler implementation. If the current FireSim flow invokes SFC, change the compiler source/build integration under `sims/firesim/sim` so it invokes the CIRCT implementation; do not merely detect or report the fallback.

Follow the milestone order in `FIRESIM_CIRCT_PORT_PLAN.md`: preserve the Scala Golden Gate oracle; establish CIRCT ingestion of the SFC handoff; build annotation, target-identity, hierarchy, clock, model, and channel analyses; then port FAME semantics incrementally. Port semantics through CIRCT/MLIR operations, not Scala syntax. For every core change, select the relevant boundary artifact from the recorded SFC golden directories, compare the CIRCT candidate's corresponding FIRRTL/annotation/RTL/collateral output to it, and use the observed match or mismatch to choose the next change. Prefer semantic and structured comparisons (annotation classes, targets, channels, modules, ports, clocks, and emitted collateral) over brittle byte-for-byte RTL equality. State the exact golden artifact compared and the result in the final report. Do not claim completion of the overall port.

For debugging, read the recorded SFC golden-reference section in that steps document. Its immutable fixtures are `/scratch/jfx/fsim-circt/sims/firesim-staging/generated-src/firechip.chip.FireSim.FireSimRocketConfig.sfc-golden-2026-10-01` (the compiler oracle) and `/scratch/jfx/fsim-circt/sims/firesim/deploy/results-build/2026-10-01--04-55-23-circt_u250_firesim_rocket_singlecore/cl_xilinx_alveo_u250-firesim-FireSim-FireSimRocketConfig-BaseXilinxAlveoU250Config.sfc-golden-2026-10-01` (the U250 build-tree reference). Never overwrite or edit either fixture; use them to diagnose a candidate/output mismatch.

Before Chipyard or FireSim commands, run `cd /scratch/jfx/fsim-circt/sims/firesim && source ./sourceme-manager.sh --skip-ssh-setup`; it includes the project environment and the FireSim CLI. The harness owns verification and enforces this mandatory order: (1) FireSim `replacertl` compile, which must select and exercise the CIRCT FireSim compiler path rather than silently falling back to SFC; (2) FireSim **Verilator** metasimulation of that transformed RTL (`metasimulation_host_simulator: verilator`); and only if that Verilator simulation passes, (3) the FireSim U250 `buildbitstream` manager step. Never SSH directly to a build or FPGA machine. Do not start any of those verification steps yourself. When a gate fails, read the supplied complete stdout/stderr and FireSim-manager log paths before making the smallest fix. Preserve user changes outside files you intentionally touch.

At the end, report: changed files, tests/artifacts inspected, remaining risk, and the next smallest porting step. End with exactly one of these lines: `LOOP_COMPLETE: yes` only if you judge that the CIRCT port's stated goal is fully achieved, or `LOOP_COMPLETE: no` otherwise. A passing validation gate alone is not sufficient to declare completion.

Iteration {iteration} of {total}. Verification feedback from the prior iteration:
{previous_verification or '(No prior verification feedback.)'}

Codex model selected for this iteration: {model or 'Codex CLI default'}

Live worktree snapshot at the start of this iteration:
{_workspace_snapshot()}
"""


def _format_verification(raw: list[dict[str, object]]) -> str:
    if not raw:
        return "Verification disabled."
    reports = []
    for item in raw:
        result = CommandResult(**item)
        reports.append(result.summary())
    return "\n\n".join(reports)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument(
        "--verify",
        choices=("none", "preflight", "compile", "metasim", "full", "boot"),
        default="preflight",
        help="Verification profile; full runs compile and bare-metal Verilator metasim every iteration, with periodic U250 Linux boot (boot is a compatibility alias).",
    )
    parser.add_argument("--model", default=None, help="Optional Codex CLI model override")
    parser.add_argument(
        "--linux-boot-after-bare-successes",
        type=int,
        default=15,
        help="For full/boot verification, run the U250 Linux gate after this many consecutive bare-metal metasim passes (default: 15).",
    )
    parser.add_argument("--ray-address", default=os.environ.get("RAY_ADDRESS", "auto"))
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    if args.linux_boot_after_bare_successes < 1:
        parser.error("--linux-boot-after-bare-successes must be positive")

    ray.init(address=args.ray_address)
    start_collector(log_dir=str(AUTOMATION / "profiles"))
    agent = StreamingCodexLLM(
        model=args.model,
        reasoning_effort="high",
        work_dir=str(REPO),
        # The user explicitly authorizes autonomous repository edits and git
        # checkpoints.  Chia's Codex wrapper implements that via this CLI flag.
        dangerously_bypass_approvals_and_sandbox=True,
        resume_session=True,
        timeout_seconds=3600,
        retries=1,
        log_dir=str(AUTOMATION / "logs"),
    )
    feedback = ""
    loop_run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-pid{os.getpid()}"
    goal_complete = False
    baremetal_success_streak = 0
    try:
        for iteration in range(1, args.iterations + 1):
            model = _live_model(args.model)
            if model != agent.model:
                print(f"Switching Codex model for iteration {iteration}: {agent.model or 'Codex CLI default'} -> {model or 'Codex CLI default'}")
                agent.model = model
            prompt = _agent_prompt(feedback, iteration, args.iterations, agent.model)
            transcript_path = f"{agent._log_prefix}.log" if agent._log_prefix else "(Codex transcript logging disabled)"
            print(f"\n=== Codex iteration {iteration}: PROMPT ===\n{prompt}\n=== Codex iteration {iteration}: DISPATCHED ===", flush=True)
            print(f"[codex:{iteration}] transcript: {transcript_path}", flush=True)
            # ChiaFunction remote methods are not Python-bound at dispatch
            # time: pass the CodexLLM instance explicitly as ``self``.
            response = get(agent.prompt.chia_remote(
                agent,
                prompt,
            ))
            print(f"\n=== Codex iteration {iteration}: RESPONSE ===\n{response.result}\n", flush=True)
            if response.stream_result:
                print(f"=== Codex iteration {iteration}: EVENT STREAM ===\n{response.stream_result}", flush=True)
            if response.stderr:
                print(f"=== Codex iteration {iteration}: STDERR ===\n{response.stderr}", flush=True)
            hardware_gate_attempted = (
                args.verify in {"full", "boot"}
                and baremetal_success_streak + 1 >= args.linux_boot_after_bare_successes
            )
            if args.verify in {"full", "boot"}:
                if hardware_gate_attempted:
                    print(
                        f"=== FireSim verification {iteration}: DISPATCHED ({args.verify}; "
                        f"bare-metal streak will reach {args.linux_boot_after_bare_successes}, U250 Linux gate enabled) ===",
                        flush=True,
                    )
                else:
                    print(
                        f"=== FireSim verification {iteration}: DISPATCHED ({args.verify}; "
                        f"bare-metal streak {baremetal_success_streak}/{args.linux_boot_after_bare_successes}, U250 Linux gate deferred) ===",
                        flush=True,
                    )
            else:
                print(f"=== FireSim verification {iteration}: DISPATCHED ({args.verify}) ===", flush=True)
            verification = get(verify_with_firesim.chia_remote(
                args.verify,
                f"{loop_run_id}-iter{iteration}-{args.verify}",
                hardware_gate_attempted,
                _chia_tag=f"verify-{iteration}-{args.verify}",
            ))
            feedback = _format_verification(verification)
            print(f"\n=== FireSim verification {iteration}: SUMMARY ===\n{feedback}\n", flush=True)
            verification_passed = all(item["returncode"] == 0 for item in verification)
            baremetal_passed = verification_passed and any(
                item["name"] == "required_rocket_regressions" for item in verification
            )
            if args.verify in {"full", "boot"}:
                if baremetal_passed:
                    baremetal_success_streak += 1
                else:
                    baremetal_success_streak = 0
                if hardware_gate_attempted:
                    # A scheduled hardware gate consumes the streak whether it
                    # passes or fails: after a source change, earn fresh cheap
                    # functional evidence before spending another U250 boot.
                    baremetal_success_streak = 0
                    print(
                        "U250 Linux gate completed; bare-metal success streak reset to 0.",
                        flush=True,
                    )
                elif baremetal_passed:
                    print(
                        f"Bare-metal metasim pass {baremetal_success_streak}/"
                        f"{args.linux_boot_after_bare_successes}; U250 Linux gate deferred.",
                        flush=True,
                    )
            if not verification_passed:
                print("Verification failed; its compiler/error tail and full log paths will be supplied to Codex in the next iteration.")
            elif (
                args.verify in {"full", "boot"}
                and hardware_gate_attempted
                and GOAL_COMPLETE_RE.search(response.result)
            ):
                goal_complete = True
                print("The complete FireSim gate passed and Codex declared the port goal achieved; ending the loop.")
                break
            else:
                print("Verification passed, but Codex has not declared the port goal complete; continuing the engineering loop.")
    finally:
        ray.shutdown()
    if not goal_complete:
        raise RuntimeError(
            "Iteration limit reached without a validated `LOOP_COMPLETE: yes` declaration; job is not complete."
        )


if __name__ == "__main__":
    main()
