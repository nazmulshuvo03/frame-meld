import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";

afterEach(() => vi.unstubAllGlobals());

/** Exercised through a public call rather than by exporting `request`,
 *  so the test asserts the behaviour callers actually get. */
async function callWithFetch(fetchImpl: typeof fetch) {
  vi.stubGlobal("fetch", fetchImpl);
  const { listMelds } = await import("@/lib/api");
  return listMelds();
}

describe("request", () => {
  it("raises ApiError with status 0 when the request cannot be made", async () => {
    await expect(
      callWithFetch(() => Promise.reject(new TypeError("Failed to fetch"))),
    ).rejects.toMatchObject({ status: 0 });
  });

  it("surfaces the server's detail string", async () => {
    const response = new Response(JSON.stringify({ detail: "Meld not found" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
    await expect(callWithFetch(() => Promise.resolve(response))).rejects.toMatchObject({
      status: 404,
      message: "Meld not found",
    });
  });

  it("falls back to the status text when the body is not JSON", async () => {
    const response = new Response("<html>500</html>", { status: 500 });
    const error = await callWithFetch(() => Promise.resolve(response)).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(500);
  });

  it("times out a request that never answers", async () => {
    // Real timers, deliberately: AbortSignal.timeout is not driven by
    // vitest's fake clock. 50 ms proves the signal fires without slowing
    // the suite down.
    const hang: typeof fetch = (_input, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(init.signal!.reason));
      });
    vi.stubGlobal("fetch", hang);
    const { __testing } = await import("@/lib/api");

    const error = await __testing.request("/melds", { timeoutMs: 50 }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).message).toMatch(/too long/);
  });
});
