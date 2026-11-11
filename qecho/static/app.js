"use strict";

const $ = (selector) => document.querySelector(selector);
const defaultPromptPlaceholder = $("#agent-prompt").placeholder;
const state = {
  token: sessionStorage.getItem("qecho.adminToken") || "",
  automation: null,
  settings: {},
  friends: [],
  selected: new Set(),
  editRevision: null,
  dirty: false,
  settingsDirty: false,
  connected: false,
  synced: false,
  loginWatch: false,
  userInteracted: false,
  syncPending: false,
  pollPending: false,
  startPending: false,
  mutation: 0,
  setup: null,
  setupPending: false,
  promptEditor: null,
  friendsFingerprint: "",
  recentFingerprint: "",
};

const recentLabels = {
  received: "收到消息",
  processing: "正在生成回复",
  generating: "正在生成回复",
  processed: "已处理",
  draft: "回复待处理",
  sending: "正在发送",
  sent: "已自动回复",
  error: "回复失败",
  unknown: "发送结果未知，请先在 QQ 核对",
  cancelled: "已取消未发送回复",
  takeover: "本人回复，已人工接管",
  paused: "该好友已人工接管",
  ignored: "已忽略",
};

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = String(text);
  return item;
}

function notify(message, error = false) {
  const toast = node("div", `toast${error ? " error" : ""}`, message);
  $("#toast-region").append(toast);
  window.setTimeout(() => toast.remove(), error ? 6500 : 4000);
}

async function api(path, options = {}) {
  const headers = {
    Authorization: `Bearer ${state.token}`,
    ...options.headers,
  };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (_error) {
    throw new Error("无法连接 QEcho，请确认服务仍在运行。");
  }
  const payload = (response.headers.get("content-type") || "").includes(
    "application/json",
  )
    ? await response.json()
    : null;
  if (!response.ok) {
    let detail = payload?.detail;
    if (Array.isArray(detail))
      detail = detail
        .map((item) => `${item.loc?.slice(1).join(".") || "字段"}：${item.msg}`)
        .join("；");
    if (response.status === 401) showAuth();
    const error = new Error(
      response.status === 401
        ? "管理令牌无效，请重新登录。"
        : typeof detail === "string"
          ? detail
          : `请求未完成（${response.status}）`,
    );
    error.status = response.status;
    throw error;
  }
  return payload;
}

function showAuth() {
  sessionStorage.removeItem("qecho.adminToken");
  state.loginWatch = false;
  state.setup = null;
  state.promptEditor = null;
  $("#webhook-token").value = "";
  $("#app-shell").hidden = true;
  $("#auth-screen").hidden = false;
}

function sameSelection(a, b) {
  return a.size === b.length && b.every((id) => a.has(String(id)));
}

function fileMode() {
  return state.automation?.config_source === "file";
}

function savedPrompt(editor) {
  const { data, scope, key } = editor;
  if (scope === "global") return data?.global_prompt || "";
  const prompts = scope === "group" ? data?.groups : data?.friend_prompts;
  return prompts && Object.hasOwn(prompts, key) ? prompts[key] : "";
}

function promptDirty() {
  const editor = state.promptEditor;
  return Boolean(editor?.data && $("#agent-prompt").value !== savedPrompt(editor));
}

function validPromptScope(editor) {
  if (!editor?.data) return false;
  if (editor.scope === "global") return true;
  if (editor.scope === "group")
    return Object.hasOwn(editor.data.groups, editor.key);
  return state.selected.has(editor.key);
}

