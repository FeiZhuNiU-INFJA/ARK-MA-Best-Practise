# HTML snapshot upload

Read this reference after the finished report has passed `validate-case-image-sources.mjs`, `node --check`, the build, `validate-rendered-content.mjs`, and visual/content review. The uploaded object is a publicly accessible HTML snapshot, so upload only the exact final report the user asked this skill to produce. The upload creates no Sites resource and must not inspect, change, or delete an existing site.

## Bundled tool and prerequisites

- Use `<skill-directory>/scripts/upload-html.mjs`, bundled from the user-provided `upload-html.mjs`. It requires Node.js 20.19 or newer and accepts one `.html` or `.htm` file per invocation.
- On first interactive use, the uploader prompts with hidden input and saves the API key in the current user's `.config/blueai-html-upload/credentials.json`; later uploads reuse it. This file is not encrypted; on systems supporting file permissions it is restricted to the current user. Never pass a key in chat, a command argument, an environment variable, or a report/project file. If no key is saved and the agent runs noninteractively, stop before uploading and ask the user to run `node "<skill-directory>/scripts/upload-html.mjs" --setup-key` in their own interactive terminal. Do not ask them to paste the key into chat.
- The default endpoint is the uploader's configured HTML-snapshot test service. Do not change `--api-url`, `--extra-path`, or `--prefix-name` unless the user provides a destination requirement.
- The service accepts at most 50 MiB and uploads one file. The report must therefore be self-contained; local relative CSS, JavaScript, images, fonts, or links are not part of the upload.

## Required sequence

From the report working directory, use the exact absolute path of the validated output:

```bash
node "<skill-directory>/scripts/upload-html.mjs" "/absolute/path/to/report/index.html" --dry-run
node "<skill-directory>/scripts/upload-html.mjs" "/absolute/path/to/report/index.html"
```

1. Stop before the live upload if the dry run exits nonzero, reports `relativeAssets`, or the file is not the final validated report. Fix the self-contained HTML or input; do not use `--allow-relative-assets`.
2. If the live command reports that no key is saved, ask the user to complete the one-time `--setup-key` step in an interactive terminal, then retry. Run the live command once with a saved key. Do not use `--no-verify`. If the network result is uncertain, inspect the result before retrying to avoid duplicate public snapshots.
3. Accept cloud delivery only when the command exits 0, `uploaded.url` is present, and `verification.ok` is `true` with an HTML content type. Exit code 1 means upload failed; exit code 2 means the object may exist but CDN verification failed. Report those states accurately rather than inventing a working link.
4. Return the local file path, the public `uploaded.url`, and the verification status. Do not expose the API key or full request headers. Keep the local file even if publishing fails.

The uploader's original usage documentation is the user-provided `/Users/yuanyuan/Documents/ChatGPT/a2ui 看板链接/README.md`; the bundled tool keeps the skill runnable without requiring that external directory at execution time.
