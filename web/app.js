"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const token = $('meta[name="bridge-token"]').content;
const iconPaths = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="m6 6 12 12M18 6 6 18"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z"/>',
  "folder-plus": '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z"/><path d="M12 10v6m-3-3h6"/>',
  link: '<path d="m10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M12 8l1-1a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(2 0)"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  grid: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
  sliders: '<path d="M4 7h7m5 0h4M4 17h3m5 0h8"/><circle cx="13" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  "chevron-down": '<path d="m7 10 5 5 5-5"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M15 4H6a2 2 0 0 0-2 2v9"/>',
  "arrow-up-right": '<path d="M6 18 18 6M6 6h12v12"/>',
  "arrow-right": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  inbox: '<path d="m4 5-2 10v5h20v-5L20 5ZM2 15h6l2 3h4l2-3h6"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  play: '<path d="m8 5 11 7-11 7Z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/>',
  download: '<path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  document: '<path d="M6 3h8l4 4v14H6Z"/><path d="M14 3v5h4M9 12h6m-6 4h6"/>',
  code: '<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-12-2 16"/>',
  dsh: '<path d="M3 8c3 0 3 5 9 5 4 0 5-3 8-3l1 7c-6 4-17 3-18-3Z"/><path d="M19 10V5l3 3M7 16h.01"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  edit: '<path d="m15 5 4 4-10 10-5 1 1-5ZM13 7l4 4"/>',
  alert: '<path d="m12 3 10 18H2Z"/><path d="M12 9v5m0 3h.01"/>',
};
function icon(name) {
  const span = document.createElement("span");
  span.className = "icon";
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = `<svg viewBox="0 0 24 24">${iconPaths[name] || iconPaths.document}</svg>`;
  return span;
}
document.querySelectorAll("[data-icon]").forEach(node => node.append(icon(node.dataset.icon)));

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "disabled" || key === "checked" || key === "hidden") node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child != null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}

const toolInfo = {
  codex: { name: "Codex", description: "代码审阅与实现", short: "审阅 · 实现" },
  pi: { name: "Pi", description: "灵活处理项目任务", short: "分析 · 协作" },
  dsh: { name: "DSH", description: "DeepSeek Harness", short: "推理 · 执行" },
};
const labels = { queued: "待处理", starting: "启动中", running: "运行中", succeeded: "已返回结果", failed: "调用失败", interrupted: "已停止", completed_external: "外部已完成", source_idle: "轮次已结束", source_turn_open: "轮次未结束", source_unknown: "已保存", source_completed: "工具标记完成", source_error: "会话有报错" };
const state = { data: null, project: localStorage.getItem("bridge.project") || "", selected: null, filter: "all", search: "", detail: null, tab: "request", tool: "codex", handoffTool: "pi", refreshing: false, lastError: 0 };
const terminal = status => ["succeeded", "failed", "interrupted"].includes(status);
const active = status => ["starting", "running"].includes(status);
const currentProject = () => state.data?.projects.find(project => project.id === state.project);
const currentTasks = () => state.data?.tasks.filter(task => task.project === state.project || task.project === currentProject()?.mapped_project) || [];
const currentSessions = () => (state.data?.sessions || []).filter(session => session.project === state.project || session.mapped_project === state.project);
const sessionTask = session => ({ ...session, id: session.key, native_id: session.id, external: true, tool: session.source, status: "source_" + (session.state || "unknown"), created_at: session.updated_at, intent: "session" });
const currentItems = () => [...currentTasks(), ...currentSessions().map(sessionTask)].sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
const resolved = task => task.display_status === "completed_external";
const statusLabel = task => resolved(task) ? `已在 ${toolInfo[task.resolution.tool]?.name || task.resolution.tool} 完成` : labels[task.status] || task.status;

async function api(path, body) {
  const response = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: { "X-Bridge-Token": token, ...(body === undefined ? {} : { "Content-Type": "application/json" }) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "操作未完成，请稍后重试。");
  return data;
}

function toast(message, error = false) {
  const node = el("div", { class: "toast" + (error ? " error" : "") }, icon(error ? "alert" : "check-circle"), el("span", { text: message }));
  $("#toasts").append(node);
  setTimeout(() => node.remove(), error ? 8000 : 4200);
}

async function copy(text, message = "已复制") {
  try {
    await navigator.clipboard.writeText(text);
    toast(message);
  } catch {
    toast("浏览器未允许复制，请手动选择文本。", true);
  }
}

function avatar(tool) {
  return el("span", { class: "tool-avatar " + tool, "aria-hidden": "true" }, tool === "pi" ? "π" : icon(tool === "codex" ? "code" : "dsh"));
}
function statusPill(task) {
  const status = task.display_status || task.status;
  const name = task.stopping ? "停止中" : statusLabel(task);
  return el("span", { class: "status-pill " + status + (active(status) ? " spinner" : "") }, icon(active(status) ? "refresh" : ["succeeded", "completed_external", "source_completed"].includes(status) ? "check" : status === "failed" ? "alert" : status === "interrupted" ? "stop" : "clock"), name);
}
function formattedDate(value, full = false) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", ...(full ? { year: "numeric" } : {}) }).format(new Date(value));
}

async function refresh(force = false) {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const data = await api("/api/bootstrap");
    const prior = new Map((state.data?.tasks || []).map(task => [task.id, task]));
    for (const task of data.tasks) {
      const previous = prior.get(task.id);
      if (previous && active(previous.status) && terminal(task.status)) toast(task.status === "succeeded" ? `${task.title}：工具已返回结果` : `${task.title}：${labels[task.status]}`, task.status === "failed");
    }
    state.data = data;
    if (!data.projects.some(project => project.id === state.project)) state.project = data.projects[0]?.id;
    if (!currentItems().some(task => task.id === state.selected)) state.selected = (currentProject()?.synced ? currentSessions()[0]?.key : currentTasks()[0]?.id) || currentItems()[0]?.id || null;
    const signature = JSON.stringify({ projects: data.projects, tasks: data.tasks, tools: data.tools, sessions: data.sessions, sources: data.sources, syncing: data.syncing, checking: data.checking });
    if (force || signature !== state.overviewSignature) { state.overviewSignature = signature; renderOverview(); }
    await loadDetail(force);
    state.lastError = 0;
  } catch (error) {
    if (Date.now() - state.lastError > 20000) { toast("连接协作台失败：" + error.message, true); state.lastError = Date.now(); }
  } finally {
    state.refreshing = false;
  }
}

