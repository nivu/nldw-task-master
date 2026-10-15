// All backend calls are routed through the Next.js /api/proxy catch-all route
// so the browser never makes a direct request to the backend server.
function resolveUrl(path: string): string {
  return `/api/proxy${path}`;
}

interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
}

export class BackendError extends Error {
  status: number;
  detail: string;

  constructor(problem: ProblemDetail) {
    super(problem.title);
    this.status = problem.status;
    this.detail = problem.detail;
  }
}

export async function backendFetch<T>(
  path: string,
  options: RequestInit & { token?: string } = {}
): Promise<T> {
  const { token, ...fetchOptions } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(fetchOptions.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(resolveUrl(path), {
    ...fetchOptions,
    headers,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const fallbackTitle =
      typeof body?.detail === "string" && body.detail.trim().length > 0
        ? body.detail
        : "Request Failed";
    const problem: ProblemDetail = {
      type: body?.type ?? "about:blank",
      title: body?.title ?? fallbackTitle,
      status: response.status, // always use the real HTTP status code
      detail: typeof body?.detail === "string" ? body.detail : response.statusText,
    };
    throw new BackendError(problem);
  }

  return response.json() as Promise<T>;
}

async function problemFrom(response: Response): Promise<BackendError> {
  const body = await response.json().catch(() => null);
  return new BackendError({
    type: body?.type ?? "about:blank",
    title: body?.title ?? "Request Failed",
    status: response.status,
    detail: typeof body?.detail === "string" ? body.detail : response.statusText,
  });
}

/**
 * A file upload through the proxy — spec 007 FR-CRM-CHG-08. No
 * `Content-Type` is set here: the browser writes `multipart/form-data` with
 * its boundary. The proxy refuses a body past 4 MB with 413.
 */
export async function upload<T>(path: string, form: FormData, token?: string): Promise<T> {
  const response = await fetch(resolveUrl(path), {
    method: "POST",
    body: form,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) throw await problemFrom(response);
  return response.json() as Promise<T>;
}

/**
 * A file download through the proxy — spec 007 FR-CRM-IO-05, 006
 * FR-STMT-02: the body as a blob, and the name the backend gave it in
 * `Content-Disposition` (or `fallback`).
 */
export async function download(
  path: string,
  token?: string,
  fallback = "download",
  /** A POST with a JSON body, as a view's export is (007 FR-CRM-IO-04). */
  json?: unknown
): Promise<{ blob: Blob; filename: string }> {
  const auth: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
  const response = await fetch(
    resolveUrl(path),
    json === undefined
      ? { headers: auth }
      : {
          method: "POST",
          headers: { ...auth, "Content-Type": "application/json" },
          body: JSON.stringify(json),
        }
  );
  if (!response.ok) throw await problemFrom(response);
  const disposition = response.headers.get("content-disposition") ?? "";
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  const filename = encoded ? decodeURIComponent(encoded[1]) : plain ? plain[1] : fallback;
  return { blob: await response.blob(), filename };
}
