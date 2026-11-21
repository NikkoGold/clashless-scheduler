// Every call the page makes to the local server. Errors come back as {"error": "..."}; they're thrown as ApiError.

export class ApiError extends Error {}

async function request(method, path, body, signal) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? {} : {"Content-Type": "application/json"},
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("Can't reach the scheduler. Is python -m scheduler web still running?");
  }
  let data = null;
  try { data = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) throw new ApiError(data && data.error ? data.error : `The server answered ${response.status}.`);
  return data;
}

export const api = {
  sample: () => request("GET", "/api/sample"),
  catalog: (catalog) => request("POST", "/api/catalog", {catalog}),
  setupStatus: () => request("GET", "/api/setup"),
  setup: (choice) => request("POST", "/api/setup", choice),
  parse: (catalog, text, offline, signal) => request("POST", "/api/parse", {catalog, text, offline}, signal),
  rules: (constraints) => request("POST", "/api/rules", {constraints}),
  solve: (body, signal) => request("POST", "/api/solve", body, signal),
};
