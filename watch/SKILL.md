---
name: watch
description: Watch a video from a URL or local file, answer questions about its visual and spoken content, or create a chronological illustrated Markdown source report when the user asks to save a video breakdown.
allowed-tools: Bash, Read, AskUserQuestion
license: MIT
---

# /watch

You don't have a video input; this skill gives you one. A Python script gets captions first, optionally downloads the video, extracts frames as JPEGs (scene-aware, or fast keyframes at `efficient` detail), gets a timestamped transcript (native captions first, then Whisper API as fallback), and packs the selected frames into chronological contact sheets. Read the sheets, open individual frames only when needed, and combine the visuals with the transcript to answer the user. When the question is specifically about the opening hook, pass `--hook` for a separate 15 fps visual microscope over the first 15 seconds.

## Resolve `SKILL_DIR` (do this before any command)

Every `python3 ...` command below runs a bundled script under `SKILL_DIR/scripts/`. Set `SKILL_DIR` to the **absolute path of the directory containing THIS SKILL.md you just Read** — your harness told you that path in the Read result. The scripts are always a direct sibling of this file (`SKILL_DIR/scripts/watch.py`), in every install layout:

```
Read ~/.claude/plugins/cache/claude-video/watch/<ver>/skills/watch/SKILL.md → SKILL_DIR=…/skills/watch
Read ~/.codex/skills/watch/SKILL.md                                          → SKILL_DIR=~/.codex/skills/watch
Read ~/.agents/skills/watch/SKILL.md                                         → SKILL_DIR=~/.agents/skills/watch
```

Substitute that literal path for `${SKILL_DIR}` in every command. This works on every harness (Claude Code, Codex, Cursor, Gemini CLI, …) without relying on any harness-specific environment variable. Guard once at the start of a run:

```bash
SKILL_DIR="<absolute path of the directory containing the SKILL.md you Read>"
if [ ! -f "$SKILL_DIR/scripts/watch.py" ]; then
  echo "ERROR: scripts/watch.py not found under SKILL_DIR=$SKILL_DIR" >&2
  echo "Re-check the directory of the SKILL.md you Read and substitute it as SKILL_DIR." >&2
  exit 1
fi
```

## Step 0 — Setup preflight (runs every `/watch` invocation, silent on success)

On a genuine first run, read `${SKILL_DIR}/SETUP.md` completely and follow its Russian onboarding. It is the canonical installation guide for dependencies, transcription, optional Apify MCP access, and host-aware vidIQ connection.

**Python interpreter:** every `python3 ...` command in this skill is for macOS/Linux. On **Windows**, substitute `python` — the `python3` command on Windows is the Microsoft Store stub and will not run the script.

On the first `/watch` invocation in a session, use structured preflight so you can detect first-run setup:

```bash
python3 "${SKILL_DIR}/scripts/setup.py" --json
```

Branch on two fields:

- **`can_proceed: true` and `first_run: false`** → setup is already done (the user may have deliberately skipped a Whisper key — that's allowed). Proceed to Step 1 without comment.
- **`first_run: true`** → genuine first-time setup. Do these in order:
  1. If `missing_binaries` is non-empty, run the installer first (it auto-installs on macOS / prints commands elsewhere — see below) and confirm the binaries land. **Do not skip this and jump to preferences.**
  2. Run the installer once more if needed so it scaffolds `~/.config/watch/.env` (it only writes the template when the file is absent, so let it create the file *before* you write any values into it).
  3. Ask which transcription mode the user wants: Groq cloud, local whisper.cpp, or captions-only. Configure it, offer Apify MCP, connect vidIQ through the current host's supported adapter, then set `SETUP_COMPLETE=true`. Do not ask for or persist a default detail mode during setup; detail is selected for each new video.
- **`can_proceed: false` and `first_run: false`** → setup was finished before but the environment regressed (e.g. `missing_binaries` after an OS change). Run the installer to remediate, then proceed. Don't re-ask preferences.

A missing Whisper key is *encouraged to fix, not required*: on a genuine first run `status` will read `needs_key` even when binaries are present — that's your cue to encourage a key, not a blocker.

On follow-up `/watch` calls in the same session, use the silent check:

```bash
python3 "${SKILL_DIR}/scripts/setup.py" --check
```

This is a <100ms lookup. Exit 0 means /watch can run — this **includes a user who finished setup without a Whisper key** (keyless is allowed). On exit 0 the script emits **nothing** — proceed to Step 1 without comment. **Do NOT announce "setup is complete" to the user** — they don't need a status message on every turn. The only acceptable user-visible output from Step 0 is when remediation is required.

On non-zero exit, follow the table:

| Exit | Meaning | Action |
|------|---------|--------|
| `2` | Missing binaries (`ffmpeg` / `ffprobe` / `yt-dlp`) | Run installer |
| `3` | Genuine first run with no Whisper API key | Run installer to scaffold `.env`, then encourage a key (the user may decline — proceed with `--no-whisper`) |
| `4` | Both missing | Run installer, then encourage a key |

Exit `3` only fires before the user has completed setup. Once `SETUP_COMPLETE=true` is written, a keyless install returns exit 0 and is never nagged again.

The installer is idempotent — safe to re-run:

```bash
python3 "${SKILL_DIR}/scripts/setup.py"
```

On macOS with Homebrew, it auto-installs `ffmpeg` and `yt-dlp`. On Linux/Windows, it prints the exact install commands for the user to run. It scaffolds `~/.config/watch/.env` with commented placeholders and default watch settings at `0600` perms.

**If transcription is not configured after install:** follow `${SKILL_DIR}/SETUP.md`. Groq is the recommended cloud option; local whisper.cpp keeps audio on the machine; captions-only requires no key. Never repeat a pasted secret in chat or place it in a repository/command argument. Store cloud keys only in `~/.config/watch/.env` with mode `0600`.

**Detail is not a first-run preference.** Never save the user's detail choice as part of onboarding. Ask for it immediately before every new video as described in Step 1.

**Structured mode (optional):** `python3 "${SKILL_DIR}/scripts/setup.py" --json` emits `{status, can_proceed, first_run, setup_complete, missing_binaries, whisper_backend, has_api_key, config_file, watch_detail, platform}` where `status` is one of `ready | needs_install | needs_key | needs_install_and_key`. `status` describes the *ideal* state (a key is encouraged, so a keyless first run reads `needs_key`); `can_proceed` is the operational gate (binaries present AND a key is set OR setup was already completed). Branch on `can_proceed`/`first_run` to decide whether to run; use `status` to decide what to encourage.

Within a single session, you can skip Step 0 on follow-up `/watch` calls — once `--check` returned 0, nothing about the environment changes between turns.

## When to use

- User pastes a video URL (YouTube, Vimeo, X, TikTok, Twitch clip, most yt-dlp-supported sites) and asks about it.
- User points at a local video file (`.mp4`, `.mov`, `.mkv`, `.webm`, etc.) and asks about it.
- User types `/watch <url-or-path> [question]`.

## Choose the output

**Chat answer** is the default for a question or a simple request to summarize a video. **Source report** is a separate output workflow when the user asks to save a breakdown as Markdown, put it in `raw`, make a text version with pictures, or create a document they can later use to recall the whole video. This output choice is separate from the script's `--detail` sampling mode; do not add an `--detail` value or script flag for it.

For a source report, read [references/source-report.md](references/source-report.md) and create the file and selected screenshots after reviewing the full transcript and visual sequence. The user may override its location, depth, and image count. Do not switch an ordinary chat answer into a saved document just because it used `watch`.

## Recommended limits

- **Best accuracy: videos under 10 minutes.** Frame coverage scales inversely with duration.
- **Universal rate cap: 2 fps.** The script never samples faster than 2 fps, even when a budget or `--fps` would imply more.
- **The frame ceiling is set by the detail mode** (`WATCH_DETAIL` in `~/.config/watch/.env`, or `--detail`), not a single global cap:
  - `transcript` → no frames
  - `efficient` → up to **50** (keyframes)
  - `balanced` (default) → up to **100** (scene-aware)
  - `token-burner` → **uncapped** (scene-aware; a soft warning prints past 250 frames)
  - `--max-frames N` overrides whichever cap the mode would otherwise use.
- **Full-video frame budget by duration.** Token cost grows with frame count, so the script targets a budget by duration. This budget sets the fps and the uniform-sampling fallback; scene-aware selection can fill up to the detail cap above, whichever is lower:
  - ≤30s → ~12-30 frames
  - 30s-1min → ~40 frames
  - 1-3min → ~60 frames
  - 3-10min → ~80 frames
  - \>10min → up to the detail cap, sparsely spaced (warning printed)
- If the user hands you a long video, consider asking whether they want a specific section before burning tokens on a sparse scan.

## How to invoke

**Step 1 — parse the user input, choose the output, and choose detail.** Separate the video source (URL or path) from any question the user asked. Example: `/watch https://youtu.be/abc what language is this in?` → source = `https://youtu.be/abc`, question = `what language is this in?`.

For a **chat answer**, before processing a new video source, ask the user which detail mode to use unless they already named one. Present the choices from lightest to heaviest:

- `transcript` — transcript only, no frames.
- `efficient` — fast keyframe pass, up to 50 frames.
- `balanced` (recommended) — scene-aware pass, up to 100 frames.
- `token-burner` — scene-aware and uncapped; highest token cost.

For a **source report with pictures**, use `balanced` unless the user specifies a detail mode. The requested deliverable already requires a visual pass; asking for a sampling preference should not delay it. For a source report without pictures, choose the lightest mode that can satisfy the request. On a long video, use the full transcript and contact sheets for the sequence, then inspect unclear sections or exact candidate screenshots with focused local extraction; the 100-frame cap is not a reason to omit sections.

Do not silently reuse a chat-answer detail choice from an earlier video or treat `WATCH_DETAIL` as user consent for this run. If the user declines or skips the chat-answer question, use `balanced` and say that you used the recommended fallback. If the user sends several new videos in one chat-answer request, obtain a detail choice for each source; one answer may cover all only when the user explicitly says to use the same mode for the batch. Do not ask again for follow-up questions about a video already processed in the current session, because those should reuse the existing frames and transcript.

**Step 2 — run the watch script.** Pass the source verbatim. Do not shell-escape it yourself beyond normal quoting:

```bash
python3 "${SKILL_DIR}/scripts/watch.py" "<source>"
```

Optional flags:
- `--detail transcript|efficient|balanced|token-burner` — fidelity/speed dial. `transcript` = no frames (transcript only, skips video download when captions exist); `efficient` = fast keyframes (cap 50); `balanced` = scene-aware frames (cap 100); `token-burner` = scene-aware, uncapped.
- `--start T` / `--end T` — focus on a section. Accepts `SS`, `MM:SS`, or `HH:MM:SS`. When either is set, fps auto-scales denser (see "Focusing on a section" below).
- `--timestamps T1,T2,…` — grab a frame at each of these absolute timestamps (`SS`, `MM:SS`, or `HH:MM:SS`). Use this after reading the transcript to capture deictic moments the presenter flags ("look here", "as you can see", "notice this") that visual selection alone may miss. See "Transcript-cue frames" below.
- `--max-frames N` — override the preset cap for tighter token budget (e.g. `--max-frames 40`)
- `--resolution W` — change frame width in px (default 512; bump to 1024 only if the user needs to read on-screen text)
- `--fps F` — override auto-fps (clamped to 2 fps max)
- `--out-dir DIR` — keep working files somewhere specific (default: an auto-generated tmp dir)
- `--whisper groq|openai|local` — force a Whisper backend (default: use `WATCH_WHISPER`, otherwise prefer Groq if a key exists)
- `--no-whisper` — disable the Whisper fallback entirely (frames-only if no captions)
- `--no-dedup` — keep near-duplicate frames. By default a frame-delta pass drops frames that are visually near-identical to the previous kept one (held slides, static screen recordings, paused video) so the frame budget goes to distinct content; the report's **Frames** line notes how many were dropped. Pass this only if the user needs every sampled frame (e.g. judging subtle frame-to-frame motion).
- `--no-contact-sheets` — list and read individual JPEGs instead of the default 3x2 chronological contact sheets.
- `--hook` — additionally sample the first 15 seconds at 15 fps and pack the result into 5x9 contact sheets. Use only when the user asks about the hook, opening, retention, or sub-second visual timing.

### Focusing on a section (higher frame rate)

When the user asks about a specific moment — "what happens at the 2 minute mark?", "zoom into 0:45 to 1:00", "the first 10 seconds" — pass `--start` and/or `--end`. The script switches to focused-mode budgets, which are denser than full-video budgets (still capped at 2 fps, and still bounded by the detail-mode cap — the counts below assume the default `balanced` cap of 100; `efficient` tops out at 50):

- ≤5s → 2 fps (up to 10 frames)
- 5-15s → 2 fps (up to 30 frames)
- 15-30s → ~2 fps (up to 60 frames)
- 30-60s → ~1.3 fps (up to 80 frames)
- 60-180s → ~0.6 fps (100 frames, capped)

Focused mode is the right call for:
- Any moment/range the user names explicitly ("around 2:30", "the intro", "the last 30 seconds").
- Any video longer than ~10 minutes where the user's question is about a specific part — running focused on the relevant section is far more useful than a sparse scan of the whole thing.
- Re-runs after a full scan didn't have enough detail in some region.

Transcript is auto-filtered to the same range. Frame timestamps are absolute (real video timeline, not offset-from-start).

Examples:
```bash
# Last 10 seconds of a 1 minute video
python3 "${SKILL_DIR}/scripts/watch.py" video.mp4 --start 50 --end 60

# Zoom into 2:15 → 2:45 at 2 fps (60 frames)
python3 "${SKILL_DIR}/scripts/watch.py" "$URL" --start 2:15 --end 2:45 --fps 2

# From 1h12m to the end of the video
python3 "${SKILL_DIR}/scripts/watch.py" "$URL" --start 1:12:00
```

**Step 3 — Read every contact sheet path the script lists.** Tiles run left-to-right, top-to-bottom, and their timestamps are printed in the same order. Contact sheets are the default review surface because they preserve sequence at a much lower image-token cost. Open an individual source frame only when small text or a subtle change needs closer inspection. If `--no-contact-sheets` was explicitly used, read the listed JPEGs instead. When `--hook` was used, read the hook sheets first and align their sub-second changes with the opening transcript.

**Step 4 — produce the chosen output.** You now have two streams of evidence:
- **Frames** — what's on screen at each timestamp
- **Transcript** — what's said at each timestamp. The report's header shows the source (`captions` = yt-dlp pulled native subs; `whisper (groq)` / `whisper (openai)` = cloud transcription; `whisper (local)` = local whisper.cpp).

For a chat answer, respond to the specific question with timestamps, or summarize structure, key moments, notable visuals, and spoken content when no question was asked. For a source report, follow [references/source-report.md](references/source-report.md) and give the user a link to the saved Markdown file.

This holds for `transcript` detail too: even with no frames, produce a **summary** like the other modes — do not paste the full transcript into chat. Synthesize structure, key moments, and spoken content with timestamps; quote only the lines that matter. Offer the raw transcript only if the user explicitly asks for it.

**Step 5 — clean up.** The script prints a working directory at the end. For a source report, first verify that the Markdown, chosen screenshots, and any source transcript the user needs were saved outside the temporary work directory. Keep working files while they may be useful for follow-ups; otherwise clean up the exact temporary directory.

## Detail and frames

For chat answers, the agent asks for detail on each new video; source reports use the selection above. The CLI still supports a fallback default for non-agent/manual use:

- `WATCH_DETAIL=transcript|efficient|balanced|token-burner` (manual CLI fallback; default: `balanced`)

At `transcript` detail, captions are enough to return a report without downloading video. If captions are missing, the script downloads audio only and tries Whisper. If no transcript can be produced, it reports the limitation clearly; re-run with `--detail balanced` for frames.

At `efficient` detail, the script downloads the video and extracts **keyframes only** (`ffmpeg -skip_frame nokey`) — a near-instant pass that lands frames on scene cuts. If a clip has fewer than 4 keyframes it falls back to uniform sampling.

At `balanced` / `token-burner` detail, the script extracts **scene-aware** frames: ffmpeg scene-change selection first, falling back to uniform sampling only when the video is effectively static. `balanced` caps at 100 frames; `token-burner` is uncapped. Frame report lines include both timestamp and selection reason. Extracted images are clamped to a maximum 1998px height for Claude Read compatibility.

Selected frames are packed into 3x2 contact sheets by default. The original JPEGs remain in the work directory for close inspection. Pass `--no-contact-sheets` only when the task requires inspecting every frame at full resolution.

## Opening-hook microscope

Use `--hook` when the user asks about the hook, first seconds, retention mechanics, or exact visual-to-spoken timing. It samples up to the first 15 seconds at 15 fps and packs 45 chronological frames into each 5x9 sheet. This budget is separate from the normal detail-mode frame cap. Do not enable it for ordinary summaries: the normal scene/keyframe pass is enough.

## Transcript-cue frames

Visual frame selection (scene/keyframe) can miss the moments a presenter explicitly flags — "look here", "as you can see", "notice this", "watch what happens" — because pointing at a slide is often a *low* visual change. `--timestamps` lets you force a frame at those exact moments. **You** decide which moments matter, by reading the transcript:

1. Run once at `--detail transcript` (or any detail) to get the timestamped transcript.
2. Scan it for deictic cues — phrases where the speaker directs attention to something on screen. This is a judgment call (ignore rhetorical "look, the point is…"); that's why it's done by you, not a regex.
3. Re-run with `--timestamps 4:32,7:10,9:55` (absolute source times). For a URL, point the second run at the **downloaded local file** in the work dir so it doesn't re-download.

Behavior:
- **Additive by default.** Cue frames (`reason=transcript-cue`) are merged into whatever `--detail` already selected, in chronological order.
- **Pinned and counted first.** Cue frames are reserved against the frame cap before the detail engine runs, so they're never evicted by even-sampling.
- **Honors focus mode.** With `--start/--end`, any cue timestamp outside the window is dropped (reported in the summary). Coordinates are always absolute source time.
- **Cue-only frames.** `--detail transcript --timestamps …` skips scene/keyframe sampling and returns *only* the cue frames (it will download the video to do so, since frames need pixels).

## Transcription

The script gets a timestamped transcript from native captions or a configured Whisper backend:

1. **Native captions (free, preferred).** yt-dlp pulls manual or auto-generated subtitles from the source platform if available.
2. **Groq/OpenAI fallback.** If no captions came back (or the source is a local file), the script extracts audio (`ffmpeg -vn -ac 1 -ar 16000 -b:a 64k`, ~0.5 MB/min) and uploads it to the configured API:
   - **Groq** — `whisper-large-v3`. Preferred default: cheaper, faster. Get a key at console.groq.com/keys.
   - **OpenAI** — `whisper-1`. Fallback. Get a key at platform.openai.com/api-keys.
3. **Local whisper.cpp.** With `WATCH_WHISPER=local`, the script converts audio to 16 kHz PCM WAV and runs `whisper-cli` with the configured GGML model. Audio stays on the machine.

Configuration lives in `~/.config/watch/.env`. Use `--whisper groq|openai|local` to force a configured backend, or `--no-whisper` to skip the fallback entirely.

## Failure modes and handling

- **Setup preflight failed** → read `${SKILL_DIR}/SETUP.md`, then run `python3 "${SKILL_DIR}/scripts/setup.py"` and configure the user's chosen transcription mode.
- **No transcript available** → captions are missing and the configured Whisper backend is unavailable or failed. Proceed frames-only and tell the user.
- **Long video warning printed** → acknowledge it in your answer. Offer to re-run focused on a specific section via `--start`/`--end` rather than a sparse full-video scan.
- **Download fails** → yt-dlp's error goes to stderr. For Instagram/TikTok, if Apify MCP is connected, offer its fallback route from `SETUP.md`. For login-required or region-locked video, tell the user plainly; do not keep retrying.
- **Cloud Whisper request fails** → the error is printed to stderr (likely: invalid key or rate limit). Audio over the API's 25 MB upload cap is split into chunks automatically. You can retry another configured backend.
- **Local Whisper fails** → verify `whisper-cli` and `WHISPER_LOCAL_MODEL`, then follow `SETUP.md`; do not silently switch to cloud because that changes where audio is processed.

## Token efficiency

This skill burns tokens primarily on frames. Contact sheets substantially reduce the cost of reviewing many selected frames while preserving sequence:
- Read the 3x2 body sheets instead of every source JPEG.
- Read the 5x9 hook sheets instead of all 225 dense hook JPEGs.
- The transcript is cheap (a few thousand tokens at most for a 10-minute video).
- Bumping `--resolution` to 1024 roughly quadruples the image tokens per frame. Only do it when necessary.

If you already watched a video this session and the user asks a follow-up, do **not** re-run the script — you already have the frames and transcript in context. Just answer from what you have.

## Security & Permissions

**What this skill does:**
- Runs `yt-dlp` locally to download the video and pull native captions when the source supports them (public data; the request goes directly to whatever host the URL points at)
- Runs `ffmpeg` / `ffprobe` locally to extract frames as JPEGs and, when Whisper is needed, a mono 16 kHz audio clip
- Runs local `whisper-cli` when `WATCH_WHISPER=local`; in this mode no audio is sent to a transcription service
- Sends the extracted audio clip to Groq's Whisper API (`api.groq.com/openai/v1/audio/transcriptions`) when `GROQ_API_KEY` is set (preferred — cheaper, faster)
- Sends the extracted audio clip to OpenAI's audio transcription API (`api.openai.com/v1/audio/transcriptions`) when `OPENAI_API_KEY` is set and Groq is not, or when `--whisper openai` is forced
- May use the separately connected Apify MCP as a user-approved fallback for Instagram/TikTok retrieval after yt-dlp fails; Actor runs can consume Apify credits
- Writes the downloaded video, frames, audio, and an intermediate transcript to a working directory under the system temp dir (or `--out-dir` if specified) so Claude can `Read` them
- Reads / creates `~/.config/watch/.env` (mode `0600`) to store the Whisper API key(s) and a `SETUP_COMPLETE` marker. As a fallback, also reads `.env` in the current working directory

**What this skill does NOT do:**
- Does not upload the video itself to any API — only the extracted audio goes out, and only when native captions are missing AND Whisper is not disabled with `--no-whisper`
- Does not access any platform account (no login, no session cookies, no posting) — yt-dlp only ever requests public data
- Does not share API keys between providers (Groq key only goes to `api.groq.com`, OpenAI key only goes to `api.openai.com`)
- Does not log, cache, or write API keys to stdout, stderr, or output files
- The bundled script does not persist anything outside the working directory and `~/.config/watch/.env`; a requested source report separately saves the reviewed Markdown and chosen assets in the user's destination

**Bundled scripts:** `scripts/watch.py` (entry point), `scripts/download.py` (yt-dlp wrapper), `scripts/frames.py` (ffmpeg frame extraction and contact sheets), `scripts/hook.py` (15 fps opening microscope), `scripts/transcribe.py` (caption selection + Whisper orchestration), `scripts/whisper.py` (Groq / OpenAI / local whisper.cpp), `scripts/setup.py` (preflight + installer)

Review scripts before first use to verify behavior.
