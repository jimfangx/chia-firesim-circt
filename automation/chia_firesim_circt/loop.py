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
import signal
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
from chia.models.codex import CodexLLM, CodexQueryResult, InvalidRequestError, parse_session_id
from chia.trace.profiler import start_collector


REPO = Path(__file__).resolve().parents[2]
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
MANAGER_ENV = 'source "${CONDA_EXE%/bin/conda}/etc/profile.d/conda.sh" && cd ' + shlex.quote(str(REPO / "sims/firesim")) + ' && source ./sourceme-manager.sh'
METASIM_RUNTIME = DEPLOY / "config_runtime_metasim.yaml"
LIVE_CONTROL = AUTOMATION / "control.yaml"
FIRESIM_LOG_RE = re.compile(r"The full log of this run is:\s*(?P<path>\S+)")
GOAL_COMPLETE_RE = re.compile(r"^LOOP_COMPLETE:\s*yes\s*$", re.IGNORECASE | re.MULTILINE)
FEATURE_READY_RE = re.compile(r"^FEATURE_IMPLEMENTATION_COMPLETE:\s*yes\s*$", re.IGNORECASE | re.MULTILINE)
FEEDBACK_MAX_CHARS = 96_000
PROMPT_MAX_CHARS = 200_000


def _bounded_text(value: object, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    marker = f"\n[omitted {len(text) - limit} characters; read the evidence file]\n"
    keep = limit - len(marker)
    return text[:keep // 2] + marker + text[-(keep - keep // 2):]


def _verification_feedback(reports: list[dict], graph_path: Path) -> str:
    """Give the agent bounded status/diagnostics, never nested RTL inventories."""
    def brief(item):
        row = {key: _bounded_text(item[key], 1600) for key in (
            "node", "target", "mode", "status", "reason", "error", "evidence", "identity"
        ) if key in item}
        if "ok" in item:
            row["ok"] = item["ok"] is True
        result = item.get("result", {})
        if isinstance(result, dict):
            row["adapter"] = {key: _bounded_text(result[key], 1600) for key in (
                "verification_stage", "stage", "error", "proof_status"
            ) if key in result}
            for key in ("passed", "syntax", "semantic", "static_semantics"):
                if key in result:
                    row["adapter"][key] = result[key] is True
            if result.get("unresolved_obligations"):
                row["adapter"]["unresolved_obligations"] = _bounded_text(
                    result["unresolved_obligations"], 3000)
        command = item.get("command", {})
        if isinstance(command, dict):
            row["command"] = {key: _bounded_text(command[key], 1600) for key in (
                "returncode", "stdout", "stderr", "report"
            ) if key in command}
        return row

    nodes = []
    for report in reports[:8]:
        node = brief(report)
        node["details"] = [brief(detail) for detail in report.get("details", [])[:16]
                           if isinstance(detail, dict)]
        nodes.append(node)
    return _bounded_text(
        f"Full verification report: {graph_path}\n"
        "Read the referenced reports/logs for full diagnostics; inventories and tool output are kept on disk.\n"
        + json.dumps(nodes, indent=2), FEEDBACK_MAX_CHARS)


class SessionWriterConflict(InvalidRequestError):
    """A live Codex session/call cannot be resumed by another writer."""

    error_type = "session_writer_conflict"


class StreamingCodexLLM(CodexLLM):
    """CodexLLM variant that persists and prints CLI JSON events as they arrive.

    Chia's stock backend deliberately collects ``codex exec --json`` output
    with ``subprocess.run`` and creates its transcript after the CLI exits.
    This loop runs for hours, so make the transcript durable at dispatch time
    and append every stdout/stderr line immediately.  The final result still
    uses the stock parser. This shared-checkout worker resumes by session ID
    only: it must never restore snapshots over the live shared Codex database.
    """

    def _restore_session_state(self) -> None:
        # Chia's portable backend restores SQLite/WAL/SHM and rollout snapshots.
        # They are already on this worker's shared disk, potentially in use by
        # other Codex clients. Never overwrite them with an earlier snapshot.
        self._session_state = None
        self._session_state_paths = ()

    def _capture_session_state(self, cli: CodexQueryResult) -> None:
        self._session_state = cli.session_state = None
        self._session_state_paths = cli.session_state_paths = ()
        cli.session_id = self._session_id

    def _sync_session(self, cli: CodexQueryResult) -> CodexQueryResult:
        if self._resume_session and cli.session_id:
            self._session_id = cli.session_id
        # Discard any old portable state too, including on deserialized agents.
        self._session_state = cli.session_state = None
        self._session_state_paths = cli.session_state_paths = ()
        self._call_counter += 1
        return cli

    def _classify_error(self, cli: CodexQueryResult) -> None:
        # The installed backend matches bare numeric HTTP codes anywhere in
        # stderr, including "402" in timestamps. Detect this failure first and
        # make it non-retryable via the backend's InvalidRequestError handling.
        if cli.returncode != 0 and any(token in cli.stderr.lower() for token in (
            "thread-store conflict", "already has an active writer",
        )):
            raise SessionWriterConflict(
                node_id=self._get_node_id(), exit_code=cli.returncode,
                raw_message=cli.stderr[:2000],
            )
        super()._classify_error(cli)

    def _run_codex(self, user_message: str, tools=None) -> CodexQueryResult:
        # The driver owns .loop.lock; a worker-side lock also protects against a
        # still-running task after its driver died or an accidental duplicate
        # dispatch. Internal Codex subagents remain unrestricted by this lock.
        with (AUTOMATION / ".codex-exec.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SessionWriterConflict(
                    node_id=self._get_node_id(), exit_code=1,
                    raw_message="Another CHIA implementation call owns this checkout; wait for it to exit.",
                )
            return self._run_codex_streamed(user_message, tools)

    def _run_codex_streamed(self, user_message: str, tools=None) -> CodexQueryResult:
        formatted_prompt = self._format_prompt(user_message)
        if len(formatted_prompt) > PROMPT_MAX_CHARS:
            raise ValueError(f"Implementation prompt exceeds local {PROMPT_MAX_CHARS}-character budget; "
                             "move verbose content into evidence files before dispatch")
        fd, output_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        transcript_path = Path(f"{self._log_prefix}.log") if self._log_prefix else None
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        process = None
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
                    start_new_session=True,
                )
                assert process.stdin is not None
                assert process.stdout is not None
                assert process.stderr is not None
                try:
                    process.stdin.write(formatted_prompt)
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
                        if not timed_out and time.monotonic() >= deadline:
                            timed_out = True
                            # Include descendant tool/agent processes, which
                            # otherwise can outlive the CLI and retain a writer.
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
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
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
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


def _agent_prompt(previous_verification: str, iteration: int, total: int, model: str | None,
                  phase: str = "full", readiness_path: Path | None = None) -> str:
    if phase == "feature-implementation":
        return _feature_implementation_prompt(previous_verification, iteration, total, model, readiness_path)
    if phase == "nic-repair":
        return _nic_repair_prompt(previous_verification, iteration, total, model)
    if phase == "feature-linux-repair":
        return _feature_linux_repair_prompt(previous_verification, iteration, total, model)
    return f"""You are the main implementation agent for the FireSim Golden Gate SFC-to-CIRCT port.

Read FIRESIM_CIRCT_PORT_PLAN.md at the beginning of every turn, inspect current sources and git status, and preserve unrelated shared-worktree edits and immutable SFC goldens. Work inside {REPO}; the Scala compiler under sims/firesim/sim/midas is the semantic oracle, and sims/firesim/sim/goldengate-circt plus its direct build wiring are the implementation. Focus on real compiler progress. Minimal verification adapters are in scope when needed to unblock the current compiler change; harness work alone does not establish compiler parity.

Current goals supersede the old 13-target U250 matrix and repeated Rocket streak:
1. Correctness for exactly FOUR fixed targets: RocketConfig, MegaBoomV3Config, ShuttleConfig, and Saturn Shuttle (FireSimGENV256D128ShuttleConfig). Use automation/chia_firesim_circt/selected_config_build_recipes.yaml, sourced from sims/firesim-staging/sample_config_build_recipes.yaml; plain Shuttle is explicitly derived because the sample lacks it. Never substitute LargeBoom for MegaBoom, Saturn for plain Shuttle, or U250 for F2. Preserve exact recipe/platform semantics. Compare the same pre-MIDAS FIRRTL and annotations against immutable SFC transforms using syntax checks and normalized semantic comparisons, not byte equality or enumeration of every config. Compare interfaces, annotations, bridge constructors, channels, memory behavior, clock/reset/token schedules, MMIO/driver ABI and XDC. A compile-only result is insufficient.
2. Jasper LEC AND SEC on rocky@32.185.44.117 (ssh alias eda, ~/.ssh/config), installation /tools/Cadence/jasper_2026.06p002. Use /scratch/jfx for remote work. Inspect /scratch/circt_compare or local /scratch/jfx/circt_compare as a starting flow; its SEC and compare-point reports do not establish a separate LEC proof. Both proof modes need actual results and explicit scope/assumptions, with counterexamples, bounded, inconclusive or missing proofs treated as incomplete.
3. After all four semantic comparisons pass, the harness dispatches a separate FireSim F2 node immediately, in parallel with Jasper. Build bitstreams on THIS manager with an externally provisioned localhost build farm. Use FireSim Manager and F2 run YAML to launch F2 instances and run workloads; ~/firesim.pem is the SSH key if direct investigation is needed. Follow the F2 docs linked in the plan. Never relabel historical U250 artifacts as F2. Require br-base-uniform Linux userspace boot, feature-specific checks where enabled, explicit poweroff and driver/script exit zero. Return exact artifact/runtime evidence; manager completion or the earlier baremetal checksum smoke is insufficient.
4. Speed: CIRCT must beat SFC FireSim compiler transformation/compile wall time on all four matched inputs. Time from compiler launch on identical pre-MIDAS FIRRTL/annotations until equivalent simulator RTL/collateral is fully emitted, including startup and all required compiler stages. Set timing_scope: firesim_compiler. Simulation/workload runtime, driver execution, Vivado/bitstream builds, deployment, proofs and target elaboration before the compiler handoff are excluded. There is no separate end-to-end workflow speed gate. Use all available cores and MLIR/module parallelism wherever safe; cap simultaneous jobs by affinity, RAM and Jasper licenses. Record cold JVM/startup conditions separately from warmed runs, exact commands, repetitions, thread count, compiler-invoked bridge elaboration costs and peak RSS. No timing claims from cache hits, unlike phases, removed passes or partial outputs.
5. Full port scope is REQUIRED: FAME5 (grouping, per-thread registers/reset/memories and IO scheduling), enabled AutoCounter/Print integration, generalized multi-model/multi-clock transformation, and NIC support (normal, multiple-widget and loopback coverage). Read automation/chia_firesim_circt/feature_scope.yaml. QSFP/partition transport is deferred. Keep exactly four baseline configurations; add only bounded feature-enabled representatives and focused unit cases, never enumerate all sample configs. Every representative must pass matched SFC/CIRCT semantic/syntax checks, separate Jasper LEC AND SEC, and actual F2 Linux userspace boot, feature-specific exercise, and clean poweroff. Ordinary baseline boot with a feature disabled cannot prove that feature. Implement feature-case execution in the verification nodes and retain hash-bound case receipts under logs/feature-evidence/<candidate-identity>/<feature>/; the features audit is a completion guard, not an implementation checkbox. Baseline-only success cannot end the loop.

Before FireSim commands on F2, run `source /home/ubuntu/miniforge3/etc/profile.d/conda.sh && cd /scratch/jfx/fsim-circt/sims/firesim && source ./sourceme-manager.sh`. Explicit Conda initialization is required in noninteractive SSH/login shells. Do not use --skip-ssh-setup on F2; SSH setup is required to load the run-host key (~/firesim.pem). CHIA owns separate semantic, Jasper, FireSim, speed and required-feature audit nodes. Do not launch duplicate long verification jobs from this LLM call. Configure real feature execution adapters as prerequisites become available; unset adapters or missing evidence intentionally block completion. Use the latest checkpoints and feature_scope.yaml to choose the next implementation tasks. BaseF2/Linux support and four-target speed have already been demonstrated; preserve them while implementing the mandatory feature streams and diagnosing formal failures.

Use as many subagents as you see fit for independent analysis, implementation and verification support; there is no task-specific limit on delegation. Coordinate shared-worktree edits, but centralize completion here: wait for every subagent, integrate results, finish checks and stop remaining workers before returning. CHIA treats THIS main LLM call returning as implementation completion; background agents cannot finish it later. Likewise the outer loop waits for all verification nodes before allowing the next source mutation.

Prefer concurrent owners for FAME5, AutoCounter/Print, multi-clock/generalized models and NIC when dependencies permit. Assign shared-file ownership and integrate common model/clock/state APIs centrally. Begin useful independent feature implementation while formal diagnosis continues across turns; these requirements are not deferred until the original four-target milestone ends. Reuse FPGA build/runtime evidence only with verified matching artifact/build/driver/workload/policy identities; unrelated source or documentation changes should not force duplicate Vivado work.

Report changed files, exact golden comparisons and tests, remaining gaps and next action. End with LOOP_COMPLETE: yes only if baseline correctness, faster transformation, and all four full-port feature requirements are achieved, otherwise LOOP_COMPLETE: no. CHIA accepts yes only when every current verification node, including features, passes for this candidate.

Iteration {iteration} of {total}. Previous verification:
{_bounded_text(previous_verification, FEEDBACK_MAX_CHARS) if previous_verification else '(No prior verification feedback.)'}
Model: {model or 'Codex CLI default'}
Live worktree snapshot:
{_bounded_text(_workspace_snapshot(), 24_000)}
"""



def _nic_repair_prompt(previous: str, iteration: int, total: int, model: str | None) -> str:
    return f"""You are the main NIC runtime repair agent for the FireSim CIRCT port.
ACTIVE PHASE: NIC REPAIR. Work inside {REPO}. Read FIRESIM_CIRCT_PORT_PLAN.md,
CHIA_OPERATOR_VERIFICATION_20261103.md when present, and retained failure evidence.
Preserve unrelated edits, immutable SFC goldens and all failed FPGA/proof results.
Diagnose and fix the actual production compiler/platform/host driver/NIC path.

The harness checks exactly Rocket, MegaBoomV3, plain Shuttle and Saturn Shuttle
baseline syntax/static semantics. After they pass, a separate CHIA node runs
exactly four NIC Linux representatives: single normal NIC, two independent NICs,
compile-time loopback, and mixed normal/per-widget runtime loopback. It joins all
workers and checks owned farm cleanup before another implementation call. Runtime
failures feed the next implementation turn automatically. Source readiness text
and LOOP_COMPLETE declarations cannot complete this repair. Completion requires
all four current-source NIC Linux/packet/poweroff/cleanup results. Full feature,
formal and compiler-speed acceptance remain separate and incomplete.

Do not launch Jasper, baseline FPGA builds, compiler benchmarks or duplicate long
FPGA jobs from this implementation call. Do not wait for formal verification.
Retain defined-state/token/MMIO/packet checks; do not waive zero-byte writes or
stalls as success. A nonblocking stream push may return zero: any pending reply
must remain byte-identical and owned until an exactly-once complete transfer.
Trace actual FIFO occupancy, address/count wiring, host ordering and token
conservation rather than concealing drift with blind retries. Keep nonzero short
transfers fatal and DRAM zero_out_dram false. Written workload memory is strict;
unwritten memory is unspecified. Real Linux userspace, packet checks, poweroff,
driver zero exit and run-farm cleanup are mandatory.

Use as many subagents as useful with explicit file ownership; integrate and join
all implementation subagents before returning. Use available cores within RAM and
shared Vivado locks. FPGA inputs stay frozen while verification runs. Reuse a
bitstream only when hardware identity matches; changed host drivers require new
source-bound runtime receipts. Before manager commands source the Conda profile,
then source ./sourceme-manager.sh in sims/firesim with SSH setup enabled.

If native compiler sources change, rebuild with available cores, copy the binary
to a new immutable hash-named path, and atomically update the active verification
YAML compiler references and candidate output root before returning. Never
overwrite pinned compilers or archived candidates. Host-only changes still need
current-source production compile/link and fresh runtime verification. The harness
reads the active YAML each iteration. Report changed files, regressions, evidence
and remaining issues. Keep LOOP_COMPLETE: no; no full-port completion is awarded.

Iteration {iteration} of {total}. Model: {model or 'Codex CLI default'}
Previous verification diagnostics:
{_bounded_text(previous, FEEDBACK_MAX_CHARS) if previous else '(No prior feedback.)'}
Live worktree snapshot:
{_bounded_text(_workspace_snapshot(), 24_000)}
"""


def _feature_linux_repair_prompt(previous: str, iteration: int, total: int,
                                  model: str | None) -> str:
    return f"""You are the main integrated-feature Linux verification repair agent.
ACTIVE PHASE: ALL-SEVEN FEATURE LINUX REPAIR. Work inside {REPO}. Read
FIRESIM_CIRCT_PORT_PLAN.md and the retained current verification evidence. Preserve
unrelated edits, immutable SFC goldens, historical failures and verified candidates.

The harness checks exactly Rocket, MegaBoomV3, plain Shuttle and Saturn Shuttle
baseline syntax/static semantics, then a separate node executes exactly seven
bounded enabled-feature Linux representatives on the same current candidate:
FAME5 with all four logical threads, combined AutoCounter/Print, multiclock models,
normal single NIC, two independent NICs, compile-time loopback and mixed
normal/per-widget runtime loopback. Every case needs real F2 Linux userspace,
its feature-specific checks, explicit poweroff, driver zero exit and owned farm
cleanup. The harness joins all workers before another implementation call; failed
joined verification feeds the next repair turn automatically. All seven current
passing results with matching identities are required. Source-readiness or
LOOP_COMPLETE declarations cannot end this phase. This verifies integrated
runtime coverage; full feature semantic/formal and compiler-speed acceptance
remain separate and incomplete.

Repair production compiler/platform/host/guest/checker failures without weakening
observations, accepting stale receipts or replacing feature-enabled cases with
baseline boots. Keep DRAM zero_out_dram false, defined memory and token/packet
checks strict, and unwritten memory unspecified. Preserve exactly-once pending
reply ownership and reject short/malformed transfers. Do not launch Jasper,
baseline FPGA builds, compiler benchmarks or duplicate long FPGA jobs from this
implementation call. Do not wait for formal verification.

Use as many subagents as useful with explicit file ownership; integrate centrally
and join every implementation subagent before returning. Use available cores
within RAM and shared Vivado locks. The verification node freezes each build/run
snapshot. Reuse hardware only when its exact hardware identity matches; rebuild
changed host/guest artifacts and bind fresh runtime receipts. Before manager
commands source the Conda profile, then source ./sourceme-manager.sh in
sims/firesim with SSH setup enabled; never use --skip-ssh-setup on F2.

If native compiler sources change, rebuild using available cores, pin the binary
to a new immutable hash-named path, and atomically update the active verification
YAML compiler references and candidate output root before returning. Never
overwrite pinned compilers or archived candidates. Keep the main default
automation/chia_firesim_circt/verification.yaml compiler pin aligned with the
active YAML so the integrated workspace remains runnable. The harness rereads
the active YAML on each iteration. Report changes, tests, evidence and remaining issues.
Keep LOOP_COMPLETE: no; this phase cannot award full-port completion.

Iteration {iteration} of {total}. Model: {model or 'Codex CLI default'}
Previous verification diagnostics:
{_bounded_text(previous, FEEDBACK_MAX_CHARS) if previous else '(No prior feedback.)'}
Live worktree snapshot:
{_bounded_text(_workspace_snapshot(), 24_000)}
"""


def _feature_implementation_prompt(previous: str, iteration: int, total: int,
                                   model: str | None, readiness_path: Path | None) -> str:
    return f"""You are the main FireSim SFC-to-CIRCT implementation agent.
ACTIVE PHASE: FEATURE IMPLEMENTATION. The user explicitly stopped formal verification
and requested finishing all remaining implementation first. Do not launch Jasper
LEC/SEC in this phase. After each implementation turn, the harness checks exactly four
baseline semantics, freezes a passing candidate and launches its FPGA verification in
an independent background job. Implementation resumes as soon as semantic checks and
the immutable snapshot finish; it does not wait for FPGA execution, formal proofs or
compiler benchmarks. Do not edit frozen background snapshots. Do not wait for formal
signoff before implementing independent features. Final port acceptance still requires
the normal full CHIA graph; this phase cannot satisfy LOOP_COMPLETE.

Read FIRESIM_CIRCT_PORT_PLAN.md and CHIA_OPERATOR_FEATURE_FIRST_20261102.md first,
then CHIA_OPERATOR_FEATURE_COMPLETENESS_20261102.md subject to that newer instruction,
automation/chia_firesim_circt/feature_scope.yaml, current sources and git status.
Work in {REPO}; preserve unrelated edits, immutable SFC goldens and justified
CIRCT correctness fixes. Complete actual production paths, not additional isolated
helpers: FAME5 grouping/scheduling and external sharing; enabled AutoCounter/Print
with shared MMIO/streams/driver headers; generalized model/domain platform transport;
and normal/multiple-widget/compile-time/runtime-loopback NIC integration. Finish
bounded Linux-capable configurations, feature workloads/checkers and real execution
adapters for every representative in feature_scope.yaml. Exactly four baseline
targets remain Rocket, MegaBoomV3, plain Shuttle and Saturn Shuttle. QSFP is deferred.

Use as many concurrent subagents as useful with explicit shared-file ownership.
Integrate centrally and join every implementation-owned worker/process before returning.
CHIA-owned FPGA jobs run on frozen snapshots and may continue between turns. Use available
cores safely within RAM and shared build limits. Run meaningful focused unchanged-SFC
semantic/RTL/ABI comparisons, driver builds and workload/checker validation as needed.
Feature-specific FPGA builds/runs must also use isolated background execution
adapters with the real FireSim manager, controlled resources and cleanup; do not
wait for FPGA jobs inside an implementation call. Consume their retained results
in subsequent turns and do not repeatedly rebuild the four baseline designs.
Join implementation subagents and their short checks, not independent FPGA jobs.
Before FireSim commands:
source /home/ubuntu/miniforge3/etc/profile.d/conda.sh, then cd sims/firesim and
source ./sourceme-manager.sh with SSH setup enabled. Leave zero_out_dram false.
Unwritten memory is unspecified; defined controls, initialized/written state and
observable transactions remain strict. Preserve retained formal diagnostics for later.

Continue implementing until all four required streams and all seven bounded cases
are runnable through production compiler/platform/driver/workload paths. Report exact
changed files, successful commands, evidence hashes and remaining implementation gaps.
When actually implemented, write {readiness_path} using the schema in
automation/chia_firesim_circt/feature_implementation_readiness.py and README.md.
The report must be fresh this turn, bind current source_identity (call that module's
source_identity(REPO)), compiler hash, all four features with no remaining gaps, and
every declared representative with config/rtl/driver/workload/checker/execution_adapter
hashes plus production_compile, driver_build and workload_checker successful command
receipts. This is implementation readiness only; do not fabricate formal or FPGA
acceptance. End with FEATURE_IMPLEMENTATION_COMPLETE: yes only with that evidence,
otherwise no. Always keep LOOP_COMPLETE: no in this phase. Text declarations alone
cannot finish this milestone. The loop continues implementation if evidence is absent.

Iteration {iteration} of {total}. Model: {model or 'Codex CLI default'}
Previous implementation/verification diagnostics:
{_bounded_text(previous, FEEDBACK_MAX_CHARS) if previous else '(No prior feedback.)'}
Live worktree snapshot:
{_bounded_text(_workspace_snapshot(), 24_000)}
"""


def _feature_implementation_result(response: str, readiness_path: Path, started_ns: int) -> dict:
    result = {"phase": "feature-implementation", "implementation_ready": False,
              "verified_complete": False, "report": str(readiness_path),
              "verification_dispatched": False}
    if not FEATURE_READY_RE.search(response):
        result["reason"] = "Implementation continues; no evidence-backed feature readiness declaration."
        return result
    try:
        validator = runpy.run_path(str(AUTOMATION / "feature_implementation_readiness.py"))
        result.update(validator["validate_readiness"](readiness_path, REPO, started_ns))
    except Exception as error:
        result["reason"] = f"Feature readiness rejected: {type(error).__name__}: {error}"
    return result


def _feature_implementation_iteration(config: dict, run_id: str, response: str,
                                      readiness_path: Path, started_ns: int) -> tuple[dict, list[dict]]:
    """Semantic join, immutable FPGA dispatch, then resume implementation."""
    semantic = get(verify_semantic.chia_remote(config, run_id, _chia_tag="feature-semantic"))
    result = {"phase": "feature-implementation", "implementation_ready": False,
              "verified_complete": False, "report": str(readiness_path),
              "formal_dispatched": False, "semantic_status": semantic.get("status"),
              "semantic_evidence": semantic.get("evidence")}
    if not semantic.get("ok"):
        result["reason"] = "Repair the four-target semantic failure before progressing to another feature."
        return result, [semantic]
    result.update(_feature_implementation_result(response, readiness_path, started_ns))
    result.pop("verification_dispatched", None)
    try:
        background = runpy.run_path(str(AUTOMATION / "background_f2.py"))
        result["background_f2"] = background["launch"](config, run_id, semantic, REPO)
    except Exception as error:
        result["background_f2"] = {"status": "dispatch_failed",
                                   "reason": f"{type(error).__name__}: {error}"}
        result["implementation_ready"] = False
    return result, [semantic]


def _feature_background_status() -> dict:
    """Read frozen FPGA job outcomes without joining their execution."""
    try:
        background = runpy.run_path(str(AUTOMATION / "background_f2.py"))
        return background["status"](REPO)
    except Exception as error:
        return {"status": "unavailable", "reason": f"{type(error).__name__}: {error}"}


def _format_verification(raw: list[dict[str, object]]) -> str:
    if not raw:
        return "Verification disabled."
    reports = []
    for item in raw:
        result = CommandResult(**item)
        reports.append(result.summary())
    return "\n\n".join(reports)


def _run_verification_node(node: str, config: dict, run_id: str,
                           semantic: dict | None = None) -> dict:
    """Load current node implementations on workers; retain setup errors as evidence."""
    evidence_dir = AUTOMATION / "logs" / "verification" / _safe_filename(run_id)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    try:
        module = ("feature_acceptance.py" if node == "features" else
                  "feature_linux.py" if node == "feature_linux" else "verification_nodes.py")
        implementation = runpy.run_path(str(AUTOMATION / module))
        function = implementation[f"run_{node}"]
        if node == "semantic":
            return function(config, evidence_dir, REPO)
        return function(config, evidence_dir, REPO, semantic)
    except Exception as error:
        report = {"node": node, "status": "failed", "ok": False,
                  "reason": f"{type(error).__name__}: {error}", "details": [],
                  "evidence": str(evidence_dir / f"{node}-dispatch-error.json")}
        Path(report["evidence"]).write_text(json.dumps(report, indent=2) + "\n")
        return report


@ChiaFunction(resources={"golden_compare": 0.01})
def verify_semantic(config: dict, run_id: str) -> dict:
    return _run_verification_node("semantic", config, run_id)


@ChiaFunction(resources={"jasper": 0.01})
def verify_jasper(config: dict, run_id: str, semantic: dict) -> dict:
    return _run_verification_node("jasper", config, run_id, semantic)


@ChiaFunction(resources={"firesim_manager": 0.01})
def verify_f2(config: dict, run_id: str, semantic: dict) -> dict:
    return _run_verification_node("firesim", config, run_id, semantic)


@ChiaFunction(resources={"firesim_manager": 0.01})
def verify_feature_linux(config: dict, run_id: str, semantic: dict) -> dict:
    return _run_verification_node("feature_linux", config, run_id, semantic)


@ChiaFunction(resources={"compile_benchmark": 0.01})
def verify_speed(config: dict, run_id: str, semantic: dict) -> dict:
    return _run_verification_node("speed", config, run_id, semantic)


@ChiaFunction(resources={"golden_compare": 0.01})
def verify_features(config: dict, run_id: str, semantic: dict) -> dict:
    return _run_verification_node("features", config, run_id, semantic)


def _verification_graph(config: dict, run_id: str) -> list[dict]:
    """Baseline verification followed by a mandatory full-port evidence audit."""
    semantic = get(verify_semantic.chia_remote(config, run_id, _chia_tag="semantic"))
    if not semantic.get("ok"):
        return [semantic]
    reports = [semantic]
    # Dispatch both branches before waiting. A dispatch failure must still
    # drain any task that already started before source mutations resume.
    pending = []
    for name, function, tag in (("firesim", verify_f2, "firesim-f2"),
                                ("jasper", verify_jasper, "jasper-lec-sec"),
                                ("feature_linux", verify_feature_linux, "feature-linux")):
        try:
            pending.append((name, function.chia_remote(
                config, run_id, semantic, _chia_tag=tag)))
        except Exception as error:
            reports.append({"node": name, "status": "failed", "ok": False,
                            "reason": f"CHIA dispatch error: {error}"})
    # Drain all tasks even if one raises, before the next agent can edit inputs.
    for name, task in pending:
        try:
            reports.append(get(task))
        except Exception as error:
            reports.append({"node": name, "status": "failed", "ok": False,
                            "reason": f"CHIA worker error: {error}"})
    if any(report.get("workers_joined") is False for report in reports):
        for name in ("speed", "features"):
            reports.append({"node": name, "status": "blocked", "ok": False,
                            "reason": "Enabled FPGA workers are not confirmed stopped; retain inputs and resolve cleanup."})
        return reports
    # Measure with the local bitstream build finished, so Vivado cannot skew
    # compiler wall time or compete for the compiler's available core budget.
    try:
        reports.append(get(verify_speed.chia_remote(
            config, run_id, semantic, _chia_tag="compile-speed")))
    except Exception as error:
        reports.append({"node": "speed", "status": "failed", "ok": False,
                        "reason": f"CHIA worker error: {error}"})
    try:
        reports.append(get(verify_features.chia_remote(
            config, run_id, semantic, _chia_tag="required-features")))
    except Exception as error:
        reports.append({"node": "features", "status": "failed", "ok": False,
                        "reason": f"CHIA worker error: {error}"})
    return reports



NIC_REPAIR_CASES = frozenset({
    "normal_single_nic_linux_bidirectional_packet_checks",
    "two_nic_linux_independent_concurrent_traffic",
    "compile_time_loopback_nic_linux_packet_checks",
    "runtime_per_widget_nic_loopback_linux_packet_checks",
})
NIC_BASELINE_TARGETS = frozenset({"rocket", "megaboomv3", "shuttle", "saturn_shuttle"})


def _nic_repair_graph(config: dict, run_id: str) -> list[dict]:
    """Join four baseline comparisons then four NIC Linux cases; no formal jobs."""
    options = config.get("feature_linux", {})
    cases = options.get("cases", []) if isinstance(options, dict) else []
    if (not isinstance(cases, list) or len(cases) != len(NIC_REPAIR_CASES)
            or any(not isinstance(case, str) for case in cases)
            or set(cases) != NIC_REPAIR_CASES):
        return [{"node": "configuration", "status": "failed", "ok": False,
                 "workers_joined": True,
                 "reason": "nic-repair requires exactly the four declared NIC Linux representatives"}]
    semantic = get(verify_semantic.chia_remote(config, run_id, _chia_tag="nic-baseline-semantic"))
    reports = [semantic]
    if semantic.get("ok") is not True:
        return reports
    # The node owns cancellation, terminal joins and farm cleanup. A lost task
    # cannot establish those joins; stop source mutation on uncertainty.
    try:
        task = verify_feature_linux.chia_remote(config, run_id, semantic,
                                                _chia_tag="nic-feature-linux")
        report = get(task)
        if report.get("workers_joined") is not True:
            report["workers_joined"] = False
        reports.append(report)
    except Exception as error:
        reports.append({"node": "feature_linux", "status": "failed", "ok": False,
                        "workers_joined": False,
                        "reason": f"NIC verification task failed; cleanup must be confirmed before editing: {error}"})
    return reports



def _nic_repair_progress(run_id: str, iteration: int, phase: str,
                         reports: list[dict] | None = None) -> None:
    """Publish small operator progress outside the candidate source identity."""
    path = AUTOMATION / "logs/nic-repair-active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"run_id": run_id, "iteration": iteration, "phase": phase,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "evidence": str(AUTOMATION / "logs/verification" / run_id),
            "formal_enabled": False, "full_acceptance": False}
    if reports is not None:
        data["nodes"] = [{key: row[key] for key in
                          ("node", "status", "ok", "workers_joined", "identity", "evidence")
                          if key in row} for row in reports]
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def _nic_repair_complete(reports: list[dict]) -> bool:
    """Only current four-case runtime evidence ends this scoped repair."""
    if (not isinstance(reports, list) or len(reports) != 2
            or any(not isinstance(row, dict) for row in reports)):
        return False
    semantic, linux = reports
    if (semantic.get("node") != "semantic" or linux.get("node") != "feature_linux"
            or not semantic.get("identity")
            or semantic.get("identity") != linux.get("identity")
            or any(row.get("ok") is not True or row.get("status") != "passed" for row in reports)
            or linux.get("workers_joined") is not True
            or linux.get("full_acceptance") is not False
            or linux.get("formal_claim") is not False):
        return False
    baseline = semantic.get("details", [])
    selected = linux.get("selected_cases", [])
    details = linux.get("details", [])
    if (not isinstance(baseline, list) or not isinstance(selected, list)
            or not isinstance(details, list)
            or any(not isinstance(row, dict) or not isinstance(row.get("target"), str) for row in baseline)
            or any(not isinstance(case, str) for case in selected)
            or any(not isinstance(row, dict) or not isinstance(row.get("case_id"), str) for row in details)):
        return False
    return (len(baseline) == 4
            and {row.get("target") for row in baseline} == NIC_BASELINE_TARGETS
            and all(row.get("ok") is True and row.get("status") == "passed" for row in baseline)
            and len(selected) == 4 and set(selected) == NIC_REPAIR_CASES
            and len(details) == 4
            and {row.get("case_id") for row in details} == NIC_REPAIR_CASES
            and all(row.get("ok") is True and row.get("status") == "passed"
                    and row.get("result") and row.get("feature_checks") for row in details))



