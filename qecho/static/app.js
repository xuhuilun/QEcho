"use strict";

const $ = (selector) => document.querySelector(selector);
const state = {
  token: sessionStorage.getItem("qecho.adminToken") || "",
  settings: {},
  contacts: [],
  messages: [],
  selected: null,
  source: "simulator",
  view: "workspace",
  profileDirty: false,
  settingsDirty: false,
  messageFingerprint: "",
  loadingMessages: false,
  draftEdits: new Map(),
  busyDrafts: new Set(),
  polling: false,
};

const presets = {
  daily: {
    persona:
      "自然、真诚、有自己的想法，像平时的我一样聊天。关注对方说的具体内容，不刻意表现，不机械迎合。",
    tone: "口语化，轻松亲切。通常用一两句话回应，可以适度开玩笑，少用表情。",
    instructions:
      "接住当前话题，避免每条回复都追问。不知道的个人事实不要编造。",
  },
  crush: {
    persona:
      "真诚、有趣、温柔，有分寸地表达好感。认真听对方说话，记住已确认的共同话题，保留自己的态度和生活感。",
    tone: "轻松自然，偶尔带一点俏皮。回复简短，避免油腻的情话、过度赞美和连续追问。",
    instructions:
      "从对方提到的细节展开话题，表达关心时具体一点。尊重对方的节奏，不催回复。涉及我的真实安排或重要决定时，不擅自编造或答应。",
  },
  concise: {
    persona: "靠谱、清晰、务实。先理解对方的事情，直接回应重点。",
    tone: "礼貌、简洁，少寒暄，不使用夸张语气和不必要的表情。",
    instructions:
      "优先给出明确答复。需要补充信息时只问最关键的问题。未经确认的时间、数据和承诺不要编造。",
  },
};

const statusLabels = {
  received: "已接收",
  processing: "生成中",
  generating: "生成中",
  processed: "已处理",
  draft: "待确认草稿",
  sending: "发送中",
  sent: "已发送",
  error: "处理失败",
  unknown: "发送结果未知 · 请先核对 QQ",
  cancelled: "已取消",
  ignored: "已忽略",
  simulated: "演练回复",
  completed: "已完成",
};

function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = String(text);
  return item;
}

function notify(message, error = false) {
  const toast = element("div", `toast${error ? " error" : ""}`, message);
  $("#toast-region").append(toast);
  window.setTimeout(() => toast.remove(), error ? 6500 : 3500);
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
    throw new Error("无法连接 QEcho，请确认本地服务仍在运行。");
  }
  let payload = null;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) payload = await response.json();
  if (!response.ok) {
    let detail = payload?.detail;
    if (Array.isArray(detail))
      detail = detail
        .map((item) => `${item.loc?.slice(1).join(".") || "字段"}：${item.msg}`)
        .join("；");
    if (response.status === 401) {
      showAuth();
      throw new Error("访问令牌无效或已变更，请重新登录。");
    }
    throw new Error(
      typeof detail === "string" ? detail : `请求未完成（${response.status}）`,
    );
  }
  return payload;
}

async function withButton(button, task) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    return await task();
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function currentContact() {
  return state.contacts.find((contact) => contact.id === state.selected);
}
function avatarText(contact) {
  return (contact?.name || "Q").trim().slice(0, 1).toUpperCase();
}
function avatarColor(contact) {
  return ["peach", "sage", "sand"][(state.contacts.indexOf(contact) + 3) % 3];
}

function showAuth() {
  sessionStorage.removeItem("qecho.adminToken");
  $("#auth-screen").hidden = false;
  $("#app-shell").hidden = true;
}

function updateHeader() {
  const enabled = Boolean(state.settings.enabled);
  const status = $("#global-status");
  status.replaceChildren(
    element("span", "status-dot"),
    document.createTextNode(enabled ? "代聊已启用" : "全部代聊已暂停"),
  );
  status.classList.toggle("running", enabled);
  $("#global-toggle").textContent = enabled ? "一键暂停" : "启用代聊";
  $("#global-toggle").classList.toggle("primary", enabled);
  $("#contact-count").textContent = state.contacts.length;
  $("#model-badge").textContent =
    state.settings.model_mode === "live"
      ? state.settings.model_name || "真实模型"
      : "演练模型";
}

