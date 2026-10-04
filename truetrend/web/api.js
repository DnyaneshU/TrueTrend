// Every request the page makes. Nothing else in the app knows a URL or a status code.
//
// The server is this laptop, so a failed request means the laptop is asleep or the
// cable is out — not that something is wrong with the reports. The message says so.

const OFFLINE = "Can't reach the laptop. Check that it is awake and TrueTrend is running.";

/** A request that failed in a way the page should show, in one sentence. */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { credentials: "same-origin", ...options });
  } catch {
    throw new ApiError(OFFLINE, 0);
  }
  if (response.status === 204) return null;

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    // FastAPI puts a sentence in `detail`; a validation error puts a list there instead.
    const detail = body?.detail;
    throw new ApiError(typeof detail === "string" ? detail : OFFLINE, response.status);
  }
  return body;
}

const json = (path, method, body) =>
  request(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

// ---------------------------------------------------------------- who is signed in

export const me = () => request("/api/me");

export const signUp = (name, password) => json("/api/sign-up", "POST", { name, password });

export function signIn(name, password) {
  // The OAuth 2.0 password grant wants a form, not JSON.
  const form = new URLSearchParams({ username: name, password });
  return request("/api/sign-in", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form,
  });
}

export const signOut = () => request("/api/sign-out", { method: "POST" });

// ---------------------------------------------------------------- reports

export function send(files) {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  return request("/api/uploads", { method: "POST", body: form });
}

export const uploads = () => request("/api/uploads");
export const patients = () => request("/api/patients");

/** The screens are all about one person; `patient` says which, or null for the latest. */
const forPatient = (path, patient) =>
  request(patient ? `${path}?patient=${patient}` : path);

export const summary = (patient) => forPatient("/api/summary", patient);
export const timelines = (patient) => forPatient("/api/timelines", patient);
export const questions = (patient) => forPatient("/api/questions", patient);

/** Her question, answered only from values the app found in her reports. */
export const ask = (question, patient = null) =>
  json("/api/ask", "POST", { question, patient_id: patient });

/** Whether this computer can read the summary aloud in Marathi itself. */
export const voice = () => request("/api/voice");

/** The summary spoken in Marathi, as a WAV this computer made. */
export const summaryAudio = (patient) =>
  patient ? `/api/summary/audio?patient=${patient}` : "/api/summary/audio";

export const review = (resultId, decision) =>
  json(`/api/results/${resultId}/review`, "POST", { decision });

export const mergePatients = (keep, other) => json("/api/patients/merge", "POST", { keep, other });

export const assignReport = (reportId, patientId) =>
  json(`/api/reports/${reportId}/patient`, "PUT", { patient_id: patientId });

export const deleteReport = (reportId) => request(`/api/reports/${reportId}`, { method: "DELETE" });

/** Where to find the picture of a report page, with one value ringed on it. */
export const pagePicture = (reportId, page, resultId) =>
  `/api/reports/${reportId}/page/${page}` + (resultId ? `?result=${resultId}` : "");
