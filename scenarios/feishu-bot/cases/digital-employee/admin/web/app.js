"use strict";

// ---- 常量 ----
const MEMORY_CATEGORIES = [
  "profile",
  "preferences",
  "facts",
  "decisions",
  "conventions",
  "notes",
];

// ---- 通用工具 ----
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, children = []) => {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([k, v]) => {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  });
  (Array.isArray(children) ? children : [children]).forEach((c) => {
    if (c == null) return;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  });
  return node;
};

function toast(msg, kind = "ok") {
  const t = $("toast");
  t.textContent = msg;
  t.className = `show ${kind}`;
  setTimeout(() => (t.className = ""), 2600);
}

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  const token = $("token").value.trim();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const opts = { method, headers };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(`/api${path}`, opts);
  let data = null;
  try {
    data = await resp.json();
  } catch (_) {
    data = null;
  }
  if (!resp.ok) {
    const message = (data && data.error) || `${resp.status} ${resp.statusText}`;
    throw new Error(message);
  }
  return data;
}

function catCheckboxes(containerId, checked) {
  const box = $(containerId);
  box.innerHTML = "";
  MEMORY_CATEGORIES.forEach((cat) => {
    const input = el("input", { type: "checkbox", value: cat });
    if (checked.includes(cat)) input.checked = true;
    box.appendChild(el("label", {}, [input, ` ${cat}`]));
  });
}

function readCats(containerId) {
  return [...$(containerId).querySelectorAll("input:checked")].map((i) => i.value);
}

// ---- Tab 切换 ----
function initTabs() {
  $("tabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-tab]");
    if (!btn) return;
    [...$("tabs").children].forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab").forEach((s) => s.classList.add("hidden"));
    $(`tab-${btn.dataset.tab}`).classList.remove("hidden");
    LOADERS[btn.dataset.tab]?.();
  });
}

// ---- 总览 ----
async function loadOverview() {
  const data = await api("GET", "/overview");
  const cards = [
    ["数字员工", data.counts.employees],
    ["项目", data.counts.projects],
    ["群绑定", data.counts.bindings],
    ["能力包", data.counts.bundles],
  ];
  $("overview-cards").innerHTML = "";
  cards.forEach(([lbl, num]) =>
    $("overview-cards").appendChild(
      el("div", { class: "card" }, [
        el("div", { class: "num" }, String(num)),
        el("div", { class: "lbl" }, lbl),
      ])
    )
  );
  const tbody = $("overview-logs");
  tbody.innerHTML = "";
  (data.recent_sync_logs || []).forEach((log) => {
    tbody.appendChild(
      el("tr", {}, [
        el("td", {}, new Date(log.created_at * 1000).toLocaleString()),
        el("td", {}, `${log.entity_type}`),
        el("td", {}, log.action),
        el("td", {}, [el("span", { class: `badge ${log.status}` }, log.status)]),
        el("td", { class: "mono" }, log.detail || ""),
      ])
    );
  });
}

// ---- 数字员工 ----
async function loadEmployees() {
  const [{ employees }, { bundles }] = await Promise.all([
    api("GET", "/employees"),
    api("GET", "/bundles"),
  ]);
  const sel = $("emp-bundle");
  sel.innerHTML = '<option value="">（无）</option>';
  bundles.forEach((b) => sel.appendChild(el("option", { value: b.id }, b.name)));

  const tbody = $("emp-list");
  tbody.innerHTML = "";
  employees.forEach((e) => {
    tbody.appendChild(
      el("tr", {}, [
        el("td", {}, e.name),
        el("td", { class: "mono" }, e.model_id),
        el("td", { class: "mono" }, e.ark_agent_id || "—"),
        el("td", { class: "mono" }, e.ark_agent_version || "—"),
        el("td", {}, [el("span", { class: `badge ${e.sync_status}` }, e.sync_status)]),
        el("td", { class: "actions" }, [
          el("button", { class: "btn small", onclick: () => editEmployee(e) }, "编辑"),
          " ",
          el("button", { class: "btn small ghost", onclick: () => syncEmployee(e.id) }, "同步"),
          " ",
          el("button", { class: "btn small danger", onclick: () => delEmployee(e.id) }, "删除"),
        ]),
      ])
    );
  });
}

function editEmployee(e) {
  $("emp-id").value = e.id;
  $("emp-name").value = e.name;
  $("emp-model").value = e.model_id;
  $("emp-prompt").value = e.identity_prompt;
  $("emp-bundle").value = e.bundle_id || "";
  $("emp-form-title").textContent = `编辑数字员工：${e.name}`;
  $("emp-cancel").classList.remove("hidden");
}