function renderPromptOptions() {
  const editor = state.promptEditor;
  if (!editor?.data) return;
  const groups = Object.keys(editor.data.groups);
  const names = new Map(
    state.friends.map((friend) => [String(friend.qq_id), friend.name]),
  );
  const friends = [...state.selected].map((id) => [id, names.get(id)]);
  const selected = JSON.stringify([editor.scope, editor.key]);
  const missing = validPromptScope(editor) ? "" : selected;
  const fingerprint = JSON.stringify([groups, friends, missing]);
  if (fingerprint === editor.optionsFingerprint) return;
  editor.optionsFingerprint = fingerprint;

  const select = $("#prompt-scope");
  function option(scope, key, label) {
    const item = node("option", "", label);
    item.value = JSON.stringify([scope, key]);
    return item;
  }
  select.replaceChildren(option("global", "", "全局提示词"));
  if (groups.length) {
    const choices = node("optgroup");
    choices.label = "分组";
    choices.append(...groups.map((name) => option("group", name, name)));
    select.append(choices);
  }
  if (friends.length) {
    const choices = node("optgroup");
    choices.label = "好友";
    choices.append(
      ...friends.map(([id, name]) =>
        option("friend", id, name ? `${name} · QQ ${id}` : `QQ ${id}`),
      ),
    );
    select.append(choices);
  }
  if (missing) select.append(option(editor.scope, editor.key, "已移除的编辑范围"));
  select.value = selected;
}

function renderPromptEditor() {
  const managed = fileMode();
  const select = $("#prompt-scope");
  if (!select) {
    $("#agent-prompt").readOnly = managed;
    return;
  }
  const editor = state.promptEditor;
  $("#prompt-scope-field").hidden = !managed;
  $("#prompt-editor-actions").hidden = !managed;
  $("#prompt-review").hidden = !managed || !editor?.review;
  if (!managed) {
    $("#agent-prompt").readOnly = false;
    return;
  }
  const valid = validPromptScope(editor);
  const dirty = promptDirty();
  const pending = Boolean(editor?.pending);
  $("#agent-prompt").readOnly = pending || !valid;
  select.disabled = pending || !editor?.data || Boolean(editor.conflict);
  $("#save-prompt").disabled =
    pending || !valid || !dirty || Boolean(editor?.conflict);
  $("#reload-prompt").disabled = pending;
  $("#reload-prompt").textContent = editor?.conflict
    ? "查看最新版本"
    : dirty
      ? "取消修改"
      : "重新加载";
  const status = $("#prompt-editor-status");
  status.classList.toggle("error", Boolean(editor?.error));
  let message = editor?.message || (dirty ? "有未保存的修改。" : "");
  if (editor?.pending === "saving") message = "正在保存提示词…";
  else if (editor?.pending === "loading") message = "正在加载提示词…";
  else if (editor?.data && !valid) message = "此范围已从配置移除。可复制草稿后切换范围。";
  if (status.textContent !== message) status.textContent = message;
  if (editor?.review) {
    const review = $("#prompt-review-text");
    const latest = savedPrompt(editor) || "未设置提示词";
    if (review.textContent !== latest) review.textContent = latest;
  }
}

async function loadPromptEditor() {
  const editor = state.promptEditor;
  if (!editor || editor.pending) return;
  const draft = editor.conflict ? $("#agent-prompt").value : null;
  editor.pending = "loading";
  editor.error = false;
  renderPromptEditor();
  try {
    const data = await api("/api/automation/prompts");
    if (editor !== state.promptEditor) return;
    editor.data = data;
    editor.conflict = false;
    editor.review = draft !== null;
    $("#agent-prompt").value = draft ?? savedPrompt(editor);
    editor.message = editor.review
      ? "请对照已保存的文本，再决定是否保存上方的草稿。"
      : "";
    renderPromptOptions();
  } catch (error) {
    if (editor !== state.promptEditor) return;
    editor.error = true;
    editor.message = error.message;
  } finally {
    editor.pending = "";
    if (editor === state.promptEditor) renderControls();
  }
}

