// The website: five screens over the API, built for a phone.
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
import { $, badge, button, el, empty, fill, icon, note, show, whileWorking } from "./dom.js";
import { drawTimeline } from "./chart.js";
import { guideSeen, openGuide } from "./guide.js";
import * as t from "./text.js";

const SCREENS = ["home", "add", "trend", "ask", "people"];
const POLL_MS = 2500; // how often the add screen asks whether a report has been read

const state = {
  screen: "home",
  summary: null,
  timelines: [],
  questions: null,
  people: null,
  chart: null,
  polling: null,
};

// ---------------------------------------------------------------- errors

/** Run a load, and put the problem on the screen instead of leaving it blank. */
async function load(into, work) {
  try {
    await work();
  } catch (error) {
    if (error.status === 401) return gate(); // her session ran out: ask her to sign in again
    fill(into, note(error.message, true));
  }
}

// ---------------------------------------------------------------- the summary screen

/** One sentence of the summary, with a dot in the colour of what it says. */
const findingLine = (sentence, kind) =>
  el(`div.finding.tone-${t.FINDING_TONE[kind] ?? "flat"}`, el("i.pip"), el("p", sentence));

async function drawHome() {
  await load($("findings"), async () => {
    const summary = (state.summary = await api.summary());
    const sentences = summary.sentences ?? [];

    $("home-date").textContent = summary.latest_sample_date
      ? `Report of ${t.day(summary.latest_sample_date)}`
      : "—";

    if (!sentences.length) {
      fill($("findings"), empty("book", t.T.noReports, t.T.noReportsHow));
    } else {
      // The findings are in the same order as the sentences that were built from them;
      // a sentence past the last finding (there is never more than one) is a closing line.
      fill(
        $("findings"),
        sentences.map((sentence, i) => findingLine(sentence, summary.findings[i]?.kind)),
      );
    }

    // Values from this report that a person still has to confirm.
    const toCheck = summary.to_check ?? 0;
    show($("home-tocheck"), toCheck > 0);
    if (toCheck > 0) {
      fill(
        $("home-tocheck"),
        `${toCheck} value${toCheck === 1 ? "" : "s"} in this report still need checking. `,
        button("Open Check", { className: "btn btn--quiet", onClick: () => go("ask") }),
      );
    }

    const questions = summary.questions ?? [];
    show($("home-questions-card"), questions.length > 0);
    fill($("home-questions"), questions.map((q) => el("li", q)));

    // Reports left out because they name someone else: said plainly, never silently dropped.
    const others = summary.other_people ?? [];
    show($("home-people-card"), others.length > 0);
    if (others.length) {
      fill(
        $("home-people"),
        `${summary.reports_left_out} report${summary.reports_left_out === 1 ? "" : "s"} ` +
          `name${summary.reports_left_out === 1 ? "s" : ""} someone else (${others.join(", ")}), ` +
          "so they are left out of this summary. ",
        button("Open People", { className: "btn btn--quiet", onClick: () => go("people") }),
      );
    }

    show($("speak"), sentences.length > 0);
  });
}

// ---------------------------------------------------------------- reading it aloud

const voice = {
  speaking: false,
  /** The best Marathi voice this phone has, or Hindi, which reads Devanagari acceptably. */
  pick() {
    const voices = speechSynthesis.getVoices();
    return (
      voices.find((v) => v.lang === "mr-IN") ||
      voices.find((v) => v.lang?.startsWith("mr")) ||
      voices.find((v) => v.lang?.startsWith("hi")) ||
      null
    );
  },
};

function setSpeaking(on) {
  voice.speaking = on;
  $("speak").setAttribute("aria-pressed", String(on));
  $("speak-label").textContent = on ? t.T.stop : t.T.listen;
}

function speak() {
  if (voice.speaking) {
    speechSynthesis.cancel();
    return setSpeaking(false);
  }
  // What is spoken is the Marathi the server wrote, unchanged: this is the one place
  // the app talks to her, and it must say exactly what the screen says.
  const lines = [...(state.summary?.sentences ?? [])];
  const questions = state.summary?.questions ?? [];
  if (questions.length) lines.push("डॉक्टरांना विचारा:", ...questions);
  if (!lines.length) return;

  const chosen = voice.pick();
  if (!chosen) {
    // No Marathi or Hindi voice on this phone: say so where she pressed, not silently.
    fill($("home-tocheck"), t.T.noVoice);
    return show($("home-tocheck"), true);
  }

  // One utterance per sentence, so a pause falls where a full stop does.
  speechSynthesis.cancel();
  lines.forEach((line, i) => {
    const said = new SpeechSynthesisUtterance(line);
    said.voice = chosen;
    said.lang = chosen.lang;
    said.rate = 0.88; // slower than default: these are numbers, not chat
    if (i === lines.length - 1) said.onend = () => setSpeaking(false);
    speechSynthesis.speak(said);
  });
  setSpeaking(true);
}

