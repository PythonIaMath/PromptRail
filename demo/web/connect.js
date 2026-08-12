const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".tab-panel");
const drawer = document.querySelector("#drawer");
const scrim = document.querySelector("#scrim");
const drawerTitle = document.querySelector("#drawer-title");
const drawerLogo = document.querySelector("#drawer-logo");
const closeButton = document.querySelector("#drawer-close");
const connectionForm = document.querySelector("#connection-form");
const toast = document.querySelector("#toast");
const uploadZone = document.querySelector("#upload-zone");
const fileInput = document.querySelector("#file-input");
const browseButton = document.querySelector("#browse-button");
let activeSource = null;
let sdkCapabilities = null;
const sourceIds = {
  "Custom API": "custom",
  OpenTelemetry: "opentelemetry",
};

function selectTab(name) {
  tabs.forEach((tab) => {
    const selected = tab.dataset.tab === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });
  panels.forEach((panel) => panel.classList.toggle("active", panel.id === `${name}-panel`));
}

function openDrawer(provider, color = "#111827") {
  activeSource = sourceIds[provider] || provider.toLowerCase();
  drawerTitle.textContent = provider;
  drawerLogo.textContent = provider === "OpenTelemetry" ? "◫" : provider.charAt(0);
  drawerLogo.style.background = color;
  scrim.hidden = false;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  window.setTimeout(() => document.querySelector("#api-key").focus(), 260);
}

function closeDrawer() {
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  scrim.hidden = true;
  document.body.style.overflow = "";
}

function showToast(title, detail) {
  toast.querySelector("strong").textContent = title;
  toast.querySelector("small").textContent = detail;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 4200);
}

async function importFiles(files) {
  const file = files?.[0];
  if (!file) return;
  const validName = /\.(json|jsonl)$/i.test(file.name);
  if (!validName) {
    showToast("Unsupported file", "Choose a JSON or JSONL trace export.");
    return;
  }
  if (file.size > 50 * 1024 * 1024) {
    showToast("File is too large", "Trace imports are limited to 50 MB.");
    return;
  }
  uploadZone.querySelector("h3").textContent = file.name;
  uploadZone.querySelector("p").textContent = `${(file.size / 1024).toFixed(1)} KB · Normalizing with PromptRail SDK…`;
  try {
    const response = await fetch("/api/traces/import", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-PromptRail-Privacy-Mode": "metadata_only",
      },
      body: file,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Trace import failed");
    const summary = payload.import;
    uploadZone.querySelector("p").textContent = `${summary.accepted_events} events · ${summary.run_count} runs · ${summary.llm_call_count} LLM calls`;
    uploadZone.querySelector(".upload-icon").textContent = "✓";
    showToast("Trace data imported", `${summary.source_format} normalized to SDK schema ${summary.schema_version}.`);
  } catch (error) {
    uploadZone.querySelector("p").textContent = "Import failed. Check the trace structure and try again.";
    uploadZone.querySelector(".upload-icon").textContent = "!";
    showToast("Trace import failed", error.message);
  }
}

tabs.forEach((tab) => tab.addEventListener("click", () => selectTab(tab.dataset.tab)));
document.querySelectorAll(".provider-card").forEach((card) => {
  card.addEventListener("click", () => openDrawer(card.dataset.provider, card.dataset.color));
});
document.querySelector("#custom-link").addEventListener("click", () => openDrawer("Custom API", "#222"));
closeButton.addEventListener("click", closeDrawer);
scrim.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && drawer.classList.contains("open")) closeDrawer();
});

connectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = connectionForm.querySelector(".connect-button span");
  button.textContent = "Validating with SDK…";
  try {
    const response = await fetch("/api/trace-sources/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: activeSource,
        credential: document.querySelector("#api-key").value,
        project: document.querySelector("#project-name").value,
        metadata_only: connectionForm.querySelector('input[type="checkbox"]').checked,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Connection validation failed");
    button.textContent = "Validate configuration";
    closeDrawer();
    connectionForm.reset();
    connectionForm.querySelector('input[type="checkbox"]').checked = true;
    showToast(`${drawerTitle.textContent} configured`, `SDK schema ${payload.connection.sdk.schema_version} is ready. Remote sync is pending.`);
  } catch (error) {
    button.textContent = "Validate configuration";
    showToast("Configuration failed", error.message);
  }
});

browseButton.addEventListener("click", (event) => {
  event.stopPropagation();
  fileInput.click();
});
uploadZone.addEventListener("click", () => fileInput.click());
uploadZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") fileInput.click();
});
fileInput.addEventListener("change", () => importFiles(fileInput.files));
["dragenter", "dragover"].forEach((name) => uploadZone.addEventListener(name, (event) => {
  event.preventDefault();
  uploadZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach((name) => uploadZone.addEventListener(name, (event) => {
  event.preventDefault();
  uploadZone.classList.remove("dragging");
}));
uploadZone.addEventListener("drop", (event) => importFiles(event.dataTransfer.files));

async function loadSdkCapabilities() {
  try {
    const response = await fetch("/api/trace-sources", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    sdkCapabilities = await response.json();
    const note = document.querySelector(".secure-note");
    note.textContent = `⌾ SDK schema ${sdkCapabilities.sdk.schema_version} · ${sdkCapabilities.sdk.trace_processor}`;
  } catch (error) {
    document.querySelector(".secure-note").textContent = "SDK connection unavailable";
    showToast("SDK unavailable", error.message);
  }
}

loadSdkCapabilities();
