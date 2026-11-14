// Polls BASE_URL/api/health until it returns 200 (or times out after 120 s).
const base = process.env.BASE_URL ?? 'http://localhost:8000';
const deadline = Date.now() + 120_000;
for (;;) {
  try {
    const r = await fetch(`${base}/api/health`);
    if (r.ok) { console.log(`app is healthy at ${base}`); process.exit(0); }
  } catch { /* not up yet */ }
  if (Date.now() > deadline) { console.error(`timed out waiting for ${base}/api/health`); process.exit(1); }
  await new Promise((res) => setTimeout(res, 1000));
}
