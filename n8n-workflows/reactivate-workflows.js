// Works around an n8n bug (as of the version pinned here): a workflow can be
// marked active=true in the DB at boot without its webhook route actually
// being registered at runtime. Toggling active off->on via the live REST API
// (what the UI's activation switch does) forces real registration. Without
// this, /webhook/... 404s after every n8n restart even though the workflow
// shows "active".

const N8N_URL = process.env.N8N_URL || 'http://n8n:5678';
const { N8N_OWNER_EMAIL, N8N_OWNER_PASSWORD } = process.env;

async function waitForHealthy() {
  console.log('Waiting for n8n to be healthy...');
  for (;;) {
    try {
      const res = await fetch(`${N8N_URL}/healthz`);
      if (res.ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 2000));
  }
}

async function login() {
  console.log('Logging in...');
  // /healthz can report OK slightly before every route (e.g. /rest/login) is
  // registered, so retry a few times rather than treating one 404 as fatal.
  // n8n can also drop the connection outright (ECONNRESET) while it finishes starting up, so network
  // errors are retried the same way.
  for (let attempt = 1; attempt <= 15; attempt++) {
    let res;
    try {
      res = await fetch(`${N8N_URL}/rest/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ emailOrLdapLoginId: N8N_OWNER_EMAIL, password: N8N_OWNER_PASSWORD }),
      });
    } catch (err) {
      console.log(`Login attempt ${attempt} failed (${err.cause?.code || err.message}), retrying...`);
      await new Promise((r) => setTimeout(r, 2000));
      continue;
    }
    if (res.ok) {
      const cookie = res.headers.get('set-cookie');
      if (!cookie) throw new Error('No session cookie returned from login');
      return cookie.split(';')[0];
    }
    console.log(`Login attempt ${attempt} failed with ${res.status}, retrying...`);
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error('Login failed after repeated attempts');
}

async function reactivateAll(cookie) {
  const res = await fetch(`${N8N_URL}/rest/workflows?active=true`, {
    headers: { Cookie: cookie },
  });
  if (!res.ok) throw new Error(`Failed to list workflows: ${res.status}`);
  const body = await res.json();
  const workflows = Array.isArray(body.data) ? body.data : body;

  for (const wf of workflows) {
    console.log(`Re-activating workflow ${wf.id} (${wf.name})`);
    for (const active of [false, true]) {
      const patchRes = await fetch(`${N8N_URL}/rest/workflows/${wf.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', Cookie: cookie },
        body: JSON.stringify({ active }),
      });
      if (!patchRes.ok) throw new Error(`Failed to set active=${active} on ${wf.id}: ${patchRes.status}`);
    }
  }
  console.log(`Done. Re-activated ${workflows.length} workflow(s).`);
}

(async () => {
  if (!N8N_OWNER_EMAIL || !N8N_OWNER_PASSWORD) {
    console.error('N8N_OWNER_EMAIL / N8N_OWNER_PASSWORD not set, skipping.');
    process.exit(0);
  }
  await waitForHealthy();
  const cookie = await login();
  await reactivateAll(cookie);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