function renderContacts() {
  const list = $("#contact-list");
  const query = $("#contact-search").value.trim().toLowerCase();
  const contacts = state.contacts.filter((contact) =>
    `${contact.name} ${contact.qq_id}`.toLowerCase().includes(query),
  );
  list.replaceChildren();
  if (!contacts.length) {
    list.append(
      element(
        "div",
        "directory-empty",
        query
          ? "没有找到这位联系人"
          : "你的聊天名册还空着。\n点右上角 ＋，添加第一位。",
      ),
    );
    return;
  }
  for (const contact of contacts) {
    const button = element(
      "button",
      `contact-row${contact.id === state.selected ? " active" : ""}`,
    );
    button.type = "button";
    button.setAttribute("aria-pressed", String(contact.id === state.selected));
    const avatar = element(
      "span",
      `avatar ${avatarColor(contact)}`,
      avatarText(contact),
    );
    const details = element("span", "contact-row-details");
    const line = element("span", "contact-row-name");
    line.append(element("strong", "", contact.name));
    const authorized = contact.enabled && !contact.paused;
    line.append(
      element(
        "span",
        `contact-state${authorized ? "" : " off"}`,
        contact.paused
          ? "接管中"
          : contact.enabled
            ? contact.mode === "auto"
              ? "自动"
              : "草稿"
            : "未授权",
      ),
    );
    details.append(
      line,
      element(
        "span",
        "contact-row-meta",
        contact.relationship || `QQ ${contact.qq_id}`,
      ),
    );
    button.append(avatar, details);
    button.addEventListener("click", () => selectContact(contact.id));
    list.append(button);
  }
}

async function selectContact(id) {
  if (
    id !== state.selected &&
    state.profileDirty &&
    !window.confirm("当前人格修改尚未保存。离开并丢弃这些修改？")
  )
    return;
  state.selected = id;
  state.messages = [];
  state.messageFingerprint = "";
  state.profileDirty = false;
  $("#contact-panel").classList.remove("open");
  renderContacts();
  renderConversationHeading();
  renderPersona();
  renderMessages(true);
  if (id) {
    try {
      await loadMessages(true);
    } catch (error) {
      notify(error.message, true);
    }
  }
}

function renderConversationHeading() {
  const contact = currentContact();
  $("#conversation-empty").hidden = Boolean(contact);
  $("#conversation-content").hidden = !contact;
  $("#persona-empty").hidden = Boolean(contact);
  $("#persona-form").hidden = !contact;
  if (!contact) return;
  $("#conversation-name").textContent = contact.name;
  const status = contact.paused
    ? "人工接管中"
    : !contact.enabled
      ? "尚未授权代聊"
      : contact.mode === "auto"
        ? "已授权 · 自动回复"
        : "已授权 · 草稿确认";
  $("#conversation-subtitle").textContent = `QQ ${contact.qq_id} · ${status}`;
  $("#conversation-avatar").textContent = avatarText(contact);
  $("#conversation-avatar").className = `avatar large ${avatarColor(contact)}`;
  $("#takeover").textContent = contact.paused ? "恢复代聊" : "人工接管";
  $("#takeover").classList.toggle("primary", Boolean(contact.paused));
}

function renderPersona() {
  const contact = currentContact();
  if (!contact) return;
  for (const name of [
    "name",
    "relationship",
    "persona",
    "tone",
    "facts",
    "instructions",
    "mode",
  ]) {
    $(`#contact-${name}`).value =
      contact[name] || (name === "mode" ? "draft" : "");
  }
  $("#contact-enabled").checked = Boolean(contact.enabled);
  $("#contact-qq").textContent = `QQ ${contact.qq_id}`;
  setProfileDirty(false);
}

