/* =========================================================================
   CampusQuery AI - frontend logic
   Talks to: GET /schema, GET /health, POST /chat, POST /confirm
   Everything shown to the user is plain English + tables - no SQL, no
   JSON, no stack traces ever get rendered here.
   ========================================================================= */

(function () {
  "use strict";

  const STORAGE_CONVERSATIONS = "cq_conversations";
  const STORAGE_THEME = "cq_theme";

  // ---- State ------------------------------------------------------------
  let messages = [];            // current conversation: {role:'user'|'ai', content, data, type}
  let currentConversationId = null;
  let conversations = loadConversations();
  let schemaLoaded = false;

  // ---- DOM refs -----------------------------------------------------------
  const navItems = document.querySelectorAll(".nav-item");
  const views = document.querySelectorAll(".view");

  const welcomeHero = document.getElementById("welcome-hero");
  const chatMessagesEl = document.getElementById("chat-messages");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const sendBtn = document.getElementById("send-btn");

  const connectionStatus = document.getElementById("connection-status");
  const sidebarStatusDot = document.getElementById("sidebar-status-dot");
  const sidebarStatusText = document.getElementById("sidebar-status-text");

  const schemaContent = document.getElementById("schema-content");

  const historyList = document.getElementById("history-list");
  const historyNewChatBtn = document.getElementById("history-new-chat");
  const historyClearBtn = document.getElementById("history-clear");
  const settingsClearBtn = document.getElementById("settings-clear-history");

  const themeToggle = document.getElementById("theme-toggle");

  // ============================= NAVIGATION ===============================

  navItems.forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });

  function switchView(view) {
    navItems.forEach((b) => b.classList.toggle("is-active", b.dataset.view === view));
    views.forEach((v) => v.classList.toggle("is-active", v.id === `view-${view}`));

    if (view === "schema" && !schemaLoaded) loadSchema();
    if (view === "history") renderHistoryList();
  }

  // ============================= HEALTH / STATUS ===========================

  async function pollHealth() {
    try {
      const res = await fetch("/health");
      const data = await res.json();
      const online = data.status === "ok";
      setConnectionIndicator(online, data);
    } catch (err) {
      setConnectionIndicator(false, null);
    }
  }

  function setConnectionIndicator(online, data) {
    connectionStatus.classList.toggle("online", online);
    connectionStatus.classList.toggle("offline", !online);
    connectionStatus.querySelector(".status-pill-text").textContent = online ? "Connected" : "Offline";

    sidebarStatusDot.classList.toggle("online", online);
    sidebarStatusDot.classList.toggle("offline", !online);
    sidebarStatusText.textContent = online ? "System Online" : "System Offline";
  }

  pollHealth();
  setInterval(pollHealth, 30000);

  // ============================= CHAT: SENDING ==============================

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = "";
    autoGrow();
    sendMessage(text);
  });

  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      chatForm.requestSubmit();
    }
  });

  chatInput.addEventListener("input", autoGrow);

  function autoGrow() {
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 140) + "px";
  }

  document.getElementById("welcome-suggestions").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-query]");
    if (btn) sendMessage(btn.dataset.query);
  });

  document.querySelectorAll(".example-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      switchView("chat");
      sendMessage(btn.dataset.query);
    });
  });

  async function sendMessage(text) {
    hideWelcome();

    const userMsg = { role: "user", content: text };
    messages.push(userMsg);
    renderMessage(userMsg);
    scrollChatToBottom();
    saveCurrentConversation();

    const typingEl = renderTypingIndicator();
    setSending(true);

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          history: messages
            .filter((m) => m.role === "user" || m.role === "ai")
            .slice(-8)
            .map((m) => ({ role: m.role === "ai" ? "assistant" : "user", content: m.content })),
        }),
      });

      typingEl.remove();

      if (!res.ok) {
        renderAndStore({ role: "ai", type: "error", content: "I couldn't reach the server. Please try again." });
        return;
      }

      const data = await res.json();
      handleChatResponse(data);
    } catch (err) {
      typingEl.remove();
      renderAndStore({ role: "ai", type: "error", content: "I couldn't reach the server. Please check your connection and try again." });
    } finally {
      setSending(false);
    }
  }

