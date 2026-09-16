import { describe, expect, it } from "vitest";
import { sanitizeProductHtml } from "./sanitizeHtml";

describe("sanitizeProductHtml", () => {
  it("strips script and event handlers", () => {
    const dirty =
      '<p onclick="alert(1)">Hi</p><script>alert(1)</script><img src="https://static-01.daraz.pk/p/a.png" onerror="alert(1)" />';
    const clean = sanitizeProductHtml(dirty);
    expect(clean.toLowerCase()).not.toContain("script");
    expect(clean.toLowerCase()).not.toContain("onclick");
    expect(clean.toLowerCase()).not.toContain("onerror");
    expect(clean).toContain("static-01.daraz.pk");
    expect(clean).toContain("Hi");
  });

  it("blocks javascript: URLs", () => {
    const dirty = '<img src="javascript:alert(1)" /><p>ok</p>';
    const clean = sanitizeProductHtml(dirty);
    expect(clean.toLowerCase()).not.toContain("javascript:");
    expect(clean).toContain("<p>ok</p>");
  });

  it("blocks iframe", () => {
    const dirty = '<iframe src="https://evil.test"></iframe><p>x</p>';
    expect(sanitizeProductHtml(dirty)).not.toContain("iframe");
  });
});
