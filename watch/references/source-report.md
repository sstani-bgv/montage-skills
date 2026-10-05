# Chronological source report

Use this workflow when the user wants a video breakdown saved as a Markdown source record. It is an output format, not a new video-sampling mode.

## Where to save it

Honor an explicit path first. Otherwise inspect the current workspace's instructions and existing `raw` records. If the workspace keeps a knowledge base (for example `wiki/raw/<topic>/` for notes and `wiki/attachments/<source-slug>/` for images), place a topical source note there, link frames with relative paths, and add a short entry to the topic's index or `README.md`. Follow local instructions in any child project instead of assuming the outer workspace layout. Preserve unrelated edits.

Name the note so it can be found by source and topic; use the video's publication date when verified from source metadata, or a clearly labeled capture date when it is not. The note is evidence from the video, not an automatically approved durable wiki claim.

## Review before writing

1. Review the full timestamped transcript and **every** contact sheet in order. Use the video itself to distinguish what is spoken from what is shown. For a long video, inspect sparse or ambiguous regions more closely using the downloaded local file; do not infer missing sections from a few representative frames.
2. Build a chronological outline of all **substantive** scenes: new argument, demonstration, example, story, visual change that affects meaning, sponsor segment, and final call to action. Repeated camera angles or nearly identical talking-head shots need one description, not one section per cut.
3. Read captions critically. Auto-captions can duplicate, omit, or mishear words and names. Verify important labels and on-screen text against a clear frame. Attribute numbers, anecdotes, benefits, and causal claims to the speaker unless separately checked.

## Three-frame illustrated format

For illustrated source reports, use **three screenshots by default**. An explicit user count or different visual requirement takes precedence. Pick frames that let a future reader remember this *particular* video while also seeing its most useful visual evidence:

- Usually one opening or distinctive person/location shot to anchor the video's look.
- Two different high-information shots such as a completed diagram, interface, before/after, demonstration, or result.
- If the video has no useful human shot, use three distinct informative frames. Avoid a second shot that repeats the same visual content merely to meet the count.

Contact sheets are for review, not final embeds. Extract the chosen screenshots at readable native or sufficient resolution from the downloaded video, check the exact frame and timestamp, and save only the selected images in the attachment directory. A generated graphic, thumbnail, or contact-sheet tile is not a screenshot from the video. Put each `![descriptive alt](relative/path.jpg)` near the matching passage and add a short caption with the timestamp and why it matters. Do not add more image embeds than the requested count.

## Markdown shape

- Front matter or a short source block: title, creator, URL, publication date if verified, duration, language, transcript provenance, attachment location.
- Opening sentence explaining that this is a sequential text version of the source and how the selected frames were chosen.
- Sections in video order, with timestamps. For each substantive section: what is said, concrete examples or steps, and memorable on-screen action or cutaway when it adds context. Preserve exact original terms for named frameworks and explain them in the report's language.
- Tables for a list, framework, or behavior→fix mapping when that is clearer than prose. Keep promotional invitations and closing CTAs in their actual place in the sequence.
- A compact recap or navigation map only after the full chronological account; avoid repeating every paragraph in it.
- Provenance: source link, caption/transcript type, any saved native subtitle file, and what remains the speaker's unverified assertion. Do not paste a full transcript into the note unless the user asks for verbatim transcription.

Before finishing, check that the whole video is represented in chronological order, each embed resolves to a file, the number of embedded screenshots matches the request, their captions match the actual video timestamps, and the topic index links to the new note. Open the saved Markdown file for the user when the environment supports it.