function resetEmployeeForm() {
  $("emp-id").value = "";
  $("emp-name").value = "";
  $("emp-model").value = "";
  $("emp-prompt").value = "";
  $("emp-bundle").value = "";
  $("emp-form-title").textContent = "新建数字员工";
  $("emp-cancel").classList.add("hidden");
}

async function saveEmployee() {
  const id = $("emp-id").value;
  const payload = {
    name: $("emp-name").value.trim(),
    model_id: $("emp-model").value.trim() || "doubao-seed-evolving",
    identity_prompt: $("emp-prompt").value,
    bundle_id: $("emp-bundle").value || null,
  };
  try {
    if (id) await api("PUT", `/employees/${id}`, payload);
    else await api("POST", "/employees", payload);
    toast("已保存数字员工");
    resetEmployeeForm();
    loadEmployees();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function syncEmployee(id) {
  try {
    await api("POST", `/sync/employee/${id}`);
    toast("已同步到方舟");
    loadEmployees();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function delEmployee(id) {
  if (!confirm("确认删除该数字员工？")) return;
  await api("DELETE", `/employees/${id}`);
  toast("已删除");
  loadEmployees();
}

// ---- 项目 ----
async function loadProjects() {
  const { projects } = await api("GET", "/projects");
  const tbody = $("proj-list");
  tbody.innerHTML = "";
  projects.forEach((p) => {
    tbody.appendChild(
      el("tr", {}, [
        el("td", {}, p.name),
        el("td", { class: "mono" }, p.memory_store_id || "—"),
        el("td", {}, p.reply_uses_topic ? "是" : "否"),
        el("td", { class: "mono" }, (p.writable_memory_categories || []).join(", ") || "—"),
        el("td", { class: "actions" }, [
          el("button", { class: "btn small", onclick: () => editProject(p) }, "编辑"),
          " ",
          el(
            "button",
            { class: "btn small ghost", onclick: () => provisionProjectMemory(p.id) },
            "建Store"
          ),
          " ",
          el("button", { class: "btn small danger", onclick: () => delProject(p.id) }, "删除"),
        ]),
      ])
    );
  });
}

function editProject(p) {
  $("proj-id").value = p.id;
  $("proj-name").value = p.name;
  $("proj-desc").value = p.description;
  $("proj-topic").checked = p.reply_uses_topic;
  $("proj-multimodal").checked = p.multimodal_enabled;
  $("proj-markdown").checked = p.markdown_enabled;
  catCheckboxes("proj-cats", p.writable_memory_categories || []);
  $("proj-form-title").textContent = `编辑项目：${p.name}`;
  $("proj-cancel").classList.remove("hidden");
}

function resetProjectForm() {
  $("proj-id").value = "";
  $("proj-name").value = "";
  $("proj-desc").value = "";
  $("proj-topic").checked = true;
  $("proj-multimodal").checked = true;
  $("proj-markdown").checked = true;
  catCheckboxes("proj-cats", MEMORY_CATEGORIES);
  $("proj-form-title").textContent = "新建项目";
  $("proj-cancel").classList.add("hidden");
}

async function saveProject() {
  const id = $("proj-id").value;
  const payload = {
    name: $("proj-name").value.trim(),
    description: $("proj-desc").value,
    reply_uses_topic: $("proj-topic").checked,
    multimodal_enabled: $("proj-multimodal").checked,
    markdown_enabled: $("proj-markdown").checked,
    writable_memory_categories: readCats("proj-cats"),
  };
  try {
    if (id) await api("PUT", `/projects/${id}`, payload);
    else await api("POST", "/projects", payload);
    toast("已保存项目");
    resetProjectForm();
    loadProjects();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function provisionProjectMemory(id) {
  try {
    await api("POST", `/sync/project-memory/${id}`);
    toast("已确保 Memory Store");
    loadProjects();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function delProject(id) {
  if (!confirm("确认删除该项目？其群绑定会级联删除。")) return;
  await api("DELETE", `/projects/${id}`);
  toast("已删除");
  loadProjects();
}

// ---- 群绑定 ----
async function loadBindings() {
  const [{ bindings }, { projects }, { employees }] = await Promise.all([
    api("GET", "/bindings"),
    api("GET", "/projects"),
    api("GET", "/employees"),
  ]);
  const projName = Object.fromEntries(projects.map((p) => [p.id, p.name]));
  const empName = Object.fromEntries(employees.map((e) => [e.id, e.name]));

  const pSel = $("bind-project");
  pSel.innerHTML = "";
  projects.forEach((p) => pSel.appendChild(el("option", { value: p.id }, p.name)));
  const eSel = $("bind-employee");
  eSel.innerHTML = "";
  employees.forEach((e) => eSel.appendChild(el("option", { value: e.id }, e.name)));

  const tbody = $("bind-list");
  tbody.innerHTML = "";
  bindings.forEach((b) => {
    tbody.appendChild(
      el("tr", {}, [
        el("td", { class: "mono" }, b.chat_id),
        el("td", {}, projName[b.project_id] || b.project_id),
        el("td", {}, empName[b.digital_employee_id] || b.digital_employee_id),
        el("td", { class: "actions" }, [
          el(
            "button",
            { class: "btn small danger", onclick: () => delBinding(b.chat_id) },
            "删除"
          ),
        ]),
      ])
    );
  });
}

async function saveBinding() {
  const payload = {
    chat_id: $("bind-chat").value.trim(),
    project_id: $("bind-project").value,
    digital_employee_id: $("bind-employee").value,
  };
  try {
    await api("POST", "/bindings", payload);
    toast("已绑定");
    $("bind-chat").value = "";
    loadBindings();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function delBinding(cid) {
  if (!confirm("确认删除该绑定？")) return;
  await api("DELETE", `/bindings/${encodeURIComponent(cid)}`);
  toast("已删除");
  loadBindings();
}

// ---- 能力包 ----
async function loadBundles() {
  const { bundles } = await api("GET", "/bundles");
  const tbody = $("bundle-list");
  tbody.innerHTML = "";
  bundles.forEach((b) => {
    tbody.appendChild(
      el("tr", {}, [
        el("td", {}, b.name),
        el("td", { class: "mono" }, String((b.skills || []).length)),
        el("td", { class: "mono" }, String((b.mcp_servers || []).length)),
        el("td", { class: "mono" }, (b.writable_memory_categories || []).join(", ") || "—"),
        el("td", { class: "actions" }, [
          el("button", { class: "btn small", onclick: () => editBundle(b) }, "编辑"),
          " ",
          el("button", { class: "btn small danger", onclick: () => delBundle(b.id) }, "删除"),
        ]),
      ])
    );
  });
}

function editBundle(b) {
  $("bundle-id").value = b.id;
  $("bundle-name").value = b.name;
  $("bundle-skills").value = JSON.stringify(b.skills || [], null, 2);
  $("bundle-mcp").value = JSON.stringify(b.mcp_servers || [], null, 2);
  const toggles = b.builtin_tool_toggles || {};
  $("bundle-websearch").checked = !!toggles.web_search;
  $("bundle-webfetch").checked = !!toggles.web_fetch;
  catCheckboxes("bundle-cats", b.writable_memory_categories || []);
  $("bundle-form-title").textContent = `编辑能力包：${b.name}`;
  $("bundle-cancel").classList.remove("hidden");
}

function resetBundleForm() {
  $("bundle-id").value = "";
  $("bundle-name").value = "";
  $("bundle-skills").value = "[]";
  $("bundle-mcp").value = "[]";
  $("bundle-websearch").checked = false;
  $("bundle-webfetch").checked = false;
  catCheckboxes("bundle-cats", MEMORY_CATEGORIES);
  $("bundle-form-title").textContent = "新建能力包";
  $("bundle-cancel").classList.add("hidden");
}

async function saveBundle() {
  const id = $("bundle-id").value;
  let skills, mcp;
  try {
    skills = JSON.parse($("bundle-skills").value || "[]");
    mcp = JSON.parse($("bundle-mcp").value || "[]");
  } catch (_) {
    toast("skills / mcp_servers 不是合法 JSON", "err");
    return;
  }
  const payload = {
    name: $("bundle-name").value.trim(),
    skills,
    mcp_servers: mcp,
    builtin_tool_toggles: {
      web_search: $("bundle-websearch").checked,
      web_fetch: $("bundle-webfetch").checked,
    },
    writable_memory_categories: readCats("bundle-cats"),
  };
  try {
    if (id) await api("PUT", `/bundles/${id}`, payload);
    else await api("POST", "/bundles", payload);
    toast("已保存能力包");
    resetBundleForm();
    loadBundles();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function delBundle(id) {
  if (!confirm("确认删除该能力包？")) return;
  await api("DELETE", `/bundles/${id}`);
  toast("已删除");
  loadBundles();
}

// ---- 项目记忆 ----
async function loadMemoryTab() {
  const { projects } = await api("GET", "/projects");
  const sel = $("mem-project");
  const prev = sel.value;
  sel.innerHTML = "";
  projects.forEach((p) =>
    sel.appendChild(el("option", { value: p.id, "data-store": p.memory_store_id || "" }, p.name))
  );
  if (prev) sel.value = prev;
  updateMemHint();
  if (sel.value) loadMemoryList();
}

function updateMemHint() {
  const opt = $("mem-project").selectedOptions[0];
  const store = opt ? opt.getAttribute("data-store") : "";
  $("mem-store-hint").textContent = store
    ? `Memory Store：${store}`
    : "该项目尚未创建 Memory Store，点上方「懒建」按钮创建。";
}

async function loadMemoryList() {
  const pid = $("mem-project").value;
  if (!pid) return;
  const tbody = $("mem-list");
  try {
    const { memories } = await api("GET", `/memory/${pid}`);
    tbody.innerHTML = "";
    memories.forEach((m) => {
      tbody.appendChild(
        el("tr", {}, [
          el("td", { class: "mono" }, m.path),
          el("td", {}, m.type || ""),
          el("td", { class: "mono" }, m.id),
          el("td", { class: "actions" }, [
            el("button", { class: "btn small", onclick: () => viewMemory(pid, m.id) }, "查看"),
            " ",
            el("button", { class: "btn small danger", onclick: () => delMemory(pid, m.id) }, "删除"),
          ]),
        ])
      );
    });
    if (!memories.length)
      tbody.appendChild(el("tr", {}, [el("td", { colspan: "4", class: "muted" }, "（暂无记忆条目）")]));
  } catch (err) {
    tbody.innerHTML = "";
    tbody.appendChild(el("tr", {}, [el("td", { colspan: "4", class: "muted" }, err.message)]));
  }
}

async function provisionMemoryTabStore() {
  const pid = $("mem-project").value;
  if (!pid) return;
  try {
    await api("POST", `/sync/project-memory/${pid}`);
    toast("已确保 Memory Store");
    loadMemoryTab();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function addMemory() {
  const pid = $("mem-project").value;
  const payload = { path: $("mem-path").value.trim(), content: $("mem-content").value };
  if (!payload.path) return toast("path 不能为空", "err");
  try {
    await api("POST", `/memory/${pid}`, payload);
    toast("已新增记忆");
    $("mem-path").value = "";
    $("mem-content").value = "";
    loadMemoryList();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function viewMemory(pid, mid) {
  try {
    const m = await api("GET", `/memory/${pid}/${mid}`);
    const next = prompt(`编辑 ${m.path} 的内容：`, m.content);
    if (next === null) return;
    await api("PUT", `/memory/${pid}/${mid}`, { content: next });
    toast("已更新记忆");
    loadMemoryList();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function delMemory(pid, mid) {
  if (!confirm("确认删除该记忆条目？")) return;
  await api("DELETE", `/memory/${pid}/${mid}`);
  toast("已删除");
  loadMemoryList();
}

// ---- Tab 加载器映射 ----
const LOADERS = {
  overview: loadOverview,
  employees: loadEmployees,
  projects: loadProjects,
  bindings: loadBindings,
  bundles: loadBundles,
  memory: loadMemoryTab,
};

// ---- 事件绑定 ----
function bindEvents() {
  $("emp-save").onclick = saveEmployee;
  $("emp-cancel").onclick = resetEmployeeForm;
  $("proj-save").onclick = saveProject;
  $("proj-cancel").onclick = resetProjectForm;
  $("bind-save").onclick = saveBinding;
  $("bundle-save").onclick = saveBundle;
  $("bundle-cancel").onclick = resetBundleForm;
  $("mem-project").onchange = () => {
    updateMemHint();
    loadMemoryList();
  };
  $("mem-provision").onclick = provisionMemoryTabStore;
  $("mem-refresh").onclick = loadMemoryList;
  $("mem-add").onclick = addMemory;
}

// ---- 启动 ----
window.addEventListener("DOMContentLoaded", () => {
  initTabs();
  bindEvents();
  catCheckboxes("proj-cats", MEMORY_CATEGORIES);
  catCheckboxes("bundle-cats", MEMORY_CATEGORIES);
  loadOverview();
});
