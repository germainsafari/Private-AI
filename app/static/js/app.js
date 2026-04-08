const STREAK_KEY = "polish_practice_streak_v1";

const LESSONS = [
  {
    topic: "Greetings",
    desc: "hellos, goodbyes, polite phrases",
  },
  {
    topic: "Food & drink",
    desc: "ordering, tastes, in a café",
  },
  {
    topic: "Travel",
    desc: "trains, tickets, directions",
  },
  {
    topic: "Family",
    desc: "relatives and simple descriptions",
  },
  {
    topic: "Numbers & time",
    desc: "counting, days, clock times",
  },
  {
    topic: "Common verbs",
    desc: "everyday actions in present tense",
  },
];

const els = {
  viewDashboard: document.getElementById("viewDashboard"),
  viewLesson: document.getElementById("viewLesson"),
  viewDone: document.getElementById("viewDone"),
  lessonGrid: document.getElementById("lessonGrid"),
  lessonTopicLabel: document.getElementById("lessonTopicLabel"),
  lessonTitle: document.getElementById("lessonTitle"),
  lessonHint: document.getElementById("lessonHint"),
  passage: document.getElementById("passage"),
  wordBank: document.getElementById("wordBank"),
  feedback: document.getElementById("feedback"),
  streakCount: document.getElementById("streakCount"),
  btnBack: document.getElementById("btnBack"),
  btnReset: document.getElementById("btnReset"),
  btnCheck: document.getElementById("btnCheck"),
  btnToDashboard: document.getElementById("btnToDashboard"),
  doneMessage: document.getElementById("doneMessage"),
  modelHint: document.getElementById("modelHint"),
};

let state = {
  topic: "",
  lessonId: null,
  fragments: [],
  wordBank: [],
  /** blank id -> word or null */
  slots: {},
  dragPayload: null,
};

