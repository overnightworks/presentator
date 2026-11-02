// The lobby is a single-page app: FastAPI serves its shell, so nothing renders
// on a server and nothing is prerendered (ADR 0021).
export const ssr = false;
export const prerender = false;
