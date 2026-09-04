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
