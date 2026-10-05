#!/usr/bin/env python3
"""Нарезает живое видео для сцен, где оно в кадре.

    python3 cut_clips.py <W> <исходник.mp4> [--cam x,y,w,h]

По W/scenes.json:
  split-cam    → W/clips/cam_NN.mp4  кроп вебки-PiP (--cam, по умолчанию 452,254,1467,3 — правый верхний
                 угол записи экрана), апскейл до 1280×720 lanczos + unsharp. Потом в кадре — карточкой
                 1060×596, НЕ на полкадра: PiP 460px на 960×1080 — мыло.
  split-screen,
  lower        → W/clips/scr_NN.mp4  тот же кусок экрана 1:1.
Время берётся из поля start (секунды исходника), длительность — dur.
Координаты вебки измерь по кадру исходника (ffmpeg -ss 600 -i src -frames:v 1 f.png), не угадывай.
"""
import json, os, subprocess, sys

def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    W, src = os.path.abspath(sys.argv[1]), sys.argv[2]
    w, h, x, y = 452, 254, 1467, 3
    if "--cam" in sys.argv:
        x, y, w, h = map(int, sys.argv[sys.argv.index("--cam") + 1].split(","))
    S = json.load(open(f"{W}/scenes.json"))
    os.makedirs(f"{W}/clips", exist_ok=True)
    for n, v in sorted(S.items()):
        if v["layout"] == "split-cam":
            vf, out = f"crop={w}:{h}:{x}:{y},scale=1280:720:flags=lanczos,unsharp=5:5:0.6,fps=30", f"{W}/clips/cam_{n}.mp4"
        elif v["layout"] in ("split-screen", "lower"):
            vf, out = "fps=30", f"{W}/clips/scr_{n}.mp4"
        else:
            continue
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(v["start"]), "-t", str(v["dur"]), "-i", src, "-an",
                        "-vf", vf, "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p", out], check=True)
        print(os.path.basename(out))

main()
