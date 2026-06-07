// Thin API client. Dev: calls go through the Vite /api proxy → FastAPI.
// Prod (single container): VITE_API_BASE="" so calls hit the same origin that serves the app.
const base = import.meta.env.VITE_API_BASE !== undefined ? import.meta.env.VITE_API_BASE : "/api";

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
async function uploadFile(path, file) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(base + path, { method: "POST", body: fd }); // browser sets multipart boundary
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const api = {
  remittances: () => get("/remittances"),
  claims: () => get("/claims"),
  claimSettlement: (id) => get(`/claims/${id}/settlement`),
  samples: () => get("/samples"),
  loadSample: (id) => post(`/samples/${id}/load`),
  uploadClaims: (file) => uploadFile("/claims", file),
  upload: (file) => uploadFile("/remittances", file),
  process: (trn) => post(`/remittances/${trn}/process`),
  pipeline: (trn) => get(`/remittances/${trn}/pipeline`),
  decisions: (trn) => get(`/remittances/${trn}/decisions`),
  sourceUrl: (trn) => `${base}/remittances/${trn}/source`,   // direct link (dev: /api proxy · prod: same-origin)
  reconciliation: (trn) => get(`/remittances/${trn}/reconciliation`),
  exceptions: (status = "open") => get(`/exceptions?status=${status}`),
  resolve: (id, decision, action) =>
    post(`/exceptions/${id}/resolve`, { decision, action }),
  runEval: () => post("/eval/run"),
};
