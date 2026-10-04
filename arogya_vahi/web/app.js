// The website: one signed-in session with five sections over the API.
//
// The interface is English. What the app says about her results is Marathi -- the
// summary sentences, the questions for the doctor, and the voice -- because that is
// the point of the project. Those come from the server already written, with every
// number filled in by code, and are passed through here untouched.
//
// The one rule that shapes this file: the page never works a number out. Every value
// and every sentence it shows came from the server, which got it from a report it had
// already found the number in. The page lays them out, says which are still waiting
// for a person, and shows the printed page behind any of them on request.

import * as api from "./api.js";
import * as t from "./text.js";
import * as voice from "./voice.js";
import { $, button, el, empty, fill, icon, note, show, tag, whileWorking } from "./dom.js";
import { drawTimeline } from "./chart.js";
import { guideSeen, openGuide } from "./guide.js";

const SECTIONS = ["home", "add", "trend", "ask", "people"];
const POLL_MS = 2500; // how often the add screen asks whether a report has been read

const state = {
  section: "home",
  summary: null,
  questions: null,
  chart: null,
  polling: null,
  saidAboutVoice: false, // the stand-in voice is explained once, not on every press
};

// ---------------------------------------------------------------- loading

/** Run a load, and put the problem on the screen instead of leaving it blank. */
async function load(into, work) {
  try {
    await work();
  } catch (error) {
    if (error.status === 401) return gate(); // her session ran out: ask her to sign in again
    fill(into, note(error.message, "bad"));
  }
}

// ---------------------------------------------------------------- the summary

/** One sentence of the summary, with a bar in the colour of what it says. */
const findingLine = (sentence, kind) =>
  el(`div.finding.tone-${t.FINDING_TONE[kind] ?? "flat"}`, el("p", sentence));

async function drawHome() {
  await load($("findings"), async () => {
    const summary = (state.summary = await api.summary());
    const sentences = summary.sentences ?? [];

    $("home-date").textContent = summary.latest_sample_date
      ? t.day(summary.latest_sample_date)
      : "";

    fill(
      $("findings"),
      sentences.length
        ? sentences.map((sentence, i) => findingLine(sentence, summary.findings[i]?.kind))
        : empty("page", t.T.noReports, t.T.noReportsHow),
    );
    show($("speak"), sentences.length > 0);

    // Anything the summary leaves out is said here rather than silently dropped.
    const notes = [];
    const toCheck = summary.to_check ?? 0;
    if (toCheck > 0) {
      notes.push(
        note(
          [
            `${toCheck} value${toCheck === 1 ? "" : "s"} in this report still need checking. `,
            button("Open Check", { className: "btn btn--sm", onClick: () => go("ask") }),
          ],
          "warn",
        ),
      );
    }
    const others = summary.other_people ?? [];
    if (others.length) {
      const n = summary.reports_left_out;
      notes.push(
        note([
          `${n} report${n === 1 ? "" : "s"} name someone else (${others.join(", ")}), `,
          "so they are left out of this summary. ",
          button("Open People", { className: "btn btn--sm", onClick: () => go("people") }),
        ]),
      );
    }
    fill($("home-notes"), notes);

    const questions = summary.questions ?? [];
    show($("home-questions-panel"), questions.length > 0);
    fill($("home-questions"), questions.map((q) => el("li", q)));
  });
}

// ---------------------------------------------------------------- reading it aloud

function setSpeaking(on) {
  $("speak").setAttribute("aria-pressed", String(on));
  $("speak-label").textContent = on ? t.T.stop : t.T.listen;
  $("speak-icon").firstChild.setAttribute("href", on ? "#i-stop" : "#i-speak");
  $("speak").classList.toggle("btn--primary", on);
}

