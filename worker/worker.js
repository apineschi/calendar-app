/**
 * calendar-app Cloudflare Worker
 *
 * Single-file Worker meant to be pasted directly into the Cloudflare dashboard's
 * "Quick Edit" code editor (Workers & Pages > Create Worker > Edit code). No
 * npm/wrangler install needed. Structure mirrors meal-tracker/worker/worker.js.
 *
 * Required secrets (Worker Settings > Variables > "Encrypt" toggle on):
 *   GITHUB_TOKEN       - fine-grained PAT, scoped to ONLY the calendar-app repo,
 *                        Contents: Read and write permission
 *   APP_SECRET         - a password only you know. The Worker's URL and this
 *                        file's source are visible to anyone who views the
 *                        GitHub Pages source, so without this check anyone
 *                        who finds the URL could write junk into your events.
 *                        The front end sends it back in the X-App-Secret
 *                        header on every request.
 *
 * Plain variables (not secret, but fine to also set as encrypted):
 *   GITHUB_OWNER       - e.g. "apineschi"
 *   GITHUB_REPO        - e.g. "calendar-app"
 *   ALLOWED_ORIGIN     - e.g. "https://apineschi.github.io"
 *
 * Dates aren't scraped here - a new event is stored with start_date: null and
 * gets filled in by the next monthly scan (main.py), or by you triggering
 * "Run workflow" on monthly-check.yml manually from the Actions tab. This
 * keeps the scraping logic in exactly one place (Python) instead of two.
 */

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-App-Secret",
  };
}

function checkSecret(request, env) {
  return request.headers.get("X-App-Secret") === env.APP_SECRET;
}

function jsonResponse(data, status, origin) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders(origin) },
  });
}

class NotFoundError extends Error {}

async function githubApi(env, path, options = {}) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "User-Agent": "calendar-app-worker",
      Accept: "application/vnd.github+json",
      ...(options.headers || {}),
    },
  });
  return res;
}

async function getJsonFile(env, path, fallback) {
  const res = await githubApi(env, path);
  if (res.status === 404) {
    return { data: fallback, sha: null };
  }
  if (!res.ok) {
    throw new Error(`GitHub GET ${path} failed: ${res.status} ${await res.text()}`);
  }
  const body = await res.json();
  const content = atob(body.content.replace(/\n/g, ""));
  return { data: JSON.parse(content), sha: body.sha };
}

async function getFileSha(env, path) {
  const res = await githubApi(env, path);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GitHub GET ${path} failed: ${res.status} ${await res.text()}`);
  return (await res.json()).sha;
}

async function putFile(env, path, contentText, sha, message) {
  const content = btoa(unescape(encodeURIComponent(contentText)));
  const res = await githubApi(env, path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      content,
      sha: sha || undefined,
      committer: { name: "calendar-app-bot", email: "bot@calendar-app.local" },
    }),
  });
  if (!res.ok) {
    const err = new Error(`GitHub PUT ${path} failed: ${res.status} ${await res.text()}`);
    err.status = res.status;
    throw err;
  }
}

// GitHub's Contents API requires the current file's SHA on every write, so a
// plain "read SHA, then write" is a read-modify-write race: if anything else
// touches the file in between (a second rapid tap, the monthly scan workflow
// committing at the same moment), the write is rejected with a 409 because
// the SHA it has is now stale. Retrying from a fresh read fixes this - the
// same technique is used for both docs/events.json (via withEventsUpdate)
// and docs/calendar.ics (via regenerateIcs) below.
async function putWithRetry(env, path, buildContent, message, attempts = 3) {
  for (let i = 0; i < attempts; i++) {
    const sha = await getFileSha(env, path);
    try {
      await putFile(env, path, buildContent(), sha, message);
      return;
    } catch (err) {
      if (err.status !== 409 || i === attempts - 1) throw err;
    }
  }
}

function slugify(text) {
  return (
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-|-$)/g, "") || "event"
  );
}

function uniqueId(events, base) {
  let id = base;
  let n = 2;
  while (events[id]) {
    id = `${base}-${n}`;
    n += 1;
  }
  return id;
}

function normalizeTags(value) {
  if (Array.isArray(value)) return value.map((t) => String(t).trim().toLowerCase()).filter(Boolean);
  if (typeof value === "string") {
    return value
      .split(",")
      .map((t) => t.trim().toLowerCase())
      .filter(Boolean);
  }
  return [];
}

// Small JS port of scanner/ics.py's build_calendar() - kept in sync manually
// if the ICS shape ever changes there. Only used so the Worker's writes
// (add/edit/delete) show up on the calendar immediately rather than waiting
// for the next monthly scan to regenerate it.
function buildIcs(events) {
  const lines = ["BEGIN:VCALENDAR", "PRODID:-//calendar-app//apineschi//", "VERSION:2.0"];
  const stamp = new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .split(".")[0] + "Z";

  const fold = (label, value) => `${label}:${String(value).replace(/\n/g, "\\n")}`;
  const asDate = (iso) => iso.replace(/-/g, "");
  const addDays = (iso, days) => {
    const d = new Date(`${iso}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
  };

  for (const record of Object.values(events)) {
    if (!record.start_date) continue;
    const endExclusive = addDays(record.end_date || record.start_date, 1);
    const descriptionParts = [];
    if (record.is_free === true) descriptionParts.push("Free");
    else if (record.price_text) descriptionParts.push(record.price_text);
    if (record.notes) descriptionParts.push(record.notes);
    if (record.url) descriptionParts.push(record.url);

    lines.push("BEGIN:VEVENT");
    lines.push(fold("UID", record.calendar_uid));
    lines.push(fold("SUMMARY", record.name || "Untitled event"));
    lines.push(`DTSTART;VALUE=DATE:${asDate(record.start_date)}`);
    lines.push(`DTEND;VALUE=DATE:${asDate(endExclusive)}`);
    lines.push(`DTSTAMP:${stamp}`);
    if (record.location) lines.push(fold("LOCATION", record.location));
    if (descriptionParts.length) lines.push(fold("DESCRIPTION", descriptionParts.join("\\n")));
    lines.push("END:VEVENT");
  }

  lines.push("END:VCALENDAR");
  return lines.join("\r\n");
}