async function savePromptEditor() {
  const editor = state.promptEditor;
  if (!validPromptScope(editor) || editor.pending || editor.conflict || !promptDirty())
    return;
  const prompt = $("#agent-prompt").value;
  if (editor.scope !== "friend" && !prompt.trim()) {
    editor.error = true;
    editor.message = "请填写提示词。";
    renderPromptEditor();
    return;
  }
  editor.pending = "saving";
  editor.error = false;
  renderPromptEditor();
  try {
    const data = await api("/api/automation/prompts", {
      method: "PATCH",
      body: {
        scope: editor.scope,
        key: editor.key,
        prompt,
        expected_revision: editor.data.revision,
      },
    });
    if (editor !== state.promptEditor) return;
    editor.data = data;
    editor.review = false;
    editor.message = "提示词已保存，下一条来信将使用新规则。";
    $("#agent-prompt").value = savedPrompt(editor);
    refreshAutomation().catch(() => {
      /* The regular status poll will retry. */
    });
  } catch (error) {
    if (editor !== state.promptEditor) return;
    editor.error = true;
    editor.conflict = error.status === 409;
    editor.review = false;
    editor.message = editor.conflict
      ? `${error.message} 草稿已保留。查看最新版本后可继续保存。`
      : error.message;
  } finally {
    editor.pending = "";
    if (editor === state.promptEditor) renderControls();
  }
}

function markDirty() {
  const applied = state.automation;
  state.dirty =
    !applied ||
    !sameSelection(state.selected, applied.selected_ids || []) ||
    $("#agent-prompt").value !== (applied.prompt || "");
  if (!state.dirty && applied) state.editRevision = applied.revision;
  renderControls();
}

function safeLoginUrl(value) {
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) &&
      !parsed.username &&
      !parsed.password
      ? parsed.href
      : null;
  } catch (_error) {
    return null;
  }
}

function applyAutomation(
  payload,
  { adoptDraft = false, preserveDraft = false } = {},
) {
  if (!payload) return;
  if (
    state.automation &&
    Number(payload.revision) < Number(state.automation.revision)
  )
    return;
  state.automation = payload;
  if (fileMode()) {
    state.selected = new Set((payload.configured_ids || []).map(String));
    state.dirty = false;
    if (!$("#prompt-scope")) {
      $("#agent-prompt").value = payload.prompt || "";
    } else if (!state.promptEditor) {
      state.promptEditor = { scope: "global", key: "", data: null };
      $("#agent-prompt").value = "";
      if (!$("#app-shell").hidden) loadPromptEditor();
    }
    renderPromptOptions();
  } else {
    state.promptEditor = null;
    if (adoptDraft || (!state.dirty && !preserveDraft)) {
      state.selected = new Set((payload.selected_ids || []).map(String));
      $("#agent-prompt").value = payload.prompt || "";
      state.editRevision = payload.revision;
      state.dirty = false;
    }
  }
  const loginUrl = safeLoginUrl(
    payload.login_url || state.settings.bridge_login_url,
  );
  if (loginUrl) $("#qq-login").href = loginUrl;
  $("#account-status").textContent = payload.account_id
    ? `${state.connected ? "已连接" : "已绑定"} QQ ${payload.account_id}`
    : "登录后自动识别账号并获取好友。";
  $("#model-notice").hidden = Boolean(payload.model_ready);
  renderControls();
  renderFriends();
  renderRecent();
}

