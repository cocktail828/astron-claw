import { join, parse as parsePath } from "node:path";
import { homedir } from "node:os";
import { mkdir } from "node:fs/promises";

/**
 * Resolve the SDK-convention inbound media directory.
 * Priority: OPENCLAW_STATE_DIR > CLAWDBOT_STATE_DIR > ~/.openclaw
 */
export function inboundMediaDir(): string {
  return join(
    process.env.OPENCLAW_STATE_DIR?.trim()
      || process.env.CLAWDBOT_STATE_DIR?.trim()
      || join(homedir(), ".openclaw"),
    "media",
    "inbound",
  );
}

/** Ensure the inbound media directory exists (recursive, safe if already exists). */
export async function ensureInboundMediaDir(): Promise<string> {
  const dir = inboundMediaDir();
  await mkdir(dir, { recursive: true });
  return dir;
}

/**
 * Sanitize a file name stem: keep letters/digits/._-, collapse separators, limit length.
 * Returns "" for inputs that sanitize to nothing (e.g. pure whitespace / special chars).
 */
export function sanitizeStem(raw: string, maxLen = 60): string {
  return raw.trim()
    .replace(/[^\p{L}\p{N}._-]+/gu, "_")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "")
    .slice(0, maxLen);
}

/**
 * Build a saved media file name following SDK convention: `{stem}---{uuid}{ext}`.
 * Falls back to `{uuid}{ext}` when stem is empty.
 */
export function buildMediaFileName(fileName: string, uuid: string, ext: string): string {
  // Decode percent-encoded file names (common in S3/HTTP URLs)
  let decoded = fileName;
  try { decoded = decodeURIComponent(fileName); } catch { /* keep original if malformed */ }
  const stem = sanitizeStem(parsePath(decoded).name);
  return stem ? `${stem}---${uuid}${ext}` : `${uuid}${ext}`;
}
