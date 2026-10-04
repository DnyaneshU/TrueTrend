// The guide a first-time reader sees: what the app does, what each button means,
// and what it will never do. Like a game's controls screen, before she needs it.
//
// It runs once, remembers that it ran, and can be replayed from the मदत button.
// Every page it shows is about something she can actually do here: a tour that
// describes a feature the app does not have would be worse than no tour.

import { $, button, el, fill, icon, show } from "./dom.js";

const SEEN = "arogya.guide.seen.v1";

/** One page of the guide: a mark, a title, and what she needs to know. */
const PAGES = [
  {
    art: ["mark", 54],
    step: "१ / ५",
    title: "सगळे रिपोर्ट एका ठिकाणी",
    body: [
      "WhatsApp वर आलेले, जुने, नव्या लॅबचे — सगळे रक्त तपासणीचे रिपोर्ट या एका वहीत राहतात.",
      "प्रत्येक तपासणीचा आकडा वर्षानुवर्षं एका रेषेत दिसतो, लॅब बदलली तरी.",
    ],
  },
  {
    art: ["shield", 48],
    step: "२ / ५",
    title: "आकडा रिपोर्टमधूनच येतो",
    body: [
      "हे अ‍ॅप प्रत्येक आकडा मूळ रिपोर्टमध्ये शोधून बघतं. सापडला तरच तो सांगतं.",
      "न सापडलेला आकडा “तपासा” मध्ये जातो आणि तुमची खात्री विचारतो. तो कधीही खरा म्हणून सांगितला जात नाही.",
    ],
  },
  {
    art: ["chart", 48],
    step: "३ / ५",
    title: "खरा बदल, की नेहमीचा चढ-उतार?",
    body: [
      "दोन रिपोर्टमधला फरक नेहमीच्या चढ-उतारापेक्षा मोठा आहे का, हे अ‍ॅप आकडेमोड करून सांगतं.",
      "म्हणून छोट्या फरकाची उगीच काळजी करावी लागत नाही.",
    ],
    legend: [
      ["badge--up", "खरी वाढ", "फरक नेहमीच्या चढ-उतारापेक्षा मोठा आहे"],
      ["badge--down", "खरी घट", "आकडा खरोखर कमी झाला आहे"],
      ["badge--flat", "नेहमीचा चढ-उतार", "काळजीचं कारण नाही"],
      ["badge--check", "तपासायचं आहे", "तुमची खात्री हवी"],
    ],
  },
  {
    art: ["home", 48],
    step: "४ / ५",
    title: "खालच्या पाच कळा",
    body: ["खाली दिसणाऱ्या पाच कळा — प्रत्येकीचं काम हे:"],
    keys: [
      ["home", "सारांश", "या रिपोर्टमध्ये काय बदललं, आणि डॉक्टरांना काय विचारायचं"],
      ["plus", "जोडा", "नवीन रिपोर्टची फाइल द्या"],
      ["chart", "बदल", "एका तपासणीचा आलेख — आकडा दाबल्यावर मूळ रिपोर्ट दिसतो"],
      ["ask", "तपासा", "अ‍ॅपला खात्री नसलेले आकडे तुम्ही बघा"],
      ["people", "माणसं", "कोणता रिपोर्ट कोणाचा"],
    ],
  },
  {
    art: ["speak", 48],
    step: "५ / ५",
    title: "सारांश ऐकता येतो",
    body: [
      "“ऐका” दाबलं की सारांश मराठीत वाचून दाखवला जातो — वाचायची गरज नाही.",
      "हे अ‍ॅप औषध किंवा उपचार सांगत नाही. फक्त काय बदललं ते सांगतं; बाकीचं डॉक्टरांना विचारा.",
    ],
  },
];

/** A row of the key-map: the icon she will see, its name, and what it does. */
const keyRow = ([iconName, name, what]) =>
  el("li", el("span.key", icon(iconName, 22)), el("span", el("b", name), " — ", what));

/** A row of the colour legend: the badge as it really looks, and what it means. */
const legendRow = ([kind, text, what]) =>
  el("li", el("span.key", el(`span.badge.${kind}`, text)), el("span", what));

export function openGuide(onDone) {
  const tour = $("tour");
  let at = 0;

  function draw() {
    const page = PAGES[at];
    const [artIcon, artSize] = page.art;
    fill($("tour-art"), icon(artIcon, artSize));
    $("tour-step").textContent = page.step;
    $("tour-h").textContent = page.title;

    fill(
      $("tour-body"),
      page.body.map((line) => el("p.muted.small", line)),
      page.keys && el("ul.legend", page.keys.map(keyRow)),
      page.legend && el("ul.legend", page.legend.map(legendRow)),
    );

    fill($("tour-dots"), PAGES.map((_, i) => el(`i${i === at ? ".on" : ""}`)));
    $("tour-next").textContent = at === PAGES.length - 1 ? "सुरू करा" : "पुढे";
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

  $("tour-next").onclick = () => (at === PAGES.length - 1 ? close() : (at += 1, draw()));
  $("tour-skip").onclick = close;
  draw();
  show(tour, true);
  $("tour-next").focus();
}

/** Whether she has been shown the guide before. */
export function guideSeen() {
  try {
    return localStorage.getItem(SEEN) === "1";
  } catch {
    return false;
  }
}
