# Source-document image extraction

Use this reference whenever the supplied report contains marketing-observation cases, inline screenshots, figures, or other document images.

## Non-negotiable provenance

- Every report image must come from the user's source document or a source image file explicitly supplied by the user.
- Never invoke ImageGen, diffusion software, design-generation tools, screenshot recreation, or any other synthetic-image workflow to fill or replace a report image.
- Never reuse a retained template case image for a new case, even when both cases have the same sequence number.
- Never infer an image from the case title, search the web for a substitute, or create a placeholder that could be mistaken for the source image.
- If an original image cannot be retrieved, stop and request an accessible document export or the missing image file.

## Feishu/Lark documents

Reading only `rawContent` is insufficient because it omits the stable image binary and often exposes only browser-session `blob:` URLs.

1. Use the authenticated Feishu/Lark connector, MCP server, API, or CLI available to the current Agent.
2. Fetch both the document's textual `rawContent` and its complete block tree (`blocks`).
3. Traverse blocks in document order. For every image block or image embedded in a grid/table block, retain the parent block id, position, caption/alt text, and its image/file token.
4. Use that token with the connector's media-download operation to download the original binary. Do not use the rendered browser `blob:` URL.
5. Save the binary in the working report, preferably as `assets/cases/<block-id>.<original-extension>`.
6. Set the matching `docx-grid-block.images[0].src` to the downloaded local path relative to `source.json`. Keep the association by block id/order, not by `案例 1/2/3/4` alone.
7. Confirm that the number and order of downloaded case images match the source document before building.

If the current Agent can read `rawContent` but cannot fetch blocks, image tokens, or media binaries, it must stop and ask the user for a DOCX export or the original case images. It must not generate replacements.

## DOCX and local exports

- For DOCX, extract the original media from `word/media/` and use the document relationships to map each image to its paragraph, table cell, or drawing position. Do not map images using filename order alone.
- For Markdown or HTML exports, resolve the referenced local image files, copy them into the working report, and preserve their document order.
- For PDF-only sources, extract embedded images when their position can be mapped reliably. Otherwise request the original document or image files.

## Build preparation and verification

1. Run:

   ```bash
   node <skill-directory>/scripts/validate-case-image-sources.mjs source.json
   ```

2. Build only after validation succeeds.
3. Verify each output case card against the source document: title, order, image subject, and image count must match.
4. The final HTML embeds the resolved local image binary as a Data URL. A valid build must not contain `blob:` URLs.
