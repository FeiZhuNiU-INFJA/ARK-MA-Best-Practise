#!/usr/bin/env node

import { chmod, lstat, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { basename, extname, join, resolve } from "node:path";
import { homedir } from "node:os";

const DEFAULT_API_URL = "https://bmc-relay.domob-inc.cn/html-snapshot";
const CLIENT_ORIGIN = "https://bmc-a2ui-doc.domob-inc.cn";
const MIME_TYPE = "text/html";
const MAX_HTML_BYTES = 50 * 1024 * 1024;
const CREDENTIAL_DIR = join(homedir(), ".config", "blueai-html-upload");
const CREDENTIAL_FILE = join(CREDENTIAL_DIR, "credentials.json");

function usage() {
  return `Usage:
  node upload-html.mjs <file.html> [options]

Options:
  --api-url <url>          Upload service base URL
  --dry-run                Validate only; do not upload
  --no-verify              Skip checking the returned CDN URL
  --allow-relative-assets  Allow references such as ./style.css
  --extra-path <path>      COS directory, such as Analysis-Dashboard-ZL/2026-09
  --prefix-name <name>     Filename prefix (requires --extra-path)
  --setup-key              Prompt with hidden input and save/update the local API key
  -h, --help               Show this help

API key: saved in a user-only local credential file after first interactive use.
The file is not encrypted. Never pass the key as an argument or environment variable.
`;
}

function parseArgs(argv) {
  const options = {
    apiUrl: DEFAULT_API_URL,
    dryRun: false,
    setupKey: false,
    verify: true,
    allowRelativeAssets: false,
    extraPath: undefined,
    prefixName: undefined,
    filePath: null,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "-h" || arg === "--help") {
      options.help = true;
    } else if (arg === "--dry-run") {
      options.dryRun = true;
    } else if (arg === "--setup-key") {
      options.setupKey = true;
    } else if (arg === "--no-verify") {
      options.verify = false;
    } else if (arg === "--allow-relative-assets") {
      options.allowRelativeAssets = true;
    } else if (["--api-url", "--extra-path", "--prefix-name"].includes(arg)) {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) {
        throw new Error(`${arg} requires a value`);
      }
      index += 1;
      if (arg === "--api-url") options.apiUrl = value;
      if (arg === "--extra-path") options.extraPath = value;
      if (arg === "--prefix-name") options.prefixName = value;
    } else if (arg.startsWith("-")) {
      throw new Error(`Unknown option: ${arg}`);
    } else if (options.filePath) {
      throw new Error("Only one HTML file can be uploaded at a time");
    } else {
      options.filePath = resolve(arg);
    }
  }

  if (options.extraPath !== undefined) {
    const path = options.extraPath.replace(/^\/+|\/+$/g, "");
    if (!path || !path.split("/").every((part) => /^[A-Za-z0-9._-]+$/.test(part) && part !== "." && part !== "..")) {
      throw new Error("--extra-path must contain URL-safe path segments without empty, . or .. segments");
    }
    options.extraPath = path;
  }
  if (options.prefixName !== undefined) {
    if (!options.extraPath) throw new Error("--prefix-name requires --extra-path");
    if (!/^[A-Za-z0-9._-]+$/.test(options.prefixName) || options.prefixName === "." || options.prefixName === "..") {
      throw new Error("--prefix-name must be a URL-safe filename segment");
    }
  }
  if (options.setupKey && (options.filePath || options.dryRun || options.extraPath || options.prefixName || options.apiUrl !== DEFAULT_API_URL || !options.verify || options.allowRelativeAssets)) {
    throw new Error("--setup-key must be run alone");
  }
  return options;
}

function buildEndpoint(apiUrl) {
  const url = new URL(apiUrl);
  const isLocal = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (url.protocol !== "https:" && !isLocal) {
    throw new Error("The upload API must use HTTPS unless it is localhost");
  }

  url.search = "";
  url.hash = "";
  let pathname = url.pathname.replace(/\/+$/, "");
  if (!pathname.endsWith("/api/upload")) {
    pathname += "/api/upload";
  }
  url.pathname = pathname;
  return url.toString();
}