function renderControls() {
  const automation = state.automation || {};
  const running = Boolean(automation.running);
  const managed = fileMode();
  const editablePrompts = managed && Boolean(state.promptEditor);
  const paused = Array.isArray(automation.paused_ids)
    ? automation.paused_ids.length
    : 0;
  const active =
    automation.active_count ??
    Math.max(0, (automation.selected_ids || []).length - paused);
  const pill = $("#running-pill");
  pill.replaceChildren(
    node("span", "status-dot"),
    document.createTextNode(running ? "自动回复中" : "已停止"),
  );
  pill.classList.toggle("running", running);
  $("#automation-summary").textContent = running
    ? `${active} 位好友自动回复中${paused ? ` · ${paused} 位已人工接管` : ""}`
    : "全部代聊已停止";
  let description;
  if (managed) {
    const messages = {
      disabled: "自动启动未开启。请在配置文件设置 auto_start = true 后重启。",
      waiting: "正在等待 NapCat 连接与 QQ 登录；可随时点击停止。",
      starting: "正在核对 QQ 账号和配置中的好友范围；可随时点击停止。",
      running: running
        ? "正在自动回复。恢复已人工接管的好友需重启。"
        : "自动回复已暂停。请核对 QQ 和最近状态，修改配置后重启。",
      stopped: "已停止。本次运行不会自动恢复；需要继续时关闭终端并重新启动。",
      failed: automation.startup_error || "自动启动失败，请检查配置后重启。",
    };
    description = messages[automation.startup_state] || "配置文件已加载。";
  } else if (state.startPending)
    description = "正在检查连接并启动，期间仍可随时停止。";
  else if (state.dirty)
    description = "有未应用的修改，点击“保存并开始自动回复”后生效。";
  else if (paused && running)
    description = "人工接管仅暂停对应好友，再次开始可恢复所选好友。";
  else
    description = running
      ? "只回复已选好友，可随时停止。"
      : "选好好友和提示词，保存后开始。";
  $("#apply-state").textContent = description;
  $("#apply-state").classList.toggle("unapplied", state.dirty);
  $("#selected-count").textContent = `已选 ${state.selected.size} 位`;
  $("#prompt-count").textContent = `${$("#agent-prompt").value.length} 字`;
  $("#start-automation").disabled = state.startPending;
  $("#start-automation").hidden = managed;
  $("#settings-panel").hidden = managed;
  $("#clear-selection").hidden = managed;
  $("#config-notice").hidden = !managed;
  $("#config-notice").textContent = managed
    ? `代聊范围来自 ${automation.config_file}。${description}`
    : "";
  $("#page-intro").textContent = editablePrompts
    ? "查看运行状态、编辑提示词、登录 QQ 或停止代聊。"
    : managed
      ? "查看运行状态、登录 QQ 或停止代聊。"
      : "登录 QQ，勾选好友，写好提示词，然后开始。";
  $("#friends-title").textContent = managed ? "配置中的代聊好友" : "选择代聊好友";
  $("#friend-search").placeholder = managed
    ? "搜索昵称、QQ 号或分组"
    : "搜索昵称或 QQ 号";
  const scope = state.promptEditor?.scope || "global";
  const scopeLabels = {
    global: "全局提示词",
    group: "分组提示词",
    friend: "好友专属提示词",
  };
  const scopeHelp = {
    global: "全局规则适用于所有代聊好友。",
    group: "分组提示词补充全局规则。",
    friend: "清空并保存后，继承全局和分组规则。",
  };
  $("#prompt-title").textContent = editablePrompts
    ? "提示词"
    : managed ? "全局提示词" : "告诉 QEcho 怎么聊";
  $(".prompt-card .card-heading .quiet-label").textContent = editablePrompts
    ? "保存后立即生效"
    : managed ? "所有代聊好友的基础规则" : "统一应用于所选好友";
  $("label[for='agent-prompt']").textContent = managed
    ? scopeLabels[scope]
    : "Agent 提示词";
  $("#agent-prompt").placeholder = editablePrompts
    ? `输入${scopeLabels[scope]}`
    : managed ? "未设置全局提示词" : defaultPromptPlaceholder;
  $("#prompt-help").textContent = editablePrompts
    ? scopeHelp[scope]
    : managed ? "刷新页面后可编辑提示词。" : "人格、语气、称呼、话题和边界，都可以写在这里。";
  $("#recent-note").textContent = managed
    ? "本人主动回复会接管该好友；停止后需重新启动 QEcho 才能恢复代聊。"
    : "本人主动回复会接管该好友；“停止”会暂停全部代聊。再次开始会按当前选择重新启用。";
  $("#model-notice").textContent = managed
    ? "请在配置文件填写真实模型地址和名称，再重启。"
    : "还未配置真实模型。展开下方连接设置，填写模型地址和名称。";
  renderPromptEditor();
  // Stop intentionally stays enabled, even while a start request is in flight.
}

