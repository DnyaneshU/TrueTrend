// Building and showing parts of the page. No app logic lives here.
//
// Everything is made with createElement and textContent, never by pasting HTML
// together: a lab's name, a patient's name and a printed value all come from a PDF
// someone else made, and none of them is ever treated as markup.

export const $ = (id) => document.getElementById(id);

/**
 * el("p.note", "text") or el("div", child, child). A class after the dot, as in CSS.
 * A plain object among the children sets attributes: el("div", { style: "..." }, "x").
 */
export function el(spec, ...children) {
  const [tag, ...classes] = spec.split(".");
  const node = document.createElement(tag || "div");
  if (classes.length) node.className = classes.join(" ");

  for (const child of children.flat()) {
    // `cond && el(...)` is how a part is left out, and an empty array's length makes
    // that 0, not false. Nothing here ever means to print a bare 0, so both are skipped.
    if (child === null || child === undefined || child === false || child === 0) continue;
    if (child instanceof Node) {
      node.append(child);
    } else if (typeof child === "object") {
      for (const [name, value] of Object.entries(child)) node.setAttribute(name, value);
    } else {
      node.append(document.createTextNode(String(child)));
    }
  }
  return node;
}

/** One of the icons defined once at the top of index.html. */
export function icon(name, size = 18) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export function button(label, { className = "btn", onClick, icon: iconName } = {}) {
  const node = el(`button.${className.split(" ").join(".")}`);
  node.type = "button";
  if (iconName) node.append(icon(iconName, 16));
  node.append(label);
  if (onClick) node.addEventListener("click", onClick);
  return node;
}

/** A word for what something is: "Real increase", "Needs checking". */
export const tag = (text, kind = "") => el(kind ? `span.tag.tag--${kind}` : "span.tag", text);

/** Replace everything inside a container. */
export function fill(node, ...children) {
  node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
}

/** What a screen shows when there is nothing on it yet: never a blank panel. */
export const empty = (iconName, line, hint) =>
  el("div.empty", icon(iconName, 30), el("p", line), hint && el("p.small", hint));

/** One thing worth saying, in the colour of the news: "", "warn" or "bad". */
export const note = (content, kind = "") =>
  el(kind ? `p.note.note--${kind}` : "p.note", ...[content].flat());

export function show(node, visible = true) {
  node.hidden = !visible;
}

/** A button that stays disabled, saying what it is doing, until the work finishes. */
export async function whileWorking(node, label, work) {
  const was = node.textContent;
  node.disabled = true;
  node.textContent = label;
  try {
    return await work();
  } finally {
    node.disabled = false;
    node.textContent = was;
  }
}