function findRelativeAssets(html) {
  const references = new Set();
  const attributePattern = /\b(?:src|href)\s*=\s*(["'])(.*?)\1/giu;
  const ignoredPrefixes = [
    "#",
    "//",
    "data:",
    "blob:",
    "http:",
    "https:",
    "mailto:",
    "tel:",
    "javascript:",
  ];

  for (const match of html.matchAll(attributePattern)) {
    const reference = match[2].trim();
    if (!reference) continue;
    const lower = reference.toLowerCase();
    if (ignoredPrefixes.some((prefix) => lower.startsWith(prefix))) continue;
    references.add(reference);
  }
  return [...references];
}

function inspectHtml(html) {
  const warnings = [];
  if (!/^\s*<!doctype\s+html/i.test(html)) {
    warnings.push("Missing <!doctype html>");
  }
  if (!/<html(?:\s|>)/i.test(html)) {
    warnings.push("Missing <html> element");
  }
  if (!/<meta[^>]+charset=/i.test(html)) {
    warnings.push("Missing a charset meta tag; UTF-8 text may render incorrectly");
  }
  return { warnings, relativeAssets: findRelativeAssets(html) };
}

async function credentialFileStatus() {
  try {
    const info = await lstat(CREDENTIAL_FILE);
    if (!info.isFile()) throw new Error("The credential path is not a regular file");
    if (process.platform !== "win32" && (info.mode & 0o077) !== 0) {
      throw new Error("The credential file is accessible to other users; restrict it to the current user before uploading");
    }
    return info;
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

async function readSavedApiKey() {
  if (!(await credentialFileStatus())) return null;
  const stored = JSON.parse(await readFile(CREDENTIAL_FILE, "utf8"));
  if (stored?.version !== 1 || typeof stored.apiKey !== "string" || !stored.apiKey.trim()) {
    throw new Error("The saved credential is invalid; run --setup-key in an interactive terminal to replace it");
  }
  return stored.apiKey.trim();
}

async function promptHiddenApiKey() {
  if (!process.stdin.isTTY || !process.stdout.isTTY || typeof process.stdin.setRawMode !== "function") {
    throw new Error("No saved API key is available. Run `node upload-html.mjs --setup-key` once in an interactive terminal; enter the key at the hidden prompt, not in chat or a command argument");
  }
  const input = process.stdin;
  const output = process.stdout;
  const previousRaw = Boolean(input.isRaw);
  output.write("HTML upload API key (input hidden): ");
  return await new Promise((resolveKey, reject) => {
    let value = "";
    const finish = (error) => {
      input.off("data", onData);
      input.setRawMode(previousRaw);
      input.pause();
      output.write("\n");
      if (error) reject(error);
      else if (!value.trim()) reject(new Error("API key cannot be empty"));
      else resolveKey(value.trim());
    };
    const onData = (chunk) => {
      for (const character of chunk.toString("utf8")) {
        if (character === "\r" || character === "\n") return finish();
        if (character === "\u0003") return finish(new Error("API key entry cancelled"));
        if (character === "\u007f" || character === "\b") value = value.slice(0, -1);
        else if (character >= " ") value += character;
      }
    };
    input.setRawMode(true);
    input.resume();
    input.on("data", onData);
  });
}

async function promptAndSaveApiKey() {
  const apiKey = await promptHiddenApiKey();
  await mkdir(CREDENTIAL_DIR, { recursive: true, mode: 0o700 });
  let existing = false;
  try {
    const info = await lstat(CREDENTIAL_FILE);
    if (!info.isFile()) throw new Error("The credential path is not a regular file");
    existing = true;
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  if (existing && process.platform !== "win32") await chmod(CREDENTIAL_FILE, 0o600);
  await writeFile(CREDENTIAL_FILE, `${JSON.stringify({ version: 1, apiKey })}\n`, {
    encoding: "utf8",
    mode: 0o600,
    flag: existing ? "w" : "wx",
  });
  if (process.platform !== "win32") await chmod(CREDENTIAL_FILE, 0o600);
  return apiKey;
}

async function readApiKey() {
  return (await readSavedApiKey()) ?? (await promptAndSaveApiKey());
}

async function uploadHtml({ endpoint, apiKey, html, extraPath, prefixName }) {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
      Origin: CLIENT_ORIGIN,
      Referer: `${CLIENT_ORIGIN}/a2ui/html-to-link`,
    },
    body: JSON.stringify({
      html,
      ...(extraPath ? { extraPath } : {}),
      ...(prefixName ? { prefixName } : {}),
      metadata: {
        source: "ai-agent",
        description: "HTML file uploaded by an AI agent",
      },
    }),
    signal: AbortSignal.timeout(60_000),
  });

  const responseText = await response.text();
  let payload;
  try {
    payload = JSON.parse(responseText);
  } catch {
    throw new Error(`Upload service returned non-JSON content (HTTP ${response.status})`);
  }

  if (!response.ok || payload?.success !== true || !payload?.data?.url) {
    const message = payload?.error?.message || `HTTP ${response.status}`;
    const code = payload?.error?.code ? `${payload.error.code}: ` : "";
    throw new Error(`Upload failed: ${code}${message}`);
  }
  return payload.data;
}

async function verifyPublishedUrl(url) {
  const response = await fetch(url, {
    method: "GET",
    redirect: "follow",
    signal: AbortSignal.timeout(30_000),
  });
  const contentType = response.headers.get("content-type") || "";
  await response.body?.cancel();

  return {
    ok: response.ok && contentType.toLowerCase().includes("text/html"),
    status: response.status,
    contentType,
    finalUrl: response.url,
  };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write(usage());
    return;
  }
  if (options.setupKey) {
    await promptAndSaveApiKey();
    process.stdout.write(`${JSON.stringify({ ok: true, storedIn: CREDENTIAL_FILE, encrypted: false }, null, 2)}\n`);
    return;
  }
  if (!options.filePath) {
    throw new Error(`HTML file path is required\n\n${usage()}`);
  }

  const extension = extname(options.filePath).toLowerCase();
  if (extension !== ".html" && extension !== ".htm") {
    throw new Error("Only .html and .htm files are accepted");
  }

  const fileInfo = await stat(options.filePath);
  if (!fileInfo.isFile() || fileInfo.size === 0) {
    throw new Error("The selected HTML file is missing or empty");
  }
  if (fileInfo.size > MAX_HTML_BYTES) {
    throw new Error("HTML exceeds the API's 50 MiB UTF-8 limit");
  }

  const fileBuffer = await readFile(options.filePath);
  const html = fileBuffer.toString("utf8");
  const inspection = inspectHtml(html);
  if (inspection.relativeAssets.length > 0 && !options.allowRelativeAssets) {
    throw new Error(
      `The HTML references local/relative assets that will not be uploaded:\n${inspection.relativeAssets
        .map((item) => `  - ${item}`)
        .join("\n")}\nUse a self-contained HTML file or pass --allow-relative-assets to continue anyway.`,
    );
  }

  const endpoint = buildEndpoint(options.apiUrl);
  const baseResult = {
    ok: true,
    dryRun: options.dryRun,
    file: options.filePath,
    filename: basename(options.filePath),
    size: fileInfo.size,
    mimeType: MIME_TYPE,
    endpoint,
    extraPath: options.extraPath ?? null,
    prefixName: options.prefixName ?? null,
    warnings: inspection.warnings,
    relativeAssets: inspection.relativeAssets,
  };

  if (options.dryRun) {
    process.stdout.write(`${JSON.stringify(baseResult, null, 2)}\n`);
    return;
  }

  const apiKey = await readApiKey();
  const uploaded = await uploadHtml({
    endpoint,
    apiKey,
    html,
    extraPath: options.extraPath,
    prefixName: options.prefixName,
  });

  const verification = options.verify
    ? await verifyPublishedUrl(uploaded.url)
    : { ok: null, skipped: true };

  const result = {
    ...baseResult,
    dryRun: false,
    uploaded,
    verification,
  };
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);

  if (options.verify && !verification.ok) {
    process.exitCode = 2;
  }
}

main().catch((error) => {
  process.stderr.write(`${JSON.stringify({ ok: false, error: error.message }, null, 2)}\n`);
  process.exitCode = 1;
});
