/* ── TrustLogix Agent Chat — Frontend Logic ─────────────────────────────── */

const API = (window.__CONFIG__ && window.__CONFIG__.API_BASE) || "";
const AGENT_API = (window.__CONFIG__ && window.__CONFIG__.AGENT_API) || "";
let currentUser = null;
let conversationId = null;
let isStreaming = false;

/* ── Auth ────────────────────────────────────────────────────────────────── */
//
// Two-phase auth flow lives entirely server-side. The SPA's only job is to
// kick off the flow (window.location.href = '/auth/login') and check the
// resulting session via /api/auth/me. The browser follows all the OAuth
// redirects between Entra (Phase 1) and the TLX gateway AS (Phase 2) on its
// own, and lands back at '/' when the chain completes.

function loginWith(idp) {
  window.location.href = "/auth/login?idp=" + encodeURIComponent(idp);
}

async function resetConnection() {
  try {
    const resp = await fetch(`${API}/api/reset`, {
      method: "POST",
      credentials: "include",
    });
    if (resp.ok) {
      clearActivity();
      var msgs = document.getElementById("messages");
      if (msgs) msgs.innerHTML = "";
      conversationId = `session-${Date.now()}`;
      addActivityItem("info", "Connection Reset", "MCP sessions cleared. Next query will re-discover tools with fresh policies.");
    }
  } catch (e) { console.error("Reset failed:", e); }
}

async function checkSession() {
  try {
    const resp = await fetch(`${API}/api/auth/me`, { credentials: "include" });
    if (resp.ok) {
      const data = await resp.json();
      currentUser = data.user;
      conversationId = `session-${Date.now()}`;
      showChatApp();
      return true;
    }
  } catch (_) {}
  return false;
}