FEATURE_LINUX_REPAIR_CASES = NIC_REPAIR_CASES | frozenset({
    "threaded_repeated_model_linux_all_threads_exercised",
    "enabled_autocounter_and_print_linux_checked_csv_and_prints",
    "multiclock_multimodel_linux_checked_domain_activity",
})


def _feature_linux_repair_graph(config: dict, run_id: str) -> list[dict]:
    """Join four baseline comparisons then all seven feature Linux cases; no formal jobs."""
    options = config.get("feature_linux", {})
    cases = options.get("cases", []) if isinstance(options, dict) else []
    if (not isinstance(cases, list) or len(cases) != len(FEATURE_LINUX_REPAIR_CASES)
            or any(not isinstance(case, str) for case in cases)
            or set(cases) != FEATURE_LINUX_REPAIR_CASES):
        return [{"node": "configuration", "status": "failed", "ok": False,
                 "workers_joined": True,
                 "reason": "feature-linux-repair requires exactly the seven declared enabled-feature Linux representatives"}]
    semantic = get(verify_semantic.chia_remote(config, run_id, _chia_tag="feature-linux-baseline-semantic"))
    reports = [semantic]
    if semantic.get("ok") is not True:
        return reports
    # The node owns cancellation, terminal joins and farm cleanup. A lost task
    # cannot establish those joins; stop source mutation on uncertainty.
    try:
        task = verify_feature_linux.chia_remote(config, run_id, semantic,
                                                _chia_tag="all-feature-linux")
        report = get(task)
        if report.get("workers_joined") is not True:
            report["workers_joined"] = False
        reports.append(report)
    except Exception as error:
        reports.append({"node": "feature_linux", "status": "failed", "ok": False,
                        "workers_joined": False,
                        "reason": f"Feature Linux verification task failed; cleanup must be confirmed before editing: {error}"})
    return reports



