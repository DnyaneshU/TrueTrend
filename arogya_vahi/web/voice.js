// Reading the summary aloud in Marathi, with a voice already on this computer.
//
// Two things make this harder than it looks:
//
// A browser fills in its voice list asynchronously. `getVoices()` right after the page
// loads usually returns nothing at all, and a button wired straight to it does nothing
// when pressed. So the list is awaited once, through the `voiceschanged` event, with a
// short timeout for the browsers that never fire it.
//
// A voice that speaks Marathi has to be installed in the operating system; a browser
// cannot supply one. When there is none, the honest thing is to say so and say how to
// get one, rather than read Devanagari with an English voice, which produces nonsense.
// Online voices are deliberately not used: the sentence holds her results, and speaking
// it through a cloud service would send them off this computer.

const WAIT_MS = 1200; // some browsers never fire voiceschanged; don't hang on them
const RATE = 0.88; // slower than default: this is numbers, not chat

/** Best first. Each step down is a worse reading, and `exact` says which are honest.
 *
 * Marathi is what this is for. Hindi shares the script and the sounds, so it reads
 * Devanagari properly. Indian English is the last resort: it pronounces the numbers
 * and units right, which is most of what a summary says, and gets the Marathi words
 * around them wrong. It is there because Windows ships no Marathi voice at all -- her
 * phone has one, this laptop never will -- and a button that does nothing is worse.
 */
const WANTED = [
  { exact: true, is: (v) => lang(v) === "mr-IN" },
  { exact: true, is: (v) => lang(v).startsWith("mr") },
  { exact: true, is: (v) => lang(v).startsWith("hi") },
  { exact: false, is: (v) => lang(v) === "en-IN" },
];

const lang = (v) => (v.lang || "").replace("_", "-");

let ready = null;

/** Whether this browser can speak at all. A headless or locked-down one cannot. */
const supported = () => typeof speechSynthesis !== "undefined";

/** The voice list, once the browser has filled it in. */
function voices() {
  if (!supported()) return Promise.resolve([]);
  if (!ready) {
    ready = new Promise((resolve) => {
      const now = speechSynthesis.getVoices();
      if (now.length) return resolve(now);
      const done = () => resolve(speechSynthesis.getVoices());
      speechSynthesis.addEventListener("voiceschanged", done, { once: true });
      setTimeout(done, WAIT_MS);
    });
  }
  return ready;
}

/**
 * The best voice on this device for reading the Marathi summary, or null.
 * `exact` is false when it is a stand-in that will mispronounce the words.
 */
export async function bestVoice() {
  const all = await voices();
  for (const wanted of WANTED) {
    // localService only: an online voice would send her results to whoever provides it.
    const found = all.find((v) => wanted.is(v) && v.localService !== false);
    if (found) return { voice: found, exact: wanted.exact };
  }
  return null;
}

/** Whether anything is being spoken now. */
export const speaking = () => supported() && (speechSynthesis.speaking || speechSynthesis.pending);

export function stop() {
  if (supported()) speechSynthesis.cancel();
}

/**
 * Speak `lines` one at a time, so a pause falls where a full stop does.
 * Calls `onEnd` when the last one finishes, or when speaking is cancelled.
 *
 * Returns null when this device can speak nothing at all, and otherwise the voice it
 * used, so the page can say when the reading is only a stand-in.
 */
export async function speak(lines, onEnd) {
  const chosen = await bestVoice();
  if (!chosen) return null;

  speechSynthesis.cancel();
  lines.forEach((line, i) => {
    const said = new SpeechSynthesisUtterance(line);
    said.voice = chosen.voice;
    said.lang = chosen.voice.lang;
    said.rate = RATE;
    if (i === lines.length - 1) {
      said.onend = onEnd;
      said.onerror = onEnd; // a cancelled utterance must not leave the button saying "Stop"
    }
    speechSynthesis.speak(said);
  });
  return chosen;
}
