const divider = "\u001ePROMPTRAIL_ANSWER_DIVIDER";
const form = document.querySelector("#prompt-form");
const promptInput = document.querySelector("#prompt");
const sendButton = form.querySelector("button");
const toast = document.querySelector("#toast");
const panes = {
  baseline: document.querySelector("#baseline-log"),
  managed: document.querySelector("#managed-log"),
};
let lastState = null;

function money(value) {
  return `$${Number(value || 0).toFixed(6)}`;
}

function tokens(value) {
  return `${Number(value || 0).toLocaleString()} tok`;
}

function showError(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 5500);
}

function lineClass(line) {
  const prefix = line.trimStart().split(/\s+/, 1)[0].toLowerCase();
  const classes = {
    user: "user", answer: "answer-label", complete: "complete", route: "route",
    control: "control", budget: "budget", usage: "usage", error: "error",
    failover: "failover", run: "run", done: "run", tool: "tool", edit: "edit",
    update: "update",
  };
  return classes[prefix] || "";
}

function renderLog(lane, lines) {
  const pane = panes[lane];
  const pinned = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 60;
  pane.replaceChildren();
  for (const line of lines) {
    const node = document.createElement("div");
    if (line === divider) {
      node.className = "answer-divider";
    } else {
      node.className = `line ${lineClass(line)}`;
      node.textContent = line || " ";
    }
    pane.append(node);
  }
  if (pinned || !lastState) pane.scrollTop = pane.scrollHeight;
}

function renderModels(state) {
  const selector = document.querySelector("#model-selector");
  if (selector.childElementCount === state.models.length) {
    for (const button of selector.querySelectorAll("button")) {
      button.classList.toggle("active", button.dataset.model === state.selected_baseline_model);
      button.disabled = state.phase !== "ready";
    }
    return;
  }
  selector.replaceChildren();
  for (const model of state.models) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.model = model.model;
    button.textContent = model.label;
    button.title = `/model ${model.alias}`;
    button.addEventListener("click", () => submitPrompt(`/model ${model.alias}`));
    selector.append(button);
  }
  renderModels(state);
}

function renderSavings(state) {
  const screen = document.querySelector("#savings-screen");
  if (state.phase !== "savings") {
    screen.hidden = true;
    return;
  }
  screen.hidden = false;
  const percentage = state.savings.percentage;
  const failed = Boolean(state.comparison_error);
  const positive = percentage !== null && percentage >= 0;
  document.querySelector("#savings-heading").textContent = failed ? "COMPARISON STOPPED" : "PROMPTRAIL SAVINGS";
  const percentageNode = document.querySelector("#savings-percentage");
  percentageNode.textContent = failed ? "STOP" : (percentage === null ? "N/A" : `${percentage.toFixed(1)}%`);
  percentageNode.className = positive ? "positive" : "negative";
  const label = document.querySelector("#savings-label");
  label.textContent = failed
    ? "NO SAVINGS RESULT"
    : (positive ? `${percentage.toFixed(1)}% SAVED` : `${Math.abs(percentage).toFixed(1)}% ADDITIONAL COST`);
  label.className = positive ? "" : "negative";
  const baseline = state.totals.baseline;
  const managed = state.totals.managed;
  document.querySelector("#savings-baseline").textContent = `BASELINE  ${baseline.model} | ${money(baseline.cost)} | ${tokens(baseline.tokens)} | ${Number(baseline.cached).toLocaleString()} cached | ${baseline.calls} calls`;
  document.querySelector("#savings-managed").textContent = `PROMPTRAIL  ${managed.model} | ${money(managed.cost)} | ${tokens(managed.tokens)} | ${Number(managed.cached).toLocaleString()} cached | ${managed.calls} calls`;
  const amount = state.savings.amount_usd;
  document.querySelector("#savings-detail").textContent = failed
    ? state.comparison_error
    : `${money(Math.abs(amount))} ${amount >= 0 ? "saved" : "additional cost"} with actual provider usage`;
  document.querySelector("#savings-countdown").textContent = `Conversation resumes in ${Math.max(1, Math.ceil(state.savings.remaining_ms / 1000))}s`;
}

function render(state) {
  const baseline = state.totals.baseline;
  const managed = state.totals.managed;
  document.querySelector("#baseline-cost").textContent = money(baseline.cost);
  document.querySelector("#baseline-tokens").textContent = tokens(baseline.tokens);
  document.querySelector("#managed-cost").textContent = money(managed.cost);
  document.querySelector("#managed-tokens").textContent = tokens(managed.tokens);
  document.querySelector("#saved-cost").textContent = money(baseline.cost - managed.cost);
  document.querySelector("#baseline-model").textContent = baseline.model;
  document.querySelector("#managed-model").textContent = managed.model;
  document.querySelector("#decision").textContent = state.decision || "Waiting for PromptRail decision";
  document.querySelector("#status").textContent = state.status;
  const phase = document.querySelector("#phase");
  phase.textContent = state.phase.toUpperCase();
  phase.className = `phase ${state.phase}`;
  const canSubmit = state.phase === "ready";
  promptInput.disabled = !canSubmit;
  sendButton.disabled = !canSubmit;
  renderModels(state);
  if (!lastState || JSON.stringify(lastState.logs.baseline) !== JSON.stringify(state.logs.baseline)) renderLog("baseline", state.logs.baseline);
  if (!lastState || JSON.stringify(lastState.logs.managed) !== JSON.stringify(state.logs.managed)) renderLog("managed", state.logs.managed);
  renderSavings(state);
  if (canSubmit && lastState?.phase !== "ready") promptInput.focus();
  lastState = state;
}

async function submitPrompt(prompt) {
  const text = prompt.trim();
  if (!text) return;
  sendButton.disabled = true;
  try {
    const response = await fetch("/api/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to start the turn");
    promptInput.value = "";
    promptInput.style.height = "auto";
    render(payload);
  } catch (error) {
    sendButton.disabled = false;
    showError(error.message);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  submitPrompt(promptInput.value);
});
promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});
promptInput.addEventListener("input", () => {
  promptInput.style.height = "auto";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, 150)}px`;
});

async function refresh() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    showError(`Local demo disconnected: ${error.message}`);
  }
}

refresh();
window.setInterval(refresh, 300);