function renderFriends() {
  const query = $("#friend-search").value.trim().toLowerCase();
  const managed = fileMode();
  const profiles = state.automation?.friend_prompt_profiles || {};
  const friends = managed
    ? [...state.selected].map((id) => {
        const friend = state.friends.find((item) => String(item.qq_id) === id);
        const profile = profiles[id];
        return {
          qq_id: id,
          name: friend?.name || id,
          promptGroup: profile?.group ?? "全局默认",
          hasPersonalPrompt: profile?.has_personal_prompt === true,
        };
      })
    : state.friends;
  const filtered = friends.filter((friend) =>
    `${friend.name} ${friend.qq_id} ${friend.promptGroup || ""}`
      .toLowerCase()
      .includes(query),
  );
  const fingerprint = JSON.stringify([
    filtered,
    [...state.selected].sort(),
    state.synced,
    managed,
  ]);
  if (fingerprint === state.friendsFingerprint) return;
  state.friendsFingerprint = fingerprint;
  const list = $("#friends-list");
  list.replaceChildren();
  if (!filtered.length) {
    const empty = node("div", "friends-empty");
    const searchHint = managed
      ? "换一个昵称、QQ 号或分组试试。"
      : "换一个昵称或 QQ 号试试。";
    empty.append(
      node(
        "strong",
        "",
        query
          ? "没有找到这位好友"
          : state.synced
            ? "好友列表为空"
            : "等待登录 QQ",
      ),
    );
    empty.append(
      node(
        "span",
        "",
        query
          ? searchHint
          : state.synced
            ? "确认 QQ 好友已加载，再点击同步好友。"
            : "点击上方“登录 QQ”，登录后自动获取好友。",
      ),
    );
    list.append(empty);
  }
  for (const friend of filtered) {
    const id = String(friend.qq_id);
    const label = node(
      "label",
      `friend-row${state.selected.has(id) ? " selected" : ""}`,
    );
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selected.has(id);
    checkbox.disabled = managed;
    checkbox.setAttribute("aria-label", `代聊好友 ${friend.name}，QQ ${id}`);
    const avatar = node(
      "span",
      "friend-avatar",
      (friend.name || "Q").trim().slice(0, 1),
    );
    const copy = node("span", "friend-copy");
    copy.append(
      node("strong", "", friend.name || id),
      node("small", "", `QQ ${id}`),
    );
    if (managed) {
      const promptProfile = friend.hasPersonalPrompt
        ? `${friend.promptGroup} · 专属提示词`
        : friend.promptGroup;
      copy.append(node("small", "friend-prompt-profile", promptProfile));
    }
    label.append(checkbox, avatar, copy);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selected.add(id);
      else state.selected.delete(id);
      label.classList.toggle("selected", checkbox.checked);
      state.friendsFingerprint = "";
      markDirty();
    });
    list.append(label);
  }
  $("#friend-total").textContent = managed
    ? `配置指定 ${friends.length} 位好友`
    : state.synced
    ? `共 ${state.friends.length} 位好友`
    : "尚未同步好友";
}

function timeText(value) {
  if (!value) return "";
  const date = new Date(
    typeof value === "number" && value < 1e12 ? value * 1000 : value,
  );
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
}

function renderRecent() {
  const recent = (state.automation?.recent || []).slice(0, 8);
  const fingerprint = JSON.stringify(recent);
  if (fingerprint === state.recentFingerprint) return;
  state.recentFingerprint = fingerprint;
  const list = $("#recent-list");
  list.replaceChildren();
  if (!recent.length) {
    list.append(
      node("p", "empty-recent", "还没有运行记录。开始后，这里会显示回复状态。"),
    );
    return;
  }
  for (const item of recent) {
    const row = node("div", "recent-row");
    const error = ["error", "unknown"].includes(item.status);
    const status = `${recentLabels[item.status] || item.status || "已处理"}${item.error ? ` · ${item.error}` : ""}`;
    row.append(
      node("span", "recent-name", item.contact_name || "QQ 好友"),
      node("span", `recent-status${error ? " error" : ""}`, status),
      node("time", "recent-time", timeText(item.created_at)),
    );
    list.append(row);
  }
}

async function refreshAutomation(options = {}) {
  const payload = await api("/api/automation");
  applyAutomation(payload, options);
  return payload;
}

