#!/usr/bin/env python3
"""Черновая сборка: исходники встык, вставки поверх кадра по таймкодам.

Вставка ЗАМЕЩАЕТ картинку на своей длительности, звук исходника идёт непрерывно —
обычное поведение b-roll. Склейки жёсткие, без переходов (так в референсном стиле).
Хронометраж не меняется.

Ожидаемая раскладка:
    <корень>/<тег>/out/NN_MM-SS-D_имя.mp4   вставки, таймкод в имени файла
    исходники передаются через --source <тег>=<путь>

Пример:
    python3 assemble.py --root ~/Desktop/montage/memory-video \\
        --source m1=~/Desktop/memory_1.mp4 \\
        --source m2=~/Desktop/memory_2.mp4 \\
        --anchor 1245,690,650,345

`--anchor x,y,w,h` — прямоугольник сквозного якоря (камера-PiP) в исходнике.
Если задан, эта область накладывается ПОВЕРХ каждой вставки, и говорящий не пропадает
из кадра. Без него вставки перекрывают кадр целиком — см. закон 16 в style-rules.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

TC = re.compile(r"^(\d+)_(\d+)-(\d+)-(\d+)_")


def probe_json(path, entries):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def duration(path):
    return float(probe_json(path, "format=duration")["format"]["duration"])


def inserts_for(root, tag):
    """Вставки одного ролика: (старт в секундах, длительность, путь)."""
    items = []
    for f in sorted((root / tag / "out").glob("*.mp4")):
        m = TC.match(f.name)
        if not m:
            sys.exit(f"не разобрал таймкод из имени: {f.name}")
        _, mm, ss, tenth = m.groups()
        items.append((int(mm) * 60 + int(ss) + int(tenth) / 10, duration(f), f))
    return items


def build_pass(root, tag, src, dst, anchor, crf, preset):
    ins = inserts_for(root, tag)
    print(f"{tag}: исходник {duration(src):.1f}с, вставок {len(ins)}")

    cmd = ["ffmpeg", "-y", "-i", str(src)]
    for _, _, f in ins:
        cmd += ["-i", str(f)]

    # приводим к ровным 1920×1080 обрезкой по центру — без ресемплинга
    chain = ["[0:v]crop=1920:1080:(iw-1920)/2:(ih-1080)/2,fps=30,format=yuv420p,split=2[base][anchorsrc]"
             if anchor else
             "[0:v]crop=1920:1080:(iw-1920)/2:(ih-1080)/2,fps=30,format=yuv420p[base]"]
    prev = "base"

    for i, (start, dur, _) in enumerate(ins, start=1):
        chain.append(f"[{i}:v]setpts=PTS-STARTPTS+{start}/TB,format=yuv420p[i{i}]")
        chain.append(
            f"[{prev}][i{i}]overlay=eof_action=pass:"
            f"enable='between(t,{start},{start + dur})'[b{i}]")
        prev = f"b{i}"

    if anchor:
        ax, ay, aw, ah = anchor
        # якорь вырезается из исходника и кладётся поверх ВСЕХ вставок разом
        chain.append(f"[anchorsrc]crop={aw}:{ah}:{ax}:{ay}[anch]")
        windows = "+".join(f"between(t,{s},{s + d})" for s, d, _ in ins)
        chain.append(f"[{prev}][anch]overlay={ax}:{ay}:enable='{windows}'[outv]")
        prev = "outv"

    cmd += [
        "-filter_complex", ";".join(chain),
        "-map", f"[{prev}]", "-map", "0:a",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(dst),
    ]
    subprocess.run(cmd, check=True)
    print(f"{tag}: готово → {dst.name} ({duration(dst):.1f}с)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=pathlib.Path)
    ap.add_argument("--source", action="append", required=True,
                    help="тег=путь, порядок задаёт порядок склейки")
    ap.add_argument("--anchor", help="x,y,w,h сквозного якоря в исходнике")
    ap.add_argument("--out", default="final.mp4")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--preset", default="fast")
    a = ap.parse_args()

    root = a.root.expanduser()
    anchor = tuple(int(v) for v in a.anchor.split(",")) if a.anchor else None
    if anchor and len(anchor) != 4:
        sys.exit("--anchor ждёт четыре числа: x,y,w,h")

    out = root / "out"
    out.mkdir(exist_ok=True)
    parts = []
    for pair in a.source:
        tag, _, path = pair.partition("=")
        src = pathlib.Path(path).expanduser()
        if not src.exists():
            sys.exit(f"нет исходника: {src}")
        dst = out / f"{tag}_with_inserts.mp4"
        build_pass(root, tag, src, dst, anchor, a.crf, a.preset)
        parts.append(dst)

    if len(parts) == 1:
        final = parts[0].rename(out / a.out)
    else:
        listfile = out / "concat.txt"
        listfile.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        final = out / a.out
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
             "-c", "copy", "-movflags", "+faststart", str(final)],
            check=True, cwd=out)

    print(f"\nфинал: {final}")
    print(f"длительность: {duration(final):.1f}с, размер: {final.stat().st_size/1024/1024:.0f} МБ")
    if not anchor:
        print("\nЯкорь не задан: на вставках говорящий пропадает из кадра. "
              "Если в исходнике есть камера-PiP, передай --anchor x,y,w,h.")


if __name__ == "__main__":
    main()
