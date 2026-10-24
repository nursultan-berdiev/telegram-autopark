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

  it("POST без тела НЕ ставит Content-Type", async () => {
    await api.post("/web-logout");
    const init = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0][1];
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    expect(init.body).toBeUndefined();
  });

  it("PATCH с телом: метод PATCH, Content-Type и X-Requested-With", async () => {
    await api.patch("/periodic-tasks/1", { enabled: false });
    const init = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0][1];
    const headers = init.headers as Record<string, string>;
    expect(init.method).toBe("PATCH");
    expect(headers["X-Requested-With"]).toBe("fleet-admin");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ enabled: false }));
  });

  it("DELETE: метод DELETE, без тела и Content-Type, 204 → undefined", async () => {
    const r = await api.del("/fines/1");
    const init = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0][1];
    expect(init.method).toBe("DELETE");
    expect((init.headers as Record<string, string>)["X-Requested-With"]).toBe("fleet-admin");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    expect(init.body).toBeUndefined();
    expect(r).toBeUndefined();
  });
});