async function syncFriends({ silent = false } = {}) {
  if (state.syncPending) return;
  state.syncPending = true;
  $("#sync-friends").disabled = true;
  try {
    const payload = await api("/api/qq/sync", { method: "POST", body: {} });
    state.connected = true;
    state.synced = true;
    state.loginWatch = false;
    state.friends = Array.isArray(payload.friends) ? payload.friends : [];
    applyAutomation(payload);
    const known = new Set(state.friends.map((friend) => String(friend.qq_id)));
    const removed = fileMode() ? [] : [...state.selected].filter((id) => !known.has(id));
    for (const id of removed) state.selected.delete(id);
    if (removed.length) markDirty();
    state.friendsFingerprint = "";
    renderFriends();
    renderControls();
    $("#connection-message").textContent = fileMode()
      ? "好友已同步，代聊范围由配置文件决定。"
      : `好友已同步${removed.length ? `，${removed.length} 位已不在好友列表，已取消选择` : "，勾选后再开始代聊"}。`;
    $("#connection-message").classList.remove("error");
    if (!silent)
      notify(`已同步 ${state.friends.length} 位好友${fileMode() ? "。" : "，没有自动勾选新好友。"}`);
  } catch (error) {
    state.connected = false;
    if (state.userInteracted) {
      $("#connection-message").textContent =
        `${error.message} 请确认已在 NapCat 登录，并启用 HTTP 连接。`;
      $("#connection-message").classList.add("error");
    }
    if (!silent) notify(error.message, true);
  } finally {
    state.syncPending = false;
    $("#sync-friends").disabled = false;
  }
}

function renderSettings() {
  for (const name of [
    "model_mode",
    "model_base_url",
    "model_name",
    "bridge_base_url",
    "bridge_login_url",
  ]) {
    $(`#settings-form [name="${name}"]`).value = state.settings[name] ?? "";
  }
  $("#model-key-status").textContent = state.settings.model_api_key_set
    ? "已设置"
    : "未设置";
  $("#bridge-key-status").textContent = state.settings.bridge_token_set
    ? "已设置"
    : "未设置";
  $("#model-api-key").value = $("#bridge-token").value = "";
  $("#clear-model-key").checked = $("#clear-bridge-token").checked = false;
  state.settingsDirty = false;
}

async function loadSetup() {
  if (state.setup || state.setupPending || !$("#qq-settings").open) return;
  state.setupPending = true;
  $("#setup-error").textContent = "";
  try {
    state.setup = await api("/api/setup");
    $("#webhook-url").value =
      state.setup.webhook_url || `${location.origin}/webhooks/onebot`;
    $("#webhook-token").value = state.setup.webhook_token || "";
  } catch (error) {
    $("#setup-error").textContent = error.message;
  } finally {
    state.setupPending = false;
  }
}

async function copyInput(selector) {
  const input = $(selector);
  if (!input.value) {
    notify("内容尚未加载，请稍后再试。", true);
    return;
  }
  try {
    if (navigator.clipboard?.writeText)
      await navigator.clipboard.writeText(input.value);
    else {
      input.select();
      if (!document.execCommand("copy")) throw new Error("copy");
    }
    notify("已复制。");
  } catch (_error) {
    input.focus();
    input.select();
    notify("浏览器未允许复制，请手动复制选中内容。", true);
  }
}

async function enterApp() {
  const [settings, automation] = await Promise.all([
    api("/api/settings"),
    api("/api/automation"),
  ]);
  state.settings = settings;
  state.automation = null;
  state.dirty = false;
  state.connected = state.synced = state.loginWatch = false;
  state.userInteracted = false;
  state.friends = [];
  state.friendsFingerprint = state.recentFingerprint = "";
  state.setup = null;
  state.promptEditor = null;
  $("#qq-settings").open = false;
  $("#settings-panel").open = false;
  renderSettings();
  applyAutomation(automation, { adoptDraft: true });
  $("#connection-message").textContent = fileMode()
    ? "连接参数来自配置文件，QQ 扫码登录由 NapCat 提供。"
    : "登录由 NapCat 提供，首次部署需在下方配置连接。";
  $("#connection-message").classList.remove("error");
  $("#auth-screen").hidden = true;
  $("#app-shell").hidden = false;
  if (state.promptEditor) loadPromptEditor();
  // One quiet check recognizes an already-logged-in bridge; repeated login checks
  // only begin after the user opens the QQ login page.
  syncFriends({ silent: true });
}