async function onSpeak() {
  if (voice.speaking()) {
    voice.stop();
    return setSpeaking(false);
  }

  // What is spoken is the Marathi the server wrote, unchanged: this is the one place
  // the app talks to her, and it must say exactly what the screen says.
  const lines = [...(state.summary?.sentences ?? [])];
  const questions = state.summary?.questions ?? [];
  if (questions.length) lines.push("डॉक्टरांना विचारा:", ...questions);
  if (!lines.length) return;

  setSpeaking(true); // the voice list may take a moment; show that the press landed
  const spoke = await voice.speak(lines, () => setSpeaking(false));

  if (!spoke) {
    setSpeaking(false);
    return fill($("home-notes"), note([el("b", t.T.noVoice), " ", t.T.voiceOnPhone], "warn"));
  }
  // Said once, not every time: an English voice reading Marathi is understandable for
  // the numbers but wrong for the words, and she should know that is what she is hearing.
  if (!spoke.exact && !state.saidAboutVoice) {
    state.saidAboutVoice = true;
    fill($("home-notes"), note([el("b", t.T.standInVoice), " ", t.T.voiceOnPhone]));
  }
}

// ---------------------------------------------------------------- adding a report

async function sendFiles(files) {
  if (!files.length) return;
  show($("add-error"), false);
  try {
    await api.send(files);
    await drawQueue();
    startPolling();
  } catch (error) {
    $("add-error").textContent = error.message;
    show($("add-error"), true);
  }
}

/** One file in the queue, and what is happening to it. */
function queueRow(upload) {
  const reading = upload.status === "reading";
  const failed = upload.status === "failed";
  const mark = reading
    ? el("span.spinner")
    : tag(t.T[upload.status] ?? upload.status, upload.status === "saved" ? "ok" : failed ? "warn" : "");

  return el(
    "div",
    el("div.row", el("span.row-main", el("span.row-sub", upload.file_name)), el("span.row-end", mark)),
    reading && el("div.bar"),
    upload.message && note(upload.message, failed ? "bad" : ""),
  );
}

/** Draws the queue and answers how many files are still being read. */
async function drawQueue() {
  let busy = 0;
  await load($("queue"), async () => {
    const uploads = await api.uploads();
    show($("queue-panel"), uploads.length > 0);
    if (!uploads.length) return;

    busy = uploads.filter((u) => u.status === "queued" || u.status === "reading").length;
    $("queue-h").textContent = busy ? `Reading ${busy} report${busy === 1 ? "" : "s"}` : "Reports read";
    count($("count-add"), busy);
    fill($("queue"), uploads.slice(0, 8).map(queueRow));
  });
  return busy;
}

/** While a report is being read, keep asking; a read takes minutes, so this is not chatty. */
function startPolling() {
  stopPolling();
  state.polling = setInterval(async () => {
    if (await drawQueue()) return; // still reading: ask again next time
    stopPolling(); // the last one is done, so everything else is out of date
    await Promise.all([drawHome(), drawTrends(), drawAsk(), drawPeople()]);
  }, POLL_MS);
}

function stopPolling() {
  if (state.polling) clearInterval(state.polling);
  state.polling = null;
}

// ---------------------------------------------------------------- the changes screen

/** The newest change on a test, as the tag the list shows. */
function lastChange(timeline) {
  const change = timeline.changes?.[timeline.changes.length - 1];
  if (!change) return tag(t.T.firstReport);
  const kind = t.CHANGE[change.kind] ?? t.CHANGE.not_judged;
  return tag(kind.text, kind.tag);
}

/** A test's row: its name, its latest value, and what last changed. */
function testRow(timeline, onOpen) {
  const latest = timeline.points[timeline.points.length - 1];
  const row = el("button.row");
  row.type = "button";
  row.append(
    el(
      "span.row-main",
      el("span.row-name", timeline.name),
      el("span.row-sub", `${t.T.reportsSuffix(timeline.points.length)} · ${t.day(latest.sample_date)}`),
    ),
    el("span.row-value", `${latest.value_text} ${latest.unit ?? ""}`.trim()),
    el("span.row-end", lastChange(timeline), icon("chevron", 16)),
  );
  row.addEventListener("click", () => onOpen(timeline));
  return row;
}

async function drawTrends() {
  await load($("tests"), async () => {
    const timelines = await api.timelines();
    $("trend-count").textContent = timelines.length
      ? `${timelines.length} test${timelines.length === 1 ? "" : "s"}`
      : "";
    fill(
      $("tests"),
      timelines.length
        ? timelines.map((timeline) => testRow(timeline, openTest))
        : empty("chart", t.T.noReports, t.T.noReportsHow),
    );
  });
}

