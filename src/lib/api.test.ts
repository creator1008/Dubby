import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getSession = vi.fn();

vi.mock("@/lib/supabase", () => ({
  getSupabase: () => ({ auth: { getSession } }),
}));

describe("API client", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_API_ORIGIN", "https://api.example.test");
    getSession.mockResolvedValue({
      data: { session: { access_token: "test-access-token" } },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.clearAllMocks();
  });

  it("adds the Supabase bearer token and parses JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ balance_minutes: 12, entries: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/lib/api");

    await expect(api.credits()).resolves.toMatchObject({ balance_minutes: 12 });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/v1/credits",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer test-access-token",
        }),
      }),
    );
  });

  it("surfaces backend details and status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Insufficient credits" }), {
          status: 402,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const { api } = await import("@/lib/api");

    await expect(api.jobs.create("project", "dub")).rejects.toEqual(
      expect.objectContaining({
        message: "Insufficient credits",
        status: 402,
      }),
    );
  });
});
