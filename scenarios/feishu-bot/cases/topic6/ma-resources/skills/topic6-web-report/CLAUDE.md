# BlueFocus hotspot-report requirements

Before generating or updating a report, read `SKILL.md` and `references/document-image-extraction.md` completely and follow them as mandatory project requirements.

Before copying the retained template, run `scripts/validate-template-assets.mjs` from the skill directory. Do not build when a retained PNG, JPEG, or TTF asset fails validation.

For Feishu/Lark sources, reading `rawContent` alone is not sufficient. Also retrieve the complete document blocks, extract each image token from its source block, download the original image binary through the authenticated Feishu/Lark connector or API, and map it back to the same `docx-grid-block` in `source.json`.

Never generate, redraw, search for, or substitute marketing-observation case images. Never reuse the retained W32 `case-*.jpg` assets for a new report. If original document images cannot be retrieved, stop and ask the user for a DOCX export or the original image files.

Run the bundled validator before building:

```bash
node scripts/validate-case-image-sources.mjs /absolute/path/source.json
```

Do not publish output containing `blob:` image URLs.

After the report passes local validation, read `references/html-snapshot-upload.md` and publish the exact finished `index.html` with the bundled `scripts/upload-html.mjs`: run `--dry-run` first, then upload and require successful CDN verification. If no key is saved, ask the user to run `--setup-key` in their own interactive terminal; never ask for the key in chat or read an environment variable. Return both the local file and verified cloud URL. Do not bypass relative-asset checks, skip verification, expose an API key, or claim cloud delivery if publishing fails. This workflow does not ask about or perform Sites creation, modification, or deletion.
