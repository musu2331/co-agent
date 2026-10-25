"""Non-secret model choices for bridge invocations; native tool accounts stay native."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import tomllib

TOOLS = ("codex", "pi", "dsh")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,159}\Z")
EFFORTS = {
    "codex": ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"),
    "pi": ("off", "minimal", "low", "medium", "high", "xhigh", "max"),
    "dsh": (),
}


def normalize(tool, selection):
    if tool not in TOOLS:
        raise ValueError("未知工具。")
    if not isinstance(selection, dict) or set(selection) - {"mode", "model", "provider", "reasoning"}:
        raise ValueError("模型配置仅支持模式、模型 ID、提供方和推理强度；账号凭据由原工具管理。")
    mode = selection.get("mode", "inherit")
    if not isinstance(mode, str) or mode not in {"inherit", "override"}:
        raise ValueError("模型模式需要是 inherit 或 override。")
    if any(not isinstance(selection.get(k, ""), str) for k in ("model", "provider", "reasoning")):
        raise ValueError("模型配置字段需要是文本。")
    model, provider, reasoning = (selection.get(k, "").strip() for k in ("model", "provider", "reasoning"))
    if mode == "inherit":
        if model or provider or reasoning:
            raise ValueError("跟随工具默认时，请清空模型覆盖字段。")
        return {"mode": "inherit"}
    if not IDENTIFIER.fullmatch(model):
        raise ValueError("请填写有效模型 ID（最多 160 字符，不能包含空白或命令参数）。")
    if provider and (tool != "pi" or not IDENTIFIER.fullmatch(provider)):
        raise ValueError("仅 Pi 可指定提供方；DSH 使用已配置的官方账号，Codex 沿用原提供方。")
    if reasoning and reasoning not in EFFORTS[tool]:
        raise ValueError("这个工具不支持所选推理强度。")
    result = {"mode": mode, "model": model}
    if provider:
        result["provider"] = provider
    if reasoning:
        result["reasoning"] = reasoning
    return result


def describe(selection, tool=""):
    if not selection or selection.get("mode") == "inherit":
        return "官方账号默认 · deepseek-flash" if tool == "dsh" else "跟随工具默认"
    return (selection.get("provider", "") + "/" if selection.get("provider") else "") + selection["model"] + (
        " · " + selection["reasoning"] if selection.get("reasoning") else "")


class ModelProfiles:
    def __init__(self, root, home=None):
        self.path = Path(root) / "model-config.json"
        self.home = Path(home) if home else Path.home()

    def read(self):
        data = json.loads(self.path.read_text(encoding="utf-8-sig")) if self.path.is_file() else {}
        if not isinstance(data, dict):
            raise ValueError("模型配置文件需要是对象。")
        profiles = data.get("profiles", {})
        if not isinstance(profiles, dict) or set(profiles) - set(TOOLS):
            raise ValueError("模型配置文件的工具列表无效。")
        return {tool: normalize(tool, profiles.get(tool, {"mode": "inherit"})) for tool in TOOLS}

    def select(self, tool, selection=None):
        value = normalize(tool, self.read()[tool] if selection is None else selection)
        if tool == "codex" and value.get("reasoning"):
            for candidate in self.catalog()["choices"]["codex"]:
                if candidate["model"] == value.get("model") and candidate.get("reasoning") and value["reasoning"] not in candidate["reasoning"]:
                    raise ValueError("本机模型目录表明该模型不支持所选推理强度。")
        return value

    def save(self, tool, selection):
        value = self.select(tool, selection)
        profiles = self.read()
        profiles[tool] = value
        # This file contains identifiers only, never tokens, API keys or native config copies.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".model-config-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"version": 1, "profiles": profiles}, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return profiles

    def catalog(self):
        """Read allowlisted model metadata only; never read authentication stores."""
        choices = {tool: [] for tool in TOOLS}
        native = {tool: {} for tool in TOOLS}
        notes = []

        def load(path, parser=json.loads):
            try:
                value = parser(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
                return value if isinstance(value, dict) else {}
            except (OSError, ValueError, TypeError):
                notes.append("部分本机模型信息读取失败；仍可手动填写模型 ID。")
                return {}

        codex_home = Path(os.environ.get("CODEX_HOME", str(self.home / ".codex")))
        cache = load(codex_home / "models_cache.json")
        for row in cache.get("models", []) or []:
            if not isinstance(row, dict) or row.get("visibility") == "hide" or not isinstance(row.get("slug"), str):
                continue
            if IDENTIFIER.fullmatch(row["slug"]):
                choices["codex"].append({"model": row["slug"], "label": str(row.get("display_name") or row["slug"]),
                    "reasoning": [v["effort"] for v in row.get("supported_reasoning_levels", []) or [] if isinstance(v, dict) and v.get("effort") in EFFORTS["codex"]]})
        codex = load(codex_home / "config.toml", tomllib.loads)
        native["codex"] = {k: codex[k] for k in ("model", "model_reasoning_effort") if isinstance(codex.get(k), str) and IDENTIFIER.fullmatch(codex[k])}
        pi_home = Path(os.environ.get("PI_CODING_AGENT_DIR", str(self.home / ".pi/agent")))
        pi = load(pi_home / "settings.json")
        native["pi"] = {k: pi[k] for k in ("defaultProvider", "defaultModel", "defaultThinkingLevel") if isinstance(pi.get(k), str) and IDENTIFIER.fullmatch(pi[k])}
        if native["pi"].get("defaultModel"):
            choices["pi"].append({"model": native["pi"]["defaultModel"], "provider": native["pi"].get("defaultProvider", ""), "label": "当前工具默认模型"})
        providers = load(pi_home / "models.json").get("providers", {})
        for provider, config in (providers.items() if isinstance(providers, dict) else []):
            if not isinstance(config, dict) or not IDENTIFIER.fullmatch(provider):
                continue
            for row in config.get("models", []):
                if isinstance(row, dict) and isinstance(row.get("id"), str) and IDENTIFIER.fullmatch(row["id"]):
                    choices["pi"].append({"model": row["id"], "provider": provider, "label": str(row.get("name") or row["id"])})
        native["dsh"] = {"provider": "deepseek-account", "model": "deepseek-flash"}
        choices["dsh"] = [{"model": "deepseek-flash", "label": "当前官方账号路由默认模型"}]
        return {"profiles": self.read(), "choices": choices, "native": native, "efforts": EFFORTS,
                "notes": list(dict.fromkeys(notes)), "notice": "候选来自本机缓存和配置，不代表账号已获全部模型权限。保存不会调用模型；可填写其他有效模型 ID。"}