function setProfileDirty(dirty) {
  state.profileDirty = dirty;
  $("#persona-save-state").textContent = dirty
    ? "有未保存的修改"
    : "所有变化，由你保存";
  $("#persona-save-state").classList.toggle("unsaved", dirty);
}

async function loadMessages(force = false) {
  const id = state.selected;
  if (!id || state.loadingMessages) return;
  state.loadingMessages = true;
  try {
    const messages = await api(
      `/api/contacts/${encodeURIComponent(id)}/messages`,
    );
    if (id !== state.selected) return;
    for (const draftId of state.draftEdits.keys()) {
      const record = messages.find((message) => message.id === draftId);
      if (record && record.status !== "draft") state.draftEdits.delete(draftId);
    }
    const fingerprint = JSON.stringify(messages);
    if (force || fingerprint !== state.messageFingerprint) {
      state.messages = messages;
      state.messageFingerprint = fingerprint;
      renderMessages(force);
    }
  } finally {
    state.loadingMessages = false;
  }
}

function formatTime(raw) {
  if (!raw) return "";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function renderMessages(forceScroll = false) {
  const list = $("#messages");
  const nearBottom =
    list.scrollHeight - list.scrollTop - list.clientHeight < 100;
  const contact = currentContact();
  const messages = state.messages.filter(
    (message) => message.source === state.source,
  );
  const latestIncoming = state.messages
    .filter(
      (message) => message.source === "qq" && message.direction === "incoming",
    )
    .at(-1);
  const latestReply = latestIncoming
    ? state.messages
        .filter(
          (message) =>
            message.parent_id === latestIncoming.id &&
            message.direction === "outgoing",
        )
        .at(-1)
    : null;
  list.replaceChildren();
  if (!messages.length) {
    const empty = element("div", "messages-empty");
    empty.append(element("div", "empty-bubbles"));
    empty.append(
      element(
        "strong",
        "",
        state.source === "simulator" ? "先试着聊一聊" : "等待真实 QQ 消息",
      ),
    );
    empty.append(
      element(
        "span",
        "",
        state.source === "simulator"
          ? "在下方输入对方可能说的话，体验你设定的人格。"
          : "完成连接并授权后，好友私聊会出现在这里。",
      ),
    );
    list.append(empty);
    return;
  }
  list.append(
    element(
      "div",
      "message-day",
      state.source === "simulator" ? "只属于你的本地演练" : "QQ 私聊记录",
    ),
  );
  for (const message of messages) {
    const outgoing = message.direction === "outgoing";
    const item = element(
      "article",
      `message-item${outgoing ? " outgoing" : ""}`,
    );
    const label = element("div", "message-label");
    label.append(
      element("span", "", outgoing ? "QEcho" : contact?.name || "对方"),
      element("span", "", formatTime(message.created_at)),
    );
    item.append(label);
    const actionable =
      outgoing && message.source === "qq" && message.status === "draft";
    if (actionable) {
      item.append(createDraftEditor(message));
    } else {
      item.append(
        element(
          "div",
          "message-bubble",
          message.content || (message.status === "error" ? "未生成回复" : "…"),
        ),
      );
    }
    if (
      outgoing ||
      ["error", "unknown", "cancelled"].includes(message.status)
    ) {
      const meta = element("div", "message-meta");
      meta.append(
        element(
          "span",
          `message-status ${message.status}`,
          message.source === "simulator" &&
            !["error", "cancelled"].includes(message.status)
            ? "本地演练 · 未发送 QQ"
            : statusLabels[message.status] || message.status,
        ),
      );
      item.append(meta);
    }
    if (message.error)
      item.append(element("p", "message-error", message.error));
    const canRegenerate =
      message.source === "qq" &&
      (latestReply
        ? message.id === latestReply.id &&
          ["draft", "error", "cancelled"].includes(message.status)
        : message.id === latestIncoming?.id &&
          ["processed", "cancelled", "error"].includes(message.status));
    if (canRegenerate) {
      const regenerate = element(
        "button",
        "text-button regenerate-button",
        "↻ 按最新规则重新生成",
      );
      regenerate.type = "button";
      regenerate.addEventListener("click", () => {
        if (state.profileDirty || state.settingsDirty) {
          notify("请先保存规则修改，再重新生成。", true);
          return;
        }
        withButton(regenerate, async () => {
          await api(
            `/api/messages/${encodeURIComponent(message.id)}/regenerate`,
            { method: "POST", body: {} },
          );
          await loadMessages(true);
          notify("已按最新规则生成新草稿，请查看后确认。");
        });
      });
      item.append(regenerate);
    }
    list.append(item);
  }
  if (forceScroll || nearBottom)
    requestAnimationFrame(() => {
      list.scrollTop = list.scrollHeight;
    });
}

function createDraftEditor(message) {
  const editor = element("div", "draft-editor");
  const heading = element("div", "draft-editor-label");
  heading.append(
    element("span", "", "回复草稿"),
    element("span", "", "由你确认后发送"),
  );
  const textarea = element("textarea");
  textarea.rows = 3;
  textarea.maxLength = Math.min(
    Number(state.settings.max_reply_chars) || 800,
    10000,
  );
  textarea.value = state.draftEdits.get(message.id) ?? message.content ?? "";
  textarea.setAttribute("aria-label", "编辑回复草稿");
  textarea.addEventListener("input", () =>
    state.draftEdits.set(message.id, textarea.value),
  );
  const actions = element("div", "draft-actions");
  const cancel = element("button", "button compact", "取消草稿");
  const send = element("button", "button primary compact", "确认发送");
  cancel.type = send.type = "button";
  cancel.disabled = send.disabled = state.busyDrafts.has(message.id);
  send.title = "发送前会重新检查联系人授权和全局状态";
  cancel.addEventListener("click", () =>
    actOnDraft(message, "cancel", {}, cancel),
  );
  send.addEventListener("click", () => {
    const content = textarea.value.trim();
    if (!content) {
      notify("请先填写回复内容。", true);
      textarea.focus();
      return;
    }
    if (!state.settings.enabled) {
      notify("全部代聊已暂停，请先在顶部启用。", true);
      return;
    }
    if (state.settings.model_mode !== "live") {
      notify("演练模型的草稿不能发 QQ，请切换真实模型并重新生成。", true);
      return;
    }
    actOnDraft(message, "send", { content }, send);
  });
  actions.append(cancel, send);
  editor.append(heading, textarea, actions);
  return editor;
}

async function actOnDraft(message, action, body, button) {
  state.busyDrafts.add(message.id);
  await withButton(button, async () => {
    await api(`/api/messages/${encodeURIComponent(message.id)}/${action}`, {
      method: "POST",
      body,
    });
    state.draftEdits.delete(message.id);
    await loadMessages();
    notify(
      action === "send" ? "发送请求已处理，请查看消息状态。" : "草稿已取消。",
    );
  });
  state.busyDrafts.delete(message.id);
  renderMessages();
}

function setSource(source) {
  state.source = source;
  for (const button of document.querySelectorAll("[data-source]")) {
    const active = button.dataset.source === source;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  }
  $("#simulate-form").hidden = source !== "simulator";
  $("#qq-footer").hidden = source === "simulator";
  $("#conversation-notice").lastElementChild.textContent =
    source === "simulator"
      ? "演练消息只保存在本地，不会发送给 QQ 联系人。"
      : "只有已授权联系人会进入代聊。你可随时接管，停止后续自动回复。";
  renderMessages(true);
}

function setView(view) {
  state.view = view;
  $("#workspace-view").hidden = view !== "workspace";
  $("#settings-view").hidden = view !== "settings";
  for (const button of document.querySelectorAll("[data-view]"))
    button.classList.toggle("active", button.dataset.view === view);
  if (view === "settings" && !state.settingsDirty) renderSettings();
}

function renderSettings() {
  const names = [
    "owner_name",
    "global_prompt",
    "model_mode",
    "model_base_url",
    "model_name",
    "temperature",
    "context_messages",
    "max_reply_chars",
    "qq_self_id",
    "bridge_base_url",
    "reply_delay_seconds",
  ];
  for (const name of names) {
    const input = $(`#settings-form [name="${name}"]`);
    input.value = state.settings[name] ?? "";
  }
  $("#model-key-state").textContent = state.settings.model_api_key_set
    ? "已设置"
    : "未设置";
  $("#bridge-key-state").textContent = state.settings.bridge_token_set
    ? "已设置"
    : "未设置";
  $("#model-api-key").value = "";
  $("#bridge-token").value = "";
  $("#clear-model-key").checked = false;
  $("#clear-bridge-token").checked = false;
  $("#webhook-url").textContent = `${location.origin}/webhooks/onebot`;
  state.settingsDirty = false;
}

async function start() {
  const [settings, contacts] = await Promise.all([
    api("/api/settings"),
    api("/api/contacts"),
  ]);
  state.settings = settings;
  state.contacts = contacts;
  state.selected = null;
  state.profileDirty = false;
  state.settingsDirty = false;
  state.draftEdits.clear();
  $("#auth-screen").hidden = true;
  $("#app-shell").hidden = false;
  updateHeader();
  renderSettings();
  renderContacts();
  await selectContact(contacts[0]?.id || null);
}

async function poll() {
  if (state.polling || document.hidden || $("#app-shell").hidden) return;
  state.polling = true;
  try {
    const [settings, contacts] = await Promise.all([
      api("/api/settings"),
      api("/api/contacts"),
    ]);
    state.settings = settings;
    state.contacts = contacts;
    if (state.selected && !currentContact())
      await selectContact(contacts[0]?.id || null);
    updateHeader();
    renderContacts();
    renderConversationHeading();
    if (!state.profileDirty) renderPersona();
    await loadMessages();
  } catch (_error) {
    // Foreground actions report failures; background refresh stays quiet.
  } finally {
    state.polling = false;
  }
}

function openContactDialog(prefill = {}) {
  $("#new-contact-form").reset();
  $("#new-contact-name").value = prefill.name || "";
  $("#new-contact-qq").value = prefill.qq_id || "";
  $("#contact-dialog").showModal();
}

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.token = $("#auth-token").value.trim();
  if (!state.token) return;
  $("#auth-error").textContent = "";
  $("#auth-submit").disabled = true;
  try {
    await start();
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
    (state.profileDirty || state.settingsDirty) &&
    !window.confirm("有尚未保存的修改，确认退出？")
  )
    return;
  state.token = "";
  showAuth();
});

