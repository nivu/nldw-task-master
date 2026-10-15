import { NextRequest, NextResponse } from "next/server";

/**
 * The catch-all proxy — the only path from the browser to the backend
 * (constitution, Clear Boundaries; spec 007 FR-CRM-CHG-08, plan §5.12).
 *
 * Requests: only `Authorization` and a `Content-Type` of JSON, multipart or
 * CSV are forwarded; any other type is refused 415, and cookies, `X-Portal-*`
 * and `X-Forwarded-*` never are. The body is read as a stream with a running
 * byte count and refused 413 the moment it passes 4 MB, whatever
 * `Content-Length` says (it is absent from a chunked upload, and can lie).
 * 4 MB leaves room under Netlify's ~6 MB function body limit, which a
 * base64-encoded binary body grows a third towards (Q-05).
 *
 * Responses: the backend is called with `redirect: "manual"`, and only the
 * status, `Content-Type` and `Content-Disposition` come back, never
 * `Set-Cookie` or `Location`. JSON is answered as before; anything else (a
 * CSV download) is streamed through with its type and file name, which is
 * what the effort statement's download needed (006 FR-STMT-02).
 */

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "http://localhost:8000";

const MAX_BODY_BYTES = 4 * 1024 * 1024;
const ALLOWED_TYPES = ["application/json", "multipart/form-data", "text/csv"];
const NO_BODY_STATUSES = new Set([101, 204, 205, 304]);

function problem(status: number, title: string, detail: string, instance: string) {
  return NextResponse.json(
    { type: "about:blank", title, status, detail, instance },
    { status, headers: { "Content-Type": "application/problem+json" } }
  );
}

/** The media type alone: `multipart/form-data; boundary=…` → `multipart/form-data`. */
function mediaType(contentType: string): string {
  return contentType.split(";", 1)[0].trim().toLowerCase();
}

/** The body as bytes, or null once it has passed `limit` bytes. */
async function readBody(request: NextRequest, limit: number): Promise<Uint8Array | null> {
  if (!request.body) return new Uint8Array(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

async function proxy(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  const joined = path.join("/");
  const url = `${BACKEND_URL}/${joined}${request.nextUrl.search}`;
  const instance = request.nextUrl.pathname;

  const headers: Record<string, string> = {};
  const authorization = request.headers.get("authorization");
  if (authorization) headers["Authorization"] = authorization;
  const contentType = request.headers.get("content-type");
  if (contentType) {
    if (!ALLOWED_TYPES.includes(mediaType(contentType))) {
      return problem(
        415,
        "Unsupported Media Type",
        "Send JSON, a multipart upload or a CSV file.",
        instance
      );
    }
    // Kept whole: a multipart body needs its boundary.
    headers["Content-Type"] = contentType;
  }

  const init: RequestInit = { method: request.method, headers, redirect: "manual" };
  if (request.method !== "GET" && request.method !== "HEAD") {
    const body = await readBody(request, MAX_BODY_BYTES);
    if (body === null) {
      return problem(413, "Content Too Large", "The upload is larger than 4 MB.", instance);
    }
    if (body.byteLength > 0) init.body = body as BodyInit;
  }

  const res = await fetch(url, init);
  const type = res.headers.get("content-type") ?? "";
  const media = mediaType(type);

  if (NO_BODY_STATUSES.has(res.status)) {
    return new Response(null, { status: res.status });
  }
  if (media === "" || media === "application/json" || media === "application/problem+json") {
    const data = await res.json().catch(() => null);
    return NextResponse.json(data, { status: res.status });
  }
  if (res.status >= 300 && res.status < 400) {
    // A redirect is answered without its Location: the browser never
    // follows the backend anywhere.
    return new Response(null, { status: res.status });
  }

  const out = new Headers();
  if (type) out.set("Content-Type", type);
  const disposition = res.headers.get("content-disposition");
  if (disposition) out.set("Content-Disposition", disposition);
  return new Response(res.body, { status: res.status, headers: out });
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxy(request, context);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxy(request, context);
}

export async function PUT(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxy(request, context);
}

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxy(request, context);
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  return proxy(request, context);
}