// ---------------------------------------------------------------- adding a report

async function sendFiles(files) {
  if (!files.length) return;
  show($("add-error"), false);
  const card = $("queue-card");
  show(card, true);
  try {
    await api.send(files);
    await drawQueue();
    startPolling();
  } catch (error) {
    fill($("add-error"), error.message);
    show($("add-error"), true);
  }
}

/** One file in the queue, with what is happening to it. */
function queueRow(upload) {
  const reading = upload.status === "reading";
  const done = upload.status === "saved";
  const failed = upload.status === "failed";

  const mark = reading
    ? el("i.spinner")
    : done
      ? el("span.badge.badge--ok", icon("check", 14), t.T.saved)
      : badge(t.T[upload.status] ?? upload.status, failed ? "badge--check" : "");

  return el(
    "div",
    el("div.queue-item", el("span.queue-name", upload.file_name), mark),
    reading && el("div.working"),
    upload.message && el("p.note" + (failed ? ".note--bad" : ""), upload.message),
  );
}

/** Draws the queue and answers whether anything is still being read. */
async function drawQueue() {
  let busy = 0;
  await load($("queue"), async () => {
    const uploads = await api.uploads();
    show($("queue-card"), uploads.length > 0);
    if (!uploads.length) return;

    const working = uploads.filter((u) => u.status === "queued" || u.status === "reading");
    busy = working.length;
    $("queue-h").textContent = busy
      ? `Reading ${busy} report${busy === 1 ? "" : "s"}`
      : "Reports read";
    show($("dot-add"), busy > 0);
    fill($("queue"), uploads.slice(0, 8).map(queueRow));
  });
  return busy;
}

/** While a report is being read, keep asking; a read takes minutes, so this is not chatty. */
function startPolling() {
  stopPolling();
  state.polling = setInterval(async () => {
    if (await drawQueue()) return; // still reading: ask again next time
    // The last report has been read, so everything else on the app is out of date.
    stopPolling();
    await Promise.all([drawHome(), drawTrends(), drawAsk(), drawPeople()]);
  }, POLL_MS);
}

function stopPolling() {
  if (state.polling) clearInterval(state.polling);
  state.polling = null;
}

// ---------------------------------------------------------------- the timelines screen

/** The newest change on a test, as the badge the list shows. */
function lastChange(timeline) {
  const change = timeline.changes?.[timeline.changes.length - 1];
  if (!change) return badge(t.T.firstReport);
  const kind = t.CHANGE[change.kind] ?? t.CHANGE.not_judged;
  return badge(kind.text, kind.badge);
}

/** A test's row in the list: its name, its latest value, and what last changed. */
function testRow(timeline, onOpen) {
  const latest = timeline.points[timeline.points.length - 1];
  const row = el("button.test");
  row.type = "button";
  row.append(
    el(
      "span.test-name",
      timeline.name,
      el(
        "span.test-sub",
        `${t.T.reportsSuffix(timeline.points.length)} · ${t.day(latest.sample_date)}`,
      ),
    ),
    el("span.test-value", `${latest.value_text} ${latest.unit ?? ""}`.trim()),
    el("span.test-arrow", icon("chevron", 18)),
  );
  row.addEventListener("click", () => onOpen(timeline));
  return row;
}

async function drawTrends() {
  await load($("tests"), async () => {
    const timelines = (state.timelines = await api.timelines());
    if (!timelines.length) {
      return fill($("tests"), empty("chart", t.T.noReports, t.T.noReportsHow));
    }
    fill(
      $("tests"),
      timelines.map((timeline) =>
        el("div", testRow(timeline, openTest), el("div.btn-row", lastChange(timeline))),
      ),
    );
  });
}

