"""Weekly housekeeping of bridge-owned indexes and temporary files only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
import uuid

from bridge import BridgeError, atomic_json, checked_id, now, read_json

DAY = 24 * 60 * 60


def time_value(value):
    return datetime.fromisoformat(value).timestamp()


class Maintenance:
    def __init__(self, root, clock=None):
        self.root = Path(root).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.guard = threading.RLock()
        self.path = self.root / "state/maintenance.json"
        self.index = self.root / "state/project-index"
        self.temp = self.root / "state/tmp"
        self.trash = self.root / "state/maintenance-trash"
        self.config = read_json(self.path) if self.path.is_file() else {
            "version": 1, "enabled": True, "interval_days": 7, "temp_retention_days": 7,
            "trash_retention_days": 30, "marked": {}, "obsolete": {}, "history": [],
            "next_run_at": (self.clock() + timedelta(days=7)).isoformat(timespec="seconds")}
        if not self.path.is_file():
            self.save()

    def save(self):
        atomic_json(self.path, self.config)

    def snapshot(self):
        with self.guard:
            return json.loads(json.dumps(self.config))

    def hidden_ids(self):
        with self.guard:
            return set(self.config["marked"])

    def configure(self, enabled):
        if not isinstance(enabled, bool):
            raise BridgeError("定期清理开关需要是布尔值。")
        with self.guard:
            self.config["enabled"] = enabled
            self.save()
        return self.snapshot()

    def mark(self, project_id, discarded, projects):
        checked_id(project_id)
        if not isinstance(discarded, bool):
            raise BridgeError("废弃标记格式不正确。")
        with self.guard:
            project = next((p for p in projects if p["id"] == project_id), None)
            if discarded:
                if not project:
                    raise BridgeError("项目已不在来源中，请先同步。")
                if project.get("busy"):
                    raise BridgeError("项目存在待办、执行中的任务或未结束的原工具轮次，请处理后再标记。")
                self.config["marked"][project_id] = {
                    "label": project["label"], "source": project.get("source", "mapping"),
                    "native_path": project.get("native_path", ""), "marked_at": now()}
            else:
                self.config["marked"].pop(project_id, None)
            self.save()
        return self.snapshot()

    @staticmethod
    def linked(path):
        return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())

    def safe_container(self, path):
        try:
            path.relative_to(self.root / "state")
            current = path
            while current != self.root:
                if self.linked(current):
                    return False
                current = current.parent
            return path.resolve().is_relative_to((self.root / "state").resolve())
        except (OSError, ValueError):
            return False

    def safe_file(self, path, base):
        path, base = Path(path), Path(base)
        try:
            path.relative_to(base)
            base.relative_to(self.root / "state")
            if self.linked(base) or not path.resolve().is_relative_to(base.resolve()):
                return False
            current = path
            while current != self.root:
                if self.linked(current):
                    return False
                current = current.parent
            return path.is_file()
        except (OSError, ValueError):
            return False

    def owned_files(self, base):
        if not self.safe_container(base) or not base.is_dir():
            return
        pending = [base]
        while pending:
            folder = pending.pop()
            if self.linked(folder):
                continue
            for entry in folder.iterdir():
                if self.linked(entry):
                    continue
                if entry.is_dir():
                    pending.append(entry)
                elif self.safe_file(entry, base):
                    yield entry

    def update_index(self, projects, *, sources_ok):
        """Keep only non-sensitive project metadata, never transcripts or credentials."""
        with self.guard:
            if not self.safe_container(self.index):
                return
            self.index.mkdir(parents=True, exist_ok=True)
            current = {p["id"] for p in projects}
            marked = self.hidden_ids()
            changed = False
            for project in projects:
                project_id = checked_id(project["id"])
                if project_id in self.config["obsolete"]:
                    self.config["obsolete"].pop(project_id)
                    changed = True
                if project_id in marked:
                    continue
                value = {key: project.get(key) for key in ["id", "label", "source", "native_path"]}
                path = self.index / (project_id + ".json")
                if path.is_file() and not self.safe_file(path, self.index):
                    continue
                if not path.is_file() or read_json(path) != value:
                    atomic_json(path, value)
            if sources_ok:
                for path in self.index.glob("*.json"):
                    if path.stem not in current and path.stem not in self.config["obsolete"]:
                        self.config["obsolete"][path.stem] = self.clock().isoformat(timespec="seconds")
                        changed = True
            if changed:
                self.save()

    def due(self):
        with self.guard:
            return self.config["enabled"] and time_value(self.config["next_run_at"]) <= self.clock().timestamp()

    def run(self, projects, *, sources_ok=True):
        with self.guard:
            tick = self.clock()
            epoch = tick.timestamp()
            run_id = tick.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            busy = {p["id"] for p in projects if p.get("busy")}
            candidates = set(self.config["marked"])
            if sources_ok:
                candidates.update(key for key, value in self.config["obsolete"].items() if epoch - time_value(value) >= 7 * DAY)
            report = {"id": run_id, "at": tick.isoformat(timespec="seconds"), "moved_files": 0, "moved_bytes": 0,
                      "purged_files": 0, "skipped_busy": sorted(candidates & busy), "errors": [], "status": "completed"}
            candidates -= busy
            if not self.safe_container(self.trash):
                raise BridgeError("维护目录存在链接，已停止清理。")
            self.trash.mkdir(parents=True, exist_ok=True)

            def move_file(path, base):
                if not self.safe_file(path, base):
                    return
                destination = self.trash / run_id / path.relative_to(self.root)
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    # Both final absolute paths must stay within bridge-owned state.
                    if not destination.resolve().is_relative_to(self.trash.resolve()):
                        raise BridgeError("维护路径越界")
                    size = path.stat().st_size
                    os.replace(path, destination)
                    report["moved_files"] += 1
                    report["moved_bytes"] += size
                except OSError as exc:
                    report["errors"].append(type(exc).__name__ + ": " + str(path.relative_to(self.root)))

            for project_id in candidates:
                checked_id(project_id)
                index = self.index / (project_id + ".json")
                if index.is_file():
                    move_file(index, self.index)
                folder = self.temp / project_id
                for path in self.owned_files(folder):
                    if epoch - path.stat().st_mtime >= self.config["temp_retention_days"] * DAY:
                        move_file(path, folder)
                if project_id not in self.config["marked"] and not index.exists() and not any(self.owned_files(folder)):
                    self.config["obsolete"].pop(project_id, None)
            # Only bridge-owned quarantined files are permanently removed after 30 days.
            for path in list(self.owned_files(self.trash)):
                relative = path.relative_to(self.trash)
                try:
                    moved_at = datetime.strptime(relative.parts[0].split("-", 1)[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
                except ValueError:
                    continue
                if epoch - moved_at >= self.config["trash_retention_days"] * DAY:
                    try:
                        path.unlink()
                        report["purged_files"] += 1
                    except OSError as exc:
                        report["errors"].append(type(exc).__name__ + ": " + str(path.relative_to(self.root)))
            report["status"] = "partial" if report["errors"] or report["skipped_busy"] else "completed"
            report["quarantine"] = str(self.trash / run_id) if report["moved_files"] else ""
            self.config["history"] = [report, *self.config["history"]][:20]
            self.config["next_run_at"] = (tick + timedelta(days=self.config["interval_days"])).isoformat(timespec="seconds")
            self.save()
            return report
