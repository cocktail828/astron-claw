import { randomUUID } from "node:crypto";
import type { ResolvedAccount } from "../types.js";

// ---------------------------------------------------------------------------
// Bridge REST API client (for media upload)
// ---------------------------------------------------------------------------

export function getBridgeHttpBaseUrl(wsUrl: string): string {
  // Convert ws(s)://host:port/path to http(s)://host:port
  try {
    const url = new URL(wsUrl);
    const protocol = url.protocol === "wss:" ? "https:" : "http:";
    return `${protocol}//${url.host}`;
  } catch {
    return "http://localhost:8765";
  }
}

export interface UploadResult {
  fileName: string;
  mimeType: string;
  fileSize: number;
  sessionId: string;
  downloadUrl: string;
}

export async function uploadMediaToBridge(
  account: ResolvedAccount,
  buffer: Buffer,
  fileName: string,
  contentType: string,
  sessionId?: string,
): Promise<UploadResult> {
  const baseUrl = getBridgeHttpBaseUrl(account.bridge.url);
  const url = `${baseUrl}/api/media/upload`;

  const boundary = `----AstronClawBoundary${randomUUID().replace(/-/g, "")}`;
  const CRLF = "\r\n";

  // Build multipart body manually to avoid external dependency
  const parts: string[] = [];

  // File part — escape filename for Content-Disposition (RFC 6266)
  const safeFileName = fileName
    .replace(/[\r\n]/g, "")            // strip CR/LF to prevent header injection
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"');
  parts.push(`--${boundary}${CRLF}`);
  parts.push(`Content-Disposition: form-data; name="file"; filename="${safeFileName}"${CRLF}`);
  parts.push(`Content-Type: ${contentType}${CRLF}`);
  parts.push(CRLF);

  const header = Buffer.from(parts.join(""), "utf8");

  // sessionId part (if provided)
  let sessionPart = Buffer.alloc(0);
  if (sessionId) {
    const sp = [
      `${CRLF}--${boundary}${CRLF}`,
      `Content-Disposition: form-data; name="sessionId"${CRLF}`,
      CRLF,
      sessionId,
    ].join("");
    sessionPart = Buffer.from(sp, "utf8");
  }

  const footer = Buffer.from(`${CRLF}--${boundary}--${CRLF}`, "utf8");
  const body = Buffer.concat([header, buffer, sessionPart, footer]);

  const headers: Record<string, string> = {
    "Content-Type": `multipart/form-data; boundary=${boundary}`,
  };
  if (account.bridge.token) {
    headers["Authorization"] = `Bearer ${account.bridge.token}`;
  }

  const res = await fetch(url, { method: "POST", headers, body });
  if (!res.ok) {
    throw new Error(`Media upload failed: ${res.status} ${res.statusText}`);
  }

  return await res.json() as UploadResult;
}

// ---------------------------------------------------------------------------
// Infer media type from MIME
// ---------------------------------------------------------------------------
export function inferMediaType(mimeType: string): "image" | "audio" | "video" | "file" {
  if (!mimeType) return "file";
  if (mimeType.startsWith("image/")) return "image";
  if (mimeType.startsWith("audio/")) return "audio";
  if (mimeType.startsWith("video/")) return "video";
  return "file";
}
