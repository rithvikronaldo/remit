// Thin API client. All calls go through the Vite /api proxy → FastAPI.
const base = "/api";

async function get(path) {
  const r = await fetch(base + path);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}
async function post(path, body) {
  const r = await fetch(base + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const api = {
  remittances: () => get("/remittances"),
  pipeline: (trn) => get(`/remittances/${trn}/pipeline`),
  decisions: (trn) => get(`/remittances/${trn}/decisions`),
  reconciliation: (trn) => get(`/remittances/${trn}/reconciliation`),
  exceptions: (status = "open") => get(`/exceptions?status=${status}`),
  resolve: (id, decision, action) =>
    post(`/exceptions/${id}/resolve`, { decision, action }),
  runEval: () => post("/eval/run"),
};