$(".rail-brand").addEventListener("click", (event) => {
  event.preventDefault();
  setView("workspace");
});
for (const button of document.querySelectorAll("[data-view]"))
  button.addEventListener("click", () => setView(button.dataset.view));
for (const button of document.querySelectorAll("[data-source]"))
  button.addEventListener("click", () => setSource(button.dataset.source));
for (const button of document.querySelectorAll("[data-close-dialog]"))
  button.addEventListener("click", () => button.closest("dialog").close());
for (const dialog of document.querySelectorAll("dialog"))
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
$("#add-contact").addEventListener("click", () => openContactDialog());
$("#empty-add-contact").addEventListener("click", () => openContactDialog());
$("#contact-search").addEventListener("input", renderContacts);
$("#show-contacts").addEventListener("click", () =>
  $("#contact-panel").classList.toggle("open"),
);
$("#show-persona").addEventListener("click", () =>
  $("#persona-panel").classList.toggle("open"),
);
$("#close-persona").addEventListener("click", () =>
  $("#persona-panel").classList.remove("open"),
);

$("#new-contact-form").addEventListener("submit", (event) => {
  event.preventDefault();
  withButton($("#create-contact"), async () => {
    const contact = await api("/api/contacts", {
      method: "POST",
      body: {
        name: $("#new-contact-name").value.trim(),
        qq_id: $("#new-contact-qq").value.trim(),
        enabled: false,
        mode: "draft",
      },
    });
    state.contacts.push(contact);
    $("#contact-dialog").close();
    $("#contact-search").value = "";
    updateHeader();
    await selectContact(contact.id);
    if (window.innerWidth <= 1020) $("#persona-panel").classList.add("open");
    notify("联系人已添加。编辑人格后，先进行一次本地演练。");
  });
});

