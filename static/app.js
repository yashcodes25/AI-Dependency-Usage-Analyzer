const API = {
  health: "/api/health",
  me: "/api/auth/me",
  login: "/api/auth/login",
  register: "/api/auth/register",
  logout: "/api/auth/logout",
  tools: "/api/tools",
  templates: "/api/templates",
  automations: "/api/automations",
  generateCode: "/api/generate-code",
  parseCodeCandidates: ["/api/parse-code"],
  run: "/api/run",
  runs: "/api/runs",
  outputs: "/api/outputs",
};

const DEFAULT_AUTOMATION = {
  name: "Study Planner",
  type: "agent",
  model: "gemma4",
  goal: "Create a personalized study plan from class materials, notes, and syllabus. Extract key topics, generate a schedule, and export a study guide.",
  task: "Read files from ./input. Summarize them and create ./output/study_plan.md with daily topics, practice tasks, revision checkpoints, important keywords, and a final checklist.",
  tools: ["list_files", "read_file", "read_pdf", "create_markdown_report"],
  max_steps: 12,
  safe_mode: true,
  temperature: 0.2,
  base_url: "http://localhost:11434",
  steps: [],
};

const state = {
  user: null,
  authRequired: false,

  tools: [],
  templates: [],
  automations: [],
  outputs: [],
  runs: [],
  events: [],

  selectedCategory: "All",
  toolTab: "all",
  runView: "simple",
  bottomTab: "outputs",
  sidebarMode: "automations",
  workspaceMode: "split",
  lastOutputCount: 0,

  automationSearch: "",
  automationSort: "updated_desc",
  deleteTargetId: null,

  automation: { ...DEFAULT_AUTOMATION },
  code: "",
  codeDirty: false,
  visualDirty: false,
  saveState: "draft",

  runStatus: "idle",
  runStartedAt: null,
  runFinishedAt: null,
  runId: null,
  source: null,

  templateSearch: "",
  templatePage: 1,
  selectedTemplateId: "",
  templateCategory: "All",
};

const $ = (id) => document.getElementById(id);

function exists(id) {
  return Boolean($(id));
}

function modelDump(obj) {
  return JSON.parse(JSON.stringify(obj));
}

async function request(path, options = {}) {
  const isForm = options.body instanceof FormData;

  const res = await fetch(path, {
    credentials: "include",
    headers: isForm
      ? { ...(options.headers || {}) }
      : {
          "Content-Type": "application/json",
          ...(options.headers || {}),
        },
    ...options,
  });

  if (res.status === 401) {
    state.authRequired = true;
    showAuth();
    throw new Error("Authentication required");
  }

  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      msg = body.detail || body.error || body.message || JSON.stringify(body);
    } catch {}
    throw new Error(msg);
  }

  if (res.status === 204) return null;

  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return res.json();
  return res.text();
}

function toast(message, type = "success") {
  const el = $("toast");
  if (!el) return;

  el.textContent = message;
  el.className = `toast ${type}`;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => {
    el.className = "toast hidden";
  }, 4200);
}

function debounce(fn, delay = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

const debouncedVisualSync = debounce(() => {
  if (isAutoSyncEnabled()) {
    syncAutomationFromForm();
  }
}, 250);

const debouncedCodeSync = debounce(() => {
  if (isAutoSyncEnabled()) {
    syncCodeToVisual();
  }
}, 700);

function showAuth() {
  if (exists("authScreen")) $("authScreen").classList.remove("hidden");
  const appShell = document.querySelector(".layout");
  const topbar = document.querySelector(".topbar");
  const footer = document.querySelector(".footer");

  if (appShell) appShell.classList.add("blurred-app");
  if (topbar) topbar.classList.add("blurred-app");
  if (footer) footer.classList.add("blurred-app");
}

function hideAuth() {
  if (exists("authScreen")) $("authScreen").classList.add("hidden");
  document
    .querySelectorAll(".blurred-app")
    .forEach((el) => el.classList.remove("blurred-app"));
}

async function checkAuth() {
  try {
    const user = await request(API.me);
    state.user = user;
    state.authRequired = false;
    hideAuth();
    renderUser();
    return true;
  } catch (e) {
    if (
      String(e.message).includes("Authentication required") ||
      String(e.message).includes("404")
    ) {
      // If auth routes are unavailable in a dev build, continue without blocking.
      if (String(e.message).includes("404")) {
        hideAuth();
        return true;
      }
    }
    showAuth();
    return false;
  }
}

function renderUser() {
  const name = state.user?.name || state.user?.username || "Local User";
  const email = state.user?.email || "local@agentkit";

  if (exists("currentUserName")) $("currentUserName").textContent = name;
  if (exists("currentUserEmail")) $("currentUserEmail").textContent = email;
  if (exists("avatarBtn")) {
    $("avatarBtn").textContent = initials(name || email);
  }
}

function bindAuthEvents() {
  if (exists("loginTabBtn")) {
    $("loginTabBtn").addEventListener("click", () => setAuthTab("login"));
  }

  if (exists("registerTabBtn")) {
    $("registerTabBtn").addEventListener("click", () => setAuthTab("register"));
  }

  if (exists("loginForm")) {
    $("loginForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      await login();
    });
  }

  if (exists("registerForm")) {
    $("registerForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      await register();
    });
  }

  if (exists("avatarBtn")) {
    $("avatarBtn").addEventListener("click", () => {
      $("userDropdown")?.classList.toggle("hidden");
    });
  }

  if (exists("logoutBtn")) {
    $("logoutBtn").addEventListener("click", logout);
  }

  document.addEventListener("click", (e) => {
    const dropdown = $("userDropdown");
    const avatar = $("avatarBtn");
    if (!dropdown || !avatar) return;
    if (!dropdown.contains(e.target) && !avatar.contains(e.target)) {
      dropdown.classList.add("hidden");
    }
  });
}

function setAuthTab(tab) {
  const login = tab === "login";

  $("loginTabBtn")?.classList.toggle("active", login);
  $("registerTabBtn")?.classList.toggle("active", !login);
  $("loginForm")?.classList.toggle("hidden", !login);
  $("registerForm")?.classList.toggle("hidden", login);
}

async function login() {
  try {
    const payload = {
      email: $("loginEmail").value.trim(),
      password: $("loginPassword").value,
    };

    await request(API.login, {
      method: "POST",
      body: JSON.stringify(payload),
    });

    toast("Signed in");
    await checkAuth();
    await bootData();
  } catch (e) {
    toast(`Sign in failed: ${e.message}`, "error");
  }
}

async function register() {
  try {
    const payload = {
      name: $("registerName").value.trim(),
      email: $("registerEmail").value.trim(),
      password: $("registerPassword").value,
    };

    await request(API.register, {
      method: "POST",
      body: JSON.stringify(payload),
    });

    toast("Account created");
    await checkAuth();
    await bootData();
  } catch (e) {
    toast(`Registration failed: ${e.message}`, "error");
  }
}

async function logout() {
  try {
    await request(API.logout, { method: "POST" });
  } catch {}
  state.user = null;
  showAuth();
  toast("Signed out");
}

async function init() {
  bindEvents();
  bindAuthEvents();

  const ok = await checkAuth();
  if (ok) {
    await bootData();
  }
}

async function bootData() {
  await Promise.allSettled([
    loadHealth(),
    loadTools(),
    loadTemplates(),
    loadAutomations(),
    loadOutputs(),
    loadRuns(),
  ]);

  syncAutomationToForm();
  await refreshCodeFromVisual();
  renderAll();
}

async function loadHealth() {
  try {
    const health = await request(API.health);

    const connected = health.ok !== false;
    const badges = document.querySelectorAll(".status-pill.connected span");
    badges.forEach((el) => {
      el.className = connected ? "green-dot" : "red-dot";
    });
  } catch {
    // Keep UI usable even if health fails.
  }
}

