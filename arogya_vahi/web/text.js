// What the page says, and how it writes dates and numbers.
//
// Two languages, on purpose:
//
//   The interface is English -- every label, button, screen name and explanation.
//   It is read by whoever sets the app up and by anyone looking at the repo.
//
//   What the app says about her results stays Marathi, because that is the point of
//   the project: the summary sentences, the questions for the doctor, and the voice.
//   Those are built by the server (arogya_vahi/summary.py) from templates, with every
//   number filled in by code. The page never writes one of those sentences itself.
//
// So: labels here are English, and anything the server sends is passed through as it
// comes. The Devanagari-digit helpers below are for Marathi text the page has to lay
// out itself -- a value printed beside a Marathi sentence, mostly.

const DEVANAGARI = "०१२३४५६७८९";

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

const MONTHS_MR = [
  "जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून",
  "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर",
];

/** "7.1 %" -> "७.१ %", for a number shown inside Marathi text. */
export const devanagari = (text) => String(text).replace(/[0-9]/g, (d) => DEVANAGARI[+d]);

/** A number as printed, without trailing zeros: 7.10 -> "7.1". */
export function number(value) {
  if (value === null || value === undefined) return "—";
  return String(Math.round(value * 1e6) / 1e6);
}

/** "2026-04-15" -> "15 Apr 2026". */
export function day(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${+d} ${MONTHS[+m - 1]} ${y}`;
}

/** "2026-04-15" -> "१५ एप्रिल २०२६", for the voice and for Marathi text. */
export function dayMr(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return devanagari(`${+d} ${MONTHS_MR[+m - 1]} ${y}`);
}

/** "2026-04-15" -> "Apr 26", for a chart axis where the full date will not fit. */
export function shortDay(iso) {
  if (!iso) return "";
  const [y, m] = iso.slice(0, 10).split("-");
  return `${MONTHS[+m - 1]} '${y.slice(2)}`;
}

/** What each kind of change is called, and which colour says it. */
export const CHANGE = {
  real_increase: { text: "Real increase", tone: "up", tag: "up" },
  real_decrease: { text: "Real decrease", tone: "down", tag: "down" },
  within_normal_variation: { text: "Normal variation", tone: "flat", tag: "flat" },
  not_judged: { text: "Not compared", tone: "flat", tag: "" },
};

/** The server sends the sentence; this only picks the colour that matches what it says. */
export const FINDING_TONE = {
  trend_increase: "up",
  real_increase: "up",
  trend_decrease: "down",
  real_decrease: "down",
  above_range: "out",
  below_range: "out",
};

export const T = {
  // a value's state
  verified: "Found in the report",
  needs_check: "Needs checking",
  rejected: "Marked wrong",

  // the upload queue
  queued: "Waiting",
  reading: "Reading…",
  saved: "Done",
  already_saved: "Already saved",
  failed: "Could not be read",

  // buttons
  listen: "Listen in Marathi",
  stop: "Stop",
  yes: "Yes, that is right",
  no: "No, that is wrong",
  showPage: "Show me in the report",
  same: "Yes, one person",
  different: "No, different people",
  removeDuplicate: "Remove the second copy",
  moveReport: "Move to someone else",

  // empty screens
  noReports: "No reports yet.",
  noReportsHow: "Tap Add to send the first one.",
  nothingToCheck: "Nothing is waiting for you.",
  noTrendsYet: "A chart needs two reports of the same test.",
  firstReport: "First report",

  // voice
  noVoice: "This device cannot speak.",
  standInVoice:
    "Read with an Indian English voice: the numbers are right, the Marathi words are not.",
  voiceOnPhone:
    "Windows ships no Marathi voice. On a phone, which has one, it is read properly.",

  // asking a question
  thinking: "Reading your question…",
  fromReports: "From:",
  couldAsk: "You could ask about:",
  noReportsYet: "No reports yet",

  // things said about the data
  reportsSuffix: (n) => `${n} report${n === 1 ? "" : "s"}`,
  page: (n) => `page ${n}`,
  female: "Female",
  male: "Male",
  bornAbout: (year) => `born ~${year}`,
  otherNames: "Other names on the reports",
  unmatched: "Not sure whose these are",
  noName: "No name printed",
};