def _feature_linux_repair_progress(run_id: str, iteration: int, phase: str,
                         reports: list[dict] | None = None) -> None:
    """Publish small operator progress outside the candidate source identity."""
    path = AUTOMATION / "logs/feature-linux-repair-active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"run_id": run_id, "iteration": iteration, "phase": phase,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "evidence": str(AUTOMATION / "logs/verification" / run_id),
            "formal_enabled": False, "full_acceptance": False}
    if reports is not None:
        data["nodes"] = [{key: row[key] for key in
                          ("node", "status", "ok", "workers_joined", "identity", "evidence")
                          if key in row} for row in reports]
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def _feature_linux_repair_complete(reports: list[dict]) -> bool:
    """Only current seven-case runtime evidence ends this scoped repair."""
    if (not isinstance(reports, list) or len(reports) != 2
            or any(not isinstance(row, dict) for row in reports)):
        return False
    semantic, linux = reports
    if (semantic.get("node") != "semantic" or linux.get("node") != "feature_linux"
            or not semantic.get("identity")
            or semantic.get("identity") != linux.get("identity")
            or any(row.get("ok") is not True or row.get("status") != "passed" for row in reports)
            or linux.get("workers_joined") is not True
            or linux.get("full_acceptance") is not False
            or linux.get("formal_claim") is not False
            or linux.get("passed") is not True
            or linux.get("full_matrix") is not True
            or linux.get("remaining_cases") != []
            or not isinstance(linux.get("required_cases"), list)
            or any(not isinstance(case, str) for case in linux["required_cases"])
            or len(linux["required_cases"]) != 7
            or set(linux["required_cases"]) != FEATURE_LINUX_REPAIR_CASES):
        return False
    baseline = semantic.get("details", [])
    selected = linux.get("selected_cases", [])
    details = linux.get("details", [])
    if (not isinstance(baseline, list) or not isinstance(selected, list)
            or not isinstance(details, list)
            or any(not isinstance(row, dict) or not isinstance(row.get("target"), str) for row in baseline)
            or any(not isinstance(case, str) for case in selected)
            or any(not isinstance(row, dict) or not isinstance(row.get("case_id"), str) for row in details)):
        return False
    return (len(baseline) == 4
            and {row.get("target") for row in baseline} == NIC_BASELINE_TARGETS
            and all(row.get("ok") is True and row.get("status") == "passed" for row in baseline)
            and len(selected) == 7 and set(selected) == FEATURE_LINUX_REPAIR_CASES
            and len(details) == 7
            and {row.get("case_id") for row in details} == FEATURE_LINUX_REPAIR_CASES
            and all(row.get("ok") is True and row.get("status") == "passed"
                    and row.get("result") and row.get("feature_checks") for row in details))


