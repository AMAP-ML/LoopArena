const state = {
  setting: "type3",
  results: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const formatPercent = (value) => `${value.toFixed(2)}%`;
const formatCost = (value) => `$${value.toFixed(2)}`;

function rankRows(rows) {
  let visibleRank = 0;
  let lastScore = null;
  let lastRank = 0;
  return rows.map((row) => {
    if (row.role === "reference") return { ...row, rank: "—" };
    visibleRank += 1;
    if (row.score === lastScore) {
      return { ...row, rank: lastRank };
    }
    lastScore = row.score;
    lastRank = visibleRank;
    return { ...row, rank: visibleRank };
  });
}

function renderLeaderboard() {
  const setting = state.results.settings[state.setting];
  const scoreHeading = $("[data-score-heading]");
  const costHeading = $("[data-cost-heading]");
  const body = $("[data-leaderboard-body]");
  const note = $("[data-results-note]");
  const caption = $("[data-table-caption]");
  const status = $("[data-leaderboard-status]");
  const hasReferences = setting.rows.some((row) => row.role === "reference");

  scoreHeading.textContent = `${setting.metric} ↑`;
  costHeading.textContent = `${setting.cost_label} ↓`;
  caption.textContent = `${setting.label} leaderboard`;

  const controllers = setting.rows
    .filter((row) => row.role === "controller")
    .sort((left, right) => right.score - left.score);
  const references = setting.rows.filter((row) => row.role === "reference");
  const rows = rankRows(hasReferences ? [...controllers, ...references] : controllers);

  body.innerHTML = rows
    .map((row, index) => {
      const rankClass = row.rank === 1 ? "best-row" : row.rank === 2 ? "runner-up-row" : "";
      return `<tr class="${row.role === "reference" ? "reference-row" : rankClass}" style="--row-index: ${index}">
        <td>${row.rank}</td>
        <td class="method-cell">${row.method}</td>
        <td class="score-cell"><strong>${formatPercent(row.score)}</strong></td>
        <td>${formatCost(row.cost_usd)}</td>
      </tr>`;
    })
    .join("");

  note.textContent = hasReferences
    ? "Higher scores and lower costs are better. Reference policies are not ranked."
    : "Higher scores and lower costs are better.";
  status.textContent = `${setting.label} results loaded; ${rows.length} rows shown.`;
}

async function loadResults() {
  try {
    const response = await fetch("data/results.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.results = await response.json();
    renderLeaderboard();
  } catch (error) {
    $("[data-leaderboard-body]").innerHTML =
      '<tr><td colspan="4">The result table could not be loaded. Open <a href="data/results.json">the JSON data</a> directly.</td></tr>';
    $("[data-results-note]").textContent = "Validated result data are temporarily unavailable.";
    console.error("Failed to load LoopArena results", error);
  }
}

function wireNavigation() {
  const header = $("[data-header]");
  const toggle = $(".nav-toggle");
  const nav = $("#site-nav");
  const onScroll = () => header.classList.toggle("is-scrolled", window.scrollY > 24);
  const closeNavigation = (restoreFocus = false) => {
    toggle.setAttribute("aria-expanded", "false");
    nav.classList.remove("is-open");
    if (restoreFocus) toggle.focus();
  };
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });

  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    nav.classList.toggle("is-open", open);
  });
  $$("a", nav).forEach((link) =>
    link.addEventListener("click", () => closeNavigation()),
  );
  document.addEventListener("click", (event) => {
    if (toggle.getAttribute("aria-expanded") === "true" && !header.contains(event.target)) {
      closeNavigation();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
      closeNavigation(true);
    }
  });
}

function wireLeaderboard() {
  $$("[data-setting]").forEach((button) => {
    button.addEventListener("click", () => {
      state.setting = button.dataset.setting;
      $$("[data-setting]").forEach((candidate) =>
        candidate.setAttribute("aria-pressed", String(candidate === button)),
      );
      if (state.results) renderLeaderboard();
    });
  });
}

function wireCopyButton() {
  const button = $("[data-copy-quickstart]");
  button.addEventListener("click", async () => {
    const code = $(".terminal-card code").innerText;
    try {
      await navigator.clipboard.writeText(code.replace(/^\$ /gm, ""));
      const oldText = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(() => {
        button.textContent = oldText;
      }, 1600);
    } catch {
      button.textContent = "Select the commands above to copy";
    }
  });
}

function wireMotion() {
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (prefersReducedMotion || !("IntersectionObserver" in window)) return;

  const groups = [
    $$(".section-results .results-heading, .section-results .leaderboard-panel"),
    $$(".section-problem .split-heading, .section-problem .failure-card"),
    $$(".section-harness .center-heading, .section-harness .harness-frame"),
    $$(".section-benchmark .split-heading, .section-benchmark .ladder-card"),
    $$(".section-quickstart .quickstart-copy, .section-quickstart .terminal-card"),
    $$(".team-section .team-callout"),
  ];
  const items = [...new Set(groups.flat())];

  groups.forEach((group) => {
    group.forEach((item, index) => {
      item.classList.add("reveal-item");
      item.style.setProperty("--reveal-delay", `${Math.min(index * 70, 210)}ms`);
    });
  });

  const revealLine = window.innerHeight * 0.9;
  items.forEach((item) => {
    const bounds = item.getBoundingClientRect();
    if (bounds.top < revealLine && bounds.bottom > 0) item.classList.add("is-visible");
  });
  document.documentElement.classList.add("motion-ready");

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    },
    { rootMargin: "0px 0px -8% 0px", threshold: 0.12 },
  );
  items.filter((item) => !item.classList.contains("is-visible")).forEach((item) => observer.observe(item));
}

wireNavigation();
wireLeaderboard();
wireCopyButton();
wireMotion();
loadResults();
