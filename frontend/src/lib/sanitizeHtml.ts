/**
 * Shared HTML sanitizer for product description rendering.
 * Mirrors backend src/description_enhance.py allowlist rules (no DOMParser).
 */

const ALLOWED_TAGS = new Set([
  "article",
  "div",
  "p",
  "span",
  "ul",
  "ol",
  "li",
  "br",
  "strong",
  "b",
  "em",
  "i",
  "u",
  "img",
]);

const VOID = new Set(["br", "img"]);
const SKIP = new Set([
  "script",
  "style",
  "iframe",
  "object",
  "embed",
  "link",
  "meta",
]);

const IMG_HOST_MARKERS = [
  "daraz.pk",
  "daraz.com",
  "slatic.net",
  "alicdn.com",
  "lazada.",
];

function escapeText(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeAttr(text: string): string {
  return escapeText(text).replace(/"/g, "&quot;");
}

function safeImgSrc(src: string): boolean {
  let raw = (src || "").trim();
  if (!raw) return false;
  const lower = raw.toLowerCase();
  if (
    lower.startsWith("data:") ||
    lower.startsWith("javascript:") ||
    lower.startsWith("vbscript:")
  ) {
    return false;
  }
  if (raw.startsWith("//")) raw = `https:${raw}`;
  try {
    const url = new URL(raw);
    if (url.protocol !== "http:" && url.protocol !== "https:") return false;
    const host = (url.hostname || "").toLowerCase();
    return IMG_HOST_MARKERS.some(
      (m) => host.endsWith(m.replace(/\.$/, "")) || host.includes(m.replace(/\.$/, ""))
    );
  } catch {
    return false;
  }
}

function attrMap(attrChunk: string): Record<string, string> {
  const out: Record<string, string> = {};
  const re = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]*)))?/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(attrChunk))) {
    const key = m[1].toLowerCase();
    out[key] = m[2] ?? m[3] ?? m[4] ?? "";
  }
  return out;
}

/** Sanitize HTML for safe use with dangerouslySetInnerHTML. */
export function sanitizeProductHtml(html: string | null | undefined): string {
  if (!html) return "";
  const out: string[] = [];
  let skipDepth = 0;
  const tagRe = /<\/?([a-zA-Z0-9]+)([^>]*)>|([^<]+)/g;
  let match: RegExpExecArray | null;
  while ((match = tagRe.exec(html))) {
    if (match[3] != null) {
      if (!skipDepth) out.push(escapeText(match[3]));
      continue;
    }
    const name = match[1].toLowerCase();
    const isClose = match[0].startsWith("</");
    const attrsRaw = match[2] || "";

    if (SKIP.has(name)) {
      if (isClose) {
        if (skipDepth) skipDepth -= 1;
      } else if (!attrsRaw.trimEnd().endsWith("/")) {
        skipDepth += 1;
      }
      continue;
    }
    if (skipDepth) continue;

    if (isClose) {
      if (ALLOWED_TAGS.has(name) && !VOID.has(name)) out.push(`</${name}>`);
      continue;
    }

    if (!ALLOWED_TAGS.has(name)) continue;

    if (name === "img") {
      const attrs = attrMap(attrsRaw);
      const src = (attrs.src || "").trim();
      const alt = (attrs.alt || "").trim();
      if (!safeImgSrc(src)) continue;
      out.push(`<img src="${escapeAttr(src)}" alt="${escapeAttr(alt)}" />`);
      continue;
    }

    // Drop all attributes (event handlers, style, etc.)
    out.push(`<${name}>`);
  }
  return out.join("");
}