$("#persona-form").addEventListener("input", () => setProfileDirty(true));
$("#persona-form").addEventListener("change", () => setProfileDirty(true));
$("#persona-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const contact = currentContact();
  if (!contact) return;
  withButton($("#save-persona"), async () => {
    const body = { enabled: $("#contact-enabled").checked };
    for (const name of [
      "name",
      "relationship",
      "persona",
      "tone",
      "facts",
      "instructions",
      "mode",
    ])
      body[name] = $(`#contact-${name}`).value.trim();
    const updated = await api(
      `/api/contacts/${encodeURIComponent(contact.id)}`,
      { method: "PATCH", body },
    );
    state.contacts = state.contacts.map((item) =>
      item.id === updated.id ? updated : item,
    );
    if (state.selected === contact.id) setProfileDirty(false);
    renderContacts();
    renderConversationHeading();
    await loadMessages();
    notify("人格与规则已保存。尚未发送的旧规则回复会失效。");
  });
});

$("#apply-preset").addEventListener("click", () => {
  const hasContent = ["persona", "tone", "instructions"].some((name) =>
    $(`#contact-${name}`).value.trim(),
  );
  if (
    hasContent &&
    !window.confirm(
      "应用预设会替换当前编辑框中的人格、语气和补充指令，保存前不会生效。继续？",
    )
  )
    return;
  const preset = presets[$("#persona-preset").value];
  for (const [name, text] of Object.entries(preset))
    $(`#contact-${name}`).value = text;
  setProfileDirty(true);
  notify("预设已填入，你可以继续修改并保存。");
});

