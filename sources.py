"""Read-only adapters for existing local tool projects and saved conversations.

No credentials, tool settings, session databases or original sessions are written.
Saved turn boundaries describe records, not proof that a process is still alive.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
import ntpath
import os
import re
from pathlib import Path
import sqlite3
import threading

from bridge import BridgeError, now, read_json


def key_for(source, identity):
    return source + "-" + hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:20]


def stamp(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / (1000 if value > 1e11 else 1), timezone.utc).isoformat()
    return value or ""


def message_text(content):
    if isinstance(content, str):
        return content
    return "\n".join(x.get("text", "") for x in content or [] if isinstance(x, dict) and x.get("type") in {"text", "output_text", "input_text"})


def json_lines(path, tail=None):
    with path.open("rb") as stream:
        if tail and path.stat().st_size > tail:
            stream.seek(-tail, 2)
            stream.readline()  # Discard the incomplete first record.
        for line in stream:
            try:
                yield json.loads(line)
            except (ValueError, UnicodeError):
                continue  # The owning tool may still be appending a record.


class Catalog:
    def __init__(self, root, home=None):
        self.root = Path(root)
        self.home = Path(home) if home else Path.home()
        self.guard = threading.RLock()
        self.cache = {}
        self.boundaries = {}
        self.projects = []
        self.sessions = {}
        self.health = {}
        self.updated_at = ""

    def _cached(self, path, parser):
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        entry = self.cache.get(str(path))
        if not entry or entry[0] != signature:
            if entry and stat.st_size <= entry[0][1]:
                self.boundaries.pop(str(path), None)
            entry = (signature, parser(path))
            self.cache[str(path)] = entry
        return dict(entry[1]) if entry[1] else None

    def canonical(self, raw):
        """Resolve existing folders; support the user's Desktop -> DeskBox move."""
        if not raw:
            return ""
        if raw.startswith("\\\\?\\"):
            raw = raw[4:]
        path = Path(raw).expanduser()
        if path.is_dir():
            return str(path.resolve())
        desktop = self.home / "Desktop"
        try:
            relative = path.relative_to(desktop)
        except ValueError:
            return str(path)
        moved = self.home / "DeskBox/workspace" / relative
        return str(moved.resolve()) if moved.is_dir() else str(path)

    def mapped_project(self, cwd, context, config):
        canonical = self.canonical(cwd)
        matches = []
        for identity, project in config.items():
            paths = [project.get("windows", ""), project.get("wsl", "")]
            for raw in filter(None, paths):
                normalized = self.canonical(raw)
                if ntpath.normcase(canonical) == ntpath.normcase(normalized):
                    return identity, normalized, "会话目录"
                # A Pi conversation can keep its initial home cwd after work moves.
                # Only associate explicitly mentioned registered paths, never a basename.
                aliases = [raw]
                try:
                    aliases.append(str(self.home / "Desktop" / Path(raw).relative_to(self.home / "DeskBox/workspace")))
                except ValueError:
                    pass
                if any(p.casefold() in context.casefold() for p in aliases):
                    matches.append((len(normalized), identity, normalized))
        if matches:
            _, identity, path = max(matches)
            return identity, path, "文档路径关联"
        return None, canonical, "会话目录"

    def refresh(self, config):
        projects, sessions, health = {}, {}, {}
        for source, reader in [("pi", self._pi), ("codex", self._codex), ("dsh", self._dsh)]:
            try:
                groups, records, note = reader()
                for group in groups:
                    group["id"] = key_for(source, "project:" + group["identity"])
                    group.update(source=source, synced=True, declared=True, native_path=self.canonical(group.get("path", "")))
                    group["windows"] = group["native_path"]
                    group["exists"] = bool(group["native_path"] and Path(group["native_path"]).is_dir())
                    projects[group["id"]] = group
                for record in records:
                    if record.get("archived"):
                        continue
                    cwd = self.canonical(record.get("cwd", ""))
                    if cwd and Path(cwd).is_relative_to(self.root.resolve() / "state/diagnostics"):
                        continue  # Connection checks are maintenance, never project work.
                    record["key"] = key_for(source, record["id"])
                    record["source"] = source
                    mapped, path, association = self.mapped_project(record.get("cwd", ""), record.get("request", ""), config)
                    record.update(mapped_project=mapped, canonical_path=path, association=association)
                    group_id = key_for(source, "project:" + (record.get("group") or path or "projectless"))
                    record["project"] = group_id
                    if group_id not in projects:
                        projects[group_id] = {"id": group_id, "label": Path(path).name if path else "无目录会话", "identity": record.get("group") or path,
                                              "source": source, "synced": True, "native_path": path, "windows": path,
                                              "exists": bool(path and Path(path).is_dir())}
                    sessions[record["key"]] = record
                health[source] = {"ok": True, "checked_at": now(), "note": note,
                                  "count": sum(s["source"] == source for s in sessions.values())}
            except (OSError, ValueError, sqlite3.Error, KeyError, TypeError, AttributeError) as exc:
                health[source] = {"ok": False, "checked_at": now(), "error": str(exc), "count": 0}
                # Keep last good records visible, explicitly marked stale on source failure.
                with self.guard:
                    for key, value in self.sessions.items():
                        if value["source"] == source:
                            sessions[key] = {**value, "stale": True}
                    for value in self.projects:
                        if value["source"] == source:
                            projects[value["id"]] = value
        for group in projects.values():
            group["session_count"] = sum(s["project"] == group["id"] for s in sessions.values())
            group["mapped_project"] = self.mapped_project(group.get("native_path", ""), "", config)[0]
        with self.guard:
            self.projects, self.sessions, self.health, self.updated_at = list(projects.values()), sessions, health, now()

    def snapshot(self):
        with self.guard:
            records = [{k: v for k, v in s.items() if k not in {"result", "request"}} for s in self.sessions.values()]
            records.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
            return {"projects": self.projects, "sessions": records, "sources": self.health, "updated_at": self.updated_at}

    def detail(self, key):
        with self.guard:
            if key not in self.sessions:
                raise BridgeError("会话已归档、删除或尚未同步，请先刷新来源。")
            return dict(self.sessions[key])

    def evidence(self, key, references):
        record = self.detail(key)
        if record["source"] != "pi" or not references:
            return record
        needles = [str(ref).casefold() for ref in references if ref]
        matched, answer, collecting = "", "", False
        for entry in json_lines(Path(record["record_path"])):
            message = entry.get("message", {})
            text = message_text(message.get("content"))
            if message.get("role") == "user":
                collecting = any(needle in text.casefold() for needle in needles)
                if collecting:
                    matched, answer = text, ""
            elif collecting and message.get("role") == "assistant" and text:
                answer = text
        if matched:
            record.update(request=matched[:20000], result=answer[-60000:], evidence_note="按原任务的参考文档定位 Pi 相关轮次")
        return record

    def _pi_record(self, path):
        entries = list(json_lines(path))
        header = next((e for e in entries if e.get("type") == "session"), {})
        if not header.get("id"):
            return None
        name = next((e.get("name", "") for e in reversed(entries) if e.get("type") == "session_info"), "")
        nodes = {e["id"]: e for e in entries if e.get("id") and e.get("type") != "session"}
        branch, seen = [], set()
        leaf = next((e for e in reversed(entries) if e.get("id") in nodes), None)
        while leaf and leaf["id"] not in seen:
            seen.add(leaf["id"])
            branch.append(leaf)
            leaf = nodes.get(leaf.get("parentId"))
        messages = [e.get("message", {}) for e in (list(reversed(branch)) if branch else entries) if e.get("type") == "message"]
        users = [message_text(m.get("content")) for m in messages if m.get("role") == "user"]
        answers = [m for m in messages if m.get("role") == "assistant" and message_text(m.get("content"))]
        last = next((m for m in reversed(messages) if m.get("role") in {"user", "assistant", "toolResult"}), {})
        state = ("error" if last.get("stopReason") == "error" else "idle" if last.get("role") == "assistant" and last.get("stopReason") in {"stop", "length"}
                 else "turn_open" if last.get("role") in {"user", "toolResult"} or last.get("stopReason") == "toolUse" else "unknown")
        request = "\n\n".join([*users[:1], *users[-3:]])[:20000]
        return {"id": header["id"], "title": name or (users[0].splitlines()[0][:90] if users and users[0] else "Pi 会话"),
                "cwd": header.get("cwd", ""), "created_at": header.get("timestamp", ""), "updated_at": entries[-1].get("timestamp", ""),
                "state": state, "archived": bool(header.get("archived", False)), "request": request,
                "preview": (users[-1] if users else "")[:200], "result": message_text(answers[-1].get("content"))[-60000:] if answers else "",
                "record_path": str(path), "state_note": "最近一轮已返回" if state == "idle" else "保存的轮次未结束（不等同于进程存活）" if state == "turn_open" else "仅有持久化记录，无法确认进程是否仍在运行"}

    def _pi(self):
        base = self.home / ".pi/agent"
        if not (base / "sessions").is_dir():
            raise FileNotFoundError("未找到 Pi 会话目录")
        records = []
        for path in (base / "sessions").glob("*/*.jsonl"):
            record = self._cached(path, self._pi_record)
            if record:
                records.append(record)
        for path in (base / "missions/index").glob("*.json"):
            data = read_json(path)
            if data.get("status") == "archived" or data.get("archived"):
                continue
            records.append({"id": "mission-" + data["missionId"], "title": data.get("title", "Pi 任务"), "cwd": data.get("projectRoot", ""),
                            "updated_at": data.get("updatedAt", ""), "state": "completed" if data.get("status") == "completed" else "unknown",
                            "state_note": "Pi 任务索引：" + data.get("status", "unknown"), "request": "", "result": "", "preview": "Pi Mission", "record_path": str(path)})
        return [], records, "读取 Pi 保留会话与任务索引；普通会话没有原生归档标记。"

    def _codex(self):
        base = self.home / ".codex"
        groups, records = [], []
        settings_path = base / ".codex-global-state.json"
        state = read_json(settings_path) if settings_path.is_file() else {}
        app_projects = state.get("local-projects", {})
        assignments = state.get("thread-project-assignments", {})
        projectless = set(state.get("projectless-thread-ids", []))
        titles = {}
        if (base / "session_index.jsonl").is_file():
            for entry in json_lines(base / "session_index.jsonl"):
                titles[entry.get("id")] = entry.get("thread_name", "")
        for identity, project in app_projects.items():
            groups.append({"identity": identity, "label": project["name"], "path": (project.get("rootPaths") or [""])[0]})
        path = base / "state_5.sqlite"
        if not path.is_file():
            raise FileNotFoundError("未找到 Codex 本地会话数据库")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.row_factory = sqlite3.Row
            names = {}
            for row in connection.execute("SELECT p.id,p.name,r.path FROM projects p LEFT JOIN project_roots r ON r.project_id=p.id AND r.position=0 ORDER BY p.position"):
                names[row["id"]] = row["name"]
                if not app_projects:
                    groups.append({"identity": row["id"], "label": row["name"], "path": row["path"] or ""})
            for row in connection.execute("SELECT id,title,cwd,project_id,created_at,updated_at,rollout_path FROM threads WHERE archived=0 AND (thread_source IS NULL OR thread_source NOT IN ('guardian_review','subagent')) AND source NOT LIKE '%subagent%' ORDER BY updated_at DESC"):
                data = dict(row)
                path = Path(data.pop("rollout_path"))
                persisted = self._cached(path, self._codex_record) if path.is_file() else {}
                group = (assignments.get(data["id"]) or {}).get("projectId") or data.get("project_id")
                if data["id"] in projectless:
                    group = None
                records.append({**data, **persisted, "title": titles.get(data["id"]) or data["title"][:120], "group": group, "created_at": stamp(data["created_at"]), "updated_at": stamp(data["updated_at"]),
                                "record_path": str(path)})
        return groups, records, "只读本地数据库，排除 archived=1；云端会话不在本地存储中。"

    def _codex_record(self, path):
        # A long current turn may push task_started outside the content preview.
        # Read relevant messages once, then only appended complete lines.
        size = path.stat().st_size
        offset, boundary = self.boundaries.get(str(path), (0, ""))
        if offset > size:
            offset, boundary = 0, ""
        previous = self.cache.get(str(path), (None, {}))[1] if offset else {}
        request, result = previous.get("request", ""), previous.get("result", "")
        with path.open("rb") as stream:
            stream.seek(offset)
            while True:
                start = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    offset = start
                    break
                if any(word in line for word in (b"task_started", b"task_complete", b"turn_aborted", b"user_message", b'"type": "message"', b'"type":"message"')):
                    try:
                        entry = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    payload = entry.get("payload", {})
                    if entry.get("type") == "event_msg":
                        kind = payload.get("type")
                        if kind in {"task_started", "task_complete", "task_completed", "turn_aborted"}:
                            boundary = kind
                        if kind == "user_message":
                            request = payload.get("message", "")[:20000]
                        if kind == "task_complete" and payload.get("last_agent_message"):
                            result = payload["last_agent_message"][-60000:]
                    if entry.get("type") == "response_item" and payload.get("type") == "message":
                        if payload.get("role") == "user":
                            request = message_text(payload.get("content"))[:20000]
                        if payload.get("role") == "assistant" and payload.get("phase") not in {"analysis", "commentary"} and payload.get("channel") not in {"analysis", "commentary"}:
                            result = message_text(payload.get("content"))[-60000:]
        self.boundaries[str(path)] = (offset, boundary)
        return {"request": request, "result": result, "preview": request[:200],
                "state": "turn_open" if boundary == "task_started" else "idle" if boundary else "unknown",
                "state_note": "持久化轮次尚未结束（不等同于进程存活）" if boundary == "task_started" else "读取最近保存的轮次"}

    def _dsh(self):
        base = self.home / ".dsh"
        workspace = read_json(base / "storages/workspace.json")
        global_data = workspace.get("global", {})
        archived = set(global_data.get("archivedSessionIds", []))
        groups, memberships = [], {}
        for identity, group in workspace.get("tables", {}).get("workspaces", {}).items():
            if identity not in global_data.get("workspaceIds", []):
                continue
            groups.append({"identity": identity, "label": group.get("title") or Path(group.get("path", "")).name, "path": group.get("path", "")})
            for session in group.get("sessionIds", []):
                memberships[session] = identity
        records = []
        for path in (base / "storages/session_projcache/sessions").glob("session-*.json"):
            if path.stem in archived:
                continue
            record = self._cached(path, self._dsh_record)
            if not record:
                continue
            record.update(id=path.stem, group=memberships.get(path.stem))
            records.append(record)
        return groups, records, "只读 DSH 工作空间和会话投影缓存，排除其归档列表与种子会话。"

    def _dsh_record(self, path):
        data = read_json(path)["record"]
        identity, rows = data.get("identity", {}), data.get("rows", {})
        if identity.get("isSeeded"):
            return None
        def value(key, default=None):
            result = (rows.get(key) or {}).get("val", default)
            return default if result is None else result
        first = (value("titleInput", {}).get("first") or {}).get("text", "")
        bridge_task = re.search(r"^# Task ([a-zA-Z0-9_-]+)\s*$", first, re.MULTILINE)
        outline = value("turnOutline", {}).get("turns") or []
        boundary = value("turnBoundary", {})
        opened = boundary.get("openTurnStartSeq") is not None
        return {"title": value("title", "") or (first.splitlines()[0][:90] if first else "DSH 会话"), "cwd": identity.get("cwd", ""),
                "created_at": stamp(identity.get("createdAt")), "updated_at": stamp(path.stat().st_mtime), "request": first[:20000],
                "result": outline[-1].get("response", "") if outline else "", "preview": first[:200], "record_path": str(path),
                "bridge_task_id": bridge_task.group(1) if bridge_task else None,
                "state": "turn_open" if opened else "idle", "state_note": "保存的轮次未结束（不等同于进程存活）" if opened else "最近保存的轮次已结束；结果为 DSH 缓存摘要"}
