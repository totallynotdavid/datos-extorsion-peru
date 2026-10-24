interface Env {
  BUCKET: R2Bucket;
}

// These objects map request paths to bucket keys and cache policies. Tiles
// update infrequently, while the data files update monthly.
const OBJECTS: Record<string, { key: string; cacheControl: string }> = {
  "/peru.pmtiles": {
    key: "peru.pmtiles",
    cacheControl: "public, max-age=86400",
  },
  "/data/districts.geojson": {
    key: "data/districts.geojson",
    cacheControl: "public, max-age=3600",
  },
  "/data/extorsion.json": {
    key: "data/extorsion.json",
    cacheControl: "public, max-age=3600",
  },
  "/data/turnos.json": {
    key: "data/turnos.json",
    cacheControl: "public, max-age=3600",
  },
  "/data/victimas.json": {
    key: "data/victimas.json",
    cacheControl: "public, max-age=3600",
  },
};

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Range, If-Match, If-None-Match",
  "Access-Control-Expose-Headers":
    "Content-Length, Content-Range, Accept-Ranges, ETag",
};

type ByteRange = { start: number; end?: number } | { suffix: number };

// The worker accepts one `bytes=` range. It ignores multiple ranges and other
// units, then serves the whole object as RFC 9110 permits.
function parseRange(header: string | null): ByteRange | null {
  const match = /^bytes=(\d*)-(\d*)$/.exec(header?.trim() ?? "");
  if (!match || (match[1] === "" && match[2] === "")) return null;
  const [, first, last] = match;
  if (first === "") return { suffix: Number(last) };
  return { start: Number(first), end: last === "" ? undefined : Number(last) };
}

function resolveRange(range: ByteRange, size: number) {
  if ("suffix" in range) {
    return { start: Math.max(0, size - range.suffix), end: size - 1 };
  }
  return { start: range.start, end: Math.min(range.end ?? size - 1, size - 1) };
}

function etagMatches(header: string, etag: string): boolean {
  return header
    .split(",")
    .some((tag) => tag.trim() === etag || tag.trim() === "*");
}

function respond(
  body: BodyInit | null,
  status: number,
  headers: Record<string, string>,
): Response {
  return new Response(body, { status, headers: { ...CORS, ...headers } });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") {
      return respond(null, 204, { "Access-Control-Max-Age": "86400" });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return respond("method not allowed", 405, {
        Allow: "GET, HEAD, OPTIONS",
      });
    }

    const entry = OBJECTS[new URL(request.url).pathname];
    const head = entry && (await env.BUCKET.head(entry.key));
    if (!entry || !head) return respond("not found", 404, {});

    const headers: Record<string, string> = {
      "Accept-Ranges": "bytes",
      "Cache-Control": entry.cacheControl,
      "Content-Type":
        head.httpMetadata?.contentType ?? "application/octet-stream",
      ETag: head.httpEtag,
    };

    const ifMatch = request.headers.get("If-Match");
    if (ifMatch && !etagMatches(ifMatch, head.httpEtag)) {
      return respond(null, 412, headers);
    }
    const ifNoneMatch = request.headers.get("If-None-Match");
    if (ifNoneMatch && etagMatches(ifNoneMatch, head.httpEtag)) {
      return respond(null, 304, headers);
    }

    const range = parseRange(request.headers.get("Range"));
    const { start, end } = range
      ? resolveRange(range, head.size)
      : { start: 0, end: head.size - 1 };
    if (range && (start >= head.size || start > end)) {
      return respond(null, 416, {
        ...headers,
        "Content-Range": `bytes */${head.size}`,
      });
    }

    const status = range ? 206 : 200;
    const partial = {
      "Content-Length": String(end - start + 1),
      ...(range && { "Content-Range": `bytes ${start}-${end}/${head.size}` }),
    };
    if (request.method === "HEAD") {
      return respond(null, status, { ...headers, ...partial });
    }

    // The etag condition keeps the body and the headers above from describing
    // two versions of the object when the bucket is replaced mid-request.
    const object = await env.BUCKET.get(entry.key, {
      range: { offset: start, length: end - start + 1 },
      onlyIf: { etagMatches: head.etag },
    });
    if (!object || !("body" in object)) {
      return respond("object changed, retry", 503, { "Retry-After": "1" });
    }
    return respond(object.body, status, { ...headers, ...partial });
  },
} satisfies ExportedHandler<Env>;
