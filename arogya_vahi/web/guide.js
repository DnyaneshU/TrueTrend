// The guide a first-time reader sees: what the app does, what each button means,
// and what it will never do. Like a game's controls screen, before they need it.
//
// It runs once, remembers that it ran, and can be replayed from the Guide button.
// Every page describes something the app actually does -- a tour that promises a
// feature which does not exist would be worse than no tour at all.

import { $, el, fill, icon, show, tag } from "./dom.js";

const SEEN = "arogya.guide.seen.v1";

/** One page of the guide: a mark, a title, and what the reader needs to know. */
const PAGES = [
  {
    art: "summary",
    step: "1 of 5",
    title: "Every report in one place",
    body: [
      "Reports that arrive on WhatsApp, old ones, new ones from a different lab — they all live in one notebook here.",
      "Each test gets one line across the years, even when the lab changes.",
    ],
  },
  {
    art: "shield",
    step: "2 of 5",
    title: "Every number comes from the report",
    body: [
      "The app looks for each value in the original report. It only states a number it has found there.",
      "A value it cannot find goes to Check, where you confirm it. It is never spoken as fact until you do.",
    ],
  },
  {
    art: "chart",
    step: "3 of 5",
    title: "A real change, or normal variation?",
    body: [
      "Blood values move a little between tests even when nothing has changed. The app works out whether a difference is larger than that normal movement.",
      "So a small difference does not become a worry, and a real one is not missed.",
    ],
    legend: [
      ["up", "Real increase", "larger than the normal movement for this test"],
      ["down", "Real decrease", "the value has genuinely come down"],
      ["flat", "Normal variation", "within what this test moves anyway"],
      ["warn", "Needs checking", "waiting for you to confirm"],
    ],
  },
  {
    art: "people",
    step: "4 of 5",
    title: "The five sections",
    body: ["The five sections along the top, and what each one is for:"],
    keys: [
      ["summary", "Summary", "what changed in the latest report, and what to ask the doctor"],
      ["plus", "Add", "send a new report from the phone"],
      ["chart", "Changes", "one test's chart — tap a point to see it in the original report"],
      ["check-circle", "Check", "values the app could not verify on its own"],
      ["people", "People", "which report belongs to whom"],
    ],
  },
  {
    art: "speak",
    step: "5 of 5",
    title: "The summary is spoken in Marathi",
    body: [
      "Tap Listen and the summary is read aloud in Marathi, so it does not have to be read off a screen.",
      "The app gives no medical advice. It says what changed and how it compares with the lab's own range — everything else is for the doctor.",
    ],
  },
];

/** A row of the key map: the icon as it appears on screen, its name, and what it does. */
const keyRow = ([iconName, name, what]) =>
  el("li", el("span.key", icon(iconName, 22)), el("span", el("b", name), " — ", what));

/** A row of the colour legend: the badge as it really looks, and what it means. */
const legendRow = ([kind, text, what]) =>
  el("li", el("span.key", tag(text, kind)), el("span", what));

export function openGuide(onDone) {
  const tour = $("tour");
  let at = 0;

  function draw() {
    const page = PAGES[at];
    $("tour-icon").firstChild.setAttribute("href", `#i-${page.art}`);
    $("tour-step").textContent = page.step;
    $("tour-h").textContent = page.title;

    fill(
      $("tour-body"),
      page.body.map((line) => el("p.muted.small", line)),
      page.keys && el("ul.legend", page.keys.map(keyRow)),
      page.legend && el("ul.legend", page.legend.map(legendRow)),
    );

    fill($("tour-dots"), PAGES.map((_, i) => el(`i${i === at ? ".on" : ""}`)));
    $("tour-next").textContent = at === PAGES.length - 1 ? "Start" : "Next";
    $("tour-skip").hidden = at === PAGES.length - 1;
  }

  function close() {
    try {
      localStorage.setItem(SEEN, "1");
    } catch {
      // A private window forgets this and shows the guide again: harmless.
    }
    show(tour, false);
    onDone?.();
  }

  $("tour-next").onclick = () => {
    if (at === PAGES.length - 1) return close();
    at += 1;
    draw();
  };
  $("tour-skip").onclick = close;
  draw();
  show(tour, true);
  $("tour-next").focus();
}

/** Whether the guide has been shown on this device before. */
export function guideSeen() {
  try {
    return localStorage.getItem(SEEN) === "1";
  } catch {
    return false;
  }
}
