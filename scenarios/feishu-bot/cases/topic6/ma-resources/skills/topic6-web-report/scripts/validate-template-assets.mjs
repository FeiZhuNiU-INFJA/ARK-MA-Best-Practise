#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const skillRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const assets = [
  ["assets/preview.png", "png"],
  ["assets/source/bluefocus-logo-white.png", "png"],
  ["assets/source/title-weekly.png", "png"],
  ["assets/source/title-monthly.png", "png"],
  ["assets/source/hero-social-bg.jpg", "jpeg"],
  ["assets/source/assets/hero-social-bg.jpg", "jpeg"],
  ["assets/source/assets/case-1-car-livestream.jpg", "jpeg"],
  ["assets/source/assets/case-2-seedance-contest.jpg", "jpeg"],
  ["assets/source/assets/case-3-movie-title.jpg", "jpeg"],
  ["assets/source/assets/case-4-brand-notice.jpg", "jpeg"],
  ["assets/source/assets/fonts/Poppins-Light.ttf", "ttf"],
  ["assets/source/assets/fonts/Poppins-Regular.ttf", "ttf"],
  ["assets/source/assets/fonts/Poppins-Medium.ttf", "ttf"],
  ["assets/source/assets/fonts/Poppins-SemiBold.ttf", "ttf"],
  ["assets/source/assets/fonts/Poppins-Bold.ttf", "ttf"]
];

function signature(buffer, type) {
  if (type === "png") {
    return buffer.length >= 24
      && buffer.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex"))
      && buffer.readUInt32BE(16) > 0
      && buffer.readUInt32BE(20) > 0;
  }
  if (type === "jpeg") return buffer.length >= 4 && buffer[0] === 0xff && buffer[1] === 0xd8 && buffer.at(-2) === 0xff && buffer.at(-1) === 0xd9;
  if (type === "ttf") {
    if (buffer.length < 12) return false;
    const validHeader = buffer.subarray(0, 4).equals(Buffer.from("00010000", "hex")) || buffer.subarray(0, 4).toString("ascii") === "OTTO";
    const tableCount = buffer.readUInt16BE(4);
    return validHeader && tableCount > 0 && 12 + tableCount * 16 <= buffer.length;
  }
  return false;
}

async function main() {
  const checked = [];
  const errors = [];

  for (const [relativePath, type] of assets) {
    try {
      const buffer = await readFile(resolve(skillRoot, relativePath));
      if (!signature(buffer, type)) throw new Error(`invalid ${type} structure or signature`);
      checked.push({ file: relativePath, type, bytes: buffer.length });
    } catch (error) {
      errors.push(`${relativePath}: ${error.message}`);
    }
  }

  if (errors.length) throw new Error(`Template asset validation failed:\n- ${errors.join("\n- ")}`);
  process.stdout.write(`${JSON.stringify({ ok: true, checked }, null, 2)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${JSON.stringify({ ok: false, error: error.message }, null, 2)}\n`);
  process.exitCode = 1;
});
