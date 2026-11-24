// Regular Bun: observed workspace workers serve bun, not the nativets queue.
// Source-reviewed fixed public renderer. No credentials, local mounts or AI.
// RT.CMorganPublicRenderer is the Windmill resource type in renderer-resource-type.json.
const APPROVED_RENDER_URL = 'https://morgan.aldous.info/render.svg';
const MAX_SVG_BYTES = 2 * 1024 * 1024;
const TIMEOUT_MS = 5000;

/** Explicit compact is the selected MCP presentation, not a quota assertion. */
export async function main(
  renderer: RT.CMorganPublicRenderer,
  revision: string | null = null,
  layout: 'compact' | 'full' | 'auto' | 'o2' | 'hexagon' | 'focus' = 'compact',
  width: number = 1600,
  height: number = 1000,
  selected: string | null = null,
  theme: 'light' | 'dark' | null = null,
) {
  const started = performance.now();
  const duration = () => Math.round((performance.now() - started) * 1000) / 1000;
  // Resource values remain deployment configuration. The code allowlist prevents
  // even a caller-supplied resource object from turning this tool into a proxy.
  if (!renderer || typeof renderer !== 'object' || Array.isArray(renderer) ||
      Object.keys(renderer).length !== 1 || renderer.renderUrl !== APPROVED_RENDER_URL ||
      (revision !== null && (typeof revision !== 'string' || !/^[0-9a-f]{64}$/.test(revision))) ||
      !['compact', 'full', 'auto', 'o2', 'hexagon', 'focus'].includes(layout) ||
      (theme !== null && !['light', 'dark'].includes(theme)) ||
      !Number.isSafeInteger(width) || width < 320 || width > 4096 ||
      !Number.isSafeInteger(height) || height < 240 || height > 2160 ||
      (selected !== null && (typeof selected !== 'string' || !/^(cloud-[A-F]|foo-(?:[1-9]|[1-5][0-9]|6[0-4])|(?:durable|summary)-(?:Cloud|UIF|ANQR|CIX|OM|CQ)|(?:Cloud|UIF|ANQR|CIX|OM|CQ)-(?:primary|overflow)-[1-6]|overall|railway-[1-6])$/.test(selected)))) {
    return { ok: false, error: 'invalid_render_options', durationMs: duration() };
  }
  const url = new URL(APPROVED_RENDER_URL);
  url.searchParams.set('layout', layout);
  url.searchParams.set('width', String(width));
  url.searchParams.set('height', String(height));
  if (theme !== null) url.searchParams.set('theme', theme);
  if (revision !== null) url.searchParams.set('revision', revision);
  if (selected !== null) url.searchParams.set('selected', selected);
  try {
    const response = await fetch(url.href, {
      method: 'GET', headers: { Accept: 'image/svg+xml' }, redirect: 'error',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!response.ok || (response.url && response.url !== url.href) ||
        !/^image\/svg\+xml(?:\s*;|$)/i.test(response.headers.get('content-type') || '') || !response.body) throw new Error();
    const declaredSize = response.headers.get('content-length');
    if (declaredSize && (!/^\d+$/.test(declaredSize) || Number(declaredSize) > MAX_SVG_BYTES)) throw new Error();
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = []; let size = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_SVG_BYTES) { await reader.cancel(); throw new Error(); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const svg = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    // Only the deterministic renderer's passive SVG vocabulary is accepted.
    // Never return arbitrary HTML, executable SVG, redirects or error bodies.
    const tags = new Set(['svg', 'g', 'path', 'polygon', 'polyline', 'rect', 'circle', 'ellipse', 'line',
      'text', 'tspan', 'title', 'desc', 'style', 'metadata', 'defs', 'clipPath', 'linearGradient', 'stop',
      'filter', 'feColorMatrix', 'feGaussianBlur', 'feMerge', 'feMergeNode', 'feOffset',
      'mask', 'image', 'feFlood', 'feComposite']);
    // Local fragment filter references are required by the reviewed shadow SVG.
    // Approved self-contained graphics use PNG logos and embedded TrueType fonts.
    // Remove only passive base64 data references before the deny-by-default scan.
    // SVG/HTML data images, remote URLs, other attributes and active tags still fail.
    const withoutPassiveData = svg
      .replace(/\bhref="data:image\/png;base64,[A-Za-z0-9+/]+={0,2}"/g, '')
      .replace(/\burl\(data:font\/ttf;base64,[A-Za-z0-9+/]+={0,2}\)/g, '');
    const withoutLocalReferences = withoutPassiveData.replace(/\burl\(\s*#[A-Za-z][\w-]*\s*\)/gi, '');
    // CSS/XML escapes must not disguise a network load from the lexical guard.
    // This renderer emits no backslashes, numeric entities, inline style attrs,
    // XML base URLs, namespace-prefixed attrs, or arbitrary CSS functions.
    const css = [...withoutLocalReferences.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)].map(match => match[1]).join('\n');
    const cssWithoutSafeFunctions = css
      .replace(/format\(['"]truetype['"]\)/g, '')
      .replace(/steps\(1,\s*end\)/g, '')
      .replace(/@media\s*\(prefers-reduced-motion\s*:\s*reduce\)/g, '');
    const withoutSvgNamespace = withoutLocalReferences.replace(/\sxmlns="http:\/\/www\.w3\.org\/2000\/svg"/g, '');
    if (!/^<svg\s/.test(svg) || !/<\/svg>\s*$/.test(svg) ||
        /\\|&(?!(?:amp|lt|gt|quot|apos);)|\s(?:style|[\w-]+:[\w-]+)\s*=/i.test(svg) ||
        /(?:https?:|javascript:|\/\/)/i.test(withoutSvgNamespace) ||
        /[\w-]+\s*\(/.test(cssWithoutSafeFunctions) ||
        /<!|<\?|\son[a-z]+\s*=|\b(?:href|src)\s*=|@import|\burl\s*\(/i.test(withoutLocalReferences) ||
        [...svg.matchAll(/<\s*\/?\s*([A-Za-z][\w:-]*)/g)].some(match => !tags.has(match[1]))) throw new Error();
    const actualRevision = svg.match(/^<svg\b[^>]*\bdata-revision="([0-9a-f]{64})"/)?.[1];
    const renderedLayout = svg.match(/^<svg\b[^>]*\bdata-layout="(compact|full|o2|hexagon|focus)"/)?.[1];
    if (!actualRevision || !renderedLayout || (revision !== null && actualRevision !== revision)) throw new Error();
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const renderSha256 = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
    const declaredDigest = response.headers.get('x-morgan-render-sha256');
    const declaredRevision = response.headers.get('x-morgan-snapshot-revision');
    if ((declaredDigest && declaredDigest !== renderSha256) || (declaredRevision && declaredRevision !== actualRevision)) throw new Error();
    // Windmill's documented rich-display {svg: string} shape.
    return { svg, metadata: { revision: actualRevision, renderSha256, requestedLayout: layout, renderedLayout,
      width, height, source: 'public_receiver', durationMs: duration() } };
  } catch {
    return { ok: false, error: 'renderer_unavailable', durationMs: duration() };
  }
}

