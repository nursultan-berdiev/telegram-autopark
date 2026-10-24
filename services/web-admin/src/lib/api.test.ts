import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

describe("api: слияние заголовков", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 204 })),
    );
  });

  it("POST без тела всё равно шлёт X-Requested-With (нужен для CSRF)", async () => {
    await api.post("/web-logout");
    const init = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0][1];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Requested-With"]).toBe("fleet-admin");
    expect(init.credentials).toBe("include");
  });

  it("POST с телом добавляет Content-Type, не теряя X-Requested-With", async () => {
    await api.post("/x", { a: 1 });
    const init = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0][1];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Requested-With"]).toBe("fleet-admin");
    expect(headers["Content-Type"]).toBe("application/json");
  });
});