function handleChatResponse(data) {
  if (data.type === "answer") {
    renderAndStore({
      role: "ai",
      type: "answer",
      content: data.message,
      data: data.data || [],
      sql: data.sql || null
    });
  } else if (data.type === "confirm") {
    renderAndStore({
      role: "ai",
      type: "confirm",
      content: data.message,
      confirmationId: data.confirmation_id,
      sql: data.sql || null
    });
  } else {
    renderAndStore({
      role: "ai",
      type: "error",
      content: data.message || "Something went wrong. Please try again."
    });
  }
}

  function renderAndStore(msg) {
    messages.push(msg);
    renderMessage(msg);
    scrollChatToBottom();
    saveCurrentConversation();
  }

  function setSending(isSending) {
    sendBtn.disabled = isSending;
  }

  // ============================= CHAT: RENDERING =============================

  function hideWelcome() {
    welcomeHero.style.display = "none";
  }

  function renderTypingIndicator() {
    const wrap = document.createElement("div");
    wrap.className = "msg ai";
    wrap.innerHTML = `
      <div class="msg-avatar">AI</div>
      <div class="msg-col">
        <div class="bubble is-typing">
          <span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>
        </div>
      </div>`;
    chatMessagesEl.appendChild(wrap);
    scrollChatToBottom();
    return wrap;
  }

  function renderMessage(msg) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${msg.role === "user" ? "user" : "ai"}`;

    const avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.textContent = msg.role === "user" ? "You" : "AI";

    const col = document.createElement("div");
    col.className = "msg-col";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (msg.type === "error") bubble.classList.add("is-error");
    bubble.textContent = msg.content;
    col.appendChild(bubble);

    if ((msg.type === "answer" || msg.type === "confirm") && msg.sql) {
      col.appendChild(buildSqlCard(msg.sql));
    }

    if (msg.type === "answer" && Array.isArray(msg.data) && msg.data.length > 0) {
      col.appendChild(buildResultTable(msg.data));
    }

    if (msg.type === "confirm" && msg.confirmationId) {
      col.appendChild(buildConfirmCard(msg.confirmationId));
    }

    wrap.appendChild(avatar);
    wrap.appendChild(col);
    chatMessagesEl.appendChild(wrap);
  }

  function buildSqlCard(sql) {
  const card = document.createElement("details");
  card.className = "sql-card";

  const header = document.createElement("summary");
  header.className = "sql-header";

  const title = document.createElement("div");
  title.className = "sql-title";

  const icon = document.createElement("span");
  icon.className = "sql-icon";
  icon.textContent = "</>";

  const text = document.createElement("span");
  text.textContent = "Generated SQL";

  title.appendChild(icon);
  title.appendChild(text);

  const copyBtn = document.createElement("button");
  copyBtn.className = "copy-sql";
  copyBtn.type = "button";
  copyBtn.textContent = "Copy";

  copyBtn.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();

    try {
      await navigator.clipboard.writeText(sql);
      copyBtn.textContent = "Copied";
      copyBtn.classList.add("copied");

      setTimeout(() => {
        copyBtn.textContent = "Copy";
        copyBtn.classList.remove("copied");
      }, 1200);
    } catch {
      copyBtn.textContent = "Failed";
      setTimeout(() => {
        copyBtn.textContent = "Copy";
      }, 1200);
    }
  });

  header.appendChild(title);
  header.appendChild(copyBtn);

  const body = document.createElement("div");
  body.className = "sql-body";

  const code = document.createElement("pre");
  code.textContent = sql;

  body.appendChild(code);

  card.appendChild(header);
  card.appendChild(body);

  return card;
}

  function buildResultTable(rows) {
    const card = document.createElement("div");
    card.className = "result-card";

    const title = document.createElement("div");
    title.className = "result-card-title";
    title.textContent = rows.length === 1 ? "1 result" : `${rows.length} results`;
    card.appendChild(title);

    const wrap = document.createElement("div");
    wrap.className = "result-table-wrap";

    const table = document.createElement("table");
    table.className = "result-table";

    const columns = Object.keys(rows[0]);

    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    columns.forEach((col) => {
      const th = document.createElement("th");
      th.textContent = humanizeLabel(col);
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    rows.slice(0, 100).forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((col) => {
        const td = document.createElement("td");
        const value = row[col];
        td.textContent = value === null || value === undefined ? "\u2014" : String(value);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);

    wrap.appendChild(table);
    card.appendChild(wrap);
    return card;
  }

  function humanizeLabel(key) {
    return key
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function buildConfirmCard(confirmationId) {
    const card = document.createElement("div");
    card.className = "confirm-card";

    const actions = document.createElement("div");
    actions.className = "confirm-actions";

    const confirmBtn = document.createElement("button");
    confirmBtn.className = "btn btn-primary";
    confirmBtn.type = "button";
    confirmBtn.textContent = "Confirm";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn btn-danger";
    cancelBtn.type = "button";
    cancelBtn.textContent = "Cancel";

    const disableBoth = () => {
      confirmBtn.disabled = true;
      cancelBtn.disabled = true;
    };

    confirmBtn.addEventListener("click", () => {
      disableBoth();
      confirmBtn.textContent = "Working\u2026";
      resolveConfirmation(confirmationId, true);
    });

    cancelBtn.addEventListener("click", () => {
      disableBoth();
      resolveConfirmation(confirmationId, false);
    });

    actions.appendChild(confirmBtn);
    actions.appendChild(cancelBtn);
    card.appendChild(actions);
    return card;
  }

  async function resolveConfirmation(confirmationId, confirmed) {
    try {
      const res = await fetch("/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation_id: confirmationId, confirmed }),
      });
      const data = await res.json();

      if (data.type === "answer") {
        renderAndStore({ role: "ai", type: "answer", content: data.message, data: data.data || [] });
      } else if (data.type === "cancelled") {
        renderAndStore({ role: "ai", type: "answer", content: data.message });
      } else {
        renderAndStore({ role: "ai", type: "error", content: data.message || "Something went wrong. Please try again." });
      }
    } catch (err) {
      renderAndStore({ role: "ai", type: "error", content: "I couldn't reach the server. Please try again." });
    }
  }

  function scrollChatToBottom() {
    const area = document.querySelector(".chat-area");
    area.scrollTop = area.scrollHeight;
  }

  // ============================= SCHEMA VIEW =================================

  async function loadSchema() {
    schemaContent.innerHTML = '<p class="muted">Loading schema\u2026</p>';
    try {
      const res = await fetch("/schema");
      if (!res.ok) throw new Error("schema unavailable");
      const data = await res.json();
      renderSchema(data.tables || []);
      schemaLoaded = true;
    } catch (err) {
      schemaContent.innerHTML = '<p class="muted">The schema couldn\u2019t be loaded right now. Please try again shortly.</p>';
    }
  }

  function renderSchema(tables) {
    if (!tables.length) {
      schemaContent.innerHTML = '<p class="muted">No schema information is available yet.</p>';
      return;
    }

    const grid = document.createElement("div");
    grid.className = "schema-grid";

    tables.forEach((table) => {
      const card = document.createElement("div");
      card.className = "schema-card";

      const head = document.createElement("div");
      head.className = "schema-card-head";
      head.innerHTML = `<h3>${humanizeLabel(table.table)}</h3>`;
      const count = document.createElement("span");
      count.className = "schema-card-count";
      count.textContent = `${table.columns.length} fields`;
      head.appendChild(count);
      card.appendChild(head);

      const list = document.createElement("ul");
      list.className = "schema-col-list";
      table.columns.forEach((col) => {
        const li = document.createElement("li");
        const nameSpan = document.createElement("span");
        nameSpan.className = "schema-col-name";
        nameSpan.textContent = humanizeLabel(col.name);
        if (col.key === "PRI") {
          const badge = document.createElement("span");
          badge.className = "schema-key-badge";
          badge.textContent = "KEY";
          nameSpan.appendChild(badge);
        }
        const typeSpan = document.createElement("span");
        typeSpan.className = "schema-col-type";
        typeSpan.textContent = col.type;
        li.appendChild(nameSpan);
        li.appendChild(typeSpan);
        list.appendChild(li);
      });
      card.appendChild(list);

      if (table.foreign_keys && table.foreign_keys.length) {
        const rel = document.createElement("div");
        rel.className = "schema-relations";
        const relTitle = document.createElement("div");
        relTitle.className = "schema-relations-title";
        relTitle.textContent = "Connected to";
        rel.appendChild(relTitle);
        table.foreign_keys.forEach((fk) => {
          const p = document.createElement("div");
          p.className = "schema-relation";
          p.textContent = `${humanizeLabel(table.table)} \u2192 ${humanizeLabel(fk.references_table)}`;
          rel.appendChild(p);
        });
        card.appendChild(rel);
      }

      grid.appendChild(card);
    });

    schemaContent.innerHTML = "";
    schemaContent.appendChild(grid);
  }

  // ============================= HISTORY / STORAGE ============================

  function loadConversations() {
    try {
      const raw = localStorage.getItem(STORAGE_CONVERSATIONS);
      return raw ? JSON.parse(raw) : [];
    } catch (err) {
      return [];
    }
  }

  function persistConversations() {
    try {
      localStorage.setItem(STORAGE_CONVERSATIONS, JSON.stringify(conversations));
    } catch (err) {
      /* localStorage unavailable - fail silently, chat still works this session */
    }
  }

  function saveCurrentConversation() {
    if (messages.length === 0) return;

    const firstUserMsg = messages.find((m) => m.role === "user");
    const title = firstUserMsg ? firstUserMsg.content : "New conversation";

    if (!currentConversationId) {
      currentConversationId = `c_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      conversations.unshift({
        id: currentConversationId,
        title,
        timestamp: Date.now(),
        messages,
      });
    } else {
      const existing = conversations.find((c) => c.id === currentConversationId);
      if (existing) {
        existing.messages = messages;
        existing.timestamp = Date.now();
      }
    }
    persistConversations();
  }

  function renderHistoryList() {
    conversations.sort((a, b) => b.timestamp - a.timestamp);

    if (conversations.length === 0) {
      historyList.innerHTML = '<div class="empty-state">No conversations yet. Start chatting and they\u2019ll show up here.</div>';
      return;
    }

    historyList.innerHTML = "";
    conversations.forEach((conv) => {
      const item = document.createElement("div");
      item.className = "history-item";

      const title = document.createElement("div");
      title.className = "history-item-title";
      title.textContent = conv.title;

      const meta = document.createElement("div");
      meta.className = "history-item-meta";
      meta.textContent = formatRelativeTime(conv.timestamp);

      item.appendChild(title);
      item.appendChild(meta);
      item.addEventListener("click", () => loadConversation(conv.id));

      historyList.appendChild(item);
    });
  }

  function formatRelativeTime(ts) {
    const diffMs = Date.now() - ts;
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return "Just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days}d ago`;
    return new Date(ts).toLocaleDateString();
  }

  function loadConversation(id) {
    const conv = conversations.find((c) => c.id === id);
    if (!conv) return;

    currentConversationId = id;
    messages = conv.messages || [];
    chatMessagesEl.innerHTML = "";

    if (messages.length === 0) {
      welcomeHero.style.display = "";
    } else {
      hideWelcome();
      messages.forEach((m) => renderMessage(m));
    }

    switchView("chat");
    scrollChatToBottom();
  }

  function startNewChat() {
    messages = [];
    currentConversationId = null;
    chatMessagesEl.innerHTML = "";
    welcomeHero.style.display = "";
    switchView("chat");
  }

  historyNewChatBtn.addEventListener("click", startNewChat);

  historyClearBtn.addEventListener("click", clearAllHistory);
  settingsClearBtn.addEventListener("click", clearAllHistory);

  function clearAllHistory() {
    conversations = [];
    persistConversations();
    startNewChat();
    renderHistoryList();
  }

  // ============================= THEME =====================================

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    themeToggle.setAttribute("aria-checked", theme === "dark" ? "true" : "false");
  }

  function initTheme() {
    let theme = "light";
    try {
      theme = localStorage.getItem(STORAGE_THEME) || "light";
    } catch (err) {
      /* ignore */
    }
    applyTheme(theme);
  }

  themeToggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    const next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    try {
      localStorage.setItem(STORAGE_THEME, next);
    } catch (err) {
      /* ignore */
    }
  });

  // ============================= INIT =======================================

  initTheme();
})();