async function regenerateIcs(env, events) {
  await putWithRetry(env, "docs/calendar.ics", () => buildIcs(events), "Regenerate calendar.ics");
}

// Shared read-modify-write for docs/events.json, retrying the *whole* cycle
// (including a fresh read) on a 409 rather than just the write - a stale
// read means `mutate` may have been working off outdated data too, not just
// an outdated SHA. `mutate` may throw NotFoundError to abort without any
// write at all (no retry, no commit). `messageFn(events, result)` builds the
// commit message from the post-mutation state, since e.g. an add's chosen ID
// isn't known until `mutate` runs.
async function withEventsUpdate(env, mutate, messageFn, attempts = 3) {
  for (let i = 0; i < attempts; i++) {
    const { data: events, sha } = await getJsonFile(env, "docs/events.json", {});
    const result = mutate(events);
    try {
      await putFile(env, "docs/events.json", JSON.stringify(events, null, 2), sha, messageFn(events, result));
      await regenerateIcs(env, events);
      return { events, result };
    } catch (err) {
      if (err.status !== 409 || i === attempts - 1) throw err;
    }
  }
}

async function handleAddEvent(request, env, origin) {
  const { url, name, tags, notes, is_free } = await request.json();
  if (typeof url !== "string" || !url) {
    return jsonResponse({ error: "Missing url" }, 400, origin);
  }

  const { events, result: id } = await withEventsUpdate(
    env,
    (events) => {
      const newId = uniqueId(events, slugify(name || new URL(url).hostname));
      events[newId] = {
        id: newId,
        name: name || new URL(url).hostname,
        url,
        location: null,
        start_date: null,
        end_date: null,
        is_free: typeof is_free === "boolean" ? is_free : null,
        price_text: null,
        tags: normalizeTags(tags),
        notes: notes || "",
        last_known_year: null,
        last_checked: null,
        alert_sent_for_year: null,
        calendar_uid: `${newId}@calendar-app.apineschi`,
      };
      return newId;
    },
    (events, newId) => `Add event: ${events[newId].name}`
  );

  return jsonResponse({ id, event: events[id] }, 200, origin);
}

async function handleEditEvent(request, env, origin) {
  const body = await request.json();
  const { id } = body;
  if (typeof id !== "string" || !id) {
    return jsonResponse({ error: "Missing id" }, 400, origin);
  }

  const editable = ["name", "location", "start_date", "end_date", "is_free", "price_text", "notes"];

  let events;
  try {
    ({ events } = await withEventsUpdate(
      env,
      (events) => {
        if (!events[id]) throw new NotFoundError();
        for (const field of editable) {
          if (field in body) events[id][field] = body[field];
        }
        if ("tags" in body) events[id].tags = normalizeTags(body.tags);

        // A manually-entered/corrected date should count as a confirmed
        // sighting, same as one the scanner found itself.
        if (body.start_date) {
          const year = Number(String(body.start_date).slice(0, 4));
          if (!events[id].last_known_year || year > events[id].last_known_year) {
            events[id].last_known_year = year;
            events[id].alert_sent_for_year = null;
          }
        }
      },
      (events) => `Edit event: ${events[id].name}`
    ));
  } catch (err) {
    if (err instanceof NotFoundError) return jsonResponse({ error: "Event not found" }, 404, origin);
    throw err;
  }

  return jsonResponse({ event: events[id] }, 200, origin);
}

async function handleDeleteEvent(request, env, origin) {
  const { id } = await request.json();

  try {
    await withEventsUpdate(
      env,
      (events) => {
        if (!events[id]) throw new NotFoundError();
        delete events[id];
      },
      () => `Delete event: ${id}`
    );
  } catch (err) {
    if (err instanceof NotFoundError) return jsonResponse({ error: "Event not found" }, 404, origin);
    throw err;
  }

  return jsonResponse({ ok: true }, 200, origin);
}

const ROUTES = {
  "/add-event": handleAddEvent,
  "/edit-event": handleEditEvent,
  "/delete-event": handleDeleteEvent,
};

export default {
  async fetch(request, env) {
    const origin = env.ALLOWED_ORIGIN || "*";

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders(origin) });
    }

    const url = new URL(request.url);
    const handler = ROUTES[url.pathname];

    if (handler && request.method === "POST") {
      if (!checkSecret(request, env)) {
        return jsonResponse({ error: "Unauthorized" }, 401, origin);
      }
      try {
        return await handler(request, env, origin);
      } catch (err) {
        console.error(err);
        return jsonResponse({ error: err.message }, 500, origin);
      }
    }

    return jsonResponse({ error: "Not found" }, 404, origin);
  },
};
