// Marathi text and numbers for the page: the browser's half of arogya_vahi/marathi.py.
//
// The server sends the sentences it built, with every number already in Devanagari
// digits. What is left for the page is its own labels, and the digits in anything it
// lays out itself — a chart axis, a date, a count. It never computes a value.

const DIGITS = "०१२३४५६७८९";

const MONTHS = [
  "जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून",
  "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर",
];

/** "7.1 %" -> "७.१ %". Only the digits change. */
export const digits = (text) => String(text).replace(/[0-9]/g, (d) => DIGITS[+d]);

/** A number as printed, without trailing zeros: 7.10 -> "७.१". */
export function number(value) {
  if (value === null || value === undefined) return "—";
  return digits(String(Math.round(value * 1e6) / 1e6));
}

/** "2026-04-15" -> "१५ एप्रिल २०२६". */
export function day(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return digits(`${+d} ${MONTHS[+m - 1]} ${y}`);
}

/** "2026-04-15" -> "एप्रिल २६", for a chart axis where the full date will not fit. */
export function shortDay(iso) {
  if (!iso) return "";
  const [y, m] = iso.slice(0, 10).split("-");
  return digits(`${MONTHS[+m - 1].slice(0, 3)} ${y.slice(2)}`);
}

/** What each kind of change is called, and which colour says it. */
export const CHANGE = {
  real_increase: { text: "खरी वाढ", tone: "up", badge: "badge--up" },
  real_decrease: { text: "खरी घट", tone: "down", badge: "badge--down" },
  within_normal_variation: { text: "नेहमीचा चढ-उतार", tone: "flat", badge: "badge--flat" },
  not_judged: { text: "तुलना करता आली नाही", tone: "flat", badge: "" },
};

/** What each kind of finding is called; the server sends the sentence, this picks the colour. */
export const FINDING_TONE = {
  trend_increase: "up",
  real_increase: "up",
  trend_decrease: "down",
  real_decrease: "down",
  above_range: "out",
  below_range: "out",
};

export const T = {
  // what a value's state is called
  verified: "रिपोर्टमध्ये सापडला",
  needs_check: "तपासायचं आहे",
  rejected: "चुकीचा ठरवला",

  // the add-report screen
  queued: "रांगेत",
  reading: "वाचत आहे…",
  saved: "तयार",
  already_saved: "हा रिपोर्ट आधीच आहे",
  failed: "वाचता आला नाही",

  // buttons
  listen: "ऐका",
  stop: "थांबवा",
  yes: "हो, बरोबर आहे",
  no: "नाही, चुकीचा आहे",
  showPage: "मूळ रिपोर्टमध्ये दाखवा",
  same: "हो, एकच व्यक्ती",
  different: "नाही, वेगळी व्यक्ती",
  removeDuplicate: "दुसरी प्रत काढून टाका",

  // empty screens
  noReports: "अजून एकही रिपोर्ट नाही.",
  noReportsHow: "“जोडा” दाबून पहिला रिपोर्ट द्या.",
  nothingToCheck: "सध्या तपासण्यासारखं काही नाही.",
  noTrendsYet: "आलेख दाखवण्यासाठी एका तपासणीचे दोन रिपोर्ट लागतात.",
  firstReport: "हा पहिलाच रिपोर्ट आहे.",

  // voice
  noVoice: "या फोनवर मराठी आवाज नाही, म्हणून वाचून दाखवता येत नाही.",
};