async function logout() {
  if (currentUser && currentUser.isDemo) {
    currentUser = null;
    showLoginScreen();
    return;
  }

  try {
    const resp = await fetch(`${API}/api/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
    const data = await resp.json();
    currentUser = null;
    if (data.logout_url) {
      window.location.href = data.logout_url;
    } else {
      showLoginScreen();
    }
  } catch (_) {
    showLoginScreen();
  }
}

/* ── UI State ───────────────────────────────────────────────────────────── */

function showLoginScreen() {
  document.getElementById("login-screen").style.display = "flex";
  document.getElementById("chat-app").classList.remove("active");
}

function showChatApp() {
  document.getElementById("login-screen").style.display = "none";
  document.getElementById("chat-app").classList.add("active");

  document.getElementById("user-name").textContent = currentUser.name;

  // Role badge starts hidden — populated live by the session_identity event
  // emitted on each chat turn (see handleSSEEvent). We don't stage a value
  // here because the only true source for the user's effective role is
  // Snowflake's CURRENT_ROLE() at the moment of the request.
  const roleBadge = document.getElementById("user-role");
  roleBadge.textContent = "";
  roleBadge.className = "role-badge pending";
  roleBadge.style.display = "none";

  document.getElementById("chat-input").focus();
  addSystemMessage(`Welcome, ${currentUser.name}! Ask a question — your live Snowflake identity will appear in the activity sidebar on the first turn.`);
}

function updateLiveIdentity(user, role) {
  // Called by handleSSEEvent on every session_identity event. Updates the
  // header badge with the actual CURRENT_USER / CURRENT_ROLE for the
  // current request, so the demo viewer can see role changes live as the
  // demoer ALTER USERs between scenarios.
  const roleBadge = document.getElementById("user-role");
  if (!roleBadge) return;
  if (!role && !user) {
    roleBadge.textContent = "identity unknown";
    roleBadge.className = "role-badge pending";
    roleBadge.style.display = "";
    return;
  }
  // Compact: "USER · POC_TIER1_ROLE" (whatever role the gateway just used)
  const text = [user, role].filter(Boolean).join(" \u00B7 ");
  roleBadge.textContent = text;
  // Class based on tier number if recognizable, else neutral.
  const tierMatch = (role || "").match(/tier\s*(\d)/i);
  roleBadge.className = "role-badge live" + (tierMatch ? ` tier${tierMatch[1]}` : "");
  roleBadge.style.display = "";
}

/* ── Chat Messages ──────────────────────────────────────────────────────── */

function addMessage(role, text) {
  const container = document.getElementById("messages");
  const msgEl = document.createElement("div");
  msgEl.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  if (role === "user") {
    avatar.textContent = currentUser ? currentUser.name.charAt(0).toUpperCase() : "U";
  } else {
    avatar.textContent = "A";
  }

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;

  msgEl.appendChild(avatar);
  msgEl.appendChild(bubble);
  container.appendChild(msgEl);
  container.scrollTop = container.scrollHeight;

  return bubble;
}

function addSystemMessage(text) {
  const container = document.getElementById("messages");
  const msgEl = document.createElement("div");
  msgEl.className = "message system";
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;
  msgEl.appendChild(bubble);
  container.appendChild(msgEl);
  container.scrollTop = container.scrollHeight;
}

function addTypingIndicator() {
  const container = document.getElementById("messages");
  const msgEl = document.createElement("div");
  msgEl.className = "message assistant";
  msgEl.id = "typing-indicator";

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = "A";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';

  msgEl.appendChild(avatar);
  msgEl.appendChild(bubble);
  container.appendChild(msgEl);
  container.scrollTop = container.scrollHeight;
}

function removeTypingIndicator() {
  const el = document.getElementById("typing-indicator");
  if (el) el.remove();
}

/* ── Activity Sidebar ───────────────────────────────────────────────────── */

function addActivityItem(type, label, detail) {
  const feed = document.getElementById("activity-feed");
  const item = document.createElement("div");
  item.className = `activity-item ${type}`;

  const dot = type === "gateway-allow" ? "&#x2714;" :
              type === "gateway-deny"  ? "&#x2718;" :
              type === "guardrail"     ? "&#x26A0;" :
              type === "routing"       ? "&#x2192;" :
              type === "identity"      ? "&#x1F511;" :
              type === "error"         ? "&#x26D4;" : "&#x25CF;";

  item.innerHTML = `<div class="label">${dot} ${escapeHtml(label)}</div>` +
                   (detail ? `<div class="detail">${escapeHtml(detail)}</div>` : "");

  feed.appendChild(item);
  feed.scrollTop = feed.scrollHeight;
}

function clearActivity() {
  document.getElementById("activity-feed").innerHTML = "";
}

/* ── Quick Prompts ──────────────────────────────────────────────────────── */

function useQuickPrompt(btn) {
  const text = btn.getAttribute("data-prompt");
  if (!text || isStreaming) return;
  document.getElementById("chat-input").value = text;
  sendMessage();
}

/* ── Send Message + SSE ─────────────────────────────────────────────────── */

function handleKeyDown(event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
}

async function sendMessage() {
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!text || isStreaming) return;

  input.value = "";
  input.style.height = "auto";
  addMessage("user", text);
  addTypingIndicator();
  setStreaming(true);

  const headers = { "Content-Type": "application/json" };
  if (currentUser && currentUser.isDemo) {
    headers["X-Demo-User"] = currentUser.username;
  }

  try {
    const chatUrl = AGENT_API ? `${AGENT_API}/api/chat` : `${API}/api/chat`;
    const resp = await fetch(chatUrl, {
      method: "POST",
      headers,
      credentials: "include",
      body: JSON.stringify({ message: text, conversation_id: conversationId }),
    });

    if (!resp.ok) {
      removeTypingIndicator();
      addMessage("assistant", `Error: ${resp.status} ${resp.statusText}`);
      setStreaming(false);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let answerBubble = null;
    const TIMEOUT_MS = 120000;

    while (true) {
      const timeout = new Promise((_, reject) =>
        setTimeout(() => reject(new Error("timeout")), TIMEOUT_MS)
      );
      let result;
      try {
        result = await Promise.race([reader.read(), timeout]);
      } catch (_) {
        reader.cancel();
        break;
      }
      const { done, value } = result;
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("event:")) {
          var eventType = line.slice(6).trim();
        } else if (line.startsWith("data:") && eventType) {
          const dataStr = line.slice(5).trim();
          if (!dataStr) continue;
          try {
            const data = JSON.parse(dataStr);
            handleSSEEvent(eventType, data);

            if (eventType === "answer") {
              removeTypingIndicator();
              answerBubble = addMessage("assistant", data.text || "");
            }

            if (eventType === "done") {
              // stream ended
            }
          } catch (_) {}
          eventType = null;
        }
      }
    }
  } catch (err) {
    console.error("Chat error:", err);
    removeTypingIndicator();
    addMessage("assistant", "Connection error. Please try again.");
  } finally {
    setStreaming(false);
  }
}

function handleSSEEvent(type, data) {
  switch (type) {
    case "agent":
      if (data.event_type === "routing") {
        addActivityItem("routing", data.agent_name || "Agent", data.message);
      } else if (data.event_type === "llm_call") {
        addActivityItem("agent", data.agent_name, data.message);
      } else if (data.event_type === "result") {
        addActivityItem("agent", `${data.agent_name} result`, truncate(data.message, 80));
      } else if (data.event_type === "error") {
        addActivityItem("error", data.agent_name, data.message);
      }
      break;

    case "gateway":
      if (data.decision === "ALLOW") {
        const server = data.mcp_server || "snowflake";
        addActivityItem("gateway-allow", `ALLOW: ${data.tool_name}`, `MCP: ${server} | Agent: ${data.agent_name}`);
      } else if (data.decision === "DENY") {
        addActivityItem("gateway-deny", `DENY: ${data.tool_name}`, data.reason);
      } else {
        addActivityItem("error", `MCP: ${data.tool_name}`, data.reason);
      }
      break;

    case "guardrail":
      addActivityItem("guardrail",
        `${data.rail_type || "Rail"}: ${data.action || data.rail_name || "check"}`,
        data.reason || data.rail_name || "");
      break;

    case "session_identity":
      // Live result of SELECT CURRENT_USER(), CURRENT_ROLE() — the actual
      // Snowflake identity that will enforce policies for THIS request.
      // Surface in both the activity sidebar AND the header role badge.
      if (data.error) {
        addActivityItem("error", "Identity probe failed", data.error);
        updateLiveIdentity("", "");
      } else {
        addActivityItem(
          "identity",
          `Snowflake identity: ${data.user || "?"}`,
          `Role: ${data.role || "?"}`,
        );
        updateLiveIdentity(data.user || "", data.role || "");
      }
      break;

    case "error":
      addActivityItem("error", "Error", data.message);
      break;
  }
}

function setStreaming(active) {
  isStreaming = active;
  document.getElementById("btn-send").disabled = active;
  document.getElementById("chat-input").disabled = active;
  if (!active) {
    document.getElementById("chat-input").focus();
  }
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function truncate(str, max) {
  return str && str.length > max ? str.slice(0, max) + "..." : str;
}

// Auto-resize textarea + populate customer chip from runtime config
document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("chat-input");
  if (input) {
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 120) + "px";
    });
  }
  const chip = document.getElementById("customer-chip");
  if (chip) {
    const label = (window.__CONFIG__ && window.__CONFIG__.CUSTOMER_LABEL) || "";
    if (label) {
      chip.textContent = "Customer: " + label;
      chip.style.display = "";
    } else {
      chip.style.display = "none";
    }
  }
});

/* ── Init ────────────────────────────────────────────────────────────────── */

(async function init() {
  // Surface auth errors that the server bounced us back with (?error=...)
  const params = new URLSearchParams(window.location.search);
  const errMsg = params.get("error");
  if (errMsg) {
    console.warn("Auth error:", errMsg);
    window.history.replaceState({}, "", "/");
    showLoginScreen();
    setTimeout(() => alert(`Sign-in failed: ${errMsg}`), 50);
    return;
  }
  // Check existing session — set by /auth/callback after Phase 1 (and
  // Phase 2 if the TLX gateway is enabled).
  if (await checkSession()) return;
  // Show login
  showLoginScreen();
})();