function selectProject(id) {
  if (state.project !== id) { state.project = id; state.selected = null; state.detail = null; state.tab = "request"; state.filter = "all"; state.search = ""; $("#task-search").value = ""; }
  localStorage.setItem("bridge.project", id);
  $("#sidebar").classList.remove("open"); $("#sidebar-backdrop").classList.remove("open");
  state.selected ||= (currentProject()?.synced ? currentSessions()[0]?.key : currentTasks()[0]?.id) || currentItems()[0]?.id || null;
  renderOverview();
  loadDetail(true).catch(error => toast(error.message, true));
}

function renderOverview() {
  const data = state.data;
  const project = currentProject();
  if (!data) return;
  $("#project-count").textContent = data.projects.length;
  const navItem = item => el("button", { class: "project-nav-item" + (item.id === state.project ? " active" : ""), "aria-current": item.id === state.project ? "page" : "false", title: (item.synced ? toolInfo[item.source].name + " · " : "") + (item.native_path || item.windows), onClick: () => selectProject(item.id) }, item.synced ? avatar(item.source) : icon("folder"), el("span", { class: "nav-name", text: item.label }), el("span", { class: "nav-count", text: item.synced ? item.session_count : item.task_count || "" }));
  $("#project-nav").replaceChildren(el("div", { class: "nav-section-label", text: "工具中的项目 · 未归档会话" }), ...data.projects.filter(p => p.synced).map(navItem), el("div", { class: "nav-section-label", text: "已映射的文件夹" }), ...data.projects.filter(p => !p.synced).map(navItem));
  $("#new-task").disabled = !project;
  $("#open-folder").disabled = !project;
  $("#copy-path").disabled = !project;
  if (!project) {
    $("#crumb").textContent = "暂无可见项目";
    $("#project-title").textContent = "暂无可见项目";
    $("#project-path").textContent = "可在后台维护中恢复项目，或添加项目映射。";
    $("#tool-cards").replaceChildren();
    for (const id of ["count-queued", "count-running", "count-done", "task-total"]) $("#" + id).textContent = "0";
    renderSync(); renderTaskList();
    return;
  }
  $("#crumb").textContent = project.label;
  $("#project-title").textContent = project.label;
  $("#project-path").textContent = project.native_path || project.windows || "没有本地目录";
  $("#project-path").title = project.native_path || project.windows;
  const available = Object.values(data.tools).filter(tool => tool.available).length;
  const blocked = Object.values(data.tools).filter(tool => tool.execution_ready === false).length;
  $("#health-label").textContent = data.checking ? "正在检测工具" : blocked ? `${blocked} 个工具需配置` : `${available} / 3 命令可启动`;
  $("#health-dot").classList.toggle("offline", available < 3 && !data.checking);
  $("#tool-cards").replaceChildren(...Object.entries(toolInfo).map(([key, info]) => {
    const health = data.tools[key];
    const toolState = el("span", { class: "tool-state" + (health.available === false || health.execution_ready === false ? " offline" : "") }, el("i"), health.execution_ready === false ? "需检查账号" : health.checking ? "检测中" : health.available ? health.account_verified ? "官方账号已验证" : "命令可启动" : "需检查");
    return el("button", { class: "tool-card", onClick: () => openCompose(key), "aria-label": `使用 ${info.name} 新建任务` }, el("div", { class: "tool-card-top" }, avatar(key), el("span", { class: "tool-name", text: info.name }), toolState), el("div", { class: "tool-card-bottom" }, info.description, icon("arrow-up-right")));
  }));
  const tasks = currentTasks();
  $("#count-queued").textContent = tasks.filter(task => task.status === "queued" && !resolved(task)).length;
  $("#count-running").textContent = tasks.filter(task => active(task.status)).length;
  $("#count-done").textContent = tasks.filter(task => task.status === "succeeded" || resolved(task)).length;
  const openTurns = currentSessions().filter(s => s.state === "turn_open").length;
  $("#running-caption").textContent = openTurns ? `${openTurns} 个原工具会话轮次未结束` : tasks.some(task => active(task.status)) ? "工具正在处理项目" : "当前没有本台执行任务";
  $("#task-total").textContent = currentItems().length;
  renderSync();
  renderTaskList();
  if ($("#health-dialog").open) renderHealth();
}

function renderTaskList() {
  $("#filters").querySelectorAll("button").forEach(button => { const selected = button.dataset.filter === state.filter; button.classList.toggle("active", selected); button.setAttribute("aria-selected", String(selected)); });
  const tasks = currentItems().filter(task => (state.filter === "all" || (state.filter === "sessions" ? task.external : state.filter === "done" ? terminal(task.status) || resolved(task) || task.status === "source_completed" : state.filter === "running" ? active(task.status) || task.status === "source_turn_open" : task.status === state.filter && !resolved(task))) && (!state.search || (task.title + " " + task.preview + " " + task.tool).toLowerCase().includes(state.search.toLowerCase())));
  if (!tasks.length) {
    $("#task-list").replaceChildren(el("div", { class: "empty-list" }, icon("inbox"), el("h3", { text: currentTasks().length ? "没有匹配的任务" : "下一项工作，从这里开始" }), el("p", { text: currentTasks().length ? "试试其他筛选条件或关键词。" : "选择一个工具，写下你想完成的事。" }), ...(!currentTasks().length ? [el("button", { class: "button secondary", onClick: () => openCompose() }, icon("plus"), "新建任务")] : [])));
    return;
  }
  $("#task-list").replaceChildren(...tasks.map(task => {
    const row = el("div", { class: "task-row" + (task.id === state.selected ? " active" : ""), role: "button", tabindex: "0", "aria-label": `${task.title}，${statusLabel(task)}`, "data-task": task.id, onClick: () => selectTask(task.id), onKeydown: event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); selectTask(task.id); } } }, avatar(task.tool), el("div", { class: "task-row-main" }, el("div", { class: "task-row-title", title: task.title, text: task.title }), el("div", { class: "task-row-meta" }, toolInfo[task.tool].name, el("span", { class: "meta-dot" }), formattedDate(task.created_at), el("span", { class: "intent-tag", text: task.external ? "已有会话" : task.intent === "review" ? "审阅" : "执行" }))), statusPill(task));
    return row;
  }));
}