$("#takeover").addEventListener("click", () => {
  const contact = currentContact();
  if (!contact) return;
  withButton($("#takeover"), async () => {
    const updated = await api(
      `/api/contacts/${encodeURIComponent(contact.id)}`,
      { method: "PATCH", body: { paused: !contact.paused } },
    );
    state.contacts = state.contacts.map((item) =>
      item.id === updated.id ? updated : item,
    );
    renderContacts();
    renderConversationHeading();
    await loadMessages();
    notify(
      updated.paused
        ? "已人工接管，尚未发送的回复已取消。"
        : "已恢复该联系人的代聊规则。",
    );
  });
});

$("#global-toggle").addEventListener("click", () =>
  withButton($("#global-toggle"), async () => {
    state.settings = await api("/api/settings", {
      method: "PATCH",
      body: { enabled: !state.settings.enabled },
    });
    updateHeader();
    await loadMessages();
    notify(
      state.settings.enabled
        ? "全局代聊已启用，仅作用于已授权联系人。"
        : "全部代聊已暂停，尚未发送的回复已取消。",
    );
  }),
);

$("#simulate-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const contact = currentContact();
  const text = $("#simulate-text").value.trim();
  if (!contact || !text) return;
  if (state.profileDirty) {
    notify("请先保存人格与规则，再进行演练。", true);
    return;
  }
  withButton($("#simulate-submit"), async () => {
    await api(`/api/contacts/${encodeURIComponent(contact.id)}/simulate`, {
      method: "POST",
      body: { text },
    });
    if (state.selected === contact.id) {
      if ($("#simulate-text").value.trim() === text)
        $("#simulate-text").value = "";
      await loadMessages(true);
    }
  });
});

$("#simulate-text").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    $("#simulate-form").requestSubmit();
  }
});

async function previewPrompt(button) {
  return withButton(button, async () => {
    const contact = currentContact();
    if (!contact) return;
    const payload = await api(
      `/api/contacts/${encodeURIComponent(contact.id)}/prompt`,
    );
    const container = $("#prompt-content");
    container.replaceChildren();
    for (const message of payload.messages) {
      const part = element("section", "prompt-part");
      part.append(
        element("strong", "", message.role),
        element("pre", "", message.content),
      );
      container.append(part);
    }
    $("#prompt-dialog").showModal();
  });
}
$("#preview-prompt").addEventListener("click", () =>
  previewPrompt($("#preview-prompt")),
);
$("#preview-prompt-mobile").addEventListener("click", () =>
  previewPrompt($("#preview-prompt-mobile")),
);