/** One test, opened: its chart, each judged change, and a way into the printed page. */
function openTest(timeline) {
  const canvas = document.createElement("canvas");
  const body = el("div", el("div.chart-wrap", canvas));

  if (timeline.points.length < 2) body.append(note(t.T.noTrendsYet));

  // Every change, newest first, with the threshold it was judged against.
  for (const change of [...(timeline.changes ?? [])].reverse()) {
    const kind = t.CHANGE[change.kind] ?? t.CHANGE.not_judged;
    body.append(
      el(
        `div.finding.tone-${kind.tone}`,
        el(
          "div",
          el(
            "p",
            el("b", `${change.before.value_text} → ${change.after.value_text}`),
            change.percent !== null ? `  (${t.number(change.percent)}%)` : "",
            "  ",
            tag(kind.text, kind.tag),
          ),
          el(
            "p.small.muted",
            `${t.day(change.before.sample_date)} → ${t.day(change.after.sample_date)}`,
            change.same_lab ? "" : " · different labs",
          ),
          change.rcv_percent !== null &&
            el(
              "p.small.muted",
              `Normal movement for this test: up to ${t.number(Math.abs(change.rcv_percent))}%`,
            ),
          change.reason && el("p.small.muted", change.reason),
        ),
      ),
    );
  }

  openSheet(timeline.name, body);
  // The chart is drawn once the sheet is on screen, so it measures the right width.
  requestAnimationFrame(() => {
    state.chart?.destroy();
    state.chart = drawTimeline(canvas, timeline, showPrintedPage);
  });
}

/** The page of the original report where a value is printed, with the value ringed. */
function showPrintedPage(point) {
  const picture = el("img");
  picture.alt = `Report of ${t.day(point.sample_date)}, ${t.T.page(point.page)}`;
  picture.src = api.pagePicture(point.report_id, point.page, point.result_id);

  openSheet(
    t.day(point.sample_date),
    el(
      "div",
      el(
        "p.small.muted",
        `${point.value_text} ${point.unit ?? ""} · ${point.lab_name ?? ""} · ${t.T.page(point.page)}`,
      ),
      picture,
    ),
  );
}

// ---------------------------------------------------------------- the check screen

/** A value the code could not find in the report: she compares it and says. */
function valueToCheck(item, refresh) {
  const decide = async (decision, node) =>
    whileWorking(node, "…", async () => {
      await api.review(item.result_id, decision);
      await Promise.all([refresh(), drawHome()]);
    });

  const yes = button(t.T.yes, { className: "btn btn--sm btn--yes" });
  const no = button(t.T.no, { className: "btn btn--sm btn--no" });
  yes.addEventListener("click", () => decide("verified", yes));
  no.addEventListener("click", () => decide("rejected", no));

  return el(
    "div.finding.tone-out",
    el(
      "div",
      el(
        "p",
        el("b", item.raw_name),
        "  ",
        el("span.row-value", `${item.value_text} ${item.unit ?? ""}`.trim()),
      ),
      el(
        "p.small.muted",
        `${item.lab_name ?? ""} · ${t.day(item.sample_date)} · ${t.T.page(item.page)}`,
      ),
      item.notes?.length && el("p.small.muted", item.notes.join("; ")),
      el(
        "div.btn-row",
        { style: "margin-top: var(--s3)" },
        button(t.T.showPage, {
          className: "btn btn--sm",
          icon: "page",
          onClick: () => showPrintedPage({ ...item, page: item.page }),
        }),
        yes,
        no,
      ),
    ),
  );
}

