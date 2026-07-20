type WaitlistKv = {
  put(
    key: string,
    value: string,
    options?: { expirationTtl?: number; metadata?: unknown },
  ): Promise<void>;
};

type Env = {
  WAITLIST?: WaitlistKv;
};

type PagesContext = {
  request: Request;
  env: Env;
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function onRequestPost({
  request,
  env,
}: PagesContext): Promise<Response> {
  const body = (await request.json().catch(() => null)) as {
    email?: string;
    locale?: string;
  } | null;
  const email = body?.email?.trim().toLowerCase() ?? "";

  if (!EMAIL_RE.test(email)) {
    return Response.json({ error: "invalid_email" }, { status: 400 });
  }
  if (!env.WAITLIST) {
    return Response.json({ error: "waitlist_not_configured" }, { status: 503 });
  }

  const locale = ["ko", "en", "vi"].includes(body?.locale ?? "")
    ? body?.locale
    : "ko";
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(email),
  );
  const key = Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");

  await env.WAITLIST.put(
    `waitlist:${key}`,
    JSON.stringify({ email, locale, createdAt: new Date().toISOString() }),
  );

  return Response.json({ ok: true }, { status: 201 });
}

export function onRequest(): Response {
  return new Response("Method Not Allowed", {
    status: 405,
    headers: { Allow: "POST" },
  });
}
