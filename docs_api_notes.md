# Verified API Integration Notes

The Manus API v2 task endpoint accepts `message.content` as an array of content blocks. A text prompt uses `{"type":"text","text":"..."}`. The endpoint supports file content blocks and accepts skill identifiers through `message.enable_skills` or `message.force_skills`.

For `/edit`, the bot will create a file record with `POST /v2/file.upload` using `{"filename":"..."}`, upload the raw image bytes via HTTP PUT to the returned `upload_url`, and pass the returned file ID in a file content block in the media task. The media task is then polled using `GET /v2/task.listMessages` until an attachment is returned.

References: https://open.manus.ai/docs/v2/task.create and https://open.manus.ai/docs/v2/file.upload