/** One test, opened: its chart, each judged change, and a way into the printed page. */
function openTest(timeline) {
  const body = el("div");
  const canvas = document.createElement("canvas");
  body.append(el("div.chart-wrap", canvas));

  if (timeline.points.length < 2) body.append(note(t.T.noTrendsYet));

  // Every change, newest first, with the threshold it was judged against.
  for (const change of [...(timeline.changes ?? [])].reverse()) {
    const kind = t.CHANGE[change.kind] ?? t.CHANGE.not_judged;
    const line = el(
      "div.finding.tone-" + kind.tone,
      el("i.pip"),
      el(
        "div",
        el("p", `${t.day(change.before.sample_date)} → ${t.day(change.after.sample_date)}`),
        el(
          "p.small.muted",
          `${change.before.value_text} → ${change.after.value_text}`,
          change.percent !== null ? ` (${t.number(change.percent)}%)` : "",
          !change.same_lab ? " · different labs" : "",
        ),
        change.rcv_percent !== null &&
          el(
            "p.small.muted",
            `Normal movement for this test: up to ${t.number(Math.abs(change.rcv_percent))}%`,
          ),
        change.reason && el("p.small.muted", change.reason),
        el("div", badge(kind.text, kind.badge)),
      ),
    );
    body.append(line);
  }

  openSheet(timeline.name_mr, body);
  // The chart is drawn after the sheet is on screen, so it measures the right width.
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

  const body = el(
    "div",
    el(
      "p.small.muted",
      `${point.value_text} ${point.unit ?? ""} · ${point.lab_name ?? ""} · ` +
        `${t.T.page(point.page)}`,
    ),
    picture,
  );
  openSheet(t.day(point.sample_date), body);
}

// ---------------------------------------------------------------- the questions screen

/** A value the code could not find in the report: she compares it and says. */
function valueToCheck(item, refresh) {
  const decide = async (decision, node) =>
    whileWorking(node, "…", async () => {
      await api.review(item.result_id, decision);
      await Promise.all([refresh(), drawHome()]);
    });

  const yes = button(t.T.yes, { className: "btn btn--yes", icon: "check" });
  const no = button(t.T.no, { className: "btn btn--no" });
  yes.addEventListener("click", () => decide("verified", yes));
  no.addEventListener("click", () => decide("rejected", no));

  return el(
    "div.finding.tone-out",
    el("i.pip"),
    el(
      "div",
      el("p", el("b", item.raw_name)),
      el(
        "p.test-value",
        `${item.value_text} ${item.unit ?? ""}`.trim(),
      ),
      el(
        "p.small.muted",
        `${item.lab_name ?? ""} · ${t.day(item.sample_date)} · ${t.T.page(item.page)}`,
      ),
      item.notes?.length && el("p.small.muted", item.notes.join("; ")),
      el(
        "div.btn-row",
        button(t.T.showPage, {
          className: "btn btn--quiet",
          icon: "book",
          onClick: () =>
            showPrintedPage({
              report_id: item.report_id,
              page: item.page,
              result_id: item.result_id,
              value_text: item.value_text,
              unit: item.unit,
              lab_name: item.lab_name,
              sample_date: item.sample_date,
            }),
        }),
      ),
      el("div.btn-row", yes, no),
    ),
  );
}

/** "Is this the same person?" — two patients with one name the code would not merge. */
function samePerson([first, second], refresh) {
  const yes = button(t.T.same, { className: "btn btn--yes" });
  yes.addEventListener("click", () =>
    whileWorking(yes, "…", async () => {
      await api.mergePatients(first.id, second.id);
      await Promise.all([refresh(), drawHome(), drawPeople()]);
    }),
  );
  return el(
    "div.finding.tone-flat",
    el("i.pip"),
    el(
      "div",
      el("p", `Is “${first.display_name}” the same person as “${second.display_name}”?`),
      el("p.small.muted", "The names match, but something else on the reports does not."),
      el("div.btn-row", yes),
    ),
  );
}

/** The same report sent twice: she may remove the second copy. */
function duplicate([earlier, later], refresh) {
  const remove = button(t.T.removeDuplicate, { className: "btn btn--no" });
  remove.addEventListener("click", () =>
    whileWorking(remove, "…", async () => {
      await api.deleteReport(later);
      await Promise.all([refresh(), drawHome(), drawTrends(), drawPeople()]);
    }),
  );
  return el(
    "div.finding.tone-out",
    el("i.pip"),
    el(
      "div",
      el("p", `Report ${later} looks like the same report as ${earlier}.`),
      el("p.small.muted", "Same person, same lab, same sample date."),
      el("div.btn-row", remove),
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
    const waiting =
      questions.results_to_check.length + questions.same_person.length + questions.duplicates.length;
    show($("dot-ask"), waiting > 0);
    fill($("ask-list"), items.length ? items : empty("check", t.T.nothingToCheck));
  });
}

