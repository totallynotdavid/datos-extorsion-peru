import { env, exports } from "cloudflare:workers";
import { beforeAll, describe, expect, it } from "vitest";

declare global {
  namespace Cloudflare {
    interface Env {
      BUCKET: R2Bucket;
    }
    interface GlobalProps {
      mainModule: typeof import("../src/index");
    }
  }
}
const TILES = "0123456789abcdefghij";

function get(path: string, init?: RequestInit) {
  return exports.default.fetch(new Request(`https://example.com${path}`, init));
}

async function bytes(res: Response) {
  return new TextDecoder().decode(await res.arrayBuffer());
}

beforeAll(async () => {
  await env.BUCKET.put("peru.pmtiles", TILES);
  await env.BUCKET.put("data/extorsion.json", '{"months":[]}', {
    httpMetadata: { contentType: "application/json" },
  });
  await env.BUCKET.put("data/turnos.json", '{"months":[],"rows":[]}', {
    httpMetadata: { contentType: "application/json" },
  });
  await env.BUCKET.put("data/victimas.json", '{"year":2024}', {
    httpMetadata: { contentType: "application/json" },
  });
});

describe("range requests on /peru.pmtiles", () => {
  const cases: {
    name: string;
    range: string;
    status: number;
    body: string;
    contentRange: string;
  }[] = [
    {
      name: "closed range",
      range: "bytes=2-5",
      status: 206,
      body: "2345",
      contentRange: "bytes 2-5/20",
    },
    {
      name: "open end",
      range: "bytes=15-",
      status: 206,
      body: "fghij",
      contentRange: "bytes 15-19/20",
    },
    {
      name: "suffix",
      range: "bytes=-3",
      status: 206,
      body: "hij",
      contentRange: "bytes 17-19/20",
    },
    {
      name: "end past the object is clamped",
      range: "bytes=18-99",
      status: 206,
      body: "ij",
      contentRange: "bytes 18-19/20",
    },
    {
      name: "start past the object",
      range: "bytes=20-30",
      status: 416,
      body: "",
      contentRange: "bytes */20",
    },
    {
      name: "end before start",
      range: "bytes=5-2",
      status: 416,
      body: "",
      contentRange: "bytes */20",
    },
  ];

  it.each(cases)("$name", async ({ range, status, body, contentRange }) => {
    const res = await get("/peru.pmtiles", { headers: { Range: range } });
    expect(res.status).toBe(status);
    expect(await bytes(res)).toBe(body);
    expect(res.headers.get("Content-Range")).toBe(contentRange);
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
  });

  it("serves the whole object when the range header is not one byte range", async () => {
    for (const range of ["bytes=0-1,4-5", "items=0-1"]) {
      const res = await get("/peru.pmtiles", { headers: { Range: range } });
      expect(res.status).toBe(200);
      expect(await bytes(res)).toBe(TILES);
    }
  });
});

describe("headers on every served object", () => {
  it("sends the length, cache, CORS and range headers on GET and HEAD", async () => {
    for (const method of ["GET", "HEAD"]) {
      const res = await get("/peru.pmtiles", { method });
      expect(res.status).toBe(200);
      expect(res.headers.get("Content-Length")).toBe("20");
      expect(res.headers.get("Accept-Ranges")).toBe("bytes");
      expect(res.headers.get("Cache-Control")).toBe("public, max-age=86400");
      expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
      expect(res.headers.get("Access-Control-Expose-Headers")).toContain(
        "Content-Range",
      );
      expect(res.headers.get("ETag")).toBeTruthy();
    }
  });

  it("answers the preflight for a Range request", async () => {
    const res = await get("/peru.pmtiles", { method: "OPTIONS" });
    expect(res.status).toBe(204);
    expect(res.headers.get("Access-Control-Allow-Headers")).toContain("Range");
  });

  it("serves the data files with their content type", async () => {
    for (const [path, body] of [
      ["/data/extorsion.json", { months: [] }],
      ["/data/turnos.json", { months: [], rows: [] }],
      ["/data/victimas.json", { year: 2024 }],
    ] as const) {
      const res = await get(path);
      expect(res.status).toBe(200);
      expect(res.headers.get("Content-Type")).toBe("application/json");
      expect(await res.json()).toEqual(body);
    }
  });
});

describe("conditional requests and misses", () => {
  it("answers 304 to a matching If-None-Match and 412 to a stale If-Match", async () => {
    const etag = (await get("/peru.pmtiles")).headers.get("ETag")!;
    const same = await get("/peru.pmtiles", {
      headers: { "If-None-Match": etag },
    });
    expect(same.status).toBe(304);
    const stale = await get("/peru.pmtiles", {
      headers: { "If-Match": '"stale"', Range: "bytes=0-1" },
    });
    expect(stale.status).toBe(412);
  });

  it.each([
    [
      "an object missing from the bucket",
      "/data/districts.geojson",
      "GET",
      404,
    ],
    ["a path the worker does not serve", "/data/other.json", "GET", 404],
    ["a method it does not serve", "/peru.pmtiles", "POST", 405],
  ])("answers %s", async (_name, path, method, status) => {
    const res = await get(path, { method });
    expect(res.status).toBe(status);
  });
});
