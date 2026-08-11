# `src/kanibako/box_supervisor.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/box_supervisor.py.md`.

```python
log = get_logger('box_supervisor')

CONTINUE_MARKER = '[Agent handoff - Continue prior task(s)]'

TAKEOVER_HEADS_UP = '[Session takeover - another surface is taking over this session; wind down and checkpoint now if you have work in progress]'

KANIBAKO_PKG_MOUNT_ROOT = '/opt/kanibako'

PINNED_ROOT_RELPATH = '.kanibako'

XDG_PROJECTIONS: tuple[tuple[str, str, str], ...] = (('XDG_STATE_HOME', '.local/state', 'state'),)

XDG_LINK_NAME = 'kanibako'

def project_pinned_xdg(home: Path | None=None, environ: Mapping[str, str] | None=None) -> list[str]:
    ...

def xdg_projection_sh() -> str:
    ...

def scrub_bootstrap_pythonpath(environ: MutableMapping[str, str] | None=None) -> None:
    ...

_Runner = Callable[..., 'subprocess.CompletedProcess[str]']

_Sleeper = Callable[[float], None]

_PidAlive = Callable[[int], bool]

_MarkersLister = Callable[[str], 'list[int]']

_Signaller = Callable[[int, int], None]

_GroupOf = Callable[[int], int]

_OwnGroup = Callable[[], int]

_Reaper = Callable[[], int]

def _parse_stat_state(stat_text: str) -> str | None:
    ...

def _proc_stat_state(pid: int) -> str | None:
    ...

def _default_pid_alive(pid: int) -> bool:
    ...

def reap_zombie_children(max_reaps: int=32) -> int:
    ...

def _default_list_marker_pids(markers_dir: str) -> list[int]:
    ...

def scan_marker_pids(markers_dir: str, *, list_pids: _MarkersLister, pid_alive: _PidAlive) -> tuple[set[int], set[int]]:
    ...

def newcomer_pids(live_pids: set[int], own_pids: set[int]) -> set[int]:
    ...

@dataclass(frozen=True)
class SupervisorConfig:
    session: str
    start_argv: list[str]
    continue_argv: list[str]
    marker: str
    poll_interval: float = 2.0
    max_restart_retries: int = 3
    backoff_base: float = 0.5
    send_keys_retries: int = 3
    send_keys_delay: float = 0.1
    on_agent_exit: str = 'self-heal'
    session_takeover: bool = False
    takeover_grace: float = 5.0
    panel_watch: bool = False
    agent_markers_dir: str | None = None
    creds_flag: str | None = None
    capture_history: int = 200

class ActionKind(Enum):
    NONE = 'none'
    SELF_HEAL = 'self_heal'

@dataclass(frozen=True)
class SupervisorAction:
    kind: ActionKind = ActionKind.NONE
    fire_detach_hook: bool = False

def decide(prev_state: AttachState, cur_state: AttachState, agent_alive: bool) -> SupervisorAction:
    ...

class PanelAgentState(Enum):
    NONE = 'none'
    ALIVE = 'alive'
    DEAD = 'dead'

class PanelActionKind(Enum):
    NONE = 'none'
    SELF_HEAL_CLI = 'self_heal_cli'
    TEARDOWN = 'teardown'

@dataclass(frozen=True)
class PanelAction:
    kind: PanelActionKind = PanelActionKind.NONE

def decide_panel(tmux_alive: bool, panel: PanelAgentState, vscode_server: bool, any_attached: bool, seen_surface: bool) -> PanelAction:
    ...

class BoxSupervisor:

    def __init__(self, config: SupervisorConfig, *, run: _Runner=subprocess.run, sleep: _Sleeper=time.sleep, proc_cmdlines: Iterable[str] | None=None, pid_alive: _PidAlive=_default_pid_alive, list_marker_pids: _MarkersLister=_default_list_marker_pids, kill: _Signaller=os.kill, killpg: _Signaller=os.killpg, getpgid: _GroupOf=os.getpgid, getpgrp: _OwnGroup=os.getpgrp, reap: _Reaper=reap_zombie_children) -> None:
        ...

    def _run_tmux(self, args: list[str]) -> int | None:
        ...

    def _tmux_output(self, args: list[str]) -> str | None:
        ...

    def _start_session_argv(self, session_argv: list[str]) -> list[str]:
        ...

    def _arm_and_start_session(self, session_argv: list[str]) -> int | None:
        ...

    def start_agent_session(self) -> bool:
        ...

    def restart_agent_session(self) -> bool:
        ...

    def _send_keys_text(self, text: str) -> bool:
        ...

    def _send_marker(self) -> bool:
        ...

    def _send_takeover_heads_up(self) -> bool:
        ...

    def agent_pane_dead_status(self) -> int | None:
        ...

    def capture_agent_output(self) -> str | None:
        ...

    def agent_session_alive(self) -> bool:
        ...

    def _kill_process_group(self, pid: int, sig: int) -> bool:
        ...

    def kill_agent_session(self) -> None:
        ...

    def _snapshot(self) -> AttachState:
        ...

    def _other_surface_attached(self, state: AttachState) -> bool:
        ...

    def _scan_markers(self) -> tuple[set[int], set[int]]:
        ...

    def _own_agent_pids(self) -> set[int]:
        ...

    def _log_newcomers(self, live_pids: set[int], own_pids: set[int]) -> None:
        ...

    def _signal_pid(self, pid: int, sig: int) -> bool:
        ...

    def _resume(self, pids: list[int]) -> None:
        ...

    def _takeover(self, own_pids: set[int], newcomers: set[int]) -> bool:
        ...

    def panel_agent_state(self) -> PanelAgentState:
        ...

    def _self_heal(self) -> bool:
        ...

    def _on_detach(self) -> None:
        ...

    def _safe_on_detach(self) -> None:
        ...

    def teardown(self) -> None:
        ...

    def _handle_sigterm(self, signum: int, frame: FrameType | None) -> None:
        ...

    def install_signal_handlers(self) -> None:
        ...

    def run_forever(self) -> int:
        ...

    def _run_panel_watch(self) -> int:
        ...

def _build_parser() -> argparse.ArgumentParser:
    ...

def config_from_argv(argv: list[str]) -> SupervisorConfig:
    ...

def main(argv: list[str] | None=None) -> int:
    ...
```
