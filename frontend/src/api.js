// The backend URL is fixed when the app is built. The default is the local backend from `make up`.
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/+$/, "");

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// The backend always answers errors as {"detail": "..."}; for 422 the detail is a list of {msg}.
function readableDetail(body, status) {
  const detail = body?.detail;
  const text = Array.isArray(detail)
    ? detail.map((item) => item.msg.replace(/^Value error, /, "")).join(". ")
    : detail;
  if (typeof text !== "string" || !text) return `The server answered with status ${status}.`;
  return text.charAt(0).toUpperCase() + text.slice(1) + (text.endsWith(".") ? "" : ".");
}

async function request(path, options) {
  let response;
  try {
    response = await fetch(API_BASE + path, options);
  } catch {
    throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, readableDetail(body, response.status));
  return body;
}

export const getHealth = () => request("/health");

export async function sendChat({ sessionId, message, systemPrompt, simulateFailure }) {
  const headers = { "Content-Type": "application/json" };
  if (simulateFailure) headers["X-Simulate-Failure"] = simulateFailure;
  const body = await request("/chat", {
    method: "POST",
    headers,
    // The prompt field is always sent, so an emptied field puts the session back on the default prompt.
    body: JSON.stringify({ session_id: sessionId, message, system_prompt: systemPrompt }),
  });
  // Anything that is not a chat reply (say, another server answering on this URL) becomes a readable error.
  if (typeof body?.reply !== "string" || !body.metadata) {
    throw new ApiError(0, "The server sent an answer this app could not read.");
  }
  return body;
}
