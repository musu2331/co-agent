"""Loopback-only Web UI for the existing local project/task bridge."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PureWindowsPath
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlparse
from urllib.request import urlopen
import webbrowser

from bridge import Bridge, BridgeError, TOOLS, TERMINAL, atomic_json, atomic_text, checked_id, detect_engine, dsh_account_config_digest, now, read_json
from sources import Catalog
from maintenance import Maintenance
from model_config import ModelProfiles, describe as describe_model
from setup_workspace import initialize_hub
from workflow import preferences, save_preferences, card as report_card, edit_card, clean_title, BLOCKERS

SERVICE = "agent-bridge-webui"
ROOT = Path(__file__).resolve().parent
MAX_BODY = 1024 * 1024


def request_text(text):
    if "## Request\n\n" in text:
        return text.split("## Request\n\n", 1)[1].rsplit("\n## Reference files", 1)[0].strip()
    return text.strip()


def title_for(task, text):
    if task.get("title"):
        return task["title"]
    line = request_text(text).splitlines()[0] if request_text(text) else "未命名任务"
    return line.split("：", 1)[0][:48]


def text_file(path, limit=400000, tail=False):
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        size = path.stat().st_size
        if tail and size > limit:
            stream.seek(size - limit)
        raw = stream.read(limit)
    text = raw.decode("utf-8-sig", errors="replace")
    if size > limit:
        text = ("…仅显示最近日志…\n" + text) if tail else (text + "\n\n[内容较长，界面仅显示前一部分，可下载完整结果。]")
    return text


@dataclass
class Job:
    task_id: str
    project: str
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class App:
    def __init__(self, root, *, probe=True):
        self.root = Path(root).resolve()
        self.guard = threading.RLock()
        self.jobs: dict[str, Job] = {}
        self.tools = {tool: {"available": None, "checking": True, "version": ""} for tool in TOOLS}
        self.checking = False
        self.catalog = Catalog(self.root)
        self.syncing = False
        settings_path = self.root / "app-settings.json"
        settings = read_json(settings_path) if settings_path.is_file() else {}
        self.auto_sync_sources = settings.get("auto_sync_sources", True) is True
        self.sync_enabled = probe and self.auto_sync_sources
        self.last_sync = 0
        self.maintenance = Maintenance(self.root)
        self.maintenance_error = ""
        if probe:
            self.refresh_tools()
            if self.auto_sync_sources:
                self.refresh_sources()
            threading.Thread(target=self._maintenance_loop, name="bridge-weekly-maintenance", daemon=True).start()

    def bridge(self):
        return Bridge(self.root)

    def decorate(self, task):
        task["model_label"] = describe_model(task.get("model_config"), task["tool"])
        task["display_status"] = "completed_external" if task.get("resolution", {}).get("status") == "completed_external" else task["status"]
        error = text_file(self.root / "tasks" / task["id"] / "stderr.log", 5000, True)
        if "ACCOUNT_SIGN_IN_REQUIRED" in error or "ACCOUNT_TOKEN_INVALID" in error:
            task["diagnosis"] = {"kind": "account_login", "message": "DSH 官方账号需要重新登录，请在 DSH 中完成登录后再调用。"}
        elif "ACCOUNT_QUOTA" in error:
            task["diagnosis"] = {"kind": "account_quota", "message": "DSH 官方账号返回额度不足，请在原工具中检查账号状态。"}
        elif "MISSING_CREDENTIAL" in error:
            task["diagnosis"] = {"kind": "missing_credential", "message": "DSH 调用环境缺少 DeepSeek API 凭据，模型没有开始处理。这次调用失败不代表工作尚未完成。"}
        elif task.get("status") == "failed":
            task["diagnosis"] = {"kind": "call_failed", "message": "工具调用失败，请查看运行诊断；工作是否已在其他工具完成需要单独确认。"}
        return task

    def tool_states(self, tasks):
        result = {key: dict(value) for key, value in self.tools.items()}
        acknowledgements = read_json(self.root / "state/tool-acknowledgements.json") if (self.root / "state/tool-acknowledgements.json").is_file() else {}
        check_path = self.root / "state/dsh-account-check.json"
        check = read_json(check_path) if check_path.is_file() else {}
        verified = check.get("available") and check.get("configuration_sha256") == dsh_account_config_digest()
        selected = ModelProfiles(self.root).read()["dsh"]
        selected_model = selected.get("model", "deepseek-flash")
        result["dsh"].update(auth_mode="official_account", provider="deepseek-account", model=selected_model,
                             account_verified=bool(verified), account_checked_at=check.get("checked_at") if verified else None,
                             checked_model=check.get("model") if verified else None,
                             model_verified=bool(verified and check.get("model") == selected_model))
        if verified:
            acknowledgements["dsh"] = max(acknowledgements.get("dsh", ""), check["checked_at"])
            result["dsh"]["execution_ready"] = True
        for task in tasks:
            tool = task["tool"]
            if task.get("diagnosis", {}).get("kind") in {"missing_credential", "account_login", "account_quota"} and task.get("finished_at", "") > acknowledgements.get(tool, ""):
                result[tool].update(execution_ready=False, account_verified=False, execution_error=task["diagnosis"]["message"] + " 当前连接方式为 DSH 已保存的官方账号。")
        return result

    def maintenance_projects(self):
        with self.guard:
            bridge = self.bridge()
            tasks = self.task_list()
            catalog = self.catalog.snapshot()
            projects = {key: {"id": key, "label": item["label"], "source": "mapping", "native_path": item["windows" if os.name == "nt" else "wsl"]} for key, item in bridge.config["projects"].items()}
            task_ids = {task["id"] for task in tasks}
            sessions = [s for s in catalog["sessions"] if s.get("bridge_task_id") not in task_ids]
            for project in catalog["projects"]:
                if project.get("declared") or any(s["project"] == project["id"] for s in sessions):
                    projects.setdefault(project["id"], dict(project))
            for project in projects.values():
                identities = {project["id"], project.get("mapped_project")}
                pending = any(t["project"] in identities and not t.get("resolution") and t["status"] in {"queued", "starting", "running"} for t in tasks)
                open_turn = any(s.get("state") == "turn_open" and (s["project"] in identities or s.get("mapped_project") == project["id"]) for s in sessions)
                project["busy"] = pending or open_turn
            return list(projects.values())

    def maintenance_status(self):
        return {**self.maintenance.snapshot(), "projects": self.maintenance_projects(), "error": self.maintenance_error,
                "service_note": "服务运行时每周执行；服务关闭期间错过的维护会在下次启动补跑。"}

    def run_maintenance(self):
        with self.guard:
            projects = self.maintenance_projects()
            sources = self.catalog.snapshot()["sources"]
            report = self.maintenance.run(projects, sources_ok=bool(sources) and all(s["ok"] for s in sources.values()))
            self.maintenance_error = ""
            return report

    def _maintenance_loop(self):
        while True:
            try:
                if self.maintenance.due() and not self.syncing:
                    self.run_maintenance()
            except Exception as exc:
                self.maintenance_error = str(exc)
            time.sleep(60)

    def refresh_sources(self):
        with self.guard:
            if self.syncing:
                return {"syncing": True}
            self.syncing = True
        def finish():
            try:
                self.catalog.refresh(self.bridge().config["projects"])
                sources = self.catalog.snapshot()["sources"]
                try:
                    self.maintenance.update_index(self.maintenance_projects(), sources_ok=bool(sources) and all(s["ok"] for s in sources.values()))
                except (OSError, ValueError) as exc:
                    self.maintenance_error = "项目索引维护失败：" + type(exc).__name__
            finally:
                with self.guard:
                    self.syncing = False
                    self.last_sync = time.monotonic()
        threading.Thread(target=finish, name="bridge-source-sync", daemon=True).start()
        return {"syncing": True}

    def ensure_project(self, project_id):
        if project_id in self.bridge().config["projects"]:
            return project_id
        project = next((p for p in self.catalog.snapshot()["projects"] if p["id"] == project_id), None)
        if not project or not project.get("exists"):
            raise BridgeError("会话目录已不存在，请在项目映射中指定现有工作文件夹。")
        for key, item in self.bridge().config["projects"].items():
            if Path(item["windows" if os.name == "nt" else "wsl"]).resolve() == Path(project["native_path"]).resolve():
                return key
        self.save_project({"id": project_id, "label": project["label"], "path": project["native_path"]})
        return project_id

    def task_list(self):
        bridge = self.bridge()
        tasks = []
        for path in sorted((self.root / "tasks").glob("*/task.json"), reverse=True):
            try:
                task = bridge.load(path.parent.name)
                source = text_file(path.parent / "TASK.md")
                task["title"] = title_for(task, source)
                task["preview"] = request_text(source)[:160]
                task["managed"] = task["id"] in self.jobs
                task["stopping"] = task["managed"] and self.jobs[task["id"]].cancel.is_set()
                if task["status"] == "queued" and task["managed"]:
                    task["status"] = "starting"
                tasks.append(self.decorate(task))
            except (OSError, ValueError, BridgeError):
                continue
        return tasks

    def bootstrap(self):
        if self.sync_enabled and time.monotonic() - self.last_sync > 12:
            self.refresh_sources()
        with self.guard:
            bridge = self.bridge()
            tasks = self.task_list()
            projects = []
            for key, item in bridge.config["projects"].items():
                relevant = [task for task in tasks if task["project"] == key]
                locked = (self.root / "state/locks" / (key + ".json")).is_file()
                projects.append({"id": key, **item,
                                 "registered": True,
                                 "native_path": item["windows" if os.name == "nt" else "wsl"],
                                 "exists": Path(item["windows" if os.name == "nt" else "wsl"]).is_dir(),
                                 "task_count": len(relevant),
                                 "busy": locked or any(t["status"] in {"running", "starting"} for t in relevant)})
            catalog = self.catalog.snapshot()
            task_ids = {task["id"] for task in tasks}
            merged = [s for s in catalog["sessions"] if s.get("bridge_task_id") in task_ids]
            catalog["sessions"] = [s for s in catalog["sessions"] if s.get("bridge_task_id") not in task_ids]
            catalog["sources"] = {name: {**info, "merged_count": sum(s["source"] == name for s in merged),
                                         "visible_count": sum(s["source"] == name for s in catalog["sessions"])} for name, info in catalog["sources"].items()}
            catalog["projects"] = [{**group, "session_count": sum(s["project"] == group["id"] for s in catalog["sessions"])} for group in catalog["projects"]]
            catalog["projects"] = [group for group in catalog["projects"] if group.get("declared") or group["session_count"]]
            for group in catalog["projects"]:
                registered = next((p for p in projects if p["id"] == group["id"]), None)
                if registered:
                    registered.update(synced=True, source=group["source"], session_count=group["session_count"])
                else:
                    projects.append({**group, "task_count": 0, "busy": False})
            hidden = self.maintenance.hidden_ids()
            projects = [p for p in projects if p["id"] not in hidden and p.get("mapped_project") not in hidden]
            tasks = [t for t in tasks if t["project"] not in hidden]
            catalog["sessions"] = [s for s in catalog["sessions"] if s["project"] not in hidden and s.get("mapped_project") not in hidden]
            return {"projects": projects, "tasks": tasks, "tools": self.tool_states(self.task_list()),
                    "sessions": catalog["sessions"], "sources": catalog["sources"], "synced_at": catalog["updated_at"], "syncing": self.syncing,
                    "hub": str(self.root), "server_time": now(), "checking": self.checking,
                    "sources_cached": any(s.get("cached") for s in catalog["sessions"]),
                    "model_profiles": ModelProfiles(self.root).read(), "auto_sync_sources": self.auto_sync_sources,
                    "workflows": preferences(self.root), "blocker_categories": BLOCKERS}

    def source_settings(self, enabled):
        if not isinstance(enabled, bool):
            raise BridgeError("自动同步开关需要是布尔值。")
        with self.guard:
            path = self.root / "app-settings.json"
            settings = read_json(path) if path.is_file() else {}
            settings["auto_sync_sources"] = enabled
            atomic_json(path, settings)
            self.auto_sync_sources = enabled
            self.sync_enabled = enabled
        if enabled:
            self.refresh_sources()
        return {"auto_sync_sources": enabled}

    def detail(self, task_id):
        with self.guard:
            bridge = self.bridge()
            task = bridge.load(task_id)
            folder = bridge.folder(task_id)
            source = text_file(folder / "TASK.md")
            task["title"] = title_for(task, source)
            task["managed"] = task_id in self.jobs
            task["stopping"] = task["managed"] and self.jobs[task_id].cancel.is_set()
            if task["status"] == "queued" and task["managed"]:
                task["status"] = "starting"
            stage = bridge.stage(task, request_text(source))
            return {"task": self.decorate(task), "request": request_text(source),
                    "handoff_stage": stage,
                    "handoff_card": report_card({**task, "contract": stage}, request_text(source), text_file(folder / ("external-result.md" if task.get("resolution") else "result.md"))),
                    "result": text_file(folder / "result.md"),
                    "external_result": text_file(folder / "external-result.md"),
                    "stdout": text_file(folder / "stdout.log", 64000, True),
                    "stderr": text_file(folder / "stderr.log", 64000, True)}

    def create(self, body, *, handoff_input=None, handoff_mode=None):
        if "run" in body and not isinstance(body["run"], bool):
            raise BridgeError("执行选项格式不正确。")
        prompt = body.get("prompt", "")
        title = body.get("title", "")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 300000:
            raise BridgeError("请填写任务说明（最多 30 万字符）。")
        if not isinstance(title, str) or len(title) > 120:
            raise BridgeError("任务标题最多 120 个字符。")
        refs = body.get("refs", [])
        if not isinstance(refs, list) or len(refs) > 32 or any(not isinstance(r, str) or len(r) > 2000 for r in refs):
            raise BridgeError("引用文件列表格式不正确。")
        with self.guard:
            if body.get("project", "") in self.maintenance.hidden_ids():
                raise BridgeError("这个项目已标记废弃，请先在后台维护中恢复显示。")
            project_key = self.ensure_project(body.get("project", ""))
            chosen_tool = body.get("tool") or preferences(self.root).get(project_key, {}).get("primary_tool") or "codex"
            task = self.bridge().create(project_key, chosen_tool, prompt,
                                        title=title, intent=body.get("intent", "review"),
                                        origin="webui", refs=refs, model_config=body.get("model_config"),
                                        handoff_input=handoff_input, handoff_mode=handoff_mode)
            if body.get("run"):
                try:
                    self.start(task["id"])
                except BridgeError as exc:
                    return {"task": task, "notice": f"任务已保存为待办：{exc}"}
            return {"task": task}

    def start(self, task_id):
        with self.guard:
            bridge = self.bridge()
            task = bridge.load(task_id)
            if task.get("resolution"):
                raise BridgeError("这项工作已确认在其他工具完成，不能重复执行。可按新要求转交下一步。")
            if task["status"] != "queued" or task_id in self.jobs:
                raise BridgeError("这个任务已经开始或结束，不能重复执行。")
            if any(job.project == task["project"] for job in self.jobs.values()):
                raise BridgeError("这个项目已有任务正在执行，请等待完成后再开始。")
            if (self.root / "state/locks" / (task["project"] + ".json")).exists():
                raise BridgeError("这个项目正由另一个桥接任务使用，请稍后再试。")
            detect_engine(task["tool"])
            problem = self.tool_states(self.task_list())[task["tool"]].get("execution_error")
            if problem:
                raise BridgeError(problem)
            job = Job(task_id, task["project"])
            self.jobs[task_id] = job
            job.thread = threading.Thread(target=self._run, args=(job,), name="bridge-" + task_id, daemon=True)
            job.thread.start()
            return {"accepted": True, "task_id": task_id}

    def _run(self, job):
        try:
            self.bridge().run(job.task_id, cancel_event=job.cancel, hide_window=True)
        except Exception as exc:
            with self.guard:
                bridge = self.bridge()
                task = bridge.load(job.task_id)
                if task["status"] in {"queued", "running"}:
                    task.update(status="failed", error=str(exc), finished_at=now())
                    bridge.save(task)
                    bridge.event(task, "failed", error=str(exc))
        finally:
            with self.guard:
                self.jobs.pop(job.task_id, None)

    def stop(self, task_id):
        with self.guard:
            job = self.jobs.get(task_id)
            if job:
                job.cancel.set()
                return {"stopping": True}
            bridge = self.bridge()
            task = bridge.load(task_id)
            if task["status"] != "queued":
                raise BridgeError("这个任务不在本界面运行，无法从这里停止。")
            task.update(status="interrupted", error="Cancelled before starting", finished_at=now())
            bridge.save(task)
            bridge.event(task, "interrupted")
            return {"stopping": False}

    def handoff(self, task_id, body, retry=False):
        with self.guard:
            bridge = self.bridge()
            parent = bridge.load(task_id)
            if retry and parent.get("resolution"):
                raise BridgeError("已在其他工具完成的工作不再重新排队。请查看关联会话或转交新的下一步。")
            if "run" in body and not isinstance(body["run"], bool):
                raise BridgeError("执行选项格式不正确。")
            instruction = body.get("instruction", "")
            if not isinstance(instruction, str):
                raise BridgeError("转交说明需要是文本。")
            instruction = instruction.strip()
            if retry:
                instruction = request_text(text_file(bridge.folder(task_id) / "TASK.md"))
            if not instruction or len(instruction) > 300000:
                raise BridgeError("请填写转交说明。")
            child = bridge.handoff(task_id, parent["tool"] if retry else body.get("tool", ""),
                                   instruction, body.get("intent"),
                                   model_config=parent.get("model_config", {"mode": "inherit"}) if retry else body.get("model_config"),
                                   mode=parent.get("handoff_mode", "switch") if retry else body.get("handoff_mode"),
                                   handoff_card=None if retry else body.get("handoff_card"))
            prefix = "重试" if retry else "审查" if child.get("handoff_mode") == "review" else "接续"
            child["title"] = prefix + " · " + clean_title(title_for(parent, text_file(bridge.folder(task_id) / "TASK.md")))
            if retry:
                child["retry_of"] = task_id
            bridge.save(child)
            if body.get("run"):
                try:
                    self.start(child["id"])
                except BridgeError as exc:
                    return {"task": child, "notice": f"已保存为待办：{exc}"}
            return {"task": child}

    def resolve(self, task_id, body):
        key = body.get("session_key", "")
        note = body.get("note", "")
        if not isinstance(note, str) or not note.strip() or len(note) > 2000:
            raise BridgeError("请填写确认依据，最多 2000 字。")
        with self.guard:
            bridge = self.bridge()
            target = bridge.load(task_id)
            external = self.catalog.evidence(key, target.get("refs", []))
            if external.get("cached"):
                raise BridgeError("来源正文尚未恢复，请等待后台同步完成后再关联结果。")
            related = [target]
            if body.get("related", False):
                tasks = [bridge.load(t["id"]) for t in self.task_list()]
                root_id, seen = target["id"], set()
                by_id = {t["id"]: t for t in tasks}
                def is_retry(task):
                    return bool(task.get("retry_of") or task.get("title", "").startswith("重试 · "))
                while by_id[root_id].get("parent") in by_id and root_id not in seen and is_retry(by_id[root_id]):
                    seen.add(root_id)
                    root_id = by_id[root_id]["parent"]
                family = {root_id}
                for _ in range(len(tasks)):
                    family.update(t["id"] for t in tasks if t.get("parent") in family and t["project"] == target["project"] and is_retry(t))
                related = [t for t in tasks if t["id"] in family and t["project"] == target["project"]]
            if any(t["status"] == "running" or t["id"] in self.jobs for t in related):
                raise BridgeError("关联工作仍在本台执行，请先停止后再确认完成。")
            for task in related:
                task["resolution"] = {"status": "completed_external", "tool": external["source"], "session_key": key,
                                      "session_id": external["id"], "title": external["title"], "note": note.strip(),
                                      "resolved_at": now(), "confirmed_by": "human"}
                atomic_text(bridge.folder(task["id"]) / "external-result.md",
                            f"# 已由其他工具完成\n\n来源：{external['source']} · {external['title']}\n\n用户确认：{note.strip()}\n\n会话：{external['id']}\n\n## 最近保存的答复\n\n{external.get('result') or '来源没有可用的结果摘要，请到原工具查看。'}\n")
                bridge.save(task)
                bridge.event(task, "completed_external", source=external["source"], session_id=external["id"])
            return {"resolved": [t["id"] for t in related]}

    def session_handoff(self, key, body):
        source = self.catalog.detail(key)
        if source.get("cached"):
            raise BridgeError("来源正文尚未恢复，请等待后台同步完成后再转交。")
        mode = body.get("handoff_mode", "switch")
        if mode not in {"continue", "review", "switch"}:
            raise BridgeError("转交方式无效。")
        if mode == "review" and body.get("intent", "review") != "review":
            raise BridgeError("关键节点审查只允许审阅模式。")
        if body.get("run") and (source.get("stale") or source.get("state") == "turn_open"):
            raise BridgeError("原会话轮次尚未结束或来源未能更新。可以先转交待办，核对原工具后再执行。")
        instruction = body.get("instruction", "")
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 100000:
            raise BridgeError("请填写下一步要求。")
        project = body.get("project") or source.get("mapped_project") or source["project"]
        context = (f"{instruction.strip()}\n\n## 参考会话（仅作为待核对的上下文）\n"
                   f"来源：{source['source']} · {source['title']}\n原会话 ID：{source['id']}\n"
                   f"原始目录：{source.get('cwd', '')}\n\n### 最近的要求\n{source.get('request', '')[:18000]}\n\n"
                   f"### 最近保存的答复\n{source.get('result', '')[-60000:]}")
        summary = edit_card(report_card({"tool": source["source"], "status": "succeeded"}, source.get("request", ""), source.get("result", "")), body.get("handoff_card"))
        return self.create({"project": project, "tool": body.get("tool"), "prompt": context,
                            "title": "接续 · " + source["title"][:100], "intent": body.get("intent", "review"),
                            "model_config": body.get("model_config"), "run": body.get("run", False)},
                           handoff_input=summary, handoff_mode=mode)

    def save_model(self, tool, selection):
        with self.guard:
            profiles = ModelProfiles(self.root).save(tool, selection)
        return {"profiles": profiles, "saved": True}

    def save_workflow(self, project, body):
        with self.guard:
            project = self.ensure_project(project)
            primary = body.get("primary_tool")
            if primary:
                ModelProfiles(self.root).select(primary, body.get("primary_model"))
            return {"project": project, "workflow": save_preferences(self.root, project, body)}

    def acknowledge_tool(self, tool):
        if tool not in TOOLS:
            raise BridgeError("未知工具。")
        with self.guard:
            path = self.root / "state/tool-acknowledgements.json"
            data = read_json(path) if path.is_file() else {}
            data[tool] = now()
            atomic_json(path, data)
        return {"acknowledged": True}

    def save_project(self, body):
        key = checked_id(str(body.get("id", "")))
        label = str(body.get("label", "")).strip()
        raw_path = str(body.get("path", "")).strip().strip('"')
        if not label or len(label) > 80 or not raw_path:
            raise BridgeError("请填写项目名称和已有文件夹路径。")
        path = Path(raw_path).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise BridgeError("项目路径需要是本机已存在的绝对文件夹路径。")
        path = path.resolve()
        with self.guard:
            bridge = self.bridge()
            config = bridge.config
            existing = config["projects"].get(key)
            if any(t["project"] == key and t["status"] in {"queued", "running", "starting"} for t in self.task_list()):
                current = existing.get("windows" if os.name == "nt" else "wsl") if existing else None
                if current and Path(current).resolve() != path:
                    raise BridgeError("项目还有待办或运行中的任务，暂不能更换目录；可以修改显示名称。")
            for other_key, other in config["projects"].items():
                if other_key != key and Path(other["windows" if os.name == "nt" else "wsl"]).resolve() == path:
                    raise BridgeError("这个文件夹已经登记在另一个项目中。")
            item = dict(existing or {})
            item["label"] = label
            if os.name == "nt":
                win = PureWindowsPath(str(path))
                if not win.drive or len(win.drive) != 2:
                    raise BridgeError("目前项目映射支持本地盘符路径。")
                item.update(windows=str(path), wsl="/mnt/" + win.drive[0].lower() + "/" + "/".join(win.parts[1:]))
            else:
                if not existing:
                    raise BridgeError("请先在 Windows 入口登记项目的双系统路径。")
                item["wsl"] = str(path)
            config["projects"][key] = item
            atomic_json(self.root / "projects.json", config)
            return {"project": {"id": key, **item}}

    def open_project(self, project):
        project = self.ensure_project(project)
        path = self.bridge().project_path(project)
        if os.name == "nt":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return {"opened": True}

    def refresh_tools(self):
        with self.guard:
            if self.checking:
                return {"checking": True}
            self.checking = True
            self.tools = {name: {**state, "checking": True} for name, state in self.tools.items()}
        def probe_one(name):
            try:
                engine = detect_engine(name)
                environment = os.environ.copy()
                environment.update(engine.env)
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
                process = subprocess.run([*engine.command, "--version"], cwd=self.root, env=environment,
                                         capture_output=True, timeout=20, shell=False, **options)
                return name, {"available": process.returncode == 0, "checking": False,
                              "executable": engine.command[0],
                              "version": process.stdout.decode("utf-8", errors="replace").strip()[:200],
                              "error": process.stderr.decode("utf-8", errors="replace")[-1500:] if process.returncode else ""}
            except Exception as exc:
                return name, {"available": False, "checking": False, "version": "", "error": str(exc)}
        def finish():
            try:
                with ThreadPoolExecutor(max_workers=3) as executor:
                    for future in as_completed([executor.submit(probe_one, name) for name in TOOLS]):
                        name, result = future.result()
                        with self.guard:
                            self.tools[name] = result
            finally:
                with self.guard:
                    self.checking = False
        threading.Thread(target=finish, name="bridge-doctor", daemon=True).start()
        return {"checking": True}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, app):
        super().__init__(address, Handler)
        self.app = app
        self.token = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    server_version = "ProjectBridge"
    sys_version = ""

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith("GET /api/"):
            return
        print(f"[{now()}] {fmt % args}", flush=True)

    def _send(self, status, payload, content_type="application/json; charset=utf-8", extra=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if isinstance(payload, (dict, list)) else payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _gate(self, *, api=True):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in hosts:
            self._send(403, {"error": "仅接受本机地址访问。"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in hosts}:
            self._send(403, {"error": "请求来源不匹配。"})
            return False
        if api and not secrets.compare_digest(self.headers.get("X-Bridge-Token", "").encode("utf-8"), self.server.token.encode("ascii")):
            self._send(403, {"error": "页面会话已更新，请刷新页面后重试。"})
            return False
        return True

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if not self._gate(api=path.startswith("/api/") and path != "/api/health"):
            return
        try:
            if path == "/api/health":
                self._send(200, {"service": SERVICE, "version": 1, "pid": os.getpid()})
            elif path == "/api/bootstrap":
                self._send(200, self.server.app.bootstrap())
            elif path == "/api/maintenance":
                self._send(200, self.server.app.maintenance_status())
            elif path == "/api/models":
                self._send(200, ModelProfiles(self.server.app.root).catalog())
            elif path.startswith("/api/sessions/"):
                pieces = path.strip("/").split("/")
                if len(pieces) != 3:
                    raise BridgeError("会话请求格式不正确。")
                self._send(200, self.server.app.catalog.detail(checked_id(pieces[2])))
            elif path.startswith("/api/tasks/"):
                pieces = path.strip("/").split("/")
                task_id = checked_id(pieces[2])
                if len(pieces) == 3:
                    self._send(200, self.server.app.detail(task_id))
                elif len(pieces) == 4 and pieces[3] == "download":
                    bridge = self.server.app.bridge()
                    bridge.load(task_id)
                    result = bridge.folder(task_id) / "result.md"
                    if not result.is_file():
                        raise BridgeError("这个任务还没有可下载的结果。")
                    if result.stat().st_size > 8 * 1024 * 1024:
                        raise BridgeError("结果文件较大，请从项目任务目录打开。")
                    self._send(200, result.read_bytes(), "text/markdown; charset=utf-8",
                               {"Content-Disposition": f'attachment; filename="{task_id}.md"'})
                else:
                    self._send(404, {"error": "接口不存在。"})
            else:
                assets = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8"),
                          "/favicon.svg": ("favicon.svg", "image/svg+xml")}
                if path not in assets:
                    self._send(404, {"error": "页面不存在。"})
                    return
                filename, kind = assets[path]
                content = (ROOT / "web" / filename).read_bytes()
                if filename == "index.html":
                    content = content.replace(b"__BRIDGE_TOKEN__", self.server.token.encode("ascii"))
                self._send(200, content, kind)
        except (BridgeError, OSError, ValueError) as exc:
            self._send(400, {"error": str(exc)})

    def do_POST(self):
        if not self._gate():
            return
        try:
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                self._send(415, {"error": "请求需要使用 JSON。"})
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > MAX_BODY:
                self._send(413, {"error": "请求内容过大。"})
                return
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise BridgeError("请求格式不正确。")
            path = unquote(urlparse(self.path).path)
            app = self.server.app
            if path == "/api/tasks":
                result = app.create(body)
            elif path.startswith("/api/models/"):
                pieces = path.strip("/").split("/")
                if len(pieces) != 3:
                    raise BridgeError("模型配置请求格式不正确。")
                result = app.save_model(pieces[2], body)
            elif path == "/api/maintenance/settings":
                result = app.maintenance.configure(body.get("enabled"))
            elif path == "/api/maintenance/run":
                result = app.run_maintenance()
            elif path.startswith("/api/maintenance/projects/"):
                pieces = path.strip("/").split("/")
                if len(pieces) != 4:
                    raise BridgeError("维护项目请求格式不正确。")
                result = app.maintenance.mark(checked_id(pieces[3]), body.get("discarded"), app.maintenance_projects())
            elif path == "/api/tools/check":
                result = app.refresh_tools()
            elif path == "/api/sources/refresh":
                result = app.refresh_sources()
            elif path == "/api/sources/settings":
                result = app.source_settings(body.get("enabled"))
            elif path.startswith("/api/tools/") and path.endswith("/acknowledge"):
                pieces = path.strip("/").split("/")
                if len(pieces) != 4:
                    raise BridgeError("工具请求格式不正确。")
                result = app.acknowledge_tool(pieces[2])
            elif path.startswith("/api/sessions/") and path.endswith("/handoff"):
                pieces = path.strip("/").split("/")
                if len(pieces) != 4:
                    raise BridgeError("会话请求格式不正确。")
                result = app.session_handoff(checked_id(pieces[2]), body)
            elif path == "/api/projects":
                result = app.save_project(body)
            elif path.startswith("/api/workflows/"):
                pieces = path.strip("/").split("/")
                if len(pieces) != 3:
                    raise BridgeError("协作配置请求格式不正确。")
                result = app.save_workflow(checked_id(pieces[2]), body)
            elif path.startswith("/api/projects/") and path.endswith("/open"):
                pieces = path.strip("/").split("/")
                if len(pieces) != 4:
                    raise BridgeError("项目请求格式不正确。")
                result = app.open_project(checked_id(pieces[2]))
            elif path.startswith("/api/tasks/"):
                pieces = path.strip("/").split("/")
                if len(pieces) != 4:
                    raise BridgeError("任务请求格式不正确。")
                task_id = checked_id(pieces[2])
                action = pieces[3]
                if action == "run":
                    result = app.start(task_id)
                elif action == "stop":
                    result = app.stop(task_id)
                elif action in {"handoff", "retry"}:
                    result = app.handoff(task_id, body, retry=action == "retry")
                elif action == "resolve":
                    result = app.resolve(task_id, body)
                else:
                    self._send(404, {"error": "操作不存在。"})
                    return
            else:
                self._send(404, {"error": "接口不存在。"})
                return
            self._send(200, result)
        except (BridgeError, OSError, ValueError, TypeError, KeyError) as exc:
            self._send(400, {"error": str(exc)})


def health(port, root=None):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.6) as response:
            data = json.load(response)
        if data.get("service") != SERVICE:
            return False
        if root is not None:
            state = Path(root).resolve() / "state/webui.json"
            if not state.is_file():
                return False
            recorded = read_json(state)
            return recorded.get("port") == port and recorded.get("pid") == data.get("pid")
        return True
    except Exception:
        return False


def launch(root, port, open_browser=True):
    root = Path(root).resolve()
    state = root / "state/webui.json"
    candidates = [port]
    if state.is_file():
        try:
            previous = int(read_json(state).get("port", port))
            if 1024 <= previous <= 65535:
                candidates.insert(0, previous)
        except (ValueError, OSError):
            pass
    for candidate in dict.fromkeys(candidates):
        if health(candidate, root):
            url = f"http://127.0.0.1:{candidate}"
            if open_browser:
                webbrowser.open(url)
            return url
    for candidate in range(port, min(port + 10, 65536)):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
                port = candidate
                break
            except OSError:
                continue
    else:
        raise BridgeError("本地端口暂不可用，请关闭占用程序后重试。")
    state.parent.mkdir(parents=True, exist_ok=True)
    executable = Path(sys.executable)
    if os.name == "nt" and executable.with_name("pythonw.exe").is_file():
        executable = executable.with_name("pythonw.exe")
    with (root / "state/webui.log").open("ab") as log:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen([str(executable), "-B", str(ROOT / "webui.py"), "--serve", "--hub", str(root), "--port", str(port)],
                                   cwd=root, stdout=log, stderr=log, stdin=subprocess.DEVNULL, **options)
    for _ in range(80):
        if health(port, root):
            url = f"http://127.0.0.1:{port}"
            if open_browser:
                webbrowser.open(url)
            return url
        if process.poll() is not None:
            break
        time.sleep(0.1)
    raise BridgeError("协作台未能启动，请查看 agent-bridge/state/webui.log。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", type=Path, default=ROOT)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise BridgeError("端口应在 1024–65535 之间。")
    initialize_hub(args.hub)
    if args.launch or not args.serve:
        url = launch(args.hub, args.port, not args.no_browser)
        if sys.stdout:
            print(url)
        return
    app = App(args.hub)
    server = Server(("127.0.0.1", args.port), app)
    atomic_json(args.hub / "state/webui.json", {"port": args.port, "pid": os.getpid(), "started_at": now(), "service": SERVICE})
    print(f"Project Bridge: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        for job in list(app.jobs.values()):
            job.cancel.set()
        for job in list(app.jobs.values()):
            if job.thread:
                job.thread.join(timeout=8)
        server.server_close()


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except Exception as exc:
        if sys.stderr:
            print(str(exc), file=sys.stderr)
        elif os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), "项目协作台 · 启动失败", 0x10)
        raise SystemExit(1)
