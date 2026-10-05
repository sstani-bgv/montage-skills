#!/usr/bin/env python3
"""Сборка стиля v4: вставки со звуком + черновик всего ролика.

    python3 assemble_v4.py <W> <исходник.mp4> [--intro intro.mp4 --cut SEC] [--scenes 02,03] [--only-inserts]

1. Каждая вставка: W/NN/renders/NN.mp4 + SFX из W/scenes/NN.sfx.json ([[t,"имя"],…]) →
   W/out/NN_ММ-СС-Д_слаг.mp4 (только SFX на дорожке — голос остаётся с исходника на таймлайне).
2. Черновик W/out/draft.mp4: база + вставки поверх по таймкодам, звук = голос базы + SFX.

--intro/--cut: автор записал отдельное интро — база = intro.mp4 + исходник с секунды SEC
(старый тизер вырезается). Сцена "00" в scenes.json — графика поверх интро (tstart 0),
остальные сдвигаются: tstart = start − SEC + длительность интро. База сохраняется в W/base.mp4.

Уровни SFX под тихий голос стрима (пик голоса ≈ −15 dBFS). stamp/neg — из assets/style-v4/sfx
скилла motion-build (слой из реальных сэмплов; синтез из синусов автор забраковал), остальное —
бандл media-use.
"""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
V4SFX = os.path.normpath(os.path.join(HERE, "../../motion-build/assets/style-v4/sfx"))
BUNDLE = os.path.expanduser("~/.claude/skills/media-use/audio/assets/sfx")
G = {"whoosh": -24, "whoosh-short": -24, "pop": -22, "click": -20, "click-soft": -21, "ping": -21,
     "stamp": -19, "neg": -20, "typing": -16, "key-press": -19}
sfx_path = lambda n: f"{V4SFX}/{n}.wav" if n in ("stamp", "neg") else f"{BUNDLE}/{n}.mp3"
run = lambda a: subprocess.run(["ffmpeg", "-v", "error", "-y"] + a, check=True)
dur_of = lambda p: float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                                        capture_output=True, text=True).stdout)

def slug(t):
    t = re.sub(r"[^\w\s-]", "", t.lower()).strip()
    tr = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "abvgdeejziiklmnoprstufhccss_y_eua")
    return re.sub(r"\s+", "-", t.translate(tr))[:40]

def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

def insert(W, n, v):
    r = f"{W}/{n}/renders/{n}.mp4"
    if not os.path.exists(r):
        print("нет рендера", n); return None
    sj = f"{W}/scenes/{n}.sfx.json"
    cues = [c for c in json.load(open(sj)) if c[1] in G] if os.path.exists(sj) else []
    st = v.get("tstart", v["start"]); mm, ss = int(st // 60), st % 60
    out = f"{W}/out/{n}_{mm:02d}-{int(ss):02d}-{int(round((ss % 1) * 10)) % 10}_{slug(v['title'])}.mp4"
    d = dur_of(r)
    ins, fl = [], []
    for i, (t, name) in enumerate(cues):
        ins += ["-i", sfx_path(name)]
        fl.append(f"[{i+1}:a]volume={G[name]}dB,adelay={int(t*1000)}:all=1,aresample=48000,aformat=channel_layouts=stereo[c{i}]")
    fc = (";".join(fl) + ";" + "".join(f"[c{i}]" for i in range(len(cues))) +
          f"amix=inputs={len(cues)}:normalize=0:duration=longest,apad,atrim=0:{d}[a]") if cues else \
         f"anullsrc=r=48000:cl=stereo,atrim=0:{d}[a]"
    run(["-i", r] + ins + ["-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", out])
    return (st, d, out)

def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    W, src = os.path.abspath(sys.argv[1]), sys.argv[2]
    S = json.load(open(f"{W}/scenes.json"))
    os.makedirs(f"{W}/out", exist_ok=True)
    intro, cut = arg("--intro"), arg("--cut")
    if intro:
        cut = float(cut); idur = dur_of(intro)
        run(["-i", intro, "-ss", str(cut), "-i", src, "-filter_complex",
             "[0:v]scale=1920:-2,crop=1920:1080,fps=30,format=yuv420p,setsar=1[v0];"
             f"[0:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{idur},asetpts=PTS-STARTPTS[a0];"
             "[1:v]fps=30,format=yuv420p,setsar=1[v1];[1:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
             "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-c:a", "aac", "-b:a", "192k", f"{W}/base.mp4"])
        for n, v in S.items():
            v["tstart"] = 0.0 if n == "00" else round(v["start"] - cut + idur, 3)
        json.dump(S, open(f"{W}/scenes.json", "w"), ensure_ascii=False, indent=1)
        src = f"{W}/base.mp4"
    only = arg("--scenes"); only = only.split(",") if only else None
    items = [x for n, v in sorted(S.items()) if (only is None or n in only) for x in [insert(W, n, v)] if x]
    print("вставок:", len(items))
    if "--only-inserts" in sys.argv:
        return
    cmd = ["-i", src] + sum([["-i", f] for _, _, f in items], [])
    ch, prev = ["[0:v]fps=30,format=yuv420p[b0]"], "b0"
    for i, (st, d, _) in enumerate(items, 1):
        ch.append(f"[{i}:v]setpts=PTS-STARTPTS+{st}/TB,format=yuv420p[i{i}]")
        ch.append(f"[{prev}][i{i}]overlay=eof_action=pass:enable='between(t,{st},{st+d})'[b{i}]"); prev = f"b{i}"
    for i, (st, _, _) in enumerate(items, 1):
        ch.append(f"[{i}:a]adelay={int(st*1000)}:all=1[s{i}]")
    ch.append("[0:a]aresample=48000,aformat=channel_layouts=stereo[vo]")
    ch.append("[vo]" + "".join(f"[s{i}]" for i in range(1, len(items) + 1)) + f"amix=inputs={len(items)+1}:normalize=0:duration=first[a]")
    run(cmd + ["-filter_complex", ";".join(ch), "-map", f"[{prev}]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", f"{W}/out/draft.mp4"])
    print("черновик:", f"{W}/out/draft.mp4")

main()
