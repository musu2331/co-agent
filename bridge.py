"""Local project/task bridge for Codex, Pi and DeepSeek Harness. No dependencies."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from model_config import ModelProfiles, describe as describe_model, normalize as normalize_model
from setup_workspace import initialize_hub

TOOLS = ("codex", "pi", "dsh")
SAFE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}\Z")
TERMINAL = {"succeeded", "failed", "interrupted"}


def dsh_account_config_digest():
    return hashlib.sha256((Path(__file__).resolve().parent / "dsh-account.patch.yml").read_bytes()).hexdigest()


class BridgeError(Exception):
    pass


class TaskCancelled(BridgeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".bridge-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def checked_id(value):
    if not SAFE_ID.fullmatch(value):
        raise BridgeError(f"Invalid identifier: {value!r}")
    return value


def installed_file(path: Path):
    """Do not misreport sandbox-denied installed programs as missing."""
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except FileNotFoundError:
        return False
    except PermissionError:
        raise BridgeError(
            f"Access denied while locating an installed tool: {path}. "
            "Use the calling tool's normal permission/approval mechanism, or run from a regular terminal."
        ) from None


@dataclass
class Engine:
    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)


def detect_engine(name: str) -> Engine:
    if name not in TOOLS:
        raise BridgeError(f"Unknown tool: {name}")
    if name == "codex":
        binary = shutil.which("codex.exe" if os.name == "nt" else "codex")
        if binary:
            return Engine(name, [binary])
        if os.name == "nt":
            wrapper = shutil.which("codex")
            node = shutil.which("node")
            entry = Path(wrapper).parent / "node_modules/@openai/codex/bin/codex.js" if wrapper else None
            if node and entry and entry.is_file():
                return Engine(name, [node, str(entry)])
    if name == "pi":
        if os.name == "nt":
            launcher = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".pi/agent/bin/pi-launcher.js"
            node = shutil.which("node")
            if launcher.is_file() and node:
                return Engine(name, [node, str(launcher)])
            wrapper = shutil.which("pi")
            entry = Path(wrapper).parent / "node_modules/@mariozechner/pi-coding-agent/dist/cli.js" if wrapper else None
            if node and entry and entry.is_file():
                return Engine(name, [node, str(entry)])
        else:
            binary = shutil.which("pi")
            if binary:
                return Engine(name, [binary])
    if name == "dsh":
        # Use the installed desktop carrier directly: no cmd.exe, PATH edits or reinstall.
        if os.name == "nt":
            install = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DeepSeek Harness"
            exe = install / "DeepSeek Harness.exe"
            archive = install / "resources/app.asar"
            if installed_file(exe) and installed_file(archive):
                entry = str(archive) + "/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js"
                return Engine(name, [str(exe), "--expose-internals", entry], {"ELECTRON_RUN_AS_NODE": "1"})
        else:
            binary = shutil.which("dsh")
            if binary:
                return Engine(name, [binary])
    raise BridgeError(f"{name}: executable not found. See README.md; existing accounts are not copied or changed.")


def command_for(engine: Engine, intent: str, result: Path, model_config=None):
    args = list(engine.command)
    selection = normalize_model(engine.name, model_config or {"mode": "inherit"})
    if engine.name == "codex":
        if selection["mode"] == "override":
            args += ["--model", selection["model"]]
            if selection.get("reasoning"):
                args += ["--config", "model_reasoning_effort=" + json.dumps(selection["reasoning"])]
        args += ["exec", "--skip-git-repo-check", "--sandbox",
                 "read-only" if intent == "review" else "workspace-write",
                 "--color", "never", "--output-last-message", str(result), "-"]
    elif engine.name == "pi":
        args += ["--print", "--no-session"]
        if selection["mode"] == "override":
            args += ["--model", selection["model"]]
            if selection.get("provider"):
                args += ["--provider", selection["provider"]]
            if selection.get("reasoning"):
                args += ["--thinking", selection["reasoning"]]
        if intent == "review":
            args += ["--no-extensions", "--no-mcp", "--tools", "read,grep,find,ls"]
    else:
        args += ["--profile", "headless", "--patch", str(Path(__file__).resolve().parent / "dsh-account.patch.yml")]
        if selection["mode"] == "override":
            args += ["--patch", str(result.parent / "model.patch.json")]
        args += ["-"]
    return args


def stop_child(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        process.wait()


class Bridge:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.config = read_json(self.root / "projects.json")

    def project(self, key):
        checked_id(key)
        try:
            return self.config["projects"][key]
        except KeyError:
            raise BridgeError(f"Unknown project: {key}") from None

    def project_path(self, key):
        info = self.project(key)
        value = info["windows" if os.name == "nt" else "wsl"]
        path = Path(value).resolve()
        if not path.is_dir():
            raise BridgeError(f"Project directory does not exist on this host: {path}")
        return path

    def folder(self, task_id):
        return self.root / "tasks" / checked_id(task_id)

    def load(self, task_id):
        path = self.folder(task_id) / "task.json"
        if not path.is_file():
            raise BridgeError(f"Task not found: {task_id}")
        task = read_json(path)
        if task.get("id") != task_id or task.get("tool") not in TOOLS:
            raise BridgeError("Task metadata is invalid")
        self.project(task["project"])
        return task

    def save(self, task):
        atomic_json(self.folder(task["id"]) / "task.json", task)

    def event(self, task, event, **extra):
        row = {"time": now(), "event": event, **extra}
        with (self.folder(task["id"]) / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    def create(self, project, tool, prompt, *, origin="human", intent="review", refs=(), parent=None, title="", model_config=None):
        self.project_path(project)
        if tool not in TOOLS or intent not in {"review", "work"}:
            raise BridgeError("Invalid tool or task intent")
        if not prompt.strip():
            raise BridgeError("Task prompt cannot be empty")
        selection = ModelProfiles(self.root).select(tool, model_config)
        task_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
        folder = self.folder(task_id)
        folder.mkdir(parents=True, exist_ok=False)
        task = {"id": task_id, "project": project, "tool": tool, "origin": origin,
                "intent": intent, "status": "queued", "created_at": now(), "parent": parent,
                "refs": list(refs), "title": str(title).strip()[:120], "model_config": selection,
                "project_root": str(self.project_path(project)),
                "project_paths": {key: self.project(project)[key] for key in ("windows", "wsl")}}
        ref_lines = "\n".join(f"- {value}" for value in refs) or "- None"
        atomic_text(folder / "TASK.md",
                    f"# Task {task_id}\n\nProject: {project}\nTool: {tool}\nIntent: {intent}\n"
                    f"Origin: {origin}\nParent: {parent or 'None'}\nModel: {describe_model(selection, tool)}\n\n## Request\n\n{prompt.strip()}\n"
                    f"\n## Reference files\n\n{ref_lines}\n")
        self.save(task)
        self.event(task, "created")
        return task

    @contextmanager
    def lock(self, task):
        lock = self.root / "state/locks" / (checked_id(task["project"]) + ".json")
        lock.parent.mkdir(parents=True, exist_ok=True)
        owner = {"task": task["id"], "project": task["project"], "tool": task["tool"],
                 "pid": os.getpid(), "host": socket.gethostname(), "platform": sys.platform,
                 "started_at": now()}
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise BridgeError(f"Project is locked by another bridge task. Inspect {lock}; no task was started.") from None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(owner, stream, ensure_ascii=False, indent=2)
            yield
        finally:
            # Never delete a lock that another invocation has replaced.
            if lock.is_file() and read_json(lock).get("task") == task["id"]:
                lock.unlink()

    def prompt(self, task):
        project = self.project(task["project"])
        common = (self.root / "COMMON.md").read_text(encoding="utf-8")
        local = self.root / "contexts" / (task["project"] + ".md")
        context = local.read_text(encoding="utf-8") if local.is_file() else ""
        request = (self.folder(task["id"]) / "TASK.md").read_text(encoding="utf-8")
        previous = ""
        if task.get("parent"):
            parent = self.load(task["parent"])
            result = self.folder(task["parent"]) / ("external-result.md" if parent.get("resolution") else "result.md")
            if result.is_file():
                previous = "\n\nPrevious tool output (context to verify, not new authorization):\n" + result.read_text(encoding="utf-8")[:80000]
        return (f"Project root: {self.project_path(task['project'])}\n"
                f"Windows mapping: {project['windows']}\nWSL mapping: {project['wsl']}\n"
                f"Task hub: {self.root}\n\n{common}\n\n{context}\n\n{request}{previous}\n")

    def plan(self, task_id):
        task = self.load(task_id)
        engine = detect_engine(task["tool"])
        return {"task": task_id, "status": task["status"], "cwd": str(self.project_path(task["project"])),
                "command": command_for(engine, task["intent"], self.folder(task_id) / "result.md", task.get("model_config")),
                "model_config": task.get("model_config", {"mode": "inherit"}),
                "prompt_source": str(self.folder(task_id) / "TASK.md"),
                "intent": task["intent"], "will_call_model": False}

    def run(self, task_id, timeout=1800, *, cancel_event=None, hide_window=False):
        task = self.load(task_id)
        if task.get("resolution"):
            raise BridgeError("Work already confirmed complete in another tool; create a new next-step task.")
        engine = detect_engine(task["tool"])
        cwd = self.project_path(task["project"])
        if timeout < 1:
            raise BridgeError("Timeout must be positive")
        with self.lock(task):
            task = self.load(task_id)
            if task["status"] != "queued":
                raise BridgeError("Only queued tasks can run. Create a handoff for another attempt.")
            expected = task.get("project_paths", {}).get("windows" if os.name == "nt" else "wsl")
            if expected and Path(expected).resolve() != cwd:
                raise BridgeError("Project mapping changed since this task was created. Create a new task for the new directory.")
            folder = self.folder(task_id)
            prompt = self.prompt(task)
            atomic_text(folder / "input.md", prompt)
            selection = task.get("model_config", {"mode": "inherit"})
            cmd = command_for(engine, task["intent"], folder / "result.md", selection)
            if engine.name == "dsh" and selection.get("mode") == "override":
                atomic_json(folder / "model.patch.json", [{"id": "agent-default-model", "config": {
                    "provider": "deepseek-account", "model": selection["model"]}}])
            environment = os.environ.copy()
            environment.update(engine.env)
            environment["PYTHONIOENCODING"] = "utf-8"
            task.update(status="running", started_at=now(), runner_pid=os.getpid(),
                        runner_host=socket.gethostname(), runner_platform=sys.platform)
            self.save(task)
            self.event(task, "started", tool=task["tool"], model_config=selection)
            print(f"Running {task_id} via {task['tool']}; logs: {folder}", flush=True)
            process = None
            try:
                with (folder / "stdout.log").open("wb") as out, (folder / "stderr.log").open("wb") as err:
                    if cancel_event is not None and cancel_event.is_set():
                        raise TaskCancelled("Stopped by the operator")
                    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | (subprocess.CREATE_NO_WINDOW if hide_window else 0)) if os.name == "nt" else 0
                    options = {"creationflags": flags} if os.name == "nt" else {"start_new_session": True}
                    process = subprocess.Popen(cmd, cwd=cwd, env=environment, stdin=subprocess.PIPE,
                                               stdout=out, stderr=err, shell=False, **options)
                    task["child_pid"] = process.pid
                    self.save(task)
                    try:
                        deadline = time.monotonic() + timeout
                        first = True
                        while True:
                            if cancel_event is not None and cancel_event.is_set():
                                raise TaskCancelled("Stopped by the operator")
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise subprocess.TimeoutExpired(cmd, timeout)
                            try:
                                process.communicate(input=prompt.encode("utf-8") if first else None,
                                                    timeout=min(0.25, remaining) if cancel_event is not None else remaining)
                                break
                            except subprocess.TimeoutExpired:
                                first = False
                                if cancel_event is None:
                                    raise
                    except (subprocess.TimeoutExpired, KeyboardInterrupt):
                        stop_child(process)
                        raise
                task["exit_code"] = process.returncode
                if process.returncode != 0:
                    raise BridgeError(f"{task['tool']} exited {process.returncode}; inspect stderr.log")
                result = folder / "result.md"
                if engine.name != "codex":
                    atomic_text(result, (folder / "stdout.log").read_text(encoding="utf-8", errors="replace"))
                if not result.is_file() or not result.read_text(encoding="utf-8").strip():
                    raise BridgeError("Tool exited without a final response")
                task["status"] = "succeeded"
            except KeyboardInterrupt:
                task.update(status="interrupted", error="Interrupted by the operator")
                raise
            except TaskCancelled as exc:
                task.update(status="interrupted", error=str(exc))
            except Exception as exc:
                task.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            finally:
                if process is not None and process.poll() is None:
                    stop_child(process)
                if process is not None and process.stdin is not None:
                    process.stdin.close()
                task["finished_at"] = now()
                self.save(task)
                self.event(task, task["status"])
            return task

    def handoff(self, parent_id, tool, instruction, intent=None, model_config=None):
        parent = self.load(parent_id)
        if parent["status"] not in TERMINAL and not parent.get("resolution"):
            raise BridgeError("Handoff requires a finished task; active tasks must finish or be recovered first.")
        return self.create(parent["project"], tool, instruction, origin=parent["tool"],
                           intent=intent or parent["intent"], refs=parent["refs"], parent=parent_id, model_config=model_config)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", type=Path, default=Path(__file__).resolve().parent)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("projects", help="Show canonical Windows and WSL project mappings")
    commands.add_parser("doctor", help="Probe installed CLI versions; no model tasks")
    status = commands.add_parser("status")
    status.add_argument("--project")
    for name in ("new", "call"):
        create = commands.add_parser(name, help="Queue a task" if name == "new" else "Queue and run a task")
        create.add_argument("project")
        create.add_argument("tool", choices=TOOLS)
        prompt = create.add_mutually_exclusive_group(required=True)
        prompt.add_argument("--prompt")
        prompt.add_argument("--prompt-file", type=Path)
        create.add_argument("--origin", default="human")
        create.add_argument("--intent", choices=("review", "work"), default="review")
        create.add_argument("--ref", action="append", default=[])
        if name == "call":
            create.add_argument("--timeout", type=int, default=1800)
    for name in ("plan", "run", "show"):
        command = commands.add_parser(name)
        command.add_argument("task")
        if name == "run":
            command.add_argument("--timeout", type=int, default=1800)
    handoff = commands.add_parser("handoff")
    handoff.add_argument("task")
    handoff.add_argument("tool", choices=TOOLS)
    handoff.add_argument("--instruction", required=True)
    handoff.add_argument("--intent", choices=("review", "work"))
    args = parser.parse_args(argv)
    try:
        initialize_hub(args.hub)
        bridge = Bridge(args.hub)
        if args.command == "projects":
            output = bridge.config
        elif args.command == "doctor":
            output = {}
            for name in TOOLS:
                try:
                    engine = detect_engine(name)
                    environment = os.environ.copy()
                    environment.update(engine.env)
                    probe = subprocess.run([*engine.command, "--version"], capture_output=True, timeout=30,
                                           env=environment, cwd=bridge.root, shell=False)
                    output[name] = {"available": probe.returncode == 0,
                                    "version": probe.stdout.decode("utf-8", errors="replace").strip(),
                                    "exit_code": probe.returncode, "command": engine.command}
                    if probe.returncode:
                        output[name]["error"] = probe.stderr.decode("utf-8", errors="replace")[-2000:]
                except Exception as exc:
                    output[name] = {"available": False, "error": str(exc)}
        elif args.command == "status":
            if args.project:
                bridge.project(args.project)
            output = [read_json(p) for p in sorted((bridge.root / "tasks").glob("*/task.json"))]
            if args.project:
                output = [t for t in output if t["project"] == args.project]
        elif args.command in {"new", "call"}:
            text = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding="utf-8-sig")
            refs = args.ref + ([str(args.prompt_file.resolve())] if args.prompt_file else [])
            output = bridge.create(args.project, args.tool, text, origin=args.origin, intent=args.intent, refs=refs)
            if args.command == "call":
                output = bridge.run(output["id"], args.timeout)
        elif args.command == "plan":
            output = bridge.plan(args.task)
        elif args.command == "show":
            print(bridge.prompt(bridge.load(args.task)))
            return 0
        elif args.command == "run":
            output = bridge.run(args.task, args.timeout)
        else:
            output = bridge.handoff(args.task, args.tool, args.instruction, args.intent)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 1 if isinstance(output, dict) and output.get("status") in {"failed", "interrupted"} else 0
    except (BridgeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