/** "Is this the same person?" -- two patients with one name the code would not merge. */
function samePerson([first, second], refresh) {
  const yes = button(t.T.same, { className: "btn btn--sm btn--yes" });
  yes.addEventListener("click", () =>
    whileWorking(yes, "…", async () => {
      await api.mergePatients(first.id, second.id);
      await Promise.all([refresh(), drawHome(), drawPeople()]);
    }),
  );
  return el(
    "div.finding.tone-flat",
    el(
      "div",
      el("p", "Is ", el("b", first.display_name), " the same person as ", el("b", second.display_name), "?"),
      el("p.small.muted", "The names match, but something else on the reports does not."),
      el("div.btn-row", { style: "margin-top: var(--s3)" }, yes),
    ),
  );
}

/** The same report sent twice: she may remove the second copy. */
function duplicate([earlier, later], refresh) {
  const remove = button(t.T.removeDuplicate, { className: "btn btn--sm btn--no" });
  remove.addEventListener("click", () =>
    whileWorking(remove, "…", async () => {
      await api.deleteReport(later);
      await Promise.all([refresh(), drawHome(), drawTrends(), drawPeople()]);
    }),
  );
  return el(
    "div.finding.tone-out",
    el(
      "div",
      el("p", `Report ${later} looks like the same report as ${earlier}.`),
      el("p.small.muted", "Same person, same lab, same sample date."),
      el("div.btn-row", { style: "margin-top: var(--s3)" }, remove),
    ),
  );
}

async function drawAsk() {
  await load($("ask-list"), async () => {
    const questions = (state.questions = await api.questions());
    const items = [
      ...questions.results_to_check.map((item) => valueToCheck(item, drawAsk)),
      ...questions.same_person.map((pair) => samePerson(pair, drawAsk)),
      ...questions.duplicates.map((pair) => duplicate(pair, drawAsk)),
    ];
    count($("count-ask"), items.length);
    fill($("ask-list"), items.length ? items : empty("check-circle", t.T.nothingToCheck));
  });
}

// ---------------------------------------------------------------- the people screen

/** One person, and the reports that are theirs. */
function personBlock(patient) {
  const lines = patient.reports.map((report) =>
    el(
      "div.row",
      el(
        "span.row-main",
        el("span.row-sub", `${t.day(report.sample_date ?? report.report_date)} · ${report.lab_name ?? ""}`),
      ),
      el(
        "span.row-end",
        button(t.T.moveReport, {
          className: "btn btn--quiet btn--sm",
          onClick: async () => {
            await api.assignReport(report.report_id, null);
            await Promise.all([drawPeople(), drawHome(), drawTrends()]);
          },
        }),
      ),
    ),
  );

  return el(
    "div",
    el(
      "div.row",
      el(
        "span.row-main",
        el("span.row-name", patient.display_name),
        el(
          "span.row-sub",
          [
            patient.sex === "F" ? t.T.female : patient.sex === "M" ? t.T.male : null,
            patient.birth_year ? t.T.bornAbout(patient.birth_year) : null,
            t.T.reportsSuffix(patient.reports.length),
          ]
            .filter(Boolean)
            .join(" · "),
        ),
      ),
    ),
    ...lines,
    patient.aliases?.length && el("p.small.muted", `${t.T.otherNames}: ${patient.aliases.join(", ")}`),
    el("hr.rule"),
  );
}

async function drawPeople() {
  await load($("people-list"), async () => {
    const listing = await api.patients();
    const blocks = listing.patients.map(personBlock);

    if (listing.unmatched.length) {
      blocks.push(
        el(
          "div",
          el("p.eyebrow", t.T.unmatched),
          ...listing.unmatched.map((report) =>
            el(
              "div.row",
              el(
                "span.row-main",
                el("span.row-name", report.name ?? t.T.noName),
                el(
                  "span.row-sub",
                  `${t.day(report.sample_date ?? report.report_date)} · ${report.lab_name ?? ""}`,
                ),
              ),
              el("span.row-end", tag(t.T.needs_check, "warn")),
            ),
          ),
        ),
      );
    }

    fill(
      $("people-list"),
      blocks.length ? blocks : empty("people", t.T.noReports, t.T.noReportsHow),
    );
  });
}

// ---------------------------------------------------------------- the overlay

function openSheet(title, body) {
  $("sheet-h").textContent = title;
  fill($("sheet-body"), body);
  show($("sheet"), true);
  $("sheet-close").focus();
}