async function poll() {
  if (document.hidden || $("#app-shell").hidden || state.pollPending) return;
  if (state.loginWatch && !state.syncPending) {
    await syncFriends({ silent: true });
    return;
  }
  state.pollPending = true;
  try {
    await refreshAutomation();
  } catch (_error) {
    /* Foreground actions show errors; polling does not repeat them. */
  } finally {
    state.pollPending = false;
  }
}

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.token = $("#auth-token").value.trim();
  if (!state.token) return;
  $("#auth-submit").disabled = true;
  $("#auth-error").textContent = "";
  try {
    await enterApp();
    sessionStorage.setItem("qecho.adminToken", state.token);
    $("#auth-token").value = "";
  } catch (error) {
    $("#auth-error").textContent = error.message;
  } finally {
    $("#auth-submit").disabled = false;
  }
});

$("#logout").addEventListener("click", () => {
  if (
    (state.dirty || state.settingsDirty || promptDirty()) &&
    !window.confirm("有未应用的修改，确认退出？")
  )
    return;
  state.token = "";
  showAuth();
});

$("#qq-login").addEventListener("click", (event) => {
  if (
    !safeLoginUrl(
      state.automation?.login_url || state.settings.bridge_login_url,
    )
  ) {
    event.preventDefault();
    $("#settings-panel").open = $("#qq-settings").open = true;
    notify("请先在连接设置填写有效的 QQ 登录页地址。", true);
    return;
  }
  state.userInteracted = true;
  state.loginWatch = true;
  $("#connection-message").textContent =
    "请在新页面完成 NapCat 认证并选择扫码登录，登录后此处会自动同步好友。";
  $("#connection-message").classList.remove("error");
});
$("#sync-friends").addEventListener("click", () => {
  state.userInteracted = true;
  syncFriends();
});
$("#friend-search").addEventListener("input", renderFriends);
$("#clear-selection").addEventListener("click", () => {
  state.selected.clear();
  state.friendsFingerprint = "";
  markDirty();
  renderFriends();
});
$("#agent-prompt").addEventListener("input", () => {
  const editor = state.promptEditor;
  if (!editor) {
    markDirty();
    return;
  }
  if (!editor.conflict) {
    editor.message = "";
    editor.error = false;
  }
  renderControls();
});

$("#prompt-scope")?.addEventListener("change", (event) => {
  const editor = state.promptEditor;
  if (!editor?.data || editor.pending || editor.conflict || !event.target.value) return;
  if (promptDirty() && !window.confirm("切换范围会放弃当前草稿，继续切换？")) {
    event.target.value = JSON.stringify([editor.scope, editor.key]);
    return;
  }
  [editor.scope, editor.key] = JSON.parse(event.target.value);
  $("#agent-prompt").value = savedPrompt(editor);
  editor.review = editor.error = false;
  editor.message = "";
  renderPromptOptions();
  renderControls();
});

$("#reload-prompt")?.addEventListener("click", () => {
  const editor = state.promptEditor;
  if (!editor || editor.pending) return;
  if (editor.conflict || !promptDirty()) {
    loadPromptEditor();
    return;
  }
  $("#agent-prompt").value = savedPrompt(editor);
  editor.review = editor.error = false;
  editor.message = "";
  renderControls();
});
$("#save-prompt")?.addEventListener("click", savePromptEditor);

