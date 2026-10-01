# FireSim CIRCT Chia loop

This is the local, persistent engineering harness for the FireSim Golden Gate
Scala-FIRRTL Compiler (SFC) to CIRCT port. It does not attempt the port itself.
`FIRESIM_CIRCT_PORT_PLAN.md` remains the implementation charter.

The loop uses CHIA's `CodexLLM` backend, which shells out to the authenticated
local `codex` CLI. It has no OpenAI API-key path. The worker starts Codex in
`/scratch/jfx/fsim-circt` and explicitly authorizes autonomous repository edits
and optional GitHub checkpoints. The agent prompt constrains its work to that
checkout and preserves unrelated user modifications.

## Bring up the local worker

The host needs passwordless localhost SSH because CHIA starts its worker through
SSH. Source the project environment, make the expected build/run scratch
directories, then start the cluster:

```bash
cd /scratch/jfx/fsim-circt
cd sims/firesim
source ./sourceme-manager.sh --skip-ssh-setup
cd ../..
ssh -o BatchMode=yes 127.0.0.1 true
mkdir -p sims/firesim/deploy/{build-farm,run-farm}
chia up automation/chia_firesim_circt/cluster.yaml
chia status --chia-cluster automation/chia_firesim_circt/cluster.yaml
```

## Run the autonomous loop

After the cluster is up, submit this one bounded task and leave it running:

```bash
chia job submit -- \
  python /scratch/jfx/fsim-circt/automation/chia_firesim_circt/loop.py \
  --iterations 12 --verify full
```

Every iteration performs one Codex porting step followed by the complete
ordered gate: FireSim `replacertl` compile, Verilator metasimulation, then the
U250 build/boot flow. A failed gate feeds its full logs into the next Codex
turn. A passing gate does not end the engineering loop: Codex must explicitly
end its response with `LOOP_COMPLETE: yes`, and that declaration is accepted
only after the complete gate passes. If the iteration cap is reached without
that validated declaration, the Ray job exits as incomplete rather than marked
successful.

Buildroot normally remains running at a login shell, so before every complete
gate the harness builds and installs its `circt-boot-poweroff` FireMarshal
workload. That guest prints its successful-boot marker and executes
`sync; poweroff -f`; FireSim's `terminate_on_completion: yes` then ends the
simulation cleanly, after which the harness runs `terminaterunfarm` before
continuing porting. The image is rebuilt only when its FireMarshal inputs have
changed.

```bash
chia job submit -- \
  python /scratch/jfx/fsim-circt/automation/chia_firesim_circt/loop.py \
  --iterations 9999 --verify full
```

## Watch a live loop

The `chia job submit` terminal streams the operator transcript. It prints the
full prompt before each Codex call and streams every raw Codex CLI JSON event
as `[codex:live]` (including tool calls and tool results) while the agent is
working. The same events are appended immediately to its transcript, so it is
safe to tail even if the job is interrupted. The final response and parsed
event stream are printed when that call returns. The loop also streams every
FireSim stage with `[stage:<name>:stdout]` or `[stage:<name>:stderr]` prefixes
and an explicit START/PASS/FAIL marker. Reattach to a detached job with:

```bash
chia job list
chia job logs -f <job-id>
```

The complete Codex transcript is written to
`automation/chia_firesim_circt/logs/codex_<timestamp>.log`. Complete stdout and
stderr for every verification stage are written beneath
`automation/chia_firesim_circt/logs/verification/<run-id>/`.

To follow a known Codex transcript directly from the shared checkout:

```bash
tail -f automation/chia_firesim_circt/logs/codex_<timestamp>.log
```

Codex edits this same checkout rather than a Ray-uploaded copy. In a second
terminal, watch tracked edits appear as it works with:

```bash
cd /scratch/jfx/fsim-circt
watch -n 2 'git status --short; git diff --stat'
```

Optionally, run one inexpensive preflight iteration before starting the full
loop:

```bash
chia job submit -- \
  python /scratch/jfx/fsim-circt/automation/chia_firesim_circt/loop.py --iterations 1 --verify preflight
```

Do not pass `--working-dir /scratch/jfx/fsim-circt` to this local cluster.
Ray packages and uploads `--working-dir` before starting a job, whereas this
worker intentionally shares the same checkout through localhost SSH.  The
checkout contains generated FireSim artifacts and local toolchains that exceed
Ray's 100 MiB job-upload limit.  The entrypoint and loop use absolute paths so
they run directly from the shared checkout without an upload.

The preflight gate checks Codex, parses all four deployed FireSim YAML files,
and asks the real FireSim manager for help. It does not build hardware.
Each gate command writes its complete stdout and stderr to
`automation/chia_firesim_circt/logs/verification/<run-id>/`. The next Codex
turn receives the stdout/stderr paths plus a short tail of the output, so it
can inspect the full artifact when debugging.

## Verification gates

`--verify compile` runs the FireSim manager's `replacertl` task, which invokes
the existing `make replace-rtl` Golden Gate compiler recipe and stops before
driver or FPGA work. `--verify metasim` adds FireSim's Verilator metasimulation
of that transformed RTL. It generates a local runtime config with
`metasimulation_enabled: true`, then invokes:

```text
firesim builddriver
firesim launchrunfarm
firesim infrasetup
firesim runworkload
```

`--verify full` is the autonomous profile: it performs both gates and, only
when they pass, invokes:

```text
firesim buildbitstream
firesim launchrunfarm
firesim infrasetup
firesim runworkload
```

The last command boots the `circt-boot-poweroff.json` smoke workload on the
newly built U250 image; its guest powers itself off after the boot marker.
The harness never SSHes directly to an FPGA or build host; the FireSim manager
handles that communication. A failed verification is returned to Codex as the
next iteration's evidence: the error tail, complete captured stdout/stderr
paths, and FireSim's own manager-log path. The full profile is one bounded
Codex-and-verify loop: it continues after a failed gate, and ends early after a
complete pass. `--verify boot` remains an alias for compatibility.

Shut the local worker down when finished:

```bash
chia down automation/chia_firesim_circt/cluster.yaml
```

The four active deployment files in `sims/firesim/deploy/` are configured for
the standard small target: `FireSimRocketConfig` +
`BaseXilinxAlveoU250Config` at 60 MHz. The HWDB starts with the upstream
single-core U250 artifact only so it is immediately structurally bootable;
after a successful local build `sync_hwdb_entry.py` replaces it with the
generated `file://.../firesim.tar.gz` artifact before `runworkload` starts.