$("#clear-messages").addEventListener("click", () => {
  const contact = currentContact();
  if (
    !contact ||
    !window.confirm(
      `清空与「${contact.name}」的全部本地聊天记录和待发送草稿？此操作不能恢复，也不会撤回 QQ 已发送消息。`,
    )
  )
    return;
  withButton($("#clear-messages"), async () => {
    await api(`/api/contacts/${encodeURIComponent(contact.id)}/messages`, {
      method: "DELETE",
    });
    state.draftEdits.clear();
    await loadMessages(true);
    notify("该联系人的本地聊天记录已清空。");
  });
});

$("#delete-contact").addEventListener("click", () => {
  const contact = currentContact();
  if (
    !contact ||
    !window.confirm(
      `删除「${contact.name}」及其人格、聊天记录和草稿？此操作不能恢复。`,
    )
  )
    return;
  withButton($("#delete-contact"), async () => {
    await api(`/api/contacts/${encodeURIComponent(contact.id)}`, {
      method: "DELETE",
    });
    state.contacts = state.contacts.filter((item) => item.id !== contact.id);
    state.profileDirty = false;
    updateHeader();
    await selectContact(state.contacts[0]?.id || null);
    $("#persona-panel").classList.remove("open");
    notify("联系人及其本地记录已删除。");
  });
});

$("#settings-form").addEventListener("input", () => {
  state.settingsDirty = true;
});
$("#settings-form").addEventListener("change", () => {
  state.settingsDirty = true;
});
$("#settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  withButton($("#save-settings"), async () => {
    const form = new FormData($("#settings-form"));
    const body = {};
    for (const [name, value] of form.entries()) {
      if (["model_api_key", "bridge_token"].includes(name) && !value.trim())
        continue;
      body[name] = [
        "temperature",
        "context_messages",
        "max_reply_chars",
        "reply_delay_seconds",
      ].includes(name)
        ? Number(value)
        : value.trim();
    }
    if ($("#clear-model-key").checked) body.clear_model_api_key = true;
    if ($("#clear-bridge-token").checked) body.clear_bridge_token = true;
    state.settings = await api("/api/settings", { method: "PATCH", body });
    renderSettings();
    updateHeader();
    await loadMessages();
    notify("全局设置已保存。");
  });
});

$("#import-friends").addEventListener("click", () =>
  withButton($("#import-friends"), async () => {
    const friends = await api("/api/bridge/friends");
    const list = $("#friends-list");
    list.replaceChildren();
    if (!friends.length)
      list.append(
        element("p", "directory-empty", "桥接没有返回好友，请确认 QQ 已登录。"),
      );
    for (const friend of friends) {
      const row = element("div", "friend-row");
      const details = element("div");
      details.append(
        element("strong", "", friend.name || friend.qq_id),
        element("small", "", `QQ ${friend.qq_id}`),
      );
      const exists = state.contacts.some(
        (contact) => String(contact.qq_id) === String(friend.qq_id),
      );
      const button = element(
        "button",
        "button compact",
        exists ? "已添加" : "添加",
      );
      button.disabled = exists;
      button.type = "button";
      button.addEventListener("click", () => {
        $("#friends-dialog").close();
        openContactDialog(friend);
      });
      row.append(details, button);
      list.append(row);
    }
    $("#friends-dialog").showModal();
  }),
);

window.addEventListener("beforeunload", (event) => {
  if (state.profileDirty || state.settingsDirty || state.draftEdits.size) {
    event.preventDefault();
    event.returnValue = "";
  }
});
$("#topbar-date").textContent = new Date().toLocaleDateString("zh-CN", {
  month: "2-digit",
  day: "2-digit",
});
window.setInterval(poll, 4500);
if (state.token)
  start().catch((error) => {
    showAuth();
    $("#auth-error").textContent = error.message;
  });
else showAuth();