function bindEvents() {
  const formIds = [
    "nameInput",
    "modelInput",
    "goalInput",
    "taskInput",
    "maxStepsInput",
    "safeModeInput",
    "temperatureInput",
    "autoSyncInput",
  ];

  formIds.forEach((id) => {
    if (!exists(id)) return;
    const eventName = ["safeModeInput", "autoSyncInput"].includes(id)
      ? "change"
      : "input";
    $(id).addEventListener(eventName, debouncedVisualSync);
  });

  $("runBtn")?.addEventListener("click", runAutomation);
  $("topRunBtn")?.addEventListener("click", runAutomation);
  $("saveBtn")?.addEventListener("click", saveAutomation);

  $("templateSearch")?.addEventListener("input", (e) => {
    state.templateSearch = e.target.value;
    state.templatePage = 1;
    renderTemplates();
  });

  $("templatePrevBtn")?.addEventListener("click", () => {
    state.templatePage = Math.max(1, (state.templatePage || 1) - 1);
    renderTemplates();
  });

  $("templateNextBtn")?.addEventListener("click", () => {
    state.templatePage = (state.templatePage || 1) + 1;
    renderTemplates();
  });

  $("templateCategoryFilter")?.addEventListener("change", (e) => {
    state.templateCategory = e.target.value || "All";
    state.templatePage = 1;
    renderTemplates();
  });

  document.querySelectorAll("[data-sidebar-mode]").forEach((btn) => {
  btn.addEventListener("click", () => {
    setSidebarMode(btn.dataset.sidebarMode || "automations");
  });
});

document.querySelectorAll("[data-workspace-mode]").forEach((btn) => {
  btn.addEventListener("click", () => {
    setWorkspaceMode(btn.dataset.workspaceMode || "build");
  });
});

  $("exportBtn")?.addEventListener("click", downloadCode);
  $("docsBtn")?.addEventListener("click", () => window.open("/docs", "_blank"));
  $("copyCodeBtn")?.addEventListener("click", copyCode);
  $("downloadCodeBtn")?.addEventListener("click", downloadCode);

  $("refreshToolsBtn")?.addEventListener("click", loadTools);
  $("refreshOutputsBtn")?.addEventListener("click", loadOutputs);
  $("refreshRunsBtn")?.addEventListener("click", loadRuns);
  $("refreshAutomationsBtn")?.addEventListener("click", loadAutomations);

  $("clearEventsBtn")?.addEventListener("click", () => {
    state.events = [];
    renderEvents();
    renderRunSummary();
  });

  $("toolSearch")?.addEventListener("input", renderToolSearch);
  $("toolPickerSearch")?.addEventListener("input", renderToolPicker);

  $("automationSearch")?.addEventListener("input", (e) => {
    state.automationSearch = e.target.value;
    renderAutomations();
  });

  $("automationSort")?.addEventListener("change", (e) => {
    state.automationSort = e.target.value;
    renderAutomations();
  });

  $("newAutomationBtn")?.addEventListener("click", openNewAutomationModal);
  $("createAutomationBtn")?.addEventListener("click", createNewAutomation);
  $("confirmDeleteBtn")?.addEventListener("click", confirmDeleteAutomation);

  $("addToolInlineBtn")?.addEventListener("click", openToolPicker);
  $("addConnectorBtn")?.addEventListener("click", () =>
    toast("Add your @tool functions in tools.py, then refresh tools."),
  );

  $("formatCodeBtn")?.addEventListener("click", formatCode);
  $("syncCodeToVisualBtn")?.addEventListener("click", syncCodeToVisual);
  $("resetCodeBtn")?.addEventListener("click", async () => {
    await refreshCodeFromVisual();
    toast("Code reset from visual builder");
  });

  $("codeEditor")?.addEventListener("input", () => {
    state.code = $("codeEditor").value;
    state.codeDirty = true;
    updateSyncStatus("dirty", "Code changed");
    debouncedCodeSync();
  });

  $("openOutputFolderBtn")?.addEventListener("click", () => {
    toast("Open the local output/ folder from your project directory.");
  });

  document.querySelectorAll("[data-bottom-tab]").forEach((btn) => {
    btn.addEventListener("click", () => setBottomTab(btn.dataset.bottomTab));
  });

  document.querySelectorAll("[data-run-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.runView = btn.dataset.runView;
      renderRunViewTabs();
      renderEvents();
    });
  });

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "e") {
      e.preventDefault();
      downloadCode();
    }

    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      $("toolSearch")?.focus();
    }

    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      saveAutomation();
    }
  });
}

function isAutoSyncEnabled() {
  return !exists("autoSyncInput") || $("autoSyncInput").checked;
}

function collectAutomation() {
  return {
    ...state.automation,
    name: $("nameInput")?.value.trim() || "Untitled Automation",
    model: $("modelInput")?.value.trim() || DEFAULT_AUTOMATION.model,
    goal: $("goalInput")?.value.trim() || "",
    task: $("taskInput")?.value.trim() || "",
    max_steps: Number($("maxStepsInput")?.value) || 12,
    safe_mode: Boolean($("safeModeInput")?.checked),
    temperature: Number(
      $("temperatureInput")?.value ?? state.automation.temperature ?? 0.2,
    ),
    base_url: state.automation.base_url || "http://localhost:11434",
    tools: [...new Set(state.automation.tools || [])],
    steps: state.automation.steps || [],
  };
}

async function syncAutomationFromForm() {
  state.automation = collectAutomation();
  state.visualDirty = true;
  state.saveState = "draft";

  renderAutomationTitle();
  renderJson();
  updateModelMiniLabel();
  updateSaveState();

  if (isAutoSyncEnabled()) {
    await refreshCodeFromVisual({ quiet: true });
  }
}

function syncAutomationToForm() {
  if (exists("nameInput")) $("nameInput").value = state.automation.name || "";
  if (exists("modelInput"))
    $("modelInput").value = state.automation.model || "";
  if (exists("goalInput")) $("goalInput").value = state.automation.goal || "";
  if (exists("taskInput")) $("taskInput").value = state.automation.task || "";
  if (exists("maxStepsInput"))
    $("maxStepsInput").value = state.automation.max_steps || 12;
  if (exists("safeModeInput"))
    $("safeModeInput").checked = state.automation.safe_mode !== false;
  if (exists("temperatureInput"))
    $("temperatureInput").value = state.automation.temperature ?? 0.2;

  renderAutomationTitle();
  renderSelectedTools();
  renderJson();
  updateModelMiniLabel();
}

function renderAll() {
  renderUser();
  renderSidebarMode();
  renderWorkspaceMode();
  renderAutomations();
  renderCategories();
  renderSelectedTools();
  renderToolSearch();
  renderToolPicker();
  renderCode();
  renderJson();
  renderOutputs();
  renderRuns();
  renderEvents();
  renderRunStatus();
  renderRunSummary();
  renderRunCurrentStep();
  renderRunSuccessCard();
  renderRunViewTabs();
  renderBottomTabs();
  updateModelMiniLabel();
  updateSaveState();
}
async function loadTools() {
  try {
    state.tools = await request(API.tools);
    renderCategories();
    renderSelectedTools();
    renderToolSearch();
    renderToolPicker();
  } catch (e) {
    toast(`Could not load tools: ${e.message}`, "error");
  }
}

function renderRunCurrentStep() {
  if (!exists("runCurrentStepText") && !exists("runCurrentStepHint")) return;

  const latest = latestMeaningfulEvent();

  let title = "Ready to run";
  let hint = "Click Run Automation to watch the agent plan, use tools, and create outputs.";

  if (state.runStatus === "queued") {
    title = "Starting automation";
    hint = "Preparing the local run.";
  } else if (state.runStatus === "running") {
    title = latest ? friendlyEventTitle(latest) : "Automation is running";
    hint = latest ? friendlyEventMessage(latest) : "The agent is working locally.";
  } else if (state.runStatus === "success") {
    title = "Automation completed";
    hint = state.outputs.length
      ? `${state.outputs.length} output file${state.outputs.length === 1 ? "" : "s"} ready.`
      : "Run completed successfully.";
  } else if (state.runStatus === "failed") {
    title = "Automation needs attention";
    hint = latest ? friendlyEventMessage(latest) : "Check the run details below.";
  }

  if (exists("runCurrentStepText")) $("runCurrentStepText").textContent = title;
  if (exists("runCurrentStepHint")) $("runCurrentStepHint").textContent = hint;
}

function renderRunSuccessCard() {
  const card = $("runSuccessCard");
  if (!card) return;

  const shouldShow =
    state.runStatus === "success" &&
    Array.isArray(state.outputs) &&
    state.outputs.length > 0;

  card.classList.toggle("hidden", !shouldShow);

  if (!shouldShow) return;

  const strong = card.querySelector("strong");
  const paragraph = card.querySelector("p");

  if (strong) {
    strong.textContent = `${state.outputs.length} output file${state.outputs.length === 1 ? "" : "s"} ready`;
  }

  if (paragraph) {
    const latest = state.outputs[0]?.name || "Generated files";
    paragraph.textContent = `${latest} and other outputs are available below.`;
  }
}

