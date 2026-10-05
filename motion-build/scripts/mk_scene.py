#!/usr/bin/env python3
"""Собирает проект сцены стиля v4 из фрагмента.

    python3 mk_scene.py <W> NN [NN ...]

<W> — рабочая папка ролика (в /tmp или scratchpad, не в montage/):
    W/scenes.json            {"NN": {"start", "dur", "layout", "title", ["tstart"]}}
    W/scenes/NN.html         фрагмент: <!-- dur=… --> + <style> + <body> + <script>
    W/img/hNN.png            картинка-герой с альфой (scripts/alpha.py)
    W/clips/*_NN.mp4         вебка/экран для split-cam / split-screen / lower (scripts/cut_clips.py)
На выходе W/NN/ — готовый HyperFrames-проект (index.html, assets/). Рендер: `hyperframes render . -o renders/NN.mp4`.
Общие стили и хелперы берутся из assets/style-v4 этого скилла и инлайнятся (линтер требует
регистрацию таймлайна прямо в index.html).
"""
import glob, json, os, re, shutil, sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(SKILL, "assets")
PKG = {"name": "scene", "private": True, "type": "module",
       "scripts": {"check": "npx --yes hyperframes@0.8.22 check", "render": "npx --yes hyperframes@0.8.22 render"}}
HFJ = {"$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
       "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
       "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
       "media": {"autoProxy": True}, "authoringSkill": "motion-graphics"}

def build(W, n):
    frag = open(f"{W}/scenes/{n}.html").read()
    dur = re.search(r"<!--\s*dur=([\d.]+)", frag).group(1)
    style = re.search(r"<style>(.*?)</style>", frag, re.S)
    style = style.group(1) if style else ""
    body = re.search(r"<body>(.*?)</body>", frag, re.S).group(1)
    js = re.search(r"<script>(.*?)</script>", frag, re.S).group(1)
    d = f"{W}/{n}"
    os.makedirs(f"{d}/assets/clips", exist_ok=True)
    shutil.copy(f"{ASSETS}/tokens.css", f"{d}/assets/tokens.css")
    shutil.copytree(f"{ASSETS}/fonts", f"{d}/assets/fonts", dirs_exist_ok=True)
    shutil.copy(f"{ASSETS}/style-v4/common.css", f"{d}/assets/common.css")
    if os.path.exists(f"{W}/img/h{n}.png"):
        shutil.copy(f"{W}/img/h{n}.png", f"{d}/assets/hero.png")
    for c in glob.glob(f"{W}/clips/*_{n}.mp4"):
        shutil.copy(c, f"{d}/assets/clips/")
    json.dump(PKG, open(f"{d}/package.json", "w"), indent=2)
    json.dump(HFJ, open(f"{d}/hyperframes.json", "w"), indent=2)
    json.dump({"id": f"scene-{n}", "name": f"scene-{n}"}, open(f"{d}/meta.json", "w"))
    common = open(f"{ASSETS}/style-v4/common.js").read()
    open(f"{d}/index.html", "w").write(f'''<!doctype html>
<html lang="ru" data-resolution="landscape">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=1920, height=1080" />
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<link rel="stylesheet" href="./assets/tokens.css" />
<link rel="stylesheet" href="./assets/common.css" />
<style>{style}</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{dur}" data-width="1920" data-height="1080">
  <div class="bg"><div class="blob"></div><div class="blob2"></div><div class="paper-grid"></div></div>
  <div id="stage">
{body}
  </div>
</div>
<script>
{common}
{js}</script>
</body>
</html>''')
    print(f"built {n}: dur {dur}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    W = os.path.abspath(sys.argv[1])
    for n in sys.argv[2:]:
        build(W, n)