function renderSync() {
  const sources = state.data.sources || {};
  $("#sync-status").replaceChildren(...Object.entries(toolInfo).map(([key, info]) => {
    const health = sources[key];
    return el("span", { class: "sync-source" + (health?.ok === false ? " error" : ""), title: health?.error || health?.note || (state.data.auto_sync_sources ? "正在读取来源" : "尚未读取本机来源") }, avatar(key), `${info.name} · ${health ? health.ok ? (health.visible_count ?? health.count) + " 个会话" + (health.merged_count ? `（${health.merged_count} 条调用已合并）` : "") : "读取失败" : state.data.auto_sync_sources ? "同步中" : "未读取"}`);
  }));
  $("#sync-caption").textContent = (state.data.synced_at ? "最近同步 " + formattedDate(state.data.synced_at) + " · " : "") + `${currentSessions().length} 个会话关联本项目。读取本地记录；“轮次已结束”不表示整个项目已完成。`;
  if (state.data.auto_sync_sources === false) $("#sync-caption").textContent += " 自动同步已关闭，可点击“同步来源”读取一次。";
  $("#sync-sources").disabled = state.data.syncing;
}

function renderSessionDetail(pane, task) {
  const source = state.detail.source_record;
  const content = el("div", { class: "detail-content" });
  content.append(el("div", { class: "source-note" }, el("strong", { text: source.stale ? "来源读取失败，当前为上次记录" : source.state_note || "已保存的工具会话" }), el("p", { text: "查看与同步不会重新调用模型。转交会带入记录中的要求及答复，创建下一步任务。" })));
  if (state.tab === "result") content.append(state.detail.result ? markdown(state.detail.result) : el("p", { text: "来源尚无可读取的答复摘要，请在原工具中查看完整记录。" }));
  else content.append(el("p", { class: "detail-request", text: state.detail.request || "来源没有可读取的任务说明。" }));
  const tabs = el("div", { class: "detail-tabs", role: "tablist", "aria-label": "会话内容" }, [["request", "已有要求"], ["result", "最近答复"]].map(([key, title]) => el("button", { class: state.tab === key ? "active" : "", role: "tab", "aria-selected": String(state.tab === key), onClick: () => { state.tab = key; renderDetail(); } }, title)));
  const meta = el("div", { class: "session-meta" }, el("small", { text: source.association }), el("p", { text: source.canonical_path || source.cwd || "无目录" }), el("small", { text: "原会话 ID：" + source.id }));
  pane.replaceChildren(el("div", { class: "detail-top" }, avatar(task.tool), toolInfo[task.tool].name, statusPill(task)), el("h3", { class: "detail-title", text: task.title }), el("div", { class: "detail-time", text: "已有会话 · " + formattedDate(task.created_at, true) }), el("div", { class: "detail-actions" }, el("button", { class: "button primary", onClick: openHandoff }, icon("arrow-right"), "带上下文转交"), el("button", { class: "button secondary", onClick: () => copy(source.record_path, "原记录路径已复制") }, icon("copy"), "复制来源路径")), tabs, content, meta);
}

function openResolve() {
  state.resolveTaskId = state.detail.task.id;
  const sessions = [...currentSessions(), ...(state.data.sessions || []).filter(s => !currentSessions().some(c => c.key === s.key))];
  if (!sessions.length) { toast("还未读到工具会话，请先同步来源。", true); return; }
  $("#resolve-session").replaceChildren(...sessions.map(s => el("option", { value: s.key }, `${toolInfo[s.source].name} · ${s.title}`)));
  $("#resolve-note").value = "我已在对应工具完成这项工作。";
  $("#resolve-related").checked = true;
  modalError("#resolve-error"); showDialog("#resolve-dialog");
}
$("#resolve-form").addEventListener("submit", async event => {
  event.preventDefault(); const button = $('button[type="submit"]', event.target); button.disabled = true;
  try {
    await api(`/api/tasks/${state.resolveTaskId}/resolve`, { session_key: $("#resolve-session").value, note: $("#resolve-note").value, related: $("#resolve-related").checked });
    $("#resolve-dialog").close(); state.tab = "result"; toast("已关联外部完成结果，不再重复排队"); await refresh(true);
  } catch (error) { modalError("#resolve-error", error.message); }
  finally { button.disabled = false; }
});
$("#sync-sources").addEventListener("click", async () => { try { await api("/api/sources/refresh", {}); toast("正在同步本机项目与未归档会话"); await refresh(true); } catch (error) { toast(error.message, true); } });

function selectTask(id) { state.selected = id; state.tab = "request"; renderTaskList(); loadDetail(true).catch(error => toast(error.message, true)); }
async function loadDetail(force = false) {
  if (!state.selected) { state.detail = null; renderDetail(); return; }
  const id = state.selected;
  const external = (state.data.sessions || []).some(s => s.key === id);
  const record = await api((external ? "/api/sessions/" : "/api/tasks/") + id);
  const detail = external ? { task: sessionTask(record), request: record.request || "", result: record.result || "", source_record: record } : record;
  if (id !== state.selected) return;
  if (state.detail && active(state.detail.task.status) && detail.task.status === "succeeded" && state.tab === "logs") state.tab = "result";
  if (force || JSON.stringify(detail) !== JSON.stringify(state.detail)) { state.detail = detail; renderDetail(); }
}