function latestMeaningfulEvent() {
  const priority = ["ERROR", "DONE", "ACTION", "OBSERVATION", "PLAN", "RETRY"];
  const events = [...state.events].reverse();

  return events.find((event) => priority.includes(event.type)) || events[0] || null;
}

function friendlyEventTitle(event) {
  if (!event) return "Working";

  if (event.type === "PLAN") return "Planning next steps";
  if (event.type === "ACTION") return friendlyActionTitle(event.message);
  if (event.type === "OBSERVATION") return "Reviewing result";
  if (event.type === "RETRY") return "Recovering from issue";
  if (event.type === "DONE") return "Automation completed";
  if (event.type === "ERROR") return "Issue detected";

  return humanize(event.type || "Working");
}

function friendlyEventMessage(event) {
  if (!event) return "";

  if (event.type === "ACTION") return summarizeAction(event.message);
  if (event.type === "OBSERVATION") return summarizeObservation(event.message);
  if (event.type === "PLAN") return short(event.message, 120);
  if (event.type === "ERROR") return short(event.message, 140);
  if (event.type === "DONE") return "Generated outputs are ready.";

  return short(event.message || "", 120);
}

function friendlyActionTitle(message) {
  const raw = String(message || "");
  const match = raw.match(/^([a-zA-Z_][a-zA-Z0-9_]*)\(/);
  const tool = match ? match[1] : raw;

  const labels = {
    list_files: "Reading folder",
    read_file: "Reading file",
    read_pdf: "Reading PDF",
    read_csv: "Reading CSV",
    summarize_csv: "Analyzing CSV",
    create_markdown_report: "Creating report",
    write_file: "Writing file",
    append_file: "Updating file",
    create_folder: "Creating folder",
    copy_file: "Copying file",
    move_file: "Moving file",
    extract_keywords: "Extracting keywords",
    compare_texts: "Comparing documents",
  };

  return labels[tool] || humanize(tool);
}

async function loadTemplates() {
  try {
    const res = await request(API.templates);

    state.templates = Array.isArray(res)
      ? res
      : Array.isArray(res.items)
        ? res.items
        : Array.isArray(res.templates)
          ? res.templates
          : [];

    state.templatePage = 1;

    console.log("Loaded templates:", state.templates);

    renderTemplates();
  } catch (e) {
    state.templates = [];
    renderTemplates();
    toast(`Could not load templates: ${e.message}`, "error");
    console.error("Template loading failed:", e);
  }
}
async function loadAutomations() {
  try {
    const res = await request(API.automations);

    state.automations = Array.isArray(res)
      ? res
      : Array.isArray(res.items)
        ? res.items
        : [];

    renderAutomations();

    if (!state.automation.id && state.automations.length) {
      await loadAutomation(state.automations[0].id);
    }
  } catch (e) {
    state.automations = [];
    renderAutomations();
    toast(`Could not load automations: ${e.message}`, "error");
  }
}
async function loadAutomation(id) {
  try {
    const automation = await request(
      `${API.automations}/${encodeURIComponent(id)}`,
    );
    state.automation = normalizeAutomation(automation);
    state.saveState = "saved";
    syncAutomationToForm();
    await refreshCodeFromVisual({ quiet: true });
    setBottomTab("outputs");
    renderAutomations();
    updateSaveState();
  } catch (e) {
    toast(`Could not load automation: ${e.message}`, "error");
  }
}

function normalizeAutomation(automation) {
  return {
    ...DEFAULT_AUTOMATION,
    ...automation,
    tools: Array.isArray(automation.tools) ? automation.tools : [],
    steps: Array.isArray(automation.steps) ? automation.steps : [],
  };
}

async function saveAutomation() {
  try {
    await syncAutomationFromForm();
    const payload = modelDump(state.automation);

    const saved = payload.id
      ? await request(`${API.automations}/${encodeURIComponent(payload.id)}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        })
      : await request(API.automations, {
          method: "POST",
          body: JSON.stringify(payload),
        });

    state.automation = normalizeAutomation(saved);
    state.saveState = "saved";
    state.visualDirty = false;
    state.codeDirty = false;

    syncAutomationToForm();
    await loadAutomations();
    updateSaveState();
    toast("Automation saved");
  } catch (e) {
    toast(`Save failed: ${e.message}`, "error");
  }
}

async function duplicateAutomation(id) {
  try {
    const duplicated = await request(
      `${API.automations}/${encodeURIComponent(id)}/duplicate`,
      {
        method: "POST",
      },
    );
    toast("Automation duplicated");
    await loadAutomations();
    await loadAutomation(duplicated.id);
  } catch (e) {
    toast(`Duplicate failed: ${e.message}`, "error");
  }
}

function askDeleteAutomation(id) {
  state.deleteTargetId = id;
  const modal = $("confirmDeleteModal");
  if (modal?.showModal) modal.showModal();
  else confirmDeleteAutomation();
}

async function confirmDeleteAutomation() {
  if (!state.deleteTargetId) return;

  try {
    await request(
      `${API.automations}/${encodeURIComponent(state.deleteTargetId)}`,
      {
        method: "DELETE",
      },
    );

    toast("Automation deleted");

    if (state.automation.id === state.deleteTargetId) {
      state.automation = { ...DEFAULT_AUTOMATION };
      syncAutomationToForm();
      await refreshCodeFromVisual();
    }

    state.deleteTargetId = null;
    $("confirmDeleteModal")?.close();
    await loadAutomations();
  } catch (e) {
    toast(`Delete failed: ${e.message}`, "error");
  }
}

function openNewAutomationModal() {
  if (exists("newAutomationName")) $("newAutomationName").value = "";

  state.templateSearch = "";
  state.templatePage = 1;
  state.selectedTemplateId = "";
  state.templateCategory = "All";

  if (exists("templateSearch")) $("templateSearch").value = "";
  if (exists("templateSelect")) $("templateSelect").value = "";
  if (exists("templateCategoryFilter"))
    $("templateCategoryFilter").value = "All";

  renderTemplates();

  const modal = $("newAutomationModal");
  if (modal?.showModal) modal.showModal();
  else createNewAutomation();
}
async function createNewAutomation() {
  const templateId =
    state.selectedTemplateId || $("templateSelect")?.value || "";
  const name = $("newAutomationName")?.value.trim();

  let automation = { ...DEFAULT_AUTOMATION };

  if (templateId) {
    const template = state.templates.find((t) => t.id === templateId);
    if (template?.automation) {
      automation = normalizeAutomation(template.automation);
    }
  }

  if (name) automation.name = name;

  delete automation.id;
  automation.created_at = undefined;
  automation.updated_at = undefined;

  state.automation = normalizeAutomation(automation);
  state.saveState = "draft";

  syncAutomationToForm();
  await refreshCodeFromVisual();
  renderAll();

  $("newAutomationModal")?.close();
  toast("New automation ready");
}

const TEMPLATE_PAGE_SIZE = 6;

function renderTemplates() {
  if (!exists("templateList")) return;

  if (state.templateSearch === undefined) state.templateSearch = "";
  if (state.templatePage === undefined) state.templatePage = 1;
  if (state.selectedTemplateId === undefined) state.selectedTemplateId = "";
  if (state.templateCategory === undefined) state.templateCategory = "All";

  const templates = Array.isArray(state.templates) ? state.templates : [];
  const query = String(state.templateSearch || "")
    .trim()
    .toLowerCase();
  const selectedCategory = state.templateCategory || "All";

  const allTemplates = [
    {
      id: "",
      name: "Blank automation",
      description: "Start from scratch with an empty automation.",
      category: "Blank",
    },
    ...templates.map((template) => ({
      id: template.id,
      name: template.name,
      description:
        template.description || "Create an automation from this template.",
      category: template.category || inferTemplateCategory(template),
    })),
  ];

  const categories = getTemplateCategories(allTemplates);

  renderTemplateCategoryFilter(categories);
  renderTemplateCategoryChips(categories);

  let filtered = allTemplates;

  if (selectedCategory !== "All") {
    filtered = filtered.filter(
      (template) => template.category === selectedCategory,
    );
  }

  if (query) {
    filtered = filtered.filter((template) => {
      return (
        String(template.name || "")
          .toLowerCase()
          .includes(query) ||
        String(template.description || "")
          .toLowerCase()
          .includes(query) ||
        String(template.category || "")
          .toLowerCase()
          .includes(query)
      );
    });
  }

  const totalPages = Math.max(
    1,
    Math.ceil(filtered.length / TEMPLATE_PAGE_SIZE),
  );
  state.templatePage = Math.min(Math.max(1, state.templatePage), totalPages);

  const start = (state.templatePage - 1) * TEMPLATE_PAGE_SIZE;
  const pageItems = filtered.slice(start, start + TEMPLATE_PAGE_SIZE);

  $("templateList").innerHTML = pageItems.length
    ? pageItems
        .map((template) => {
          const selected = template.id === state.selectedTemplateId;

          return `
            <button
              class="template-card ${selected ? "selected" : ""}"
              type="button"
              data-template-id="${escapeHtml(template.id)}"
            >
              <span class="template-icon">${templateIcon(template)}</span>
              <span>
                <strong>${escapeHtml(template.name)}</strong>
                <small>${escapeHtml(short(template.description, 110))}</small>
              </span>
              <span class="template-meta">${escapeHtml(template.category)}</span>
              <span class="template-check">${selected ? "✓" : ""}</span>
            </button>
          `;
        })
        .join("")
    : `
      <div class="empty-mini">
        <strong>No templates found</strong>
        <span>Try another search term or category.</span>
      </div>
    `;

  if (exists("templatePageInfo")) {
    $("templatePageInfo").textContent =
      `Page ${state.templatePage} of ${totalPages} • ${filtered.length} template${filtered.length === 1 ? "" : "s"}`;
  }

  if (exists("templatePrevBtn")) {
    $("templatePrevBtn").disabled = state.templatePage <= 1;
  }

  if (exists("templateNextBtn")) {
    $("templateNextBtn").disabled = state.templatePage >= totalPages;
  }

  if (exists("templateSelect")) {
    $("templateSelect").value = state.selectedTemplateId || "";
  }

  document.querySelectorAll("[data-template-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedTemplateId = button.dataset.templateId || "";

      if (exists("templateSelect")) {
        $("templateSelect").value = state.selectedTemplateId;
      }

      renderTemplates();
    });
  });
}

function getTemplateCategories(templates) {
  const preferredOrder = [
    "All",
    "Blank",
    "Education",
    "Data",
    "Documents",
    "Research",
    "Productivity",
    "Files",
    "Developer",
    "Business",
    "General",
  ];

  const found = new Set(["All"]);

  templates.forEach((template) => {
    if (template.category) found.add(template.category);
  });

  return [
    ...preferredOrder.filter((category) => found.has(category)),
    ...[...found]
      .filter((category) => !preferredOrder.includes(category))
      .sort(),
  ];
}

function renderTemplateCategoryFilter(categories) {
  if (!exists("templateCategoryFilter")) return;

  const current = state.templateCategory || "All";

  $("templateCategoryFilter").innerHTML = categories
    .map((category) => {
      const selected = category === current ? "selected" : "";
      const label = category === "All" ? "All categories" : category;
      return `<option value="${escapeHtml(category)}" ${selected}>${escapeHtml(label)}</option>`;
    })
    .join("");
}

function renderTemplateCategoryChips(categories) {
  if (!exists("templateCategoryChips")) return;

  const current = state.templateCategory || "All";

  $("templateCategoryChips").innerHTML = categories
    .map((category) => {
      const active = category === current;
      return `
        <button
          class="template-category-chip ${active ? "active" : ""}"
          type="button"
          data-template-category="${escapeHtml(category)}"
        >
          ${escapeHtml(category)}
        </button>
      `;
    })
    .join("");

  document.querySelectorAll("[data-template-category]").forEach((button) => {
    button.addEventListener("click", () => {
      state.templateCategory = button.dataset.templateCategory || "All";
      state.templatePage = 1;

      if (exists("templateCategoryFilter")) {
        $("templateCategoryFilter").value = state.templateCategory;
      }

      renderTemplates();
    });
  });
}

function inferTemplateCategory(template) {
  const text =
    `${template.id || ""} ${template.name || ""} ${template.description || ""}`.toLowerCase();

  if (
    text.includes("csv") ||
    text.includes("data") ||
    text.includes("excel") ||
    text.includes("expense") ||
    text.includes("inventory")
  )
    return "Data";
  if (
    text.includes("pdf") ||
    text.includes("resume") ||
    text.includes("contract") ||
    text.includes("invoice")
  )
    return "Documents";
  if (
    text.includes("student") ||
    text.includes("study") ||
    text.includes("quiz") ||
    text.includes("lesson") ||
    text.includes("attendance") ||
    text.includes("marks")
  )
    return "Education";
  if (
    text.includes("research") ||
    text.includes("literature") ||
    text.includes("paper")
  )
    return "Research";
  if (
    text.includes("meeting") ||
    text.includes("daily") ||
    text.includes("weekly") ||
    text.includes("email")
  )
    return "Productivity";
  if (
    text.includes("file") ||
    text.includes("zip") ||
    text.includes("image") ||
    text.includes("html") ||
    text.includes("json")
  )
    return "Files";
  if (
    text.includes("code") ||
    text.includes("python") ||
    text.includes("scaffold") ||
    text.includes("bug")
  )
    return "Developer";
  if (
    text.includes("risk") ||
    text.includes("sales") ||
    text.includes("customer") ||
    text.includes("business")
  )
    return "Business";

  return "General";
}

function templateIcon(template) {
  const category = template.category || inferTemplateCategory(template);

  const icons = {
    Blank: "◇",
    Data: "▦",
    Documents: "▤",
    Education: "▥",
    Research: "⌁",
    Productivity: "☷",
    Files: "▣",
    Developer: "</>",
    Business: "▧",
    General: "✦",
  };

  return icons[category] || icons.General;
}

function renderAutomations() {
  const list = $("automationList");
  if (!list) return;

  let items = Array.isArray(state.automations) ? [...state.automations] : [];
  const q = state.automationSearch.trim().toLowerCase();

  if (q) {
    items = items.filter(
      (item) =>
        String(item.name || "")
          .toLowerCase()
          .includes(q) ||
        String(item.model || "")
          .toLowerCase()
          .includes(q) ||
        (item.tools || []).some((t) => t.toLowerCase().includes(q)),
    );
  }

  items.sort(sortAutomationComparator(state.automationSort));

  if (!items.length) {
    list.innerHTML = `
      <div class="empty-mini">
        <strong>No automations found</strong>
        <span>Create one or adjust your search.</span>
      </div>
    `;
    return;
  }

  list.innerHTML = items
    .map((item) => {
      const active = item.id && item.id === state.automation.id;
      const status = item.last_run_status
        ? `<span class="status-mini ${item.last_run_status}">${escapeHtml(item.last_run_status)}</span>`
        : "";
      return `
      <article class="automation-card ${active ? "selected" : ""}" data-load-automation="${escapeHtml(item.id)}">
        <div class="automation-icon">${automationIcon(item)}</div>
        <div class="automation-main">
          <strong>${escapeHtml(item.name)}</strong>
          <small>${relativeTime(item.updated_at || item.last_run_at)} ${status}</small>
        </div>
        <div class="automation-actions">
          <button title="Edit" data-edit-automation="${escapeHtml(item.id)}">✎</button>
          <button title="Duplicate" data-duplicate-automation="${escapeHtml(item.id)}">⧉</button>
          <button title="Delete" data-delete-automation="${escapeHtml(item.id)}">⌫</button>
        </div>
      </article>
    `;
    })
    .join("");

  document.querySelectorAll("[data-load-automation]").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest(".automation-actions")) return;
      loadAutomation(card.dataset.loadAutomation);
    });
  });

  document.querySelectorAll("[data-edit-automation]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      loadAutomation(btn.dataset.editAutomation);
    });
  });

  document.querySelectorAll("[data-duplicate-automation]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      duplicateAutomation(btn.dataset.duplicateAutomation);
    });
  });

  document.querySelectorAll("[data-delete-automation]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      askDeleteAutomation(btn.dataset.deleteAutomation);
    });
  });
}

function sortAutomationComparator(sort) {
  return (a, b) => {
    if (sort === "name_asc")
      return String(a.name).localeCompare(String(b.name));
    if (sort === "name_desc")
      return String(b.name).localeCompare(String(a.name));
    if (sort === "updated_asc")
      return String(a.updated_at || "").localeCompare(
        String(b.updated_at || ""),
      );
    if (sort === "created_asc")
      return String(a.created_at || "").localeCompare(
        String(b.created_at || ""),
      );
    if (sort === "created_desc")
      return String(b.created_at || "").localeCompare(
        String(a.created_at || ""),
      );
    return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
  };
}

function renderAutomationTitle() {
  if (exists("automationTitle"))
    $("automationTitle").textContent =
      state.automation.name || "Untitled Automation";
  if (exists("automationStatusBadge")) {
    $("automationStatusBadge").textContent = state.automation.id
      ? "Saved"
      : "Draft";
    $("automationStatusBadge").className =
      `badge ${state.automation.id ? "saved" : "draft"}`;
  }
}

function updateSaveState() {
  if (!exists("saveStateBadge")) return;

  const badge = $("saveStateBadge");
  if (state.saveState === "saved") {
    badge.textContent = "Saved";
    badge.className = "badge subtle";
  } else {
    badge.textContent = "Unsaved";
    badge.className = "badge draft";
  }
}

function groupTools() {
  return state.tools.reduce((acc, tool) => {
    const cat = tool.category || "Other";
    (acc[cat] ||= []).push(tool);
    return acc;
  }, {});
}

function renderCategories() {
  const grouped = groupTools();

  const ordered = [
    "Files",
    "Data",
    "PDF",
    "Text",
    "Reports",
    "Memory",
    "Math",
    "Other",
  ]
    .filter((cat) => grouped[cat])
    .concat(
      Object.keys(grouped)
        .filter(
          (cat) =>
            ![
              "Files",
              "Data",
              "PDF",
              "Text",
              "Reports",
              "Memory",
              "Math",
              "Other",
            ].includes(cat),
        )
        .sort(),
    );

  const list = $("categoryList");
  if (!list) return;

  list.innerHTML = ordered
    .map((cat) => {
      const icon = toolIcon({ category: cat });
      return `
        <button class="category-card ${state.selectedCategory === cat ? "selected" : ""}" data-category="${escapeHtml(cat)}">
          <span>${icon} ${escapeHtml(cat)}</span>
          <span class="count">${grouped[cat].length}</span>
          <span>›</span>
        </button>
      `;
    })
    .join("");

  document.querySelectorAll("[data-category]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.selectedCategory = btn.dataset.category;
      renderCategories();
      renderToolSearch(true);
    });
  });
}

function renderSelectedTools() {
  const container = $("selectedTools");
  if (!container) return;

  const tools = (state.automation.tools || []).map(
    (name) =>
      state.tools.find((t) => t.name === name) || { name, category: "Other" },
  );

  container.innerHTML = tools
    .map(
      (tool) => `
    <span class="chip">
      ${toolIcon(tool)} ${escapeHtml(tool.name)}
      <button data-remove-tool="${escapeHtml(tool.name)}" type="button">×</button>
    </span>
  `,
    )
    .join("");

  document.querySelectorAll("[data-remove-tool]").forEach((btn) => {
    btn.addEventListener("click", () => toggleTool(btn.dataset.removeTool));
  });
}

function renderToolSearch(force = false) {
  const q = $("toolSearch")?.value.trim().toLowerCase() || "";
  const panel = $("toolResults");
  if (!panel) return;

  if (!force && !q && state.selectedCategory === "All") {
    panel.classList.add("hidden");
    return;
  }

  const list = state.tools
    .filter((t) => {
      const cat =
        state.selectedCategory === "All" ||
        t.category === state.selectedCategory;
      const search =
        !q ||
        t.name.toLowerCase().includes(q) ||
        String(t.description || "")
          .toLowerCase()
          .includes(q);
      return cat && search;
    })
    .slice(0, 16);

  panel.classList.remove("hidden");
  panel.innerHTML = list.length
    ? list
        .map(
          (tool) => `
    <button class="tool-result ${state.automation.tools.includes(tool.name) ? "selected" : ""}" data-tool="${escapeHtml(tool.name)}" type="button">
      <span>
        <strong>${escapeHtml(tool.name)}</strong>
        <small>${escapeHtml(short(tool.description, 90))}</small>
      </span>
      <span>${state.automation.tools.includes(tool.name) ? "✓" : "+"}</span>
    </button>
  `,
        )
        .join("")
    : '<p class="muted tiny">No matching tools.</p>';

  document.querySelectorAll("[data-tool]").forEach((btn) => {
    btn.addEventListener("click", () => toggleTool(btn.dataset.tool));
  });
}

function openToolPicker() {
  renderToolPicker();
  const modal = $("toolPickerModal");
  if (modal?.showModal) modal.showModal();
  else $("toolSearch")?.focus();
}

function renderToolPicker() {
  const list = $("toolPickerList");
  if (!list) return;

  const q = $("toolPickerSearch")?.value.trim().toLowerCase() || "";

  const tools = state.tools.filter(
    (tool) =>
      !q ||
      tool.name.toLowerCase().includes(q) ||
      String(tool.description || "")
        .toLowerCase()
        .includes(q) ||
      String(tool.category || "")
        .toLowerCase()
        .includes(q),
  );

  list.innerHTML = tools
    .map((tool) => {
      const selected = state.automation.tools.includes(tool.name);
      return `
      <button class="tool-picker-item ${selected ? "selected" : ""}" data-picker-tool="${escapeHtml(tool.name)}" type="button">
        <span class="tool-picker-icon">${toolIcon(tool)}</span>
        <span>
          <strong>${escapeHtml(tool.name)}</strong>
          <small>${escapeHtml(short(tool.description, 120))}</small>
        </span>
        <span class="tool-mode ${escapeHtml(tool.mode || "other")}">${escapeHtml(tool.mode || "other")}</span>
        <span>${selected ? "✓" : "+"}</span>
      </button>
    `;
    })
    .join("");

  document.querySelectorAll("[data-picker-tool]").forEach((btn) => {
    btn.addEventListener("click", () => {
      toggleTool(btn.dataset.pickerTool);
      renderToolPicker();
    });
  });
}

async function toggleTool(name) {
  if (state.automation.tools.includes(name)) {
    state.automation.tools = state.automation.tools.filter((t) => t !== name);
  } else {
    state.automation.tools.push(name);
  }

  renderSelectedTools();
  renderToolSearch(true);
  renderToolPicker();
  await syncAutomationFromForm();
}

async function refreshCodeFromVisual({ quiet = false } = {}) {
  try {
    const res = await request(API.generateCode, {
      method: "POST",
      body: JSON.stringify({ automation: collectAutomation() }),
    });

    state.code = res.code || "";
    state.codeDirty = false;

    renderCode({ force: true })

    updateSyncStatus("synced", "Bidirectional Sync");
  } catch (e) {
    state.code = `# Could not generate Python\n# ${e.message}`;
    renderCode();
    if (!quiet) toast(`Code generation failed: ${e.message}`, "error");
    updateSyncStatus("error", "Sync failed");
  }
}

async function syncCodeToVisual() {
  if (!exists("codeEditor")) return;

  const code = $("codeEditor").value;
  state.code = code;

  let parsed = null;
  let lastError = null;

  for (const endpoint of API.parseCodeCandidates) {
    try {
      const res = await request(endpoint, {
        method: "POST",
        body: JSON.stringify({ code }),
      });
      parsed = res.automation || res;
      break;
    } catch (e) {
      lastError = e;
    }
  }

  if (!parsed) {
    parsed = parsePythonAutomation(code);
  }

  if (!parsed) {
    updateSyncStatus("error", "Could not parse code");
    if (lastError && !String(lastError.message).includes("404")) {
      toast(`Code sync failed: ${lastError.message}`, "error");
    }
    return;
  }

  state.automation = normalizeAutomation({
    ...state.automation,
    ...parsed,
    id: state.automation.id,
    created_at: state.automation.created_at,
    updated_at: state.automation.updated_at,
  });

  state.codeDirty = false;
  state.visualDirty = true;
  state.saveState = "draft";

  syncAutomationToForm();
  renderJson();
  updateSyncStatus("synced", "Synced from Code");
  updateSaveState();
}

function parsePythonAutomation(code) {
  try {
    const getString = (key) => {
      const rx = new RegExp(`${key}\\s*=\\s*(['"\`])([\\s\\S]*?)\\1`, "m");
      const match = code.match(rx);
      return match ? match[2] : undefined;
    };

    const getNumber = (key) => {
      const rx = new RegExp(`${key}\\s*=\\s*([0-9.]+)`, "m");
      const match = code.match(rx);
      return match ? Number(match[1]) : undefined;
    };

    const getBool = (key) => {
      const rx = new RegExp(`${key}\\s*=\\s*(True|False|true|false)`, "m");
      const match = code.match(rx);
      if (!match) return undefined;
      return match[1].toLowerCase() === "true";
    };

    const toolsMatch = code.match(/tools\s*=\s*\[([\s\S]*?)\]/m);
    let tools = undefined;
    if (toolsMatch) {
      tools = toolsMatch[1]
        .split(",")
        .map((x) => x.trim())
        .map((x) => x.replace(/\(\)$/g, ""))
        .filter(Boolean)
        .filter((x) => /^[a-zA-Z_][a-zA-Z0-9_]*$/.test(x));
    }

    const runMatch = code.match(
      /agent\.run\(\s*("""|'''|["'])([\s\S]*?)\1\s*\)/m,
    );

    return {
      name: getString("name") || state.automation.name,
      model: getString("model") || state.automation.model,
      goal: getString("goal") || state.automation.goal,
      task: runMatch
        ? runMatch[2].trim()
        : getString("task") || state.automation.task,
      tools: tools || state.automation.tools,
      max_steps: getNumber("max_steps") || state.automation.max_steps,
      temperature: getNumber("temperature") ?? state.automation.temperature,
      safe_mode: getBool("safe_mode") ?? state.automation.safe_mode,
    };
  } catch {
    return null;
  }
}

function updateSyncStatus(kind, text) {
  if (exists("syncStatusText")) $("syncStatusText").textContent = text;
  if (exists("syncStatusDot")) $("syncStatusDot").className = kind;
}

function formatCode() {
  if (!exists("codeEditor")) return;
  $("codeEditor").value =
    $("codeEditor").value.replace(/\t/g, "    ").trim() + "\n";
  state.code = $("codeEditor").value;
  toast("Code formatted");
}

async function runAutomation() {
  try {
    await syncAutomationFromForm();

    setRunStatus("queued");
    state.events = [];
    state.runStartedAt = new Date();
    state.runFinishedAt = null;
    state.runId = null;
    setWorkspaceMode("build");

    renderEvents();
    renderRunSummary();

    const started = await request(API.run, {
      method: "POST",
      body: JSON.stringify({ automation: state.automation }),
    });

    state.runId = started.run_id;
    setRunStatus("running");

    if (state.source) state.source.close();

    state.source = new EventSource(started.stream_url, {
      withCredentials: true,
    });

    state.source.addEventListener("message", (event) => {
      try {
        const data = JSON.parse(event.data);
        state.events.push(data);

        if (data.type === "ERROR") setRunStatus("failed");
        if (data.type === "DONE") setRunStatus("success");

        renderEvents();
        renderRunSummary();
      } catch {}
    });

    state.source.addEventListener("end", async () => {
      state.source.close();
      state.runFinishedAt = new Date();
      await Promise.allSettled([loadOutputs(), loadRuns(), loadAutomations()]);

      if (state.runStatus === "running" || state.runStatus === "queued") {
        setRunStatus("success");
      }

      setBottomTab("outputs");
      setBottomTab("outputs");
      renderRunSummary();
    });

    state.source.addEventListener("error", () => {
      if (state.source) state.source.close();
      if (state.runStatus === "running") setRunStatus("failed");
      state.runFinishedAt = new Date();
      renderRunSummary();
    });
  } catch (e) {
    setRunStatus("failed");
    toast(`Run failed: ${e.message}`, "error");
  }
}

function setRunStatus(status) {
  state.runStatus = status;
  renderRunStatus();
}

function renderRunStatus() {
  const dot = $("runStatusDot");
  if (dot) dot.className = `run-status-dot ${state.runStatus}`;

  if (exists("runStatusText")) {
    $("runStatusText").textContent =
      state.runStatus === "idle" ? "Ready" : capitalize(state.runStatus);
  }

  const running = state.runStatus === "running" || state.runStatus === "queued";

  ["runBtn", "topRunBtn"].forEach((id) => {
    if (!exists(id)) return;
    $(id).disabled = running;
    $(id).textContent = running ? "Running..." : "Run Automation";
  });

  renderRunCurrentStep();
  renderRunSuccessCard();
}

function renderRunViewTabs() {
  document.querySelectorAll("[data-run-view]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.runView === state.runView);
  });
}

function renderEvents() {
  const stream = $("eventStream");
  if (!stream) return;

  const events = filteredEvents();

  renderRunCurrentStep();
  renderRunSuccessCard();

  if (!state.events.length) {
    stream.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">✦</div>
        <h3>Ready to run</h3>
        <p>Click Run Automation to watch the automation progress here.</p>
      </div>
    `;

    if (exists("retryText")) $("retryText").textContent = "No retries needed";
    return;
  }

  if (!events.length) {
    stream.innerHTML = `<p class="muted tiny">No events in ${escapeHtml(state.runView)} view.</p>`;
    return;
  }

  stream.innerHTML = events.map((event) => renderEvent(event)).join("");

  document.querySelectorAll("[data-event-expand]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const body = document.querySelector(
        `[data-event-body="${btn.dataset.eventExpand}"]`,
      );
      body?.classList.toggle("expanded");
    });
  });

  if (exists("retryText")) {
    const retries = state.events.filter((e) => e.type === "RETRY").length;
    $("retryText").textContent = retries
      ? `${retries} retry event${retries === 1 ? "" : "s"} used`
      : "No retries needed";
  }

  stream.scrollTop = stream.scrollHeight;
}

function renderRunCurrentStep() {
  if (!exists("runCurrentStepText") && !exists("runCurrentStepHint")) return;

  const latest = latestMeaningfulEvent();

  let title = "Ready to run";
  let hint = "Click Run Automation to watch the agent plan, use tools, and create outputs.";

  if (state.runStatus === "queued") {
    title = "Starting automation";
    hint = "Preparing the local run.";
  } else if (state.runStatus === "running") {
    title = latest ? friendlyEventTitle(latest) : "Automation is running";
    hint = latest ? friendlyEventMessage(latest) : "The agent is working locally.";
  } else if (state.runStatus === "success") {
    title = "Automation completed";
    hint = state.outputs.length
      ? `${state.outputs.length} output file${state.outputs.length === 1 ? "" : "s"} ready.`
      : "Run completed successfully.";
  } else if (state.runStatus === "failed") {
    title = "Automation needs attention";
    hint = latest ? friendlyEventMessage(latest) : "Check the run details below.";
  }

  if (exists("runCurrentStepText")) $("runCurrentStepText").textContent = title;
  if (exists("runCurrentStepHint")) $("runCurrentStepHint").textContent = hint;
}

function renderRunSuccessCard() {
  const card = $("runSuccessCard");
  if (!card) return;

  const shouldShow =
    state.runStatus === "success" &&
    Array.isArray(state.outputs) &&
    state.outputs.length > 0;

  card.classList.toggle("hidden", !shouldShow);

  if (!shouldShow) return;

  const strong = card.querySelector("strong");
  const paragraph = card.querySelector("p");

  if (strong) {
    strong.textContent = `${state.outputs.length} output file${state.outputs.length === 1 ? "" : "s"} ready`;
  }

  if (paragraph) {
    const latest = state.outputs[0]?.name || "Generated files";
    paragraph.textContent = `${latest} and other outputs are available below.`;
  }
}

function latestMeaningfulEvent() {
  const priority = ["ERROR", "DONE", "ACTION", "OBSERVATION", "PLAN", "RETRY"];
  const events = [...state.events].reverse();

  return events.find((event) => priority.includes(event.type)) || events[0] || null;
}

function friendlyEventTitle(event) {
  if (!event) return "Working";

  if (event.type === "PLAN") return "Planning next steps";
  if (event.type === "ACTION") return friendlyActionTitle(event.message);
  if (event.type === "OBSERVATION") return "Reviewing result";
  if (event.type === "RETRY") return "Recovering from issue";
  if (event.type === "DONE") return "Automation completed";
  if (event.type === "ERROR") return "Issue detected";

  return humanize(event.type || "Working");
}

function friendlyEventMessage(event) {
  if (!event) return "";

  if (event.type === "ACTION") return summarizeAction(event.message);
  if (event.type === "OBSERVATION") return summarizeObservation(event.message);
  if (event.type === "PLAN") return short(event.message, 120);
  if (event.type === "ERROR") return short(event.message, 140);
  if (event.type === "DONE") return "Generated outputs are ready.";

  return short(event.message || "", 120);
}

function friendlyActionTitle(message) {
  const raw = String(message || "");
  const match = raw.match(/^([a-zA-Z_][a-zA-Z0-9_]*)\(/);
  const tool = match ? match[1] : raw;

  const labels = {
    list_files: "Reading folder",
    read_file: "Reading file",
    read_pdf: "Reading PDF",
    read_csv: "Reading CSV",
    summarize_csv: "Analyzing CSV",
    create_markdown_report: "Creating report",
    write_file: "Writing file",
    append_file: "Updating file",
    create_folder: "Creating folder",
    copy_file: "Copying file",
    move_file: "Moving file",
    extract_keywords: "Extracting keywords",
    compare_texts: "Comparing documents",
  };

  return labels[tool] || humanize(tool);
}

function filteredEvents() {
  if (state.runView === "debug") return state.events;

  if (state.runView === "detailed") {
    return state.events.filter((e) => !["MODEL"].includes(e.type));
  }

  const allowed = ["PLAN", "ACTION", "OBSERVATION", "RETRY", "ERROR", "DONE"];
  return compressEvents(state.events.filter((e) => allowed.includes(e.type)));
}

function compressEvents(events) {
  return events.map((event) => {
    if (event.type === "OBSERVATION") {
      return { ...event, message: summarizeObservation(event.message) };
    }
    if (event.type === "ACTION") {
      return { ...event, message: summarizeAction(event.message) };
    }
    if (event.type === "PLAN") {
      return { ...event, message: short(event.message, 130) };
    }
    if (event.type === "ERROR") {
      return { ...event, message: short(event.message, 160) };
    }
    return { ...event, message: short(event.message, 150) };
  });
}

function renderEvent(event) {
  const type = event.type || "SYSTEM";
  const lower = type.toLowerCase();
  const id = event.id || `${lower}-${Math.random()}`;
  const duration = event.duration_ms
    ? `${(event.duration_ms / 1000).toFixed(1)}s`
    : "";
  const long =
    String(event.message || "").length > 180 ||
    Object.keys(event.data || {}).length;

  return `
    <article class="event-item ${lower}">
      <div class="event-icon">${eventIcon(type)}</div>
      <div class="event-body">
        <div class="event-title">
          <strong>${escapeHtml(cleanEventTitle(type, event.message))}</strong>
          <span>${formatTime(event.timestamp)}</span>
        </div>
        <p>${escapeHtml(event.message)}</p>
        ${badgeFor(event)}
        ${
          long
            ? `
          <button class="event-expand" data-event-expand="${escapeHtml(id)}" type="button">View details</button>
          <pre class="event-details" data-event-body="${escapeHtml(id)}">${escapeHtml(JSON.stringify(event.data || {}, null, 2))}</pre>
        `
            : ""
        }
      </div>
      <div class="duration">${duration}</div>
    </article>
  `;
}

function cleanEventTitle(type, message) {
  if (type === "ACTION") {
    const match = String(message || "").match(/^([a-zA-Z_][a-zA-Z0-9_]*)\(/);
    return match ? match[1] : "Action";
  }
  if (type === "OBSERVATION") return "Observation";
  if (type === "PLAN") return "Plan";
  if (type === "DONE") return "Done";
  if (type === "ERROR") return "Error";
  if (type === "RETRY") return "Retry";
  return type;
}

function summarizeAction(message) {
  const m = String(message || "");
  const toolMatch = m.match(/^([a-zA-Z_][a-zA-Z0-9_]*)\(([\s\S]*)\)$/);
  if (!toolMatch) return short(m, 130);

  const tool = toolMatch[1];
  const args = toolMatch[2];

  const pathMatch =
    args.match(/"path"\s*:\s*"([^"]+)"/) ||
    args.match(/"source"\s*:\s*"([^"]+)"/) ||
    args.match(/"output_path"\s*:\s*"([^"]+)"/);

  return pathMatch ? `${tool} • ${pathMatch[1]}` : tool;
}

function summarizeObservation(message) {
  const m = String(message || "");
  if (/created|wrote|saved|generated/i.test(m)) return short(m, 140);
  if (/read|found|listed|extracted/i.test(m)) return short(m, 140);
  return short(m, 120);
}

function eventIcon(type) {
  return (
    {
      PLAN: "✓",
      ACTION: "▶",
      OBSERVATION: "◉",
      RETRY: "↻",
      DONE: "✓",
      ERROR: "!",
      SYSTEM: "i",
      MODEL: "◌",
      AGENT: "◆",
      WORKFLOW: "▦",
    }[type] || "i"
  );
}

function badgeFor(event) {
  if (event.type === "DONE")
    return '<span class="mini-badge green">Outputs ready</span>';
  if (event.type === "PLAN")
    return '<span class="mini-badge green">steps planned</span>';
  if (event.type === "ERROR")
    return '<span class="mini-badge red">Needs attention</span>';
  if (event.type === "RETRY")
    return '<span class="mini-badge orange">Recovered</span>';
  return "";
}

function renderRunSummary() {
  const start = state.runStartedAt;
  const end = state.runFinishedAt || new Date();

  const duration = start ? Math.max(0, Math.floor((end - start) / 1000)) : 0;
  const steps = state.events.filter((e) =>
    ["ACTION", "PLAN", "OBSERVATION", "DONE"].includes(e.type),
  ).length;
  const retries = state.events.filter((e) => e.type === "RETRY").length;
  const outputs = state.outputs.length;

  if (exists("runDuration"))
    $("runDuration").textContent = formatDuration(duration);
  if (exists("runSteps")) $("runSteps").textContent = steps ? `${steps}` : "0";
  if (exists("runRetries")) $("runRetries").textContent = retries;
  if (exists("runOutputs")) $("runOutputs").textContent = outputs;
  renderRunCurrentStep();
renderRunSuccessCard();
}

function renderCode(options = {}) {
  const force = options.force === true

  if (exists("codeBlock")) {
    $("codeBlock").innerHTML = codeLines(state.code)
  }

  const editor = $("codeEditor")
  if (!editor) return

  const shouldUpdateEditor =
    force ||
    document.activeElement !== editor ||
    !state.codeDirty ||
    editor.value.trim() === ""

  if (shouldUpdateEditor && editor.value !== state.code) {
    editor.value = state.code || ""
  }
}

function renderJson() {
  if (exists("jsonBlock")) {
    $("jsonBlock").innerHTML = codeLines(
      JSON.stringify(collectAutomation(), null, 2),
    );
  }
}

function codeLines(code) {
  return String(code || "")
    .split("\n")
    .map(
      (line, i) =>
        `<div class="code-line"><span class="line-no">${i + 1}</span><code>${escapeHtml(line || " ")}</code></div>`,
    )
    .join("");
}

function setBottomTab(tab) {
  state.bottomTab = tab;
  renderBottomTabs();

  if (tab === "json") renderJson();
  if (tab === "outputs") loadOutputs();
  if (tab === "history") loadRuns();

  if (tab === "code") {
    renderCode({ force: true });
  }
}

function renderBottomTabs() {
  document.querySelectorAll("[data-bottom-tab]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.bottomTab === state.bottomTab);
  });

  document
    .querySelectorAll(".tab-panel")
    .forEach((panel) => panel.classList.remove("active"));

  const panel = $(`${state.bottomTab}Tab`);
  if (panel) panel.classList.add("active");
}

async function loadOutputs() {
  try {
    const res = await request(API.outputs);
    state.outputs = Array.isArray(res)
      ? res
      : Array.isArray(res.items)
        ? res.items
        : [];

    state.lastOutputCount = state.outputs.length;

    renderOutputs();
    renderRunSummary();
    renderRunSuccessCard();
  } catch (e) {
    state.outputs = [];
    renderOutputs();
    toast(`Could not load outputs: ${e.message}`, "error");
  }
}

function renderOutputs() {
  if (exists("outputCount"))
    $("outputCount").textContent = state.outputs.length;

  const list = $("outputsList");
  if (!list) return;

  if (!state.outputs.length) {
    list.innerHTML = `
      <div class="empty-mini">
        <strong>No output files yet</strong>
        <span>Generated files will appear here after a successful run.</span>
      </div>
    `;
    return;
  }

  list.innerHTML = state.outputs
    .map(
      (file) => `
    <article class="output-row">
      <span class="output-file-icon">▣</span>
      <span class="output-main">
        <strong>${escapeHtml(file.name)}</strong>
        <small>${escapeHtml(file.extension || "file")} • ${formatBytes(file.size_bytes)} • ${relativeTime(file.modified_at)}</small>
      </span>
      <button class="btn ghost small" data-preview-output="${encodeURIComponent(file.path)}" type="button">Preview</button>
      <a class="btn ghost small" href="/api/outputs/download?path=${encodeURIComponent(file.path)}" target="_blank" rel="noreferrer">Download</a>
      <button class="plain-icon" data-delete-output="${encodeURIComponent(file.path)}" type="button">⋮</button>
    </article>
  `,
    )
    .join("");

  document.querySelectorAll("[data-preview-output]").forEach((btn) => {
    btn.addEventListener("click", () =>
      openOutput(decodeURIComponent(btn.dataset.previewOutput)),
    );
  });
}

async function openOutput(path) {
  try {
    const preview = await request(
      `/api/outputs/preview?path=${encodeURIComponent(path)}`,
    );

    if (preview.binary) {
      if (exists("outputPreview")) {
        $("outputPreview").textContent =
          `Binary file. Download: ${preview.download_url}`;
      }
    } else {
      if (exists("outputPreview"))
        $("outputPreview").textContent = preview.content;
      if (exists("outputPreviewModalContent"))
        $("outputPreviewModalContent").textContent = preview.content;
      if (exists("outputPreviewTitle"))
        $("outputPreviewTitle").textContent = path;
      if (exists("outputPreviewMeta"))
        $("outputPreviewMeta").textContent = preview.truncated
          ? "Preview truncated"
          : "Full preview";
      if ($("outputPreviewModal")?.showModal)
        $("outputPreviewModal").showModal();
    }

    setBottomTab("outputs");
  } catch (e) {
    toast(`Preview failed: ${e.message}`, "error");
  }
}

async function loadRuns() {
  try {
    state.runs = await request(`${API.runs}?limit=30`);
    renderRuns();
  } catch {
    state.runs = [];
    renderRuns();
  }
}

function renderRuns() {
  const list = $("runHistoryList");
  if (!list) return;

  if (!state.runs.length) {
    list.innerHTML = '<p class="muted tiny">No runs yet.</p>';
    return;
  }

  list.innerHTML = state.runs
    .map(
      (run) => `
    <article class="run-history-row">
      <span class="status-mini ${escapeHtml(run.status)}">${escapeHtml(run.status)}</span>
      <span>
        <strong>${escapeHtml(run.automation_name || "Automation")}</strong>
        <small>${relativeTime(run.started_at)} • ${run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : "—"}</small>
      </span>
    </article>
  `,
    )
    .join("");
}

async function copyCode() {
  const code = exists("codeEditor") ? $("codeEditor").value : state.code;
  await navigator.clipboard.writeText(code);
  toast("Python copied");
}

function downloadCode() {
  const code = exists("codeEditor") ? $("codeEditor").value : state.code;
  const blob = new Blob([code], { type: "text/x-python" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");

  a.href = url;
  a.download = `${slug(state.automation.name)}.py`;
  a.click();

  URL.revokeObjectURL(url);
}

function humanize(s) {
  return String(s)
    .replace(/_/g, " ")
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

function slug(s) {
  return (
    String(s || "automation")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "") || "automation"
  );
}

function short(s, n) {
  s = String(s || "");
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

function escapeHtml(s) {
  return String(s ?? "").replace(
    /[&<>'"]/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "'": "&#39;",
        '"': "&quot;",
      })[c],
  );
}

function formatBytes(bytes) {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes || 0;
  let unit = 0;

  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }

  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function formatTime(value) {
  try {
    return new Date(value).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "—";
  }
}

function relativeTime(value) {
  if (!value) return "Never";

  const date = new Date(value);
  const diff = Date.now() - date.getTime();
  const sec = Math.floor(diff / 1000);
  const min = Math.floor(sec / 60);
  const hr = Math.floor(min / 60);
  const day = Math.floor(hr / 24);

  if (sec < 60) return "Just now";
  if (min < 60) return `${min} min ago`;
  if (hr < 24) return `${hr} hour${hr === 1 ? "" : "s"} ago`;
  if (day < 7) return `${day} day${day === 1 ? "" : "s"} ago`;

  return date.toLocaleDateString();
}

function formatDuration(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;

  if (h)
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function capitalize(s) {
  return (
    String(s || "")
      .charAt(0)
      .toUpperCase() + String(s || "").slice(1)
  );
}

function initials(value) {
  return (
    String(value || "SA")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((x) => x[0]?.toUpperCase())
      .join("") || "SA"
  );
}

function iconSvg(name) {
  const icons = {
    automation: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="5" y="4" width="14" height="16" rx="3"></rect>
        <path d="M9 8h6M9 12h6M9 16h4"></path>
      </svg>
    `,
    file: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 3h7l4 4v14H7z"></path>
        <path d="M14 3v5h5"></path>
      </svg>
    `,
    folder: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M3 7h7l2 2h9v10H3z"></path>
      </svg>
    `,
    database: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <ellipse cx="12" cy="5" rx="7" ry="3"></ellipse>
        <path d="M5 5v7c0 1.7 3.1 3 7 3s7-1.3 7-3V5"></path>
        <path d="M5 12v7c0 1.7 3.1 3 7 3s7-1.3 7-3v-7"></path>
      </svg>
    `,
    pdf: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 3h7l4 4v14H7z"></path>
        <path d="M14 3v5h5"></path>
        <path d="M8.5 16h7"></path>
      </svg>
    `,
    text: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5 6h14"></path>
        <path d="M12 6v12"></path>
        <path d="M9 18h6"></path>
      </svg>
    `,
    report: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5 20V4"></path>
        <path d="M5 20h15"></path>
        <path d="M9 16v-5"></path>
        <path d="M13 16V8"></path>
        <path d="M17 16v-3"></path>
      </svg>
    `,
    memory: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="6" y="6" width="12" height="12" rx="3"></rect>
        <path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"></path>
      </svg>
    `,
    math: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 6h10"></path>
        <path d="M7 12h10"></path>
        <path d="M8 18l4-12 4 12"></path>
      </svg>
    `,
    other: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="3"></circle>
        <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"></path>
      </svg>
    `,
    lock: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="5" y="10" width="14" height="10" rx="2"></rect>
        <path d="M8 10V7a4 4 0 0 1 8 0v3"></path>
      </svg>
    `,
  };

  return icons[name] || icons.other;
}

function automationIcon(item) {
  const name = String(item.name || "").toLowerCase();

  if (name.includes("csv") || name.includes("data")) return iconSvg("database");
  if (name.includes("resume")) return iconSvg("file");
  if (name.includes("file")) return iconSvg("folder");
  if (name.includes("study")) return iconSvg("automation");

  return iconSvg("automation");
}

function toolIcon(tool) {
  const category = tool.category || "Other";

  return (
    {
      Files: iconSvg("folder"),
      Data: iconSvg("database"),
      PDF: iconSvg("pdf"),
      Text: iconSvg("text"),
      Reports: iconSvg("report"),
      Memory: iconSvg("memory"),
      Math: iconSvg("math"),
      Other: iconSvg("other"),
    }[category] || iconSvg("other")
  );
}

function setSidebarMode(mode) {
  state.sidebarMode = mode || "automations";
  renderSidebarMode();
}

function renderSidebarMode() {
  const mode = state.sidebarMode || "automations";

  document.querySelectorAll("[data-sidebar-mode]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.sidebarMode === mode);
  });

  document.querySelectorAll("[data-sidebar-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.sidebarPanel !== mode);
  });
}

function setWorkspaceMode(mode) {
  state.workspaceMode = ["build", "code", "split"].includes(mode)
    ? mode
    : "split";

  if (state.workspaceMode === "code" || state.workspaceMode === "split") {
    renderCode({ force: true });
  }

  renderWorkspaceMode();
}

function renderWorkspaceMode() {
  const mode = state.workspaceMode || "split";

  document.querySelectorAll("[data-workspace-mode]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.workspaceMode === mode);
  });

  const grid = document.querySelector(".workspace-grid");
  const visual = document.querySelector('[data-workspace-panel="build"]');
  const code = document.querySelector('[data-workspace-panel="code"]');
  const bridge = document.querySelector(".sync-bridge");

  if (!grid || !visual || !code) return;

  grid.dataset.workspaceLayout = mode;

  visual.classList.remove(
    "workspace-panel-hidden",
    "workspace-panel-secondary",
    "workspace-panel-primary",
  );

  code.classList.remove(
    "workspace-panel-hidden",
    "workspace-panel-secondary",
    "workspace-panel-primary",
  );

  if (mode === "build") {
    visual.classList.add("workspace-panel-primary");
    code.classList.add("workspace-panel-hidden");
    bridge?.classList.add("hidden");
  } else if (mode === "code") {
    visual.classList.add("workspace-panel-hidden");
    code.classList.add("workspace-panel-primary");
    bridge?.classList.add("hidden");
  } else {
    visual.classList.add("workspace-panel-primary");
    code.classList.add("workspace-panel-primary");
    bridge?.classList.remove("hidden");
  }
}

function updateModelMiniLabel() {
  if (exists("modelMiniLabel")) {
    $("modelMiniLabel").textContent = state.automation.model || DEFAULT_AUTOMATION.model;
  }
}

init();
