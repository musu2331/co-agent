# co-agent：无数据复刻与部署指南

co-agent 为 Codex、Pi、DeepSeek Harness 提供一个本机 WebUI：共用原项目目录，统一保存任务、结果与转交上下文。后端只使用 Python 标准库，前端使用原生 HTML、CSS、JavaScript，不需要 npm 构建前端。

## 1. 交付包与数据边界

请分享 `co-agent-template.zip` 或解压后的 `co-agent-template/`，以及本指南。导出器只复制明确列入清单的代码和静态资源，然后生成全新配置。

复刻包包含完整实现、离线测试、通用任务协议、示例上下文和启动入口。项目映射为空，模型设置均为跟随工具默认，首次启动不自动读取本机工具会话。

包内不包含原使用者的项目名称和真实目录、任务请求与答复、工具会话、运行日志、截图、废弃标记、维护历史、锁、账号验证记录、API Key、令牌或本机自定义模型配置。原使用者的运行目录保留，导出不会清空其工作数据。

## 2. 准备运行环境

- 主要验收平台：Windows，Python **3.12 或更新版本**。安装时启用 Python Launcher 或将 Python 加入 PATH。项目没有 pip 依赖。
- 根据需要安装 Codex CLI、Pi 与 DeepSeek Harness，并在各自原工具中完成登录。可只装其中一款，未安装的工具会显示不可用。
- Codex：识别有效的 `CODEX_CLI_PATH`、PATH、常见用户安装目录及桌面应用的版本目录；也支持常见 npm 全局安装位置的 Node 启动入口。
- Pi：优先识别 `~/.pi/agent/bin/pi-launcher.js`，也支持常见 npm 全局安装位置的 `@mariozechner/pi-coding-agent/dist/cli.js`，需要 Node.js。
- Windows DSH：识别 `%LOCALAPPDATA%/Programs/DeepSeek Harness/DeepSeek Harness.exe` 和随应用提供的 CLI。安装在其他目录时，需要调整 `bridge.py` 中的 `detect_engine()`。
- Linux / WSL 保留 PATH 中 `codex`、`pi`、`dsh` 入口适配，但未进行本次 Windows 之外的完整验收。运行依赖应在各系统单独安装。

不同 CLI 版本的参数和安装布局可能变化。部署时应读取原工具的 `--version` 与 `--help`，尤其核对 DSH profile 与包内 CLI 入口；仓库不保存部署者的实际安装版本。

## 3. 首次打开

