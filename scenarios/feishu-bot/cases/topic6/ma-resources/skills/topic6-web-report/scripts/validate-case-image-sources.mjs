#!/usr/bin/env node

import { existsSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const sourcePath = resolve(process.argv[2] ?? "");
if (!sourcePath || !existsSync(sourcePath)) {
  throw new Error("Usage: node validate-case-image-sources.mjs /absolute/path/source.json");
}

const blocks = JSON.parse(readFileSync(sourcePath, "utf8"));
if (!Array.isArray(blocks)) throw new Error("The report source must be a JSON block array.");

const errors = [];
let caseCount = 0;

for (const block of blocks) {
  if (block?.hidden) continue;
  if (!/docx-grid-block/u.test(String(block?.className ?? ""))) continue;
  caseCount += 1;
  const title = String(block?.text ?? "").replace(/[\u200b\ufeff]/gu, "").trim().split("\n")[0] || `case ${caseCount}`;
  const source = String(block?.images?.[0]?.src ?? "").trim();
  if (!source) {
    errors.push(`${title}: images[0].src is missing`);
    continue;
  }
  if (/^blob:/iu.test(source)) {
    errors.push(`${title}: blob URL is browser-session-only; fetch the document block image token and download the original binary`);
    continue;
  }
  if (/^https?:/iu.test(source)) {
    errors.push(`${title}: remote URL must be downloaded from the source document and replaced with a local path or data URL`);
    continue;
  }
  if (/^data:image\//iu.test(source)) continue;

  let localPath = "";
  if (/^file:\/\//iu.test(source)) {
    try {
      localPath = fileURLToPath(source);
    } catch {
      errors.push(`${title}: invalid file URL`);
      continue;
    }
  } else if (!/^[a-z][a-z\d+.-]*:/iu.test(source)) {
    localPath = isAbsolute(source) ? source : resolve(dirname(sourcePath), source);
  }

  if (!localPath || !existsSync(localPath)) errors.push(`${title}: image file not found at ${source}`);
}

if (!caseCount) throw new Error("No docx-grid-block marketing-observation cases were found.");
if (errors.length) throw new Error(`Case-image validation failed:\n- ${errors.join("\n- ")}`);
console.log(`Validated ${caseCount} document-derived case images.`);