// ---------------------------------------------------------------- the people screen

/** One person, their reports, and which of those reports is whose. */
function personCard(patient) {
  const lines = patient.reports.map((report) =>
    el(
      "div.queue-item",
      el(
        "span.queue-name",
        t.day(report.sample_date ?? report.report_date),
        el("span.test-sub", report.lab_name ?? ""),
      ),
      button(t.T.moveReport, {
        className: "btn btn--quiet",
        onClick: async () => {
          await api.assignReport(report.report_id, null);
          await Promise.all([drawPeople(), drawHome(), drawTrends()]);
        },
      }),
    ),
  );

  return el(
    "div",
    el(
      "div.test",
      el(
        "span.test-name",
        patient.display_name,
        el(
          "span.test-sub",
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
    patient.aliases?.length &&
      el("p.small.muted", `${t.T.otherNames}: ${patient.aliases.join(", ")}`),
    el("hr.rule"),
  );
}

async function drawPeople() {
  await load($("people-list"), async () => {
    const listing = (state.people = await api.patients());
    const cards = listing.patients.map(personCard);

    if (listing.unmatched.length) {
      cards.push(
        el(
          "div",
          el("h2", t.T.unmatched),
          ...listing.unmatched.map((report) =>
            el(
              "div.queue-item",
              el(
                "span.queue-name",
                report.name ?? t.T.noName,
                el(
                  "span.test-sub",
                  `${t.day(report.sample_date ?? report.report_date)} · ${report.lab_name ?? ""}`,
                ),
              ),
              badge(t.T.needs_check, "badge--check"),
            ),
          ),
        ),
      );
    }

    fill(
      $("people-list"),
      cards.length ? cards : empty("people", t.T.noReports, t.T.noReportsHow),
    );
  });
}

// ---------------------------------------------------------------- the page sheet

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

// ---------------------------------------------------------------- moving between screens

function go(name) {
  state.screen = name;
  for (const screen of SCREENS) show($(`screen-${screen}`), screen === name);
  for (const tab of document.querySelectorAll(".tab")) {
    tab.setAttribute("aria-selected", String(tab.dataset.screen === name));
  }
  window.scrollTo({ top: 0, behavior: "smooth" });

  // Each screen reloads as she arrives: a reading that finished meanwhile shows at once.
  ({ home: drawHome, add: drawQueue, trend: drawTrends, ask: drawAsk, people: drawPeople })[name]?.();
}

// ---------------------------------------------------------------- signing in

/** The sign-in screen: making the first account, or signing in to one that exists. */
async function gate() {
  const who = await api.me().catch(() => ({ account: null, anyone: true }));
  if (who.account) return start(who);

  const first = !who.anyone;
  show($("app"), false);
  show($("gate"), true);
  $("gate-title").textContent = first ? "Let's set this up" : "Welcome back";
  $("gate-lede").textContent = first
    ? "Make an account on this laptop. Choose any name and password you like."
    : "Enter your name and password.";
  $("gate-submit").textContent = first ? "Create the account" : "Sign in";
  $("gate-password").autocomplete = first ? "new-password" : "current-password";

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

/** Everything that happens once she is in. */
function start(who) {
  show($("gate"), false);
  show($("app"), true);
  $("who").textContent = who.account?.name ?? "—";

  go("home");
  drawAsk();
  // A report left half-read when she closed the app is still being read now.
  drawQueue().then((busy) => busy && startPolling());

  if (!guideSeen()) openGuide();
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

  $("speak").addEventListener("click", speak);
  $("sheet-close").addEventListener("click", closeSheet);
  $("open-guide").addEventListener("click", () => openGuide());
  $("replay-guide").addEventListener("click", () => openGuide());

  $("sign-out").addEventListener("click", async () => {
    speechSynthesis.cancel();
    stopPolling();
    await api.signOut().catch(() => {});
    location.reload();
  });

  // Escape closes whatever is over the app.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!$("sheet").hidden) closeSheet();
  });

  // A report shared from the phone lands back here with ?shared=N.
  const shared = Number(new URLSearchParams(location.search).get("shared"));
  if (shared > 0) {
    history.replaceState(null, "", location.pathname);
    go("add");
    startPolling();
  }

  // She left the app and came back: show her what changed while it was away.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !$("app").hidden) go(state.screen);
  });
}

wire();
gate();