$("#start-automation").addEventListener("click", async () => {
  if (state.startPending) return;
  const prompt = $("#agent-prompt").value.trim();
  if (!state.selected.size) {
    notify("请先勾选要代聊的好友。", true);
    $("#friends-list").scrollIntoView({ block: "center", behavior: "smooth" });
    return;
  }
  if (!prompt) {
    notify("请先写下 Agent 提示词。", true);
    $("#agent-prompt").focus();
    return;
  }
  if (state.settingsDirty) {
    notify("请先保存连接设置，再开始自动回复。", true);
    $("#settings-panel").open = true;
    return;
  }
  const expectedRevision = state.editRevision;
  const requestedIds = [...state.selected];
  const ticket = ++state.mutation;
  state.startPending = true;
  renderControls();
  try {
    const fresh = await refreshAutomation({ preserveDraft: true });
    if (ticket !== state.mutation) return;
    if (fresh.revision !== expectedRevision) {
      state.editRevision = fresh.revision;
      state.dirty = true;
      notify(
        "运行配置已变化，已刷新状态并保留你的修改。请确认后再次点击开始。",
        true,
      );
      return;
    }
    const payload = await api("/api/automation/start", {
      method: "POST",
      body: {
        qq_ids: requestedIds,
        prompt,
        expected_revision: expectedRevision,
      },
    });
    if (ticket !== state.mutation) return;
    const unchanged =
      sameSelection(state.selected, requestedIds) &&
      $("#agent-prompt").value.trim() === prompt;
    applyAutomation(payload, {
      adoptDraft: unchanged,
      preserveDraft: !unchanged,
    });
    state.editRevision = state.automation.revision;
    if (!unchanged) state.dirty = true;
    notify("已开始自动回复，仅作用于当前选择的好友。");
  } catch (error) {
    if (error.status === 409) {
      try {
        const latest = await refreshAutomation({ preserveDraft: true });
        state.editRevision = latest.revision;
        state.dirty = true;
      } catch (_error) {
        /* Keep the original error visible. */
      }
      notify(`${error.message} 请确认当前范围与提示词后重试。`, true);
    } else {
      try {
        await refreshAutomation({ preserveDraft: true });
      } catch (_error) {
        /* Keep the original error visible. */
      }
      notify(error.message, true);
    }
  } finally {
    state.startPending = false;
    renderControls();
  }
});

$("#stop-automation").addEventListener("click", async () => {
  ++state.mutation;
  try {
    const payload = await api("/api/automation/stop", {
      method: "POST",
      body: {},
    });
    applyAutomation(payload, { preserveDraft: true });
    state.editRevision = state.automation.revision;
    notify("全部代聊已停止，尚未发送的回复已取消。");
  } catch (error) {
    notify(error.message, true);
  }
});

$("#settings-form").addEventListener("input", () => {
  state.settingsDirty = true;
});
$("#settings-form").addEventListener("change", () => {
  state.settingsDirty = true;
});
$("#settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#save-settings");
  if (button.disabled) return;
  button.disabled = true;
  ++state.mutation;
  const body = { enabled: false };
  for (const [name, value] of new FormData($("#settings-form")).entries()) {
    if (["model_api_key", "bridge_token"].includes(name) && !value.trim())
      continue;
    body[name] = value.trim();
  }
  if ($("#clear-model-key").checked) body.clear_model_api_key = true;
  if ($("#clear-bridge-token").checked) body.clear_bridge_token = true;
  try {
    state.settings = await api("/api/settings", { method: "PATCH", body });
    renderSettings();
    state.connected = false;
    const payload = await refreshAutomation({ preserveDraft: true });
    state.editRevision = payload.revision;
    notify("连接设置已保存，代聊已暂停。准备好后再点击开始。");
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#qq-settings").addEventListener("toggle", () => {
  if ($("#qq-settings").open) loadSetup();
});
$("#copy-webhook-url").addEventListener("click", () =>
  copyInput("#webhook-url"),
);
$("#copy-webhook-token").addEventListener("click", () =>
  copyInput("#webhook-token"),
);
window.addEventListener("beforeunload", (event) => {
  if (state.dirty || state.settingsDirty || promptDirty()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
window.setInterval(poll, 3000);
if (state.token)
  enterApp().catch((error) => {
    showAuth();
    $("#auth-error").textContent = error.message;
  });
else showAuth();