function closeSheet() {
  show($("sheet"), false);
  state.chart?.destroy();
  state.chart = null;
}

// ---------------------------------------------------------------- moving about

/** A number on a tab, hidden when there is nothing waiting. */
function count(node, n) {
  node.textContent = n;
  node.hidden = !n;
}

function go(name) {
  state.section = name;
  for (const section of SECTIONS) show($(`screen-${section}`), section === name);
  for (const tab of document.querySelectorAll(".tab")) {
    tab.setAttribute("aria-selected", String(tab.dataset.screen === name));
  }
  window.scrollTo({ top: 0, behavior: "smooth" });

  // Each section reloads as she arrives, so a report read meanwhile shows at once.
  ({ home: drawHome, add: drawQueue, trend: drawTrends, ask: drawAsk, people: drawPeople })[name]?.();
}

// ---------------------------------------------------------------- signing in

/** The door: making the first account, or signing in to one that exists. */
async function gate() {
  const who = await api.me().catch(() => ({ account: null, anyone: true }));
  if (who.account) return start(who);

  const first = !who.anyone;
  show($("app"), false);
  show($("gate"), true);
  $("gate-title").textContent = first ? "Set up this laptop" : "Welcome back";
  $("gate-lede").textContent = first
    ? "Choose a name and a password. They are only ever stored here."
    : "Enter your name and password.";
  $("gate-submit").textContent = first ? "Create the account" : "Sign in";
  $("gate-password").autocomplete = first ? "new-password" : "current-password";
  $("gate-name").focus();

  $("gate-form").onsubmit = async (event) => {
    event.preventDefault();
    show($("gate-error"), false);
    const name = $("gate-name").value;
    const password = $("gate-password").value;
    try {
      await whileWorking($("gate-submit"), "One moment…", () =>
        first ? api.signUp(name, password) : api.signIn(name, password),
      );
      $("gate-password").value = "";
      start(await api.me());
    } catch (error) {
      $("gate-error").textContent = error.message;
      show($("gate-error"), true);
    }
  };
}

/** Everything that happens once she is in: one live session, the work in front of her. */
async function start(who) {
  show($("gate"), false);
  show($("app"), true);
  $("session-account").textContent = who.account?.name ?? "";
  $("who").textContent = who.account?.name ?? "";

  go("home");
  drawAsk();
  // A report left half-read when the laptop was closed is still being read now.
  drawQueue().then((busy) => busy && startPolling());
  namePatient();

  if (!guideSeen()) openGuide();
}

/** Whose reports the session is showing, named in the bar so it is never a guess.
 *
 * The summary and the timelines are for one patient -- the one most recently tested --
 * and which that is matters when a family shares the laptop. The timelines carry the
 * patient's name on every point, so it comes from the same place the numbers do.
 */
async function namePatient() {
  try {
    const [timeline] = await api.timelines();
    const name = timeline?.points?.[0]?.patient_name;
    $("session-patient").textContent = name || "No reports yet";
  } catch {
    $("session-patient").textContent = "";
  }
}

// ---------------------------------------------------------------- wiring

function wire() {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => go(tab.dataset.screen));
  }

  $("pick").addEventListener("change", (event) => {
    sendFiles([...event.target.files]);
    event.target.value = ""; // the same file may be sent again after a failure
  });

  $("speak").addEventListener("click", onSpeak);
  $("sheet-close").addEventListener("click", closeSheet);
  $("replay-guide").addEventListener("click", () => openGuide());

  $("sign-out").addEventListener("click", async () => {
    voice.stop();
    stopPolling();
    await api.signOut().catch(() => {});
    location.reload();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("sheet").hidden) closeSheet();
  });

  // A report shared from the phone lands back here with ?shared=N.
  if (Number(new URLSearchParams(location.search).get("shared")) > 0) {
    history.replaceState(null, "", location.pathname);
    go("add");
    startPolling();
  }

  // She left the app and came back: show what changed while it was away.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !$("app").hidden) go(state.section);
  });

  // Speaking does not survive the page going away.
  window.addEventListener("pagehide", () => voice.stop());
}

wire();
gate();