function showView(name) {
  els.viewDashboard.hidden = name !== "dashboard";
  els.viewLesson.hidden = name !== "lesson";
  els.viewDone.hidden = name !== "done";
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function yesterdayISO() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

function loadStreak() {
  try {
    return JSON.parse(localStorage.getItem(STREAK_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveStreak(data) {
  localStorage.setItem(STREAK_KEY, JSON.stringify(data));
}

function refreshStreakDisplay() {
  const { streak = 0 } = loadStreak();
  els.streakCount.textContent = String(streak);
}

/** Call after a correct answer; updates streak by calendar days. */
function bumpStreakOnSuccess() {
  const today = todayISO();
  let { lastSuccessDay, streak = 0 } = loadStreak();
  if (lastSuccessDay === today) {
    refreshStreakDisplay();
    return streak;
  }
  const y = yesterdayISO();
  if (lastSuccessDay === y) streak += 1;
  else streak = 1;
  saveStreak({ lastSuccessDay: today, streak });
  refreshStreakDisplay();
  return streak;
}

function renderDashboard() {
  els.lessonGrid.innerHTML = "";
  LESSONS.forEach((L) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "lesson-tile";
    btn.innerHTML = `
      <div class="tile-kicker">Lesson</div>
      <div class="tile-title">${escapeHtml(L.topic)}</div>
      <p class="tile-desc">${escapeHtml(L.desc)}</p>
    `;
    btn.addEventListener("click", () => startLesson(L.topic));
    els.lessonGrid.appendChild(btn);
  });
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function startLesson(topic) {
  state.topic = topic;
  els.feedback.textContent = "";
  els.feedback.className = "feedback";
  showView("lesson");
  els.lessonTopicLabel.textContent = topic;
  els.lessonTitle.textContent = "Loading…";
  els.lessonHint.textContent = "";
  els.passage.innerHTML = "";
  els.wordBank.innerHTML = "";

  try {
    const res = await fetch("/api/lesson", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic }),
    });
    if (!res.ok) throw new Error("Lesson request failed");
    const data = await res.json();
    state.lessonId = data.lesson_id;
    state.fragments = data.fragments || [];
    state.wordBank = [...(data.word_bank || [])];
    state.slots = {};
    els.lessonTitle.textContent = data.title || topic;
    els.lessonHint.textContent = data.context_en || "";
    renderLesson();
  } catch (e) {
    els.lessonTitle.textContent = "Could not load lesson";
    els.lessonHint.textContent =
      "Is Docker Model Runner running on port 12434? Check the terminal or run offline fallback by restarting the app.";
    console.error(e);
  }
}

function blankIds() {
  const ids = [];
  state.fragments.forEach((f) => {
    if (f.type === "blank") ids.push(f.id);
  });
  return ids.sort((a, b) => a - b);
}

function renderLesson() {
  els.passage.innerHTML = "";
  state.fragments.forEach((f) => {
    if (f.type === "text") {
      const span = document.createElement("span");
      span.className = "txt";
      span.textContent = f.value || "";
      els.passage.appendChild(span);
    } else if (f.type === "blank") {
      const id = f.id;
      const slot = document.createElement("span");
      slot.className = "slot";
      slot.dataset.blankId = String(id);
      slot.addEventListener("dragover", onDragOver);
      slot.addEventListener("dragleave", onDragLeave);
      slot.addEventListener("drop", onDropOnSlot);
      slot.addEventListener("click", () => onSlotTap(id));
      const placed = state.slots[id];
      if (placed) {
        slot.appendChild(makeChip(placed, { inSlot: true, blankId: id }));
        slot.classList.add("slot--good");
      }
      els.passage.appendChild(slot);
    }
  });
  renderBank();
}

function makeChip(text, opts = {}) {
  const chip = document.createElement("span");
  chip.className = "chip" + (opts.inSlot ? " chip--in-slot" : "");
  chip.textContent = text;
  chip.draggable = true;
  chip.dataset.word = text;
  if (opts.blankId != null) chip.dataset.fromBlank = String(opts.blankId);
  chip.addEventListener("dragstart", (ev) => onDragStartChip(ev, text, opts));
  chip.addEventListener("click", (ev) => {
    ev.stopPropagation();
    if (opts.inSlot && opts.blankId != null) {
      returnWordToBank(opts.blankId, text);
    }
  });
  return chip;
}

function renderBank() {
  els.wordBank.innerHTML = "";
  state.wordBank.forEach((w) => {
    const chip = makeChip(w, { inSlot: false });
    els.wordBank.appendChild(chip);
  });
}

function onDragStartChip(ev, word, opts) {
  state.dragPayload = {
    word,
    from: opts.inSlot ? "slot" : "bank",
    blankId: opts.blankId,
  };
  ev.dataTransfer.effectAllowed = "move";
  try {
    ev.dataTransfer.setData("text/plain", word);
  } catch {
    /* ignore */
  }
}

function onDragOver(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.add("slot--hover");
}

function onDragLeave(ev) {
  ev.currentTarget.classList.remove("slot--hover");
}

function onDropOnSlot(ev) {
  ev.preventDefault();
  const slot = ev.currentTarget;
  slot.classList.remove("slot--hover");
  const blankId = Number(slot.dataset.blankId);
  const payload = state.dragPayload;
  if (!payload || !Number.isFinite(blankId)) return;

  if (payload.from === "bank") {
    takeFromBank(payload.word);
    const prev = state.slots[blankId];
    if (prev) pushBank(prev);
    state.slots[blankId] = payload.word;
  } else if (payload.from === "slot" && payload.blankId !== blankId) {
    const a = state.slots[payload.blankId];
    const b = state.slots[blankId];
    state.slots[payload.blankId] = b || null;
    state.slots[blankId] = a || null;
    cleanupSlots();
  }
  state.dragPayload = null;
  renderLesson();
}

function takeFromBank(word) {
  const i = state.wordBank.indexOf(word);
  if (i >= 0) state.wordBank.splice(i, 1);
}

function pushBank(word) {
  state.wordBank.push(word);
}

function returnWordToBank(blankId, word) {
  delete state.slots[blankId];
  state.wordBank.push(word);
  renderLesson();
}

function cleanupSlots() {
  blankIds().forEach((id) => {
    if (state.slots[id] == null) delete state.slots[id];
  });
}

/** Mobile: tap chip then tap slot — use last touched bank word */
let pendingBankWord = null;

function onSlotTap(blankId) {
  if (pendingBankWord) {
    const prev = state.slots[blankId];
    if (prev) pushBank(prev);
    takeFromBank(pendingBankWord);
    state.slots[blankId] = pendingBankWord;
    pendingBankWord = null;
    renderLesson();
    return;
  }
}

document.addEventListener("click", (e) => {
  const t = e.target;
  if (t.classList && t.classList.contains("chip") && !t.dataset.fromBlank) {
    pendingBankWord = t.dataset.word;
    window.setTimeout(() => {
      if (pendingBankWord === t.dataset.word) pendingBankWord = null;
    }, 3500);
  }
});

async function checkLesson() {
  els.feedback.textContent = "";
  els.feedback.className = "feedback";
  const ids = blankIds();
  const words = ids.map((id) => state.slots[id]).filter(Boolean);
  if (words.length !== ids.length) {
    els.feedback.textContent = "Fill every blank before checking.";
    els.feedback.classList.add("feedback--err");
    return;
  }
  try {
    const res = await fetch("/api/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lesson_id: state.lessonId, words }),
    });
    if (!res.ok) throw new Error("check failed");
    const data = await res.json();
    if (data.correct) {
      const streak = bumpStreakOnSuccess();
      els.feedback.textContent = "Correct!";
      els.feedback.classList.add("feedback--ok");
      document.querySelectorAll(".slot").forEach((s) => s.classList.add("slot--good"));
      window.setTimeout(() => {
        els.doneMessage.textContent = `Your streak: ${streak} day${streak === 1 ? "" : "s"}. Pick another lesson when you’re ready.`;
        showView("done");
      }, 650);
    } else {
      els.feedback.textContent = "Not quite — try rearranging the words.";
      els.feedback.classList.add("feedback--err");
      document.querySelectorAll(".slot").forEach((s) => s.classList.add("slot--bad"));
      window.setTimeout(() => {
        document.querySelectorAll(".slot").forEach((s) => s.classList.remove("slot--bad"));
      }, 900);
    }
  } catch (e) {
    console.error(e);
    els.feedback.textContent = "Could not verify. Try again.";
    els.feedback.classList.add("feedback--err");
  }
}

function resetLesson() {
  startLesson(state.topic);
}

els.btnBack.addEventListener("click", () => showView("dashboard"));
els.btnCheck.addEventListener("click", () => checkLesson());
els.btnReset.addEventListener("click", () => resetLesson());
els.btnToDashboard.addEventListener("click", () => showView("dashboard"));

fetch("/api/health")
  .then((r) => r.json())
  .then((d) => {
    if (d.model) els.modelHint.textContent = d.model;
  })
  .catch(() => {});

renderDashboard();
refreshStreakDisplay();
showView("dashboard");