def _graph_complete(reports: list[dict]) -> bool:
    return (len(reports) == 6
            and {report.get("node") for report in reports}
            == {"semantic", "jasper", "firesim", "feature_linux", "speed", "features"}
            and bool(reports[0].get("identity"))
            and all(report.get("identity") == reports[0]["identity"] for report in reports)
            and all(report.get("full_matrix") is True for report in reports
                    if report.get("node") == "feature_linux")
            and all(report.get("ok") is True for report in reports))


def _require_codex_success(response: CodexQueryResult) -> None:
    if response.returncode != 0 or not response.success:
        raise RuntimeError(
            "Codex implementation call failed; verification and further implementation "
            "iterations were not started. Inspect the Codex transcript before restarting.\n"
            + response.stderr[-4000:]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--verify", choices=("none", "preflight", "compile", "metasim", "full", "boot", "feature-implementation", "nic-repair", "feature-linux-repair"),
                        default="full", help="full/boot: semantic -> F2/Jasper -> speed -> features; feature-implementation: semantic -> frozen background F2 -> next implementation; nic-repair: semantic -> four NIC Linux cases -> repair; feature-linux-repair: semantic -> seven feature Linux cases -> repair, no formal dispatch")
    parser.add_argument("--verification-config", type=Path,
                        default=AUTOMATION / "verification.yaml")
    parser.add_argument("--model", default=None, help="Optional Codex CLI model override")
    parser.add_argument("--implementation-timeout-seconds", type=int, default=14400,
                        help="Wall limit for each implementation call (default: four hours)")
    parser.add_argument("--verification-first", action="store_true",
                        help="Verify the saved candidate before the first implementation call (full/boot/nic-repair/feature-linux-repair only)")
    parser.add_argument("--feedback-from", type=Path,
                        help="Prior graph.json to summarize for the first implementation turn; never reuses acceptance")
    parser.add_argument("--ray-address", default=os.environ.get("RAY_ADDRESS", "auto"))
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    if args.implementation_timeout_seconds < 1:
        parser.error("--implementation-timeout-seconds must be positive")
    if args.verification_first and args.verify not in {"full", "boot", "nic-repair", "feature-linux-repair"}:
        parser.error("--verification-first requires --verify full, boot, nic-repair or feature-linux-repair")

    # One shared candidate must not be mutated by overlapping implementation loops.
    with (AUTOMATION / ".loop.lock").open("a") as loop_lock:
        try:
            fcntl.flock(loop_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another implementation loop owns this shared checkout")
        ray.init(address=args.ray_address)
        start_collector(log_dir=str(AUTOMATION / "profiles"))
        agent = StreamingCodexLLM(
            model=args.model, reasoning_effort="high", work_dir=str(REPO),
            dangerously_bypass_approvals_and_sandbox=True,
            resume_session=True, timeout_seconds=args.implementation_timeout_seconds, retries=1,
            log_dir=str(AUTOMATION / "logs"),
        )
        feedback = ""
        if args.feedback_from:
            prior = json.loads(args.feedback_from.read_text())
            if not isinstance(prior, list) or any(not isinstance(row, dict) for row in prior):
                parser.error("--feedback-from must contain a graph.json list of node reports")
            feedback = _verification_feedback(prior, args.feedback_from.resolve())
        loop_run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-pid{os.getpid()}"
        goal_complete = False
        feature_ready = False
        nic_ready = False
        feature_linux_ready = False
        try:
            for iteration in range(1, args.iterations + 1):
                run_id = f"{loop_run_id}-iter{iteration}-{args.verify}"
                report_dir = AUTOMATION / "logs" / "feature-implementation" / run_id
                readiness_path = report_dir / "readiness.json"
                if args.verify == "feature-implementation":
                    report_dir.mkdir(parents=True, exist_ok=True)
                if args.verify == "nic-repair":
                    _nic_repair_progress(run_id, iteration,
                        "verification" if args.verification_first and iteration == 1 else "implementation")
                if args.verify == "feature-linux-repair":
                    _feature_linux_repair_progress(run_id, iteration,
                        "verification" if args.verification_first and iteration == 1 else "implementation")
                started_ns = time.time_ns()
                agent.model = _live_model(args.model)
                prompt_feedback = feedback
                if args.verify == "feature-implementation":
                    background_status = _feature_background_status()
                    (report_dir / "background-status.json").write_text(json.dumps(background_status, indent=2) + "\n")
                    prompt_feedback += "\nBackground FPGA outcomes (frozen earlier candidates):\n" + _bounded_text(
                        json.dumps(background_status, indent=2), 8_000)
                if args.verification_first and iteration == 1:
                    response = CodexQueryResult(
                        "Saved candidate submitted for fresh verification; no implementation call.\nLOOP_COMPLETE: no",
                        0, "", "", success=True)
                    print("Verification-first restart: checking the saved candidate before any source edits.", flush=True)
                else:
                    prompt = _agent_prompt(prompt_feedback, iteration, args.iterations, agent.model,
                                           args.verify, readiness_path)
                    if args.verify in {"nic-repair", "feature-linux-repair"}:
                        prompt += f"\nActive repair verification YAML: {args.verification_config.resolve()}\n"
                    print(f"\n=== Codex iteration {iteration}: PROMPT ===\n{prompt}", flush=True)
                    response = get(agent.prompt.options(
                        max_retries=0, retry_exceptions=False,
                    ).chia_remote(agent, prompt))
                    _require_codex_success(response)
                    print(f"\n=== Codex iteration {iteration}: RESPONSE ===\n{response.result}\n", flush=True)
                    if response.stderr:
                        print(response.stderr, flush=True)
                if args.verify == "feature-implementation":
                    # This phase never dispatches the full graph or legacy
                    # metasim manager. FPGA workers own immutable candidates.
                    try:
                        config = yaml.safe_load(args.verification_config.read_text())
                        if not isinstance(config, dict):
                            raise ValueError("verification config must be a mapping")
                        result, reports = _feature_implementation_iteration(
                            config, run_id, response.result, readiness_path, started_ns)
                    except Exception as error:
                        result = {"phase": "feature-implementation", "implementation_ready": False,
                                  "verified_complete": False, "formal_dispatched": False,
                                  "reason": f"{type(error).__name__}: {error}"}
                        reports = []
                    (report_dir / "response.txt").write_text(response.result + "\n")
                    (report_dir / "phase-result.json").write_text(json.dumps(result, indent=2) + "\n")
                    graph_path = report_dir / "graph.json"
                    graph_path.write_text(json.dumps(reports, indent=2) + "\n")
                    feedback = _bounded_text(
                        "Feature implementation phase; overall verification remains incomplete.\n"
                        f"Implementation result: {report_dir / 'phase-result.json'}\n"
                        + json.dumps(result, indent=2) + "\n"
                        + _verification_feedback(reports, graph_path)
                        + "\nPrevious implementation response:\n"
                        + _bounded_text(response.result, 24_000), FEEDBACK_MAX_CHARS)
                    (report_dir / "feedback.txt").write_text(feedback + "\n")
                    print(f"\n=== Feature implementation {iteration}: SUMMARY ===\n{feedback}\n", flush=True)
                    if result.get("implementation_ready") is True:
                        feature_ready = True
                        print("Feature implementation milestone is ready. Full semantic/formal/F2/speed "
                              "acceptance remains incomplete; formal proofs were not resumed.", flush=True)
                        break
                    print("Continuing feature implementation after semantic checks and background FPGA dispatch.", flush=True)
                    continue
                elif args.verify in {"full", "boot", "nic-repair", "feature-linux-repair"}:
                    try:
                        config = yaml.safe_load(args.verification_config.read_text())
                        if not isinstance(config, dict):
                            raise ValueError("verification config must be a mapping")
                        if args.verify == "nic-repair":
                            _nic_repair_progress(run_id, iteration, "verification")
                        if args.verify == "feature-linux-repair":
                            _feature_linux_repair_progress(run_id, iteration, "verification")
                        reports = (_feature_linux_repair_graph(config, run_id) if args.verify == "feature-linux-repair"
                                   else _nic_repair_graph(config, run_id) if args.verify == "nic-repair"
                                   else _verification_graph(config, run_id))
                    except Exception as error:
                        reports = [{"node": "configuration", "ok": False,
                                    "status": "failed", "reason": str(error)}]
                    complete_gate = (_feature_linux_repair_complete(reports) if args.verify == "feature-linux-repair"
                                     else _nic_repair_complete(reports) if args.verify == "nic-repair"
                                     else _graph_complete(reports))
                    report_dir = AUTOMATION / "logs" / "verification" / run_id
                    report_dir.mkdir(parents=True, exist_ok=True)
                    graph_path = report_dir / "graph.json"
                    graph_path.write_text(json.dumps(reports, indent=2) + "\n")
                    feedback = _verification_feedback(reports, graph_path)
                    (report_dir / "feedback.txt").write_text(feedback + "\n")
                    if args.verify == "nic-repair":
                        phase = ("passed" if complete_gate else "cleanup-unconfirmed" if
                                 any(row.get("workers_joined") is False for row in reports) else "repair-required")
                        _nic_repair_progress(run_id, iteration, phase, reports)
                    if args.verify == "feature-linux-repair":
                        phase = ("passed" if complete_gate else "cleanup-unconfirmed" if
                                 any(row.get("workers_joined") is False for row in reports) else "repair-required")
                        _feature_linux_repair_progress(run_id, iteration, phase, reports)
                else:
                    # Legacy U250 compile/metasim remain explicit diagnostics;
                    # they cannot establish completion of the AWS four-target goal.
                    verification = get(verify_with_firesim.chia_remote(
                        args.verify, run_id, False, _chia_tag=f"diagnostic-{iteration}"))
                    feedback = _format_verification(verification)
                    complete_gate = False
                print(f"\n=== Verification {iteration}: SUMMARY ===\n{feedback}\n", flush=True)
                if args.verify in {"full", "boot", "nic-repair", "feature-linux-repair"} and any(report.get("workers_joined") is False for report in reports):
                    raise RuntimeError("Verification workers were not joined; stopping before another implementation call")
                if args.verify == "nic-repair":
                    if complete_gate:
                        nic_ready = True
                        print("All four current NIC Linux/packet/poweroff/cleanup cases passed. "
                              "NIC runtime repair is verified; full-port acceptance remains incomplete.", flush=True)
                        break
                    print("NIC repair remains incomplete; joined runtime evidence feeds the next implementation turn.", flush=True)
                    continue
                if args.verify == "feature-linux-repair":
                    if complete_gate:
                        feature_linux_ready = True
                        print("All seven current enabled-feature Linux/runtime/poweroff/cleanup cases passed. "
                              "Integrated feature runtime verification is complete; full-port acceptance remains incomplete.", flush=True)
                        break
                    print("All-seven feature Linux verification remains incomplete; joined runtime evidence feeds the next implementation turn.", flush=True)
                    continue
                if complete_gate and GOAL_COMPLETE_RE.search(response.result):
                    goal_complete = True
                    print("All baseline and required-feature verification nodes passed and the main agent declared completion.", flush=True)
                    break
                print("Goal remains incomplete; verification evidence will inform the next implementation turn.", flush=True)
        finally:
            ray.shutdown()
        if args.verify == "feature-implementation" and not feature_ready:
            raise RuntimeError("Feature implementation iteration limit reached without evidence-backed readiness; full acceptance remains incomplete")
        if args.verify == "nic-repair" and not nic_ready:
            raise RuntimeError("NIC repair iteration limit reached without four current passing Linux/packet/cleanup cases")
        if args.verify == "feature-linux-repair" and not feature_linux_ready:
            raise RuntimeError("All-seven feature Linux repair iteration limit reached without seven current passing runtime/poweroff/cleanup cases")
        if not goal_complete and not feature_ready and not nic_ready and not feature_linux_ready:
            raise RuntimeError("Iteration limit reached without verified baseline, full feature scope and speed plus LOOP_COMPLETE: yes")


if __name__ == "__main__":
    main()
