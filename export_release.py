"""Export an allowlisted, data-free source template and ZIP without changing the live hub."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent
FILES = (
    "bridge.py", "webui.py", "model_config.py", "workflow.py", "sources.py", "maintenance.py", "export_release.py",
    "COMMON.md", "WORKFLOW.md", "dsh-account.patch.yml", "REPLICATE.md", "README.md", ".gitignore", ".gitattributes", "bridge.cmd", "open-webui.cmd",
    "setup_workspace.py", "config/projects.example.json", "config/model-config.example.json", "config/app-settings.example.json",
    "launch-webui.vbs", "打开项目协作台.vbs", "web/index.html", "web/style.css", "web/app.js", "web/favicon.svg",
    "tests/test_bridge.py", "tests/test_webui.py", "tests/test_sources.py", "tests/test_maintenance.py",
    "tests/test_model_config.py", "tests/test_workflow.py", "tests/test_export_release.py", "tests/test_setup_workspace.py",
)


def export(output, source=ROOT):
    source, output = Path(source).resolve(), Path(output).resolve()
    archive = output.with_suffix(".zip")
    if output == source or output.is_relative_to(source):
        raise ValueError("导出目录应位于运行目录之外。")
    if output.exists() or archive.exists():
        raise ValueError("目标目录或 ZIP 已存在，请使用一个新名称。")
    for relative in FILES:
        path = source / relative
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError("缺少或不允许导出的源文件：" + relative)
    output.mkdir(parents=True)
    for relative in FILES:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    generated = {
        "projects.json": {"schema_version": 1, "strategy": "one physical project directory; shared task hub", "projects": {}},
        "model-config.json": {"version": 1, "profiles": {name: {"mode": "inherit"} for name in ("codex", "pi", "dsh")}},
        "app-settings.json": {"auto_sync_sources": False},
    }
    for name, value in generated.items():
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for folder in ("state", "tasks", "contexts"):
        (output / folder).mkdir()
        (output / folder / "README.md").write_text({
            "state": "# 本机运行状态\n\n初始为空。启动后由协作台生成状态、日志、索引和维护记录，不对外分享。\n",
            "tasks": "# 本机任务\n\n初始无任务。新建任务后按 ID 保存说明、模型快照、结果与日志，不对外分享。\n",
            "contexts": "# 项目上下文\n\n可按项目 ID 新建 Markdown。仅填写自己的项目约束，导出器不会复制个人上下文。\n",
        }[folder], encoding="utf-8")
    (output / "contexts/example.md").write_text("# 示例项目约束\n\n- 开始前阅读项目 README 和适用的 AGENTS.md。\n- 按明确任务范围工作，记录修改与验证。\n", encoding="utf-8")
    # Verify that known machine-specific identifiers did not enter through code or docs.
    home = str(Path.home())
    forbidden = {home, home.replace("\\", "/"), home.replace("\\", "\\\\"), str(source), str(source).replace("\\", "/")}
    manifest = {}
    for path in sorted(output.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        text = data.decode("utf-8-sig")
        if any(marker and marker.casefold() in text.casefold() for marker in forbidden):
            raise ValueError("发现本机路径，请先从源代码/文档去除再导出：" + path.relative_to(output).as_posix())
        manifest[path.relative_to(output).as_posix()] = hashlib.sha256(data).hexdigest()
    (output / "RELEASE-MANIFEST.json").write_text(json.dumps({"format": 1, "data_free": True, "files": manifest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                bundle.write(path, output.name + "/" + path.relative_to(output).as_posix())
    return {"directory": str(output), "archive": str(archive), "file_count": len(manifest) + 1, "data_free": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT.parent / "co-agent-template")
    args = parser.parse_args()
    print(json.dumps(export(args.output), ensure_ascii=False, indent=2))
