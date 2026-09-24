#!/usr/bin/env node

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const [sourceInput, htmlInput] = process.argv.slice(2);
const sourcePath = resolve(sourceInput ?? "");
const htmlPath = resolve(htmlInput ?? "");

if (!sourceInput || !htmlInput || !existsSync(sourcePath) || !existsSync(htmlPath)) {
  throw new Error("Usage: node validate-rendered-content.mjs /absolute/path/source.json /absolute/path/index.html");
}

const blocks = JSON.parse(readFileSync(sourcePath, "utf8"));
const html = readFileSync(htmlPath, "utf8");

function cleanText(value = "") {
  return String(value)
    .replace(/[\u200b\ufeff]/gu, "")
    .replace(/\s+/gu, " ")
    .trim();
}

function comparableText(value = "") {
  return decodeEntities(value)
    .replace(/[\u200b\ufeff]/gu, "")
    .replace(/\s+/gu, "");
}

function decodeEntities(value = "") {
  return value
    .replace(/&quot;/gu, '"')
    .replace(/&#39;/gu, "'")
    .replace(/&amp;/gu, "&")
    .replace(/&lt;/gu, "<")
    .replace(/&gt;/gu, ">");
}

function sourceLines(value = "") {
  return String(value)
    .replace(/[\u200b\ufeff]/gu, "")
    .split(/\r?\n/gu)
    .map((line) => cleanText(line).replace(/^(?:[-•]\s*)/u, ""))
    .filter((line) => line.length >= 4);
}

const renderedVisibleText = comparableText(html.replace(/<[^>]*>/gu, ""));
const renderedMarkupText = comparableText(html);
const activeBlocks = blocks.filter((block) => !block?.hidden);
const errors = [];

function renderedContains(sourceText) {
  const expected = comparableText(sourceText);
  return expected && (renderedVisibleText.includes(expected) || renderedMarkupText.includes(expected));
}

if (/blob:/iu.test(html)) errors.push("Rendered HTML still contains a browser-session blob URL");
if (/<p(?:\s[^>]*)?>\s*•(?:\s|<)/u.test(html)) errors.push("Rendered prose contains a standalone bullet marker");
if (/<ul class="cell-list">[\s\S]*?<li>\s*[•·|｜-]\s*/u.test(html)) errors.push("Rendered data-card list contains a decorative text prefix");
if (/(?:^|\n)\.cell-list li::before\s*\{[^}]*content:\s*["']?•/mu.test(html)) errors.push("Rendered plain data-card list still draws decorative bullets");
if (/\.data-card-grid--industry-ranking \.data-card-field--drivers \.cell-list li\s*\{[^}]*border-left:\s*(?:[1-9]\d*px|thin|medium|thick)/su.test(html)) errors.push("Rendered main-driver list still draws vertical separators");
if (!/\.data-card-grid--industry-ranking \.data-card-field--drivers \.cell-list li::before\s*\{[^}]*content:\s*"•"[^}]*color:\s*inherit/su.test(html)) errors.push("Rendered main-driver list is missing its same-color bullets");
if (!/\.data-card-grid--industry-ranking \.data-card-field--drivers \.cell-list li:first-child\s*\{[^}]*padding-left:\s*10px/su.test(html)) errors.push("Rendered first main-driver item is missing its bullet inset");
if ([...html.matchAll(/<article class="data-card data-card--marketing-finding"[\s\S]*?<\/header>/gu)].some(([header]) => /data-card-kicker/u.test(header))) errors.push("Rendered brand-collaboration card still contains an event-type pill");

const caseBlocks = activeBlocks.filter((block) => /docx-grid-block/u.test(String(block?.className ?? "")));
const renderedCaseCount = (html.match(/class="case-card case-card--visual"/gu) ?? []).length;
if (renderedCaseCount !== caseBlocks.length) {
  errors.push(`Marketing-observation case count mismatch: source=${caseBlocks.length}, rendered=${renderedCaseCount}`);
}

for (const block of caseBlocks) {
  const title = sourceLines(block.text)[0] ?? "";
  if (!title || !renderedContains(title)) errors.push(`Missing rendered case title: ${title || "(untitled case)"}`);
}

for (const block of activeBlocks) {
  const className = String(block?.className ?? "");
  if (/docx-divider-block|docx-grid-block|quote_container/u.test(className)) continue;

  if (/docx-table-block/u.test(className)) {
    for (const row of (block.tables ?? []).flat()) {
      for (const cell of row ?? []) {
        for (const line of sourceLines(cell)) {
          if (!renderedContains(line)) errors.push(`Missing rendered table content: ${line.slice(0, 80)}`);
        }
      }
    }
    continue;
  }

  for (const line of sourceLines(block.text)) {
    const normalizedHeading = /docx-heading[12]-block/u.test(className)
      ? line.replace(/^(?:(?:第?[一二三四五六七八九十]+)|(?:\d+(?:\.\d+)*))[、.]\s*/u, "")
      : "";
    if (!renderedContains(line) && (!normalizedHeading || !renderedContains(normalizedHeading))) {
      errors.push(`Missing rendered text: ${line.slice(0, 80)}`);
    }
  }
}

for (const block of activeBlocks) {
  for (const link of block?.links ?? []) {
    const href = String(link?.href ?? "").trim();
    if (href && !html.includes(href)) errors.push(`Missing rendered source link: ${href}`);
  }
}

if (errors.length) throw new Error(`Rendered-content validation failed:\n- ${[...new Set(errors)].join("\n- ")}`);
console.log(`Validated complete rendered coverage for ${activeBlocks.length} source blocks and ${caseBlocks.length} case cards.`);