1. 克隆仓库或解压到自己的可写目录。首次启动会从 `config/*.example.json` 初始化缺失的本机配置；也可先执行 `python -B setup_workspace.py`。
2. 双击 `打开项目协作台.vbs` 或 `open-webui.cmd`。启动器在后台运行 Python，并打开实际地址；默认是 [本机协作台](http://127.0.0.1:8766/)，端口被占用时选择后续可用端口。
3. 点击右上角“项目映射”，添加一个已经存在的项目目录。三个工具会在这个原目录中处理任务，不创建文件副本。
4. 如需读取自己三款工具的已有项目和会话，在“项目映射”中开启“自动读取本机工具项目与会话”，或点击“同步来源”单次读取。
5. 点击“模型配置”，为每款工具保存默认模型；也可以在“新建任务”或“转交任务”中单独指定本次模型。
6. 先“存为待办”核对说明、项目、工作方式和模型，再按需要执行。保存配置及保存待办均不会调用模型。

如果双击入口未能打开，可用已安装的 Python 执行：

```powershell
py -3 -B webui.py --launch
```

也可用 `python -B webui.py --launch`。只有排查启动问题时才需要终端；日常操作在 WebUI 中完成。关闭浏览器不会停止后端；重启电脑后重新双击入口即可。

## 4. 模型配置如何生效

| 工具 | 模型覆盖方式 | 账号处理 |
| --- | --- | --- |
| Codex | `--model`，推理强度通过 `--config model_reasoning_effort=...` | 沿用原 CLI 的账号、提供方与审批设置 |
| Pi | `--model`、可选 `--provider`、`--thinking` | 沿用 Pi 原有提供方配置，不导出凭据 |
| DSH | 基础官方账号覆盖，再叠加任务专属 `model.patch.json` | 由 DSH 的 `deepseek-account` 提供方读取其已保存账号 |

“跟随工具默认”在执行时由原工具决定模型；DSH 在本项目中使用官方账号路由默认的 `deepseek-flash`。如果需要固定模型及推理强度，请选择“指定模型”。留空的推理强度仍沿用工具设置。

三款工具的桥接默认值保存在 `model-config.json`，不会改写原工具配置。示例结构如下（实际模型 ID 请使用自己账号支持的值）：

```json
{
  "version": 1,
  "profiles": {
    "codex": {"mode": "inherit"},
    "pi": {"mode": "inherit"},
    "dsh": {"mode": "inherit"}
  }
}
```

指定模型的配置示例：`{"mode":"override","model":"your-model-id","reasoning":"high"}`。Pi 另支持 `provider`；DSH 在这个版本固定官方账号提供方，不支持手填第三方地址或密钥。

新任务会保存模型配置快照。默认值改变后，已有待办与正在执行的任务使用原快照；“重试”保留原快照；跨工具转交默认使用目标工具当前配置，也可单独指定。跟随模式保存的是“跟随”这一选择，不是原工具内部最终解析出的精确模型。

模型候选只来自本机模型缓存、非敏感配置和 DSH 路由默认值，不承诺账号拥有所有候选权限。手工输入其他模型 ID 受格式检查，实际可用性由原工具返回。Codex 已知模型不支持的推理强度会被拒绝。保存操作不发起付费验证调用。

Codex 参数依据：[OpenAI 官方开发者设置](https://learn.chatgpt.com/docs/developer-settings)。Pi 参数依据已安装 CLI 的帮助。DSH 官方账号覆盖依据已安装 `dsh-headless` 包的凭据说明，基础覆盖文件为 `dsh-account.patch.yml`，内容没有账号令牌。

## 5. 目录结构与职责

```text
co-agent-template/
  bridge.py                 工具探测、任务执行、目录锁与交接
  webui.py                  回环 HTTP 服务与后台任务管理
  model_config.py           各工具模型设置、校验与候选读取
  sources.py                三款工具本地项目/会话的只读适配
  maintenance.py            独立每周索引和临时文件维护
  export_release.py         无数据导出器，明确文件白名单
  COMMON.md                 通用任务约束
  REPLICATE.md / README.md   本指南
  projects.json             初始为空；由 WebUI 登记项目
  model-config.json         初始全部跟随工具默认
  app-settings.json         初始 auto_sync_sources=false
  dsh-account.patch.yml     DSH 官方账号路由，无凭据值
  contexts/                 按项目 ID 命名的可选上下文
  web/                      页面、样式、脚本与图标
  tests/                    隔离临时目录中的离线测试
  tasks/                    初始无任务；运行后保存任务与结果
  state/                    初始无状态；运行后保存日志和维护信息
  打开项目协作台.vbs        Windows 无终端入口
  open-webui.cmd            ASCII 名称的启动入口
```

运行后，`tasks/<ID>/` 保存 `task.json`、`TASK.md`、`input.md`、答复和日志。DSH 指定模型时额外保存不含凭据的 `model.patch.json`。`task.json` 中的 `model_config` 是该任务的配置快照。

已有来源适配：Pi 的 JSONL / Mission 索引；Codex 当前本地 `state_5.sqlite`、标题和项目分组；DSH 的 workspace 索引及会话投影。这里只读本地保存记录，不含 Codex 云端或 ChatGPT 聊天。保存的“轮次未结束”不能证明原进程还活着，工具成功退出也不等于工作已经人工验收。

## 6. 安全与后台维护规则

HTTP 只监听 `127.0.0.1`。API 使用每次启动的页面令牌，校验 Host 和 Origin；前端不使用 CDN。不要把它直接改成公网服务，远程访问需要另外设计认证与隔离。

同一个项目的桥接任务使用协作锁串行执行；锁不能阻止原工具侧手工发起的并行修改。审阅模式：Codex 使用原生只读 sandbox；Pi 限定只读工具并禁用扩展及 MCP；DSH 使用提示约束及其原有权限机制，没有额外 OS 级只读沙箱。不会为切换模型添加跳过审批的参数。

右上角“后台维护”默认每 7 天检查一次，不调用模型，也不进入任务工作台。只处理废弃项目的协作台索引和临时文件：失去来源的索引观察至少 7 天；临时文件至少保留 7 天；先移入本地维护回收目录，满 30 天后移除。真实项目、模型、原工具会话和任务结果保留。活动项目会跳过，来源读取失败不会被当作废弃。服务关闭期间错过的维护在下次启动补跑。

## 7. 测试和再次导出

离线测试使用临时目录和模拟 CLI，不发起真实模型调用：

```powershell
python -B -m unittest discover -s tests -v
node --check web/app.js
```

完成自己的修改后，运行以下命令生成新的无数据目录和 ZIP：

```powershell
python -B export_release.py --output ../my-clean-release
```

目标目录和同名 ZIP 必须尚不存在，避免覆盖别人已在使用的副本。导出器生成 `RELEASE-MANIFEST.json`，包含打包文件的 SHA-256。它不扫描复制运行数据目录，也不复制当前 `projects.json`、模型偏好或个人上下文，而是生成新的中性配置。

对外分享前检查解压目录中的项目映射为空、自动来源同步关闭、`tasks/` 和 `state/` 只有说明文件。不要额外把运行目录、原工具账号目录或本机验收截图一起打包。

如果通过 Git 分享纯源码，运行配置会被 `.gitignore` 排除；接收者可先运行导出命令生成一个带空配置的运行目录，再从该目录启动。

## 8. 常见问题

若项目目录曾迁移，可在本机 `state/path-aliases.json` 中配置“旧绝对目录 → 新绝对目录”的 JSON 映射；仅在原目录不存在、目标目录存在时应用。该可选文件不随源码或模板分享，未配置时不猜测个人目录布局。

项目页新增“协作流程设置”：固定主执行工具和可选的具体模型，保存统一验收标准与授权范围；转交默认继续同一阶段，关键节点才手动审查。交接卡区分模型陈述和证据，支持明确的阻塞分类。新阶段使用新的默认值，已有阶段沿用快照。操作与边界见 [WORKFLOW.md](WORKFLOW.md)。

Windows 上的 Codex 定位兼容桌面启动时的旧 PATH：依次检查有效的 `CODEX_CLI_PATH`、PATH、用户独立安装目录、桌面应用版本目录和 npm 安装。版本目录每次重新定位，避免更新后绑定已删除的旧文件；三个工具的版本检测分别完成、分别显示，不等待最慢的工具。这里只验证命令能启动，不代表模型账号或额度已验证。服务重启造成页面令牌更新时，前端自动重新连接并保留未提交的表单；仅对令牌校验明确拒绝、尚未执行的请求重试一次，不重试网络错误或其他权限拒绝。

启动时先从 `state/source-snapshot.json` 恢复上次项目和会话列表，再在后台核对原工具；不等待工具版本检测。快照只保存列表元数据，不复制会话正文或账号凭据，也不会随无数据模板导出。核对前的原工具状态显示为“已保存”，正文与上下文转交等待来源同步完成；本台任务仍直接读取自己的任务文件。首次启用来源同步时没有快照，需要扫描一次。

- 工具“命令可启动”但执行失败：到原工具检查登录、额度、模型权限和审批。界面保留实际失败日志，不把版本探测当成模型验证。
- DSH 返回账号未登录：先在 DSH 官方应用登录；不要从别人机器复制 `.credentials.yaml` 或令牌。自己的安装版本不支持该 profile 时，需要按其官方文档更新适配。
- Pi 在审阅模式缺少扩展提供方：该模式会禁用扩展和 MCP，请使用能在该模式独立工作的原工具配置；不要为了通过验证直接绕过权限。
- 看不到原有项目：复刻版默认关闭自动导入，在“项目映射”中开启或手动同步；Codex 和 DSH 已归档会话不展示。Pi 普通会话没有原生归档字段，展示仍保留的记录。
- 端口或启动异常：查看本机新生成的 `state/webui.log`。升级前先完成正在执行的任务，避免中断工作。
