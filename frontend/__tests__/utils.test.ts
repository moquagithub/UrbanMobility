import { describe, it, expect } from "vitest";
import { cn } from "@/lib/utils";

describe("cn()", () => {
  it("merges plain class strings", () => {
    expect(cn("px-2", "py-4")).toBe("px-2 py-4");
  });

  it("resolves conflicting Tailwind utilities (last wins)", () => {
    expect(cn("px-2", "px-4")).toBe("px-4");
  });

  it("drops falsy values", () => {
    expect(cn("px-2", false && "hidden", undefined, null, "py-1")).toBe("px-2 py-1");
  });

  it("applies conditional classes from an object", () => {
    expect(cn("base", { "text-destructive": true, "text-success": false })).toBe("base text-destructive");
  });
});