function inline(text) {
  const fragment = document.createDocumentFragment();
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    fragment.append(document.createTextNode(text.slice(cursor, match.index)));
    const value = match[0];
    if (value.startsWith("`")) fragment.append(el("code", { text: value.slice(1, -1) }));
    else if (value.startsWith("**")) fragment.append(el("strong", { text: value.slice(2, -2) }));
    else { const link = value.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/); fragment.append(el("a", { href: link[2], target: "_blank", rel: "noopener noreferrer", text: link[1] })); }
    cursor = match.index + value.length;
  }
  fragment.append(document.createTextNode(text.slice(cursor)));
  return fragment;
}
function markdown(text) {
  const root = el("div", { class: "markdown" });
  let code = null, list = null;
  for (const line of text.split("\n")) {
    if (line.trimStart().startsWith("```")) { if (code) { root.append(el("pre", {}, code.join("\n"))); code = null; } else code = []; list = null; continue; }
    if (code) { code.push(line); continue; }
    const heading = line.match(/^(#{1,6})\s+(.+)/);
    if (heading) { root.append(el("h" + Math.min(3, heading[1].length), {}, inline(heading[2]))); list = null; continue; }
    if (/^\s*[-*]\s+/.test(line)) { if (!list) { list = el("ul"); root.append(list); } list.append(el("li", {}, inline(line.replace(/^\s*[-*]\s+/, "")))); continue; }
    list = null;
    if (line.trim()) root.append(el("p", {}, inline(line)));
  }
  if (code) root.append(el("pre", {}, code.join("\n")));
  return root;
}

function renderDetail() {
  const pane = $("#detail-pane");
  if (!state.detail) { pane.replaceChildren(el("div", { class: "detail-empty" }, icon("document"), el("h3", { text: "选择一项任务" }), el("p", { text: "在这里查看说明、运行结果与日志。" }))); return; }
  const task = state.detail.task;
  if (task.external) { renderSessionDetail(pane, task); return; }
  const oldContent = $(".detail-content", pane);
  const scrollTop = oldContent?.scrollTop || 0;
  const follow = !oldContent || oldContent.scrollHeight - oldContent.clientHeight - scrollTop < 35;
  const content = el("div", { class: "detail-content" });
  if (resolved(task)) content.append(el("div", { class: "completion-panel" }, el("strong", { text: statusLabel(task) }), el("p", { text: task.resolution.note }), el("button", { class: "text-button", onClick: () => selectTask(task.resolution.session_key) }, "查看关联的原会话", icon("arrow-up-right"))));
  if (task.error && !resolved(task)) content.append(el("div", { class: "error-panel", text: task.diagnosis?.message || (task.error === "Cancelled before starting" ? "这项待办已取消。" : task.error === "Stopped by the operator" ? "执行已停止，已经产生的项目修改会保留。" : task.error) }));
  if (active(task.status)) content.append(el("div", { class: "running-note" }, el("span", { class: "spinner" }, icon("refresh")), task.stopping ? "正在停止工具进程…" : "工具正在处理，内容会自动更新。"));
  if (state.tab === "request") {
    content.append(el("p", { class: "detail-request", text: state.detail.request }));
    if (task.refs?.length) content.append(el("div", { class: "references" }, "参考文件", task.refs.map(ref => el("div", { class: "reference-line", title: ref }, icon("document"), el("span", { text: ref.split(/[\\/]/).pop() }), el("button", { class: "icon-button", "aria-label": "复制参考路径", onClick: () => copy(ref) }, icon("copy"))))));
  } else if (state.tab === "result") {
    content.append((state.detail.external_result || state.detail.result) ? markdown(state.detail.external_result || state.detail.result) : el("div", { class: "detail-empty" }, icon("document"), el("h3", { text: "还没有调用结果" }), el("p", { text: task.status === "queued" ? "开始执行后，工具的最终答复会保存在这里。" : active(task.status) ? "执行中的输出可以在「日志」中查看。" : "工具尚未给出结果，可查看日志定位原因。" })));
  } else {
    if (state.detail.stdout) content.append(el("div", { class: "log-title", text: "任务输出" }), el("pre", { class: "log-output", text: state.detail.stdout }));
    if (state.detail.stderr) content.append(el("div", { class: "log-title", text: "运行诊断" }), el("pre", { class: "log-output", text: state.detail.stderr }));
    if (!state.detail.stdout && !state.detail.stderr) content.append(el("div", { class: "detail-empty" }, icon("activity"), el("h3", { text: "还没有运行日志" }), el("p", { text: active(task.status) ? "等待工具输出，界面会自动刷新。" : "开始执行后，这里会显示工具的输出。" })));
  }
  const actions = el("div", { class: "detail-actions" });
  const busy = currentProject()?.busy;
  if (resolved(task)) actions.append(el("button", { class: "button primary", onClick: () => { state.tab = "result"; renderDetail(); } }, icon("check-circle"), "查看完成结果"));
  else if (task.status === "queued") actions.append(el("button", { class: "button primary", disabled: busy || state.data.tools[task.tool]?.execution_ready === false, onClick: () => runTask(task.id) }, icon("play"), busy ? "等待项目空闲" : state.data.tools[task.tool]?.execution_ready === false ? "先配置工具" : "开始执行"));
  else if (active(task.status)) actions.append(el("button", { class: "button danger", disabled: !task.managed || task.stopping, onClick: () => stopTask(task.id) }, icon("stop"), task.stopping ? "正在停止" : "停止任务"));
  else if (["failed", "interrupted"].includes(task.status)) actions.append(el("button", { class: "button primary", onClick: () => retryTask(task.id) }, icon("refresh"), "重新排队"));
  if (terminal(task.status)) actions.append(el("button", { class: "button " + (task.status === "succeeded" ? "primary" : "secondary"), onClick: openHandoff }, icon("arrow-right"), "转交任务"));
  else actions.append(el("button", { class: "button secondary", disabled: true, title: "当前任务结束后可转交" }, icon("arrow-right"), "转交任务"));
  const minor = el("div", { class: "detail-minor" });
  if (!resolved(task) && !active(task.status)) minor.append(el("button", { class: "text-button", onClick: openResolve }, "已由其他工具完成"));
  if (task.status === "queued" && !resolved(task)) minor.append(el("button", { class: "text-button", onClick: () => stopTask(task.id) }, "取消待办"));
  else minor.append(el("span"));
  if (state.detail.external_result || state.detail.result) minor.append(el("div", {}, el("button", { class: "text-button", onClick: () => copy(state.detail.external_result || state.detail.result, "结果已复制") }, icon("copy"), "复制结果"), "　", el("button", { class: "text-button", onClick: () => downloadResult(task) }, icon("download"), "下载 Markdown")));
  const meta = el("details", { class: "detail-meta" }, el("summary", { text: "任务信息" }), el("dl", {}, el("dt", { text: "任务编号" }), el("dd", { text: task.id }), el("dt", { text: "执行目录" }), el("dd", { text: currentProject()?.windows }), el("dt", { text: "交接来源" }), el("dd", { text: task.parent || (task.origin === "webui" ? "在界面中创建" : task.origin) })));
  pane.replaceChildren(el("div", { class: "detail-top" }, avatar(task.tool), toolInfo[task.tool].name, statusPill(task)), el("h3", { class: "detail-title", text: task.title }), el("div", { class: "detail-time" }, icon("clock"), formattedDate(task.created_at, true), "·", task.intent === "review" ? "审阅与建议" : "执行与修改"), el("p", { class: "form-note", text: "模型配置快照：" + (task.model_label || modelLabel(task.model_config, task.tool)) }), actions, el("div", { class: "detail-tabs", role: "tablist", "aria-label": "任务内容" }, [["request", "任务说明"], ["result", "运行结果"], ["logs", "日志"]].map(([key, label]) => el("button", { class: state.tab === key ? "active" : "", role: "tab", "aria-selected": String(state.tab === key), onClick: () => { state.tab = key; renderDetail(); } }, label))), content, minor, meta);
  content.scrollTop = state.tab === "logs" && follow ? content.scrollHeight : scrollTop;
}

async function runTask(id) {
  try { await api(`/api/tasks/${id}/run`, {}); state.tab = "logs"; toast("任务已开始，进度会自动更新"); await refresh(true); } catch (error) { toast(error.message, true); }
}
async function stopTask(id) {
  try { const result = await api(`/api/tasks/${id}/stop`, {}); toast(result.stopping ? "已请求停止任务" : "待办已取消"); await refresh(true); } catch (error) { toast(error.message, true); }
}
async function retryTask(id) {
  try { const result = await api(`/api/tasks/${id}/retry`, {}); state.selected = result.task.id; state.tab = "request"; toast("已创建新的待办任务"); await refresh(true); } catch (error) { toast(error.message, true); }
}
async function downloadResult(task) {
  try {
    let blob;
    if (resolved(task)) blob = new Blob([state.detail.external_result], { type: "text/markdown;charset=utf-8" });
    else {
      const response = await fetch(`/api/tasks/${task.id}/download`, { headers: { "X-Bridge-Token": token } });
      if (!response.ok) throw new Error((await response.json()).error);
      blob = await response.blob();
    }
    const url = URL.createObjectURL(blob);
    const link = el("a", { href: url, download: (task.title || task.id).replace(/[<>:"/\\|?*]/g, "-") + ".md" });
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (error) { toast(error.message, true); }
}

function choices(container, group, chosen, onChange) {
  container.replaceChildren(...Object.entries(toolInfo).map(([key, info]) => el("label", { class: "tool-choice" }, el("input", { type: "radio", name: group, value: key, checked: key === chosen, onChange: () => onChange(key) }), avatar(key), el("span", {}, el("strong", { text: info.name }), el("small", { text: info.short })), el("span", { class: "choice-check" }, icon("check-circle")))));
}
function modalError(id, message = "") { $(id).textContent = message; $(id).hidden = !message; }
function showDialog(id) { $(id).showModal(); }
function modelLabel(value, tool) {
  if (!value || value.mode === "inherit") return tool === "dsh" ? "官方账号默认 · deepseek-flash" : "跟随工具默认";
  return (value.provider ? value.provider + "/" : "") + value.model + (value.reasoning ? " · " + value.reasoning : "");
}
function renderModelEditor(container, prefix, tool, selection, task = false) {
  const field = name => `${prefix}-model-${name}`;
  const mode = el("select", { id: field("mode"), "aria-label": task ? "本次模型设置" : "默认模型模式" },
    ...(task ? [el("option", { value: "default", text: "工作台默认：" + modelLabel(state.data.model_profiles?.[tool], tool) })] : []),
    el("option", { value: "inherit", text: tool === "dsh" ? "官方账号默认模型" : "跟随工具默认" }), el("option", { value: "override", text: "指定模型" }));
  const model = el("input", { id: field("id"), maxlength: "160", placeholder: "填写模型 ID，或选择本机候选", list: field("choices"), autocomplete: "off", value: selection?.model || "" });
  const provider = el("input", { id: field("provider"), maxlength: "160", placeholder: "留空由 Pi 解析提供方", value: selection?.provider || "" });
  const effort = el("select", { id: field("reasoning"), "aria-label": "推理强度" }, el("option", { value: "", text: "模型默认" }),
    ...(state.models?.efforts?.[tool] || (tool === "codex" ? ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"] : tool === "pi" ? ["off", "minimal", "low", "medium", "high", "xhigh", "max"] : [])).map(value => el("option", { value, text: value })));
  effort.value = selection?.reasoning || "";
  const detail = el("div", { class: "model-editor-fields" }, el("label", { for: field("id") }, "模型 ID", model),
    el("div", { class: "form-row" }, el("label", { for: field("provider"), hidden: tool !== "pi" }, "Pi 提供方", provider), el("label", { for: field("reasoning"), hidden: tool === "dsh" }, "推理强度", effort)),
    el("p", { class: "form-note", text: tool === "dsh" ? "使用 DSH 已保存的官方账号。模型 ID 需由该账号支持，保存配置不发起调用。" : "模型及推理强度需由当前账号和工具版本支持。留空的推理强度沿用工具设置。" }));
  const update = () => { detail.hidden = mode.value !== "override"; model.required = mode.value === "override"; };
  mode.value = task ? "default" : selection?.mode || "inherit";
  mode.addEventListener("change", update);
  model.addEventListener("change", () => {
    if (tool === "pi" && !provider.value) { const row = state.models?.choices?.pi?.find(row => row.model === model.value); if (row?.provider) provider.value = row.provider; }
  });
  const list = el("datalist", { id: field("choices") }, ...(state.models?.choices?.[tool] || []).map(row => el("option", { value: row.model, label: (row.provider ? row.provider + " · " : "") + row.label })));
  container.replaceChildren(el("label", { for: field("mode") }, task ? "本次模型" : "默认模型", mode), detail, list);
  update();
}
function readModelEditor(prefix) {
  const get = name => $(`#${prefix}-model-${name}`).value.trim();
  const mode = get("mode");
  if (mode === "default") return null;
  if (mode === "inherit") return { mode };
  return { mode, model: get("id"), provider: get("provider"), reasoning: get("reasoning") };
}
function renderTaskModel(prefix, tool) { renderModelEditor($(`#${prefix}-model-controls`), prefix, tool, null, true); }
function renderModelProfile(tool) {
  state.modelTool = tool;
  choices($("#model-profile-tools"), "model-profile-tool", tool, renderModelProfile);
  renderModelEditor($("#model-profile-editor"), "profile", tool, state.models.profiles[tool]);
  const native = state.models.native[tool];
  $("#model-native-note").textContent = tool === "codex" ? `本机默认：${native.model || "由 Codex 决定"}${native.model_reasoning_effort ? " · " + native.model_reasoning_effort : ""}` : tool === "pi" ? `本机默认：${native.defaultProvider || ""} / ${native.defaultModel || "由 Pi 决定"}` : "账号与提供方：DSH 已保存的官方账号 · deepseek-account";
  modalError("#models-error");
}
$("#models-button").addEventListener("click", async () => {
  try { state.models = await api("/api/models"); renderModelProfile(state.modelTool || "codex"); $("#models-notice").textContent = state.models.notice + " " + state.models.notes.join(" "); showDialog("#models-dialog"); }
  catch (error) { toast(error.message, true); }
});
$("#models-form").addEventListener("submit", async event => {
  event.preventDefault(); $("#models-save").disabled = true;
  try { const tool = state.modelTool; const result = await api(`/api/models/${tool}`, readModelEditor("profile")); state.models.profiles = result.profiles; await refresh(true); toast(`${toolInfo[tool].name} 默认模型配置已保存，新任务生效`); }
  catch (error) { modalError("#models-error", error.message); }
  finally { $("#models-save").disabled = false; }
});
async function openCompose(tool = state.tool) {
  if (!state.data) return;
  if (!state.models) { try { state.models = await api("/api/models"); } catch (error) { toast("候选读取失败，可手动填写模型 ID", true); } }
  $("#compose-form").reset(); state.tool = tool; $("#import-label").hidden = true; modalError("#compose-error");
  $("#compose-project").replaceChildren(...state.data.projects.map(project => el("option", { value: project.id, ...(project.id === state.project ? { selected: "selected" } : {}) }, project.label)));
  choices($("#compose-tools"), "compose-tool", tool, value => { state.tool = value; renderTaskModel("compose", value); intentNote(); });
  renderTaskModel("compose", tool);
  intentNote(); showDialog("#compose-dialog");
}
function intentNote() {
  const intent = $('input[name="intent"]:checked').value;
  const issue = state.data.tools[state.tool]?.execution_error;
  $('button[value="run"]', $("#compose-form")).disabled = Boolean(issue);
  $("#intent-note").textContent = issue || (intent === "work" ? "允许按任务要求修改项目。训练、部署或停止服务等操作仍需在任务中明确授权。" : state.tool === "dsh" ? "本次只请求审阅与建议；执行权限仍遵循 DSH 自身设置。" : "先检查与分析，不修改项目。所选工具会使用只读执行方式。");
}

$("#compose-form").addEventListener("submit", async event => {
  event.preventDefault(); modalError("#compose-error");
  const buttons = $("#compose-form").querySelectorAll('button[type="submit"]'); buttons.forEach(button => button.disabled = true);
  try {
    const body = { project: $("#compose-project").value, tool: state.tool, title: $("#compose-title").value.trim(), prompt: $("#compose-prompt").value, intent: $('input[name="intent"]:checked').value, refs: $("#compose-refs").value.split("\n").map(line => line.trim()).filter(Boolean), run: event.submitter?.value === "run" };
    body.model_config = readModelEditor("compose");
    const result = await api("/api/tasks", body);
    state.project = result.task.project; localStorage.setItem("bridge.project", state.project); state.selected = result.task.id; state.filter = "all"; state.search = ""; $("#task-search").value = ""; state.tab = body.run && !result.notice ? "logs" : "request";
    $("#compose-dialog").close(); toast(result.notice || (body.run ? "任务已创建并开始执行" : "已保存为待办"), Boolean(result.notice)); await refresh(true);
  } catch (error) { modalError("#compose-error", error.message); }
  finally { buttons.forEach(button => button.disabled = false); }
});
$("#import-file").addEventListener("click", () => $("#prompt-file").click());
$("#prompt-file").addEventListener("change", async event => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 900000) throw new Error("请选择小于 900 KB 的 Markdown 或文本文件。");
    const text = await file.text();
    const current = $("#compose-prompt").value;
    const value = current.trim() ? current + "\n\n参考文档：" + file.name + "\n\n" + text : text;
    if (value.length > 300000) throw new Error("导入后的任务内容超过 30 万字符，请精简后重试。");
    $("#compose-prompt").value = value;
    if (!$("#compose-title").value) $("#compose-title").value = file.name.replace(/\.(md|markdown|txt)$/i, "").slice(0, 120);
    $("#import-label").textContent = "已导入 " + file.name + " · 内容已放入任务说明"; $("#import-label").hidden = false;
    modalError("#compose-error");
  } catch (error) { modalError("#compose-error", error.message); }
  event.target.value = "";
});
document.querySelectorAll('input[name="intent"]').forEach(input => input.addEventListener("change", intentNote));

async function openHandoff() {
  if (!state.detail || (!state.detail.task.external && !terminal(state.detail.task.status) && !resolved(state.detail.task))) return;
  if (!state.models) { try { state.models = await api("/api/models"); } catch (error) { toast("候选读取失败，可手动填写模型 ID", true); } }
  const task = state.detail.task;
  state.handoffSource = state.detail;
  state.handoffTool = task.tool === "codex" ? "pi" : "codex";
  $("#handoff-source").replaceChildren(el("small", { text: "上一轮 · " + toolInfo[task.tool].name }), el("strong", { text: task.title }));
  choices($("#handoff-tools"), "handoff-tool", state.handoffTool, value => { state.handoffTool = value; renderTaskModel("handoff", value); handoffAvailability(); });
  renderTaskModel("handoff", state.handoffTool);
  $("#handoff-context-note").textContent = task.external ? "带入最近保存的要求与答复，创建新的接续任务；原会话保持原样。" : "上一轮结果会自动带入，新工具会在同一个项目目录中继续。";
  $("#handoff-instruction").value = "复核上一轮结论与项目文件是否一致，给出下一步最小方案，暂不实施。";
  $("#handoff-intent").value = "review";
  modalError("#handoff-error"); handoffAvailability(); showDialog("#handoff-dialog");
}
function handoffAvailability() {
  const issue = state.data.tools[state.handoffTool]?.execution_error;
  const unfinished = state.handoffSource?.task.external && state.handoffSource.source_record.state === "turn_open";
  const blocked = Boolean(issue || unfinished);
  $('button[value="run"]', $("#handoff-form")).disabled = blocked;
  $("#handoff-context-note").textContent = issue || (unfinished ? "原会话保存的轮次尚未结束。现在可转交为待办，请先核对原工具，再执行下一步。" : state.handoffSource?.task.external ? "带入最近保存的要求与答复，创建新的接续任务；原会话保持原样。" : "上一轮结果会自动带入，新工具会在同一个项目目录中继续。");
}
$("#handoff-form").addEventListener("submit", async event => {
  event.preventDefault(); const buttons = $("#handoff-form").querySelectorAll('button[type="submit"]'); buttons.forEach(button => button.disabled = true);
  try {
    const run = event.submitter?.value === "run";
    const source = state.handoffSource;
    const external = source.task.external;
    const result = await api(`/api/${external ? "sessions" : "tasks"}/${source.task.id}/handoff`, { tool: state.handoffTool, instruction: $("#handoff-instruction").value, intent: $("#handoff-intent").value, project: external ? source.source_record.mapped_project || source.source_record.project : undefined, model_config: readModelEditor("handoff"), run });
    state.project = result.task.project; localStorage.setItem("bridge.project", state.project); state.selected = result.task.id; state.filter = "all"; state.tab = run && !result.notice ? "logs" : "request"; $("#handoff-dialog").close(); toast(result.notice || (run ? "已转交并开始执行" : "已转交为新的待办任务"), Boolean(result.notice)); await refresh(true);
  } catch (error) { modalError("#handoff-error", error.message); }
  finally { buttons.forEach(button => button.disabled = false); }
});

function resetMapping() { $("#mapping-form").reset(); $("#mapping-id").readOnly = false; $("#mapping-reset").hidden = true; $("#mapping-form-title").textContent = "添加项目"; $("#mapping-submit").textContent = "添加项目"; modalError("#mapping-error"); }
function renderMappings() {
  $("#auto-sync-sources").checked = state.data.auto_sync_sources;
  $("#mapping-list").replaceChildren(...state.data.projects.filter(project => project.registered).map(project => el("div", { class: "mapping-row" }, el("span", { class: "mapping-project-icon" }, icon("folder")), el("div", {}, el("strong", { text: project.label }), el("small", { text: project.id }), el("p", { text: project.native_path || project.windows })), el("button", { class: "icon-button", "aria-label": `编辑 ${project.label} 的映射`, onClick: () => { $("#mapping-label").value = project.label; $("#mapping-id").value = project.id; $("#mapping-id").readOnly = true; $("#mapping-path").value = project.native_path || project.windows; $("#mapping-reset").hidden = false; $("#mapping-form-title").textContent = "编辑项目映射"; $("#mapping-submit").textContent = "保存修改"; modalError("#mapping-error"); $("#mapping-label").focus(); } }, icon("edit")))));
}
function openMappings(add = false) { if (!state.data) return; resetMapping(); renderMappings(); showDialog("#mapping-dialog"); if (add) setTimeout(() => $("#mapping-label").focus(), 30); }
$("#mapping-form").addEventListener("submit", async event => {
  event.preventDefault(); $("#mapping-submit").disabled = true;
  try {
    const result = await api("/api/projects", { id: $("#mapping-id").value.trim(), label: $("#mapping-label").value.trim(), path: $("#mapping-path").value.trim() });
    await refresh(); renderMappings(); resetMapping(); toast("项目映射已保存");
  } catch (error) { modalError("#mapping-error", error.message); }
  finally { $("#mapping-submit").disabled = false; }
});
$("#mapping-reset").addEventListener("click", resetMapping);

function renderHealth() {
  $("#health-list").replaceChildren(...Object.entries(toolInfo).map(([key, info]) => {
    const health = state.data.tools[key];
    return el("div", {}, el("div", { class: "health-row" }, avatar(key), el("div", { class: "health-row-main" }, el("strong", { text: info.name }), el("small", { text: health.version || (health.checking ? "正在读取工具版本…" : "未取得版本信息") })), el("span", { class: "status-pill " + (health.execution_ready === false ? "failed" : health.available ? "succeeded" : health.checking ? "queued" : "failed"), text: health.execution_ready === false ? "需检查账号" : health.available ? health.account_verified ? "官方账号已验证" : "命令可启动" : health.checking ? "检测中" : "需检查" })), ...(health.auth_mode === "official_account" ? [el("p", { class: "form-note", text: `复用 DSH 已保存的官方账号 · ${health.model}。${health.account_checked_at ? "账号调用验证：" + formattedDate(health.account_checked_at, true) + "（" + health.checked_model + "）" + (health.model_verified ? "" : "。当前所选模型尚未调用验证") : "凭据由 DSH 自行管理，无需在协作台填写 API Key。"}` })] : []), ...(health.execution_error || health.error ? [el("pre", { class: "health-error", text: health.execution_error || health.error })] : []), ...(health.execution_ready === false ? [el("button", { class: "button secondary", onClick: async () => { try { await api(`/api/tools/${key}/acknowledge`, {}); await refresh(true); toast("已允许再次调用；账户可用性将在实际执行时确认"); } catch (error) { toast(error.message, true); } } }, "已登录，允许再次调用")] : []));
  }));
  $("#check-tools").disabled = state.data.checking;
}
$("#health-button").addEventListener("click", () => { if (!state.data) return; renderHealth(); showDialog("#health-dialog"); });
$("#check-tools").addEventListener("click", async () => { try { await api("/api/tools/check", {}); await refresh(); toast("正在重新检测工具"); } catch (error) { toast(error.message, true); } });

async function loadMaintenance() {
  state.maintenance = await api("/api/maintenance");
  const data = state.maintenance;
  $("#maintenance-enabled").checked = data.enabled;
  $("#maintenance-schedule").textContent = `${data.enabled ? "下次清理：" + formattedDate(data.next_run_at, true) : "每周清理已关闭"}。${data.service_note}`;
  modalError("#maintenance-error", data.error || "");
  renderMaintenanceProjects();
  $("#maintenance-history").replaceChildren(...(data.history.length ? data.history.map(run => el("div", { class: "maintenance-history-row" }, el("strong", { text: formattedDate(run.at, true) }), el("p", { text: `移入维护回收目录 ${run.moved_files} 个文件 · 约 ${Math.ceil(run.moved_bytes / 1024)} KB；移除到期回收文件 ${run.purged_files} 个；跳过活动项目 ${run.skipped_busy.length} 个。` }), ...(run.errors.length ? [el("p", { class: "maintenance-warning", text: "部分文件未处理：" + run.errors.join("；") })] : []), ...(run.quarantine ? [el("button", { class: "text-button", onClick: () => copy(run.quarantine, "维护回收目录已复制") }, icon("copy"), "复制回收目录路径")] : []))) : [el("p", { class: "form-note", text: "还没有维护记录。后台清理不会创建模型任务。" })]));
}
function renderMaintenanceProjects() {
  const data = state.maintenance; if (!data) return;
  const query = $("#maintenance-search").value.toLowerCase();
  const projects = new Map(data.projects.map(p => [p.id, p]));
  for (const [id, item] of Object.entries(data.marked)) if (!projects.has(id)) projects.set(id, { id, ...item });
  $("#maintenance-projects").replaceChildren(...[...projects.values()].filter(p => (p.label + " " + p.source + " " + p.native_path).toLowerCase().includes(query)).map(p => {
    const marked = Boolean(data.marked[p.id]);
    return el("div", { class: "maintenance-project-row" }, el("div", {}, el("strong", { text: p.label }), el("small", { text: (toolInfo[p.source]?.name || "文件夹映射") + (marked ? " · 已标记废弃" : p.busy ? " · 有待办或未结束轮次" : "") }), el("p", { text: p.native_path })), el("button", { class: "button secondary", disabled: !marked && p.busy, onClick: async () => {
      try { await api(`/api/maintenance/projects/${p.id}`, { discarded: !marked }); await loadMaintenance(); await refresh(true); toast(marked ? "已恢复项目显示" : "已标记废弃并隐藏；文件按每周规则处理"); }
      catch (error) { modalError("#maintenance-error", error.message); }
    } }, marked ? "恢复显示" : "标记废弃"));
  }));
}
$("#maintenance-button").addEventListener("click", async () => { try { await loadMaintenance(); showDialog("#maintenance-dialog"); } catch (error) { toast(error.message, true); } });
$("#maintenance-search").addEventListener("input", renderMaintenanceProjects);
$("#maintenance-enabled").addEventListener("change", async event => { try { await api("/api/maintenance/settings", { enabled: event.target.checked }); await loadMaintenance(); } catch (error) { modalError("#maintenance-error", error.message); } });
$("#maintenance-run").addEventListener("click", async () => { $("#maintenance-run").disabled = true; try { await api("/api/maintenance/run", {}); await loadMaintenance(); toast("后台维护已完成，记录保存在维护面板"); } catch (error) { modalError("#maintenance-error", error.message); } finally { $("#maintenance-run").disabled = false; } });
$("#mapping-button").addEventListener("click", () => openMappings());
$("#mapping-button").setAttribute("aria-label", "项目映射");
$("#add-project").addEventListener("click", () => openMappings(true));
$("#new-task").addEventListener("click", () => openCompose());
$("#copy-path").addEventListener("click", () => currentProject() && copy(currentProject().native_path || currentProject().windows, "项目路径已复制"));
$("#open-folder").addEventListener("click", async () => { try { await api(`/api/projects/${state.project}/open`, {}); } catch (error) { toast(error.message, true); } });
$("#refresh").addEventListener("click", () => refresh(true));
$("#filters").addEventListener("click", event => { const button = event.target.closest("[data-filter]"); if (button) { state.filter = button.dataset.filter; renderTaskList(); } });
$("#task-search").addEventListener("input", event => { state.search = event.target.value; renderTaskList(); });
document.querySelectorAll(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
$("#mobile-menu").addEventListener("click", () => { $("#sidebar").classList.toggle("open"); $("#sidebar-backdrop").classList.toggle("open"); });
$("#sidebar-backdrop").addEventListener("click", () => { $("#sidebar").classList.remove("open"); $("#sidebar-backdrop").classList.remove("open"); });
document.addEventListener("keydown", event => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
  if (event.altKey && event.key.toLowerCase() === "n") { event.preventDefault(); if (!$("dialog[open]")) openCompose(); }
  if (event.key === "/" && !typing && !$("dialog[open]")) { event.preventDefault(); $("#task-search").focus(); }
});
async function poll() { await refresh(); setTimeout(poll, document.hidden ? 7000 : 1800); }
poll();

$("#auto-sync-sources").addEventListener("change", async event => { try { await api("/api/sources/settings", { enabled: event.target.checked }); await refresh(true); toast(event.target.checked ? "已启用本机来源自动同步" : "已关闭自动同步，可手动读取来源"); } catch (error) { toast(error.message, true); await refresh(true); renderMappings(); } });
