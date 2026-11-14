import { describe, expect, it } from "vitest";
import { pickTab } from "./tabs";

describe("вкладка из адреса", () => {
  const keys = ["automations", "documents"] as const;
  it("известная берётся, неизвестная и пустая — по умолчанию", () => {
    expect(pickTab("documents", keys, "automations")).toBe("documents");
    expect(pickTab("admin", keys, "automations")).toBe("automations");
    expect(pickTab(null, keys, "automations")).toBe("automations");
  });
});
