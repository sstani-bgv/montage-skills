#!/usr/bin/env python3
"""
Нарезка шортсов из лонга (скринкаст: преза + PiP-вебка + куски фул-бли́д камеры).

Три раскладки — по типу исходного кадра:

  split   преза сверху -> субтитры по центру -> вебка снизу
          (кадр = окно презентации + PiP-вебка в правом нижнем углу)
  cam     фул-бли́д вебка 9:16, субтитры внизу-по-центру (как в референсе)
          (кадр = камера на весь экран)
  broll   экран/слайд во всю ширину на своём размытом фоне, субтитры под ним
          (кадр = презентация на весь экран, вебки нет)
  auto    режет отрезок по типам кадра и склеивает — каждый под-шот своей раскладкой

Хук — «плашка» в верхнем слоте: в split подменяет карточку презентации,
в cam/broll лежит поверх видео в верхней трети. Ключевое слово в *звёздочках*
подсвечивается жёлтым маркером дизайн-системы.

Субтитры и хук рисуются PIL'ом в слои с альфой (у локального ffmpeg нет ни
libass, ни drawtext), потом накладываются overlay'ем.

Геометрия исходника (где окно экрана, где PiP) не зашита в код — она приходит
из calib.json, который делает shorts-plan/scripts/calibrate.py. Обычная точка
входа — render_all.py по плану; напрямую этот файл нужен для разовой проверки.

  uv run --with pillow --with numpy --with 'opencv-python-headless<5' python3 \
      make_short.py --calib /tmp/shorts/calib.json --src ~/Desktop/video.mov \
      --words /tmp/shorts/transcript/raw.words.json \
      --start 136.3 --end 165.9 --layout split \
      --hook "Одна задача —|*одна сессия*" --out /tmp/shorts/renders/s01.mp4
"""
import argparse, json, os, re, subprocess

import frames
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, os.pardir, "assets", "fonts")
FONT_BLACK = os.path.join(FONT_DIR, "Onest-Black.ttf")
FONT_XBOLD = os.path.join(FONT_DIR, "Onest-ExtraBold.ttf")

# ---- канвас и токены дизайн-системы -----------------------------------------
W, H = 1080, 1920
INK = (20, 22, 26)
PAPER = (246, 247, 249)
MARK = (255, 224, 74)

CARD_W = 1000
CARD_X = (W - CARD_W) // 2
RADIUS = 28

# split
TOP_Y, TOP_H = 160, 563                      # карточка презентации 16:9
BOT_Y, BOT_H = 1134, 550                     # карточка вебки (аспект PiP ~1.82)
CAP_Y_SPLIT = (TOP_Y + TOP_H + BOT_Y) // 2   # субтитры в зазоре

# cam / broll
CAP_Y_CAM_DEFAULT = 1240
CAP_Y_CAM = CAP_Y_CAM_DEFAULT              # переопределяется из calib.json
BROLL_Y, BROLL_H = 360, 750                  # экран во всю ширину, кроп 4:3 по контенту
CAP_Y_BROLL = 1290

STRIP_H = 300                                # высота слоя субтитров
FONT_SIZE = 84
POP_MS = 110
POP_FROM = 0.88

# хук
HOOK_DUR = 2.6
HOOK_IN, HOOK_OUT = 0.34, 0.30
HOOK_SIZE = 80
HOOK_MAX_W = 800
HOOK_STRIP_H = 760
HOOK_TOP_CAM = 150
# The b-roll card starts at y=360.  A two-line hook is about 308 px tall,
# so keeping its top at 300 would cover the upper part of the very insert it
# is meant to introduce.  Put it in the clear band above that card.
HOOK_TOP_BROLL = 28

# финальная плашка тизера: та же карточка, что у хука, в том же слоте, но в
# конце шортса — называет вопрос, ответ на который есть только в лонге.
# Появляется и остаётся до последнего кадра: уходить ей некуда.
OUTRO_DUR = 2.4

# ---- геометрия исходника ----------------------------------------------------
# Значения ниже — только заглушки: реальные приходят из calib.json через
# use_calib(). Прибивать их к файлу нельзя, иначе скилл работает ровно на одной
# записи, а на следующей молча режет мимо.
SRC_W, SRC_H = 1920, 1080
SLIDE_CROP = (1856, 1044, 32, 30)            # окно экрана (w, h, x, y)
PIP_CROP   = (538, 296, 1300, 707)           # PiP-вебка, внутрь от скруглений
PATCH      = (722, 501, 1134, 543)           # чем замазываем PiP внутри слайда
PATCH_SRC  = (168, 200)                      # откуда берём фон заплатки
PATCH_FEATHER = 46
CAM_CROP   = (608, 1080, 700, 0)             # фул-бли́д камера -> 9:16
CALIB = None


# Маска замазки рисуется внутрь от края `patch` на PATCH_FEATHER и ещё
# размывается на PATCH_FEATHER/2.2 — непрозрачной она становится только с этой
# глубины. Всё, что ближе к краю, просвечивает.
PATCH_OPAQUE = PATCH_FEATHER + int(PATCH_FEATHER / 2.2) + 1


def fit_patch(patch, pip_outer, slide):
    """Расширить замазку так, чтобы сквозь растушёвку не просвечивала вебка.

    Замазка обязана накрывать `pip_outer` не впритык, а с запасом PATCH_OPAQUE:
    в полупрозрачном поясе по краю маски видно живую комнату — бурой полосой по
    верхней кромке и светлым столбиком слева. Калибровка даёт запас одинаковым
    со всех сторон, но после клампа по краю кадра с какой-то стороны он может
    оказаться нулевым, и поймать это можно только глазами на контактном листе.

    Там, где вебка упирается в край слайда, запаса взять неоткуда — и не надо:
    за краем ничего нет."""
    pw, ph, px, py = patch
    iw, ih, ix, iy = pip_outer
    sw, sh, sx, sy = slide
    ix, iy = ix - sx, iy - sy              # замазка живёт в координатах слайда
    x0 = max(min(px, ix - PATCH_OPAQUE), 0)
    y0 = max(min(py, iy - PATCH_OPAQUE), 0)
    x1 = min(max(px + pw, ix + iw + PATCH_OPAQUE), sw)
    y1 = min(max(py + ph, iy + ih + PATCH_OPAQUE), sh)
    return (x1 - x0, y1 - y0, x0, y0)


def use_calib(calib, over=None):
    """Настроить модуль под конкретный исходник.

    `calib` — путь или словарь. `over` — запись плана: `patch` и `patch_src`
    можно переопределить на отдельный шортс. Это не роскошь: заливка замазки —
    это средний цвет куска кадра из `patch_src`, а экран в разных местах лонга
    разный. Один источник на весь ролик где-то попадает в тон окружения, а
    где-то кладёт светлую кляксу на тёмный интерфейс."""
    global SLIDE_CROP, PIP_CROP, PATCH, PATCH_SRC, CAM_CROP, SRC_W, SRC_H, CALIB
    global CAP_Y_CAM
    if isinstance(calib, str):
        calib = frames.load_calib(calib)
    CALIB = calib
    SRC_W, SRC_H = calib["width"], calib["height"]
    SLIDE_CROP = tuple(calib["screen_crop"])
    PIP_CROP = tuple(calib["pip_crop"])
    over = over or {}
    PATCH = tuple(over.get("patch", calib["patch"]))
    PATCH_SRC = tuple(over.get("patch_src", calib["patch_src"]))
    fitted = fit_patch(PATCH, calib["pip_outer"], SLIDE_CROP)
    if fitted != PATCH:
        print(f"    замазка расширена {PATCH} -> {fitted}: "
              f"вебка просвечивала сквозь растушёвку")
        PATCH = fitted
    # источник заливки должен целиком лежать внутри слайда
    PATCH_SRC = (max(min(PATCH_SRC[0], SLIDE_CROP[0] - PATCH[0]), 0),
                 max(min(PATCH_SRC[1], SLIDE_CROP[1] - PATCH[1]), 0))
    cw = (SRC_H * W // H) & ~1                       # 9:16 из полного кадра
    CAM_CROP = (cw, SRC_H, (SRC_W - cw) // 2 & ~1, 0)
    # Высота полосы субтитров в `cam` — тоже свойство исходника, а не константа.
    # Штатные 1240 (64%) рассчитаны на средний план, где под подбородком есть
    # грудь и кадр. На селфи с руки голова занимает почти всю высоту, и 64%
    # приходятся на губы: реплика ложится поперёк открытого рта. Тогда в
    # calib.json кладут "cap_y_cam" пониже (проверено: 1400 = 73% всё ещё выше
    # зоны интерфейса Shorts).
    CAP_Y_CAM = int(calib.get("cap_y_cam", CAP_Y_CAM_DEFAULT))
    return calib

# ---- фиксы распознавания ----------------------------------------------------
FIXES = [
    (r"\bCloth\.?\s?md\b", "CLAUDE.md"), (r"\bКлод\.?\s?md\b", "CLAUDE.md"),
    (r"\bкоде?\s+MD\b", "CLAUDE.md"), (r"\bCodeMD\b", "CLAUDE.md"),
    (r"\bAgents\.?\s?md\b", "AGENTS.md"), (r"\bAgentsMD\b", "AGENTS.md"),
    (r"\bMemoryMD\b", "MEMORY.md"), (r"\bmemory\.md\b", "MEMORY.md"),
    (r"\bДеронка\b", "нейронка"), (r"\bкодекс\b", "Codex"), (r"\bКодекс\b", "Codex"),
    (r"\bКлод\b", "Claude"), (r"\bклод\b", "Claude"),
    (r"\bхарднесс", "харнесс"),
]


def fix(text):
    for pat, rep in FIXES:
        text = re.sub(pat, rep, text)
    return text


# ---- PNG-заготовки ----------------------------------------------------------
def make_card_pngs(tmp, w, h, tag):
    mask_p = os.path.join(tmp, f"mask_{tag}.png")
    shadow_p = os.path.join(tmp, f"shadow_{tag}.png")
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), RADIUS, fill=255)
    m.save(mask_p)
    pad = 60
    s = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(s).rounded_rectangle(
        (pad, pad + 12, pad + w - 1, pad + h - 1 + 12), RADIUS + 6, fill=(0, 0, 0, 160))
    s.filter(ImageFilter.GaussianBlur(28)).save(shadow_p)
    return mask_p, shadow_p, pad


def make_patch_mask(tmp):
    w, h, _, _ = PATCH
    f = PATCH_FEATHER
    m = Image.new("L", (w, h), 0)
    # Прямоугольник должен лежать ВНУТРИ холста, иначе растушёвка есть только
    # слева и сверху, а справа и снизу остаётся жёсткая кромка — она и режет
    # градиент слайда видимой прямой линией.
    # Talking-head calibrations use a tiny placeholder patch because there is
    # no PiP to cover.  Keep the mask constructor total for that case: the
    # normal feather inset cannot fit inside a 2x2/69x69 placeholder.
    inset = min(f, max((w - 1) // 2, 0), max((h - 1) // 2, 0))
    x0, y0, x1, y1 = inset, inset, w - inset - 1, h - inset - 1
    radius = min(40, max((x1 - x0) // 2, 0), max((y1 - y0) // 2, 0))
    ImageDraw.Draw(m).rounded_rectangle((x0, y0, x1, y1), radius, fill=255)
    m = m.filter(ImageFilter.GaussianBlur(f / 2.2))
    p = os.path.join(tmp, "patch_mask.png")
    m.save(p)
    return p


# ---- субтитры ---------------------------------------------------------------
def group_words(words, max_chars=14, max_words=2, gap_break=0.45):
    cues, cur = [], []
    for w in words:
        txt = w["word"].strip()
        if not txt:
            continue
        if cur:
            joined = " ".join(x["word"].strip() for x in cur) + " " + txt
            gap = w["start"] - cur[-1]["end"]
            # Конец предложения закрывает реплику: иначе выходит «сама. Я» —
            # хвост новой фразы висит на экране вместе с концом старой.
            sent = cur[-1]["word"].strip().rstrip("»\"')").endswith((".", "?", "!", "…"))
            if (len(joined) > max_chars or len(cur) >= max_words
                    or gap > gap_break or sent):
                cues.append(cur); cur = []
        cur.append(w)
    if cur:
        cues.append(cur)
    return cues


def build_cues(words, t0, t1):
    """Реплики субтитров; время — от начала отрезка."""
    sel = [w for w in words if w["end"] > t0 and w["start"] < t1]
    raw = group_words(sel)
    out = []
    for i, cue in enumerate(raw):
        start = max(cue[0]["start"] - t0, 0.0)
        end = min(cue[-1]["end"] - t0, t1 - t0)
        if i + 1 < len(raw):
            nxt = raw[i + 1][0]["start"] - t0
            if nxt - end < 0.5:
                end = min(nxt, t1 - t0)
        else:
            end = min(end + 0.35, t1 - t0)
        txt = fix(" ".join(w["word"].strip() for w in cue)).strip(" ,.!?;:—-")
        if txt and end > start:
            out.append([round(start, 2), round(end, 2), txt])
    return out


def slice_cues(cues, a, b):
    """Реплики, попавшие в [a,b), со временем от a."""
    out = []
    for s, e, t in cues:
        if e <= a or s >= b:
            continue
        out.append([max(s - a, 0.0), min(e, b) - a, t])
    return out


def render_text(txt, font):
    """Белый текст + мягкое тёмное свечение — читается и на светлом, и на тёмном."""
    tmp = Image.new("RGBA", (W * 2, STRIP_H * 2), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((W, STRIP_H), txt, font=font,
                             fill=(255, 255, 255, 255), anchor="mm")
    bbox = tmp.getbbox()
    if not bbox:
        return None
    pad = 34
    txt_img = tmp.crop((max(bbox[0] - pad, 0), max(bbox[1] - pad, 0),
                        min(bbox[2] + pad, tmp.width), min(bbox[3] + pad, tmp.height)))
    a = txt_img.split()[3]
    glow = Image.new("RGBA", txt_img.size, (0, 0, 0, 0))
    glow.putalpha(a.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(9)))
    tight = Image.new("RGBA", txt_img.size, (0, 0, 0, 0))
    tight.putalpha(a.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(3)))
    return Image.alpha_composite(Image.alpha_composite(glow, tight), txt_img)


def write_layer(frames, size, fps, path):
    """RGBA-кадры -> ProRes 4444 с альфой."""
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{size[0]}x{size[1]}",
         "-r", str(fps), "-i", "-",
         "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", path],
        stdin=subprocess.PIPE)
    assert p.stdin is not None
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()
    return path


def caption_layer(cues, dur, fps, tmp, name="caps.mov"):
    font = ImageFont.truetype(FONT_BLACK, FONT_SIZE)
    imgs = [render_text(t, font) for _, _, t in cues]
    blank = Image.new("RGBA", (W, STRIP_H), (0, 0, 0, 0))

    def gen():
        idx = 0
        for f in range(int(round(dur * fps))):
            t = f / fps
            while idx < len(cues) and t >= cues[idx][1]:
                idx += 1
            img = imgs[idx] if idx < len(cues) else None
            if img is not None and cues[idx][0] <= t < cues[idx][1]:
                age = (t - cues[idx][0]) * 1000
                if age < POP_MS:
                    k = age / POP_MS
                    s = POP_FROM + (1 - POP_FROM) * (1 - (1 - k) ** 3)
                    img = img.resize((max(int(img.width * s), 1),
                                      max(int(img.height * s), 1)), Image.LANCZOS)
                fr = blank.copy()
                fr.alpha_composite(img, ((W - img.width) // 2, (STRIP_H - img.height) // 2))
                yield fr
            else:
                yield blank
    return write_layer(gen(), (W, STRIP_H), fps, os.path.join(tmp, name))


# ---- хук --------------------------------------------------------------------
def hook_card(text, layout):
    """Белая плашка с текстом хука. *слово* -> жёлтый маркер за словом."""
    font = ImageFont.truetype(FONT_XBOLD, HOOK_SIZE)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    space = probe.textlength(" ", font=font)
    hook_max_w = CARD_W - 112 if layout == "broll" else HOOK_MAX_W

    # звёздочки — парные границы участка, а не пометка на каждом слове:
    # в `*216 000 кадров*` подсвечены все три слова, включая среднее без звёздочек
    inside = False
    lines = []
    # У b-roll над карточкой всего 332 px чистого места. Явные переносы из
    # плана, рассчитанные на cam, легко превращают хук в 3-4 строки и закрывают
    # вставку. Для b-roll снимаем явные переносы и перевёрстываем текст по
    # доступной ширине; если он всё равно не влез в две строки, QA остановит
    # выдачу и попросит сократить формулировку.
    chunks = [text.replace("|", " ")] if layout == "broll" else text.split("|")
    for chunk in chunks:                      # | — явный перенос вне b-roll
        cur = []
        # одиночное тире приклеиваем к слову перед ним: при перевёрстке оно
        # иначе уезжает в начало следующей строки («— в видео»)
        tokens = []
        for w in chunk.split():
            if w.strip("*") in ("—", "–") and tokens:
                tokens[-1] += "\u00a0" + w
            else:
                tokens.append(w)
        for w in tokens:
            n = w.count("*")
            marked = True if n else inside
            if n % 2:
                inside = not inside
            token = w.replace("*", "")
            trial = " ".join([t for t, _ in cur] + [token])
            if probe.textlength(trial, font=font) > hook_max_w and cur:
                lines.append(cur); cur = [(token, marked)]
            else:
                cur.append((token, marked))
        if cur:
            lines.append(cur)

    lh = int(HOOK_SIZE * 1.30)
    pad_x, pad_y = 56, 50
    text_w = max(probe.textlength(" ".join(t for t, _ in l), font=font) for l in lines)
    cw = int(min(max(text_w + pad_x * 2, 560), CARD_W))
    ch = int(lh * len(lines) + pad_y * 2)
    if layout == "split":                       # плашка занимает слот презентации
        cw, ch = CARD_W, TOP_H

    card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, cw - 1, ch - 1), RADIUS, fill=PAPER + (255,))

    y = (ch - lh * len(lines)) // 2 + lh // 2
    for line in lines:
        plain = " ".join(t for t, _ in line)
        x0 = (cw - probe.textlength(plain, font=font)) / 2
        # маркер рисуем одной плашкой на подряд идущие подсвеченные слова,
        # иначе пробелы внутри участка остаются белыми дырами
        x, i = x0, 0
        widths = [probe.textlength(t, font=font) for t, _ in line]
        while i < len(line):
            if line[i][1]:
                j = i
                span = 0.0
                while j < len(line) and line[j][1]:
                    span += widths[j] + (space if j + 1 < len(line) and line[j + 1][1] else 0)
                    j += 1
                d.rounded_rectangle((x - 10, y - lh * 0.34, x + span + 10, y + lh * 0.36),
                                    10, fill=MARK + (255,))
                for k in range(i, j):
                    x += widths[k] + space
                i = j
            else:
                x += widths[i] + space
                i += 1
        x = x0
        for (token, _), wl in zip(line, widths):
            d.text((x, y), token, font=font, fill=INK + (255,), anchor="lm")
            x += wl + space
        y += lh

    sh = Image.new("RGBA", (cw + 120, ch + 120), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((60, 74, 60 + cw, 74 + ch), RADIUS + 6,
                                         fill=(0, 0, 0, 150))
    sh = sh.filter(ImageFilter.GaussianBlur(30))
    sh.alpha_composite(card, (60, 60))
    return sh


def hook_top(layout):
    """Верх плашки хука. В cam кадр — крупный план лица, и плашка на 300
    ложится ровно на линию глаз; поэтому в камере она уезжает выше."""
    if layout == "split":
        return TOP_Y
    if layout == "cam":
        return HOOK_TOP_CAM
    if layout == "broll":
        return HOOK_TOP_BROLL
    return 300


def card_motion(t, dur, outro=False):
    """(dy, alpha) плашки в момент t или None, если её не видно.

    Хук въезжает снизу в начале и уходит вверх через HOOK_DUR; финальная
    плашка въезжает так же за OUTRO_DUR до конца и стоит до последнего кадра."""
    def ease(k):
        return 1 - (1 - k) ** 3
    if outro:
        t -= max(dur - OUTRO_DUR, 0.0)
        if t < 0:
            return None
        if t < HOOK_IN:
            k = ease(t / HOOK_IN)
            return int(46 * (1 - k)), k
        return 0, 1.0
    if t >= HOOK_DUR + HOOK_OUT:
        return None
    if t < HOOK_IN:
        k = ease(t / HOOK_IN)
        return int(46 * (1 - k)), k
    if t < HOOK_DUR:
        return 0, 1.0
    k = ease((t - HOOK_DUR) / HOOK_OUT)
    return int(-52 * k), 1 - k


def hook_layer(text, layout, dur, fps, tmp, outro=False):
    card = hook_card(text, layout)
    top = hook_top(layout) - 60
    blank = Image.new("RGBA", (W, HOOK_STRIP_H), (0, 0, 0, 0))

    def gen():
        for f in range(int(round(dur * fps))):
            m = card_motion(f / fps, dur, outro)
            if m is None:
                yield blank; continue
            dy, al = m
            c = card
            if al < 0.999:
                c = card.copy()
                c.putalpha(card.split()[3].point(lambda v: int(v * al)))
            fr = blank.copy()
            fr.alpha_composite(c, ((W - c.width) // 2, top + dy))
            yield fr
    return write_layer(gen(), (W, HOOK_STRIP_H), fps,
                       os.path.join(tmp, "outro.mov" if outro else "hook.mov"))


# ---- авто-зум презентации ---------------------------------------------------
def slide_zoom(src, t0, t1, samples=8, min_frac=0.5, pad_frac=0.09, skip_pip=True,
               aspect=None):
    import numpy as np
    sw, sh, sx, sy = SLIDE_CROP
    qw, qh, qx, qy = PATCH
    SC = 4
    acc = None
    for i in range(samples):
        t = t0 + (t1 - t0) * (i + 0.5) / samples
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", src, "-frames:v", "1",
             "-vf", f"crop={sw}:{sh}:{sx}:{sy},scale={sw//SC}:{sh//SC}",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
        a = np.frombuffer(p.stdout, dtype=np.uint8)
        if a.size != (sw // SC) * (sh // SC) * 3:
            continue
        a = a.reshape(sh // SC, sw // SC, 3).astype(int)
        if skip_pip:
            a[qy // SC:(qy + qh) // SC, qx // SC:(qx + qw) // SC] = -1
        mx, my = int(a.shape[1] * 0.045), int(a.shape[0] * 0.05)
        a[:my] = -1; a[-my:] = -1; a[:, :mx] = -1; a[:, -mx:] = -1
        bg = np.median(a[my:-my, mx:-mx].reshape(-1, 3), axis=0)
        ink = (np.abs(a - bg).max(axis=2) > 26) & (a[:, :, 0] >= 0)
        acc = ink if acc is None else (acc | ink)
    def fallback():
        ar = aspect if aspect else sw / sh
        w, h = (sw, sw / ar) if sw / ar <= sh else (sh * ar, sh)
        return (int(w) & ~1, int(h) & ~1,
                int((sw - w) / 2) & ~1, int((sh - h) / 2) & ~1)

    if acc is None or not acc.any():
        return fallback()
    ys, xs = np.where(acc)
    if len(xs) < 40:
        return fallback()
    x0, x1 = xs.min() * SC, (xs.max() + 1) * SC
    y0, y1 = ys.min() * SC, (ys.max() + 1) * SC
    px, py_ = (x1 - x0) * pad_frac, (y1 - y0) * pad_frac
    x0, x1, y0, y1 = x0 - px, x1 + px, y0 - py_, y1 + py_
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    ar = aspect if aspect else sw / sh
    w = max(x1 - x0, (y1 - y0) * ar, sw * min_frac)
    h = w / ar
    if h > sh:
        h = sh; w = sh * ar
    if w > sw:
        w = sw; h = sw / ar
    x0 = min(max(cx - w / 2, 0), sw - w)
    y0 = min(max(cy - h / 2, 0), sh - h)
    z = (int(w) & ~1, int(h) & ~1, int(x0) & ~1, int(y0) & ~1)
    print(f"    зум презы {z} (x{sw / z[0]:.2f})")
    return z


# ---- раскладки: возвращают (фильтр до [base], доп.входы, индекс след.входа, y субтитров)
def fc_split(src, t0, t1, tmp, zoom):
    sw, sh, sx, sy = SLIDE_CROP
    pw, ph, px, py = PIP_CROP
    qw, qh, qx, qy = PATCH
    zw, zh, zx, zy = slide_zoom(src, t0, t1) if zoom else (sw, sh, 0, 0)
    m_top, s_top, pad = make_card_pngs(tmp, CARD_W, TOP_H, "top")
    m_bot, s_bot, _ = make_card_pngs(tmp, CARD_W, BOT_H, "bot")
    m_patch = make_patch_mask(tmp)
    ins = [s_top, m_top, s_bot, m_bot, m_patch]          # входы 1..5
    fc = (
        f"[0:v]split=3[a][b][c];"
        f"[a]crop={pw}:{ph}:{px}:{py},scale=120:214,gblur=sigma=9,"
        f"scale={W}:{H}:flags=bicubic,eq=brightness=-0.26:saturation=0.5:contrast=1.02[bg];"
        f"[b]crop={sw}:{sh}:{sx}:{sy},split[sl][slp];"
        f"[slp]crop={qw}:{qh}:{PATCH_SRC[0]}:{PATCH_SRC[1]},scale=14:9,gblur=sigma=3,"
        f"scale={qw}:{qh}:flags=bicubic,format=rgba[pt];"
        f"[5:v]format=gray[mp];[pt][mp]alphamerge[ptc];"
        f"[sl][ptc]overlay={qx}:{qy}[slf];"
        f"[slf]crop={zw}:{zh}:{zx}:{zy},scale={CARD_W}:{TOP_H}:flags=lanczos,format=rgba[sld];"
        f"[2:v]format=gray[mt];[sld][mt]alphamerge[sldc];"
        f"[c]crop={pw}:{ph}:{px}:{py},scale={CARD_W}:{BOT_H}:flags=lanczos,"
        f"unsharp=5:5:0.7:5:5:0.0,format=rgba[cam];"
        f"[4:v]format=gray[mb];[cam][mb]alphamerge[camc];"
        f"[bg][1:v]overlay={CARD_X-pad}:{TOP_Y-pad}[s1];"
        f"[s1][sldc]overlay={CARD_X}:{TOP_Y}[s2];"
        f"[s2][3:v]overlay={CARD_X-pad}:{BOT_Y-pad}[s3];"
        f"[s3][camc]overlay={CARD_X}:{BOT_Y}[base]"
    )
    return fc, ins, len(ins) + 1, CAP_Y_SPLIT


def fc_cam(src, t0, t1, tmp, zoom):
    cw, chh, cx, cy = CAM_CROP
    fc = (
        f"[0:v]split=2[a][b];"
        f"[a]scale=120:214,gblur=sigma=9,scale={W}:{H}:flags=bicubic,"
        f"eq=brightness=-0.22:saturation=0.55[bg];"
        f"[b]crop={cw}:{chh}:{cx}:{cy},scale={W}:{H}:flags=lanczos,"
        f"unsharp=5:5:0.45:5:5:0.0[fg];"
        f"[bg][fg]overlay=0:0[base]"
    )
    return fc, [], 1, CAP_Y_CAM


def fc_broll(src, t0, t1, tmp, zoom):
    sw, sh, sx, sy = SLIDE_CROP
    ar = (W - 80) / BROLL_H                      # кроп под пропорции карточки
    zw, zh, zx, zy = (slide_zoom(src, t0, t1, samples=10, min_frac=0.42,
                                 pad_frac=0.07, skip_pip=False, aspect=ar)
                      if zoom else (int(sh * ar) & ~1, sh,
                                    int((sw - sh * ar) / 2) & ~1, 0))
    m_b, s_b, pad = make_card_pngs(tmp, W - 80, BROLL_H, "broll")
    ins = [s_b, m_b]
    fc = (
        f"[0:v]split=2[a][b];"
        f"[a]crop={sw}:{sh}:{sx}:{sy},scale=120:214,gblur=sigma=10,"
        f"scale={W}:{H}:flags=bicubic,eq=saturation=0.25,lutrgb=r=val*0.34:g=val*0.34:b=val*0.36[bg];"
        f"[b]crop={sw}:{sh}:{sx}:{sy},crop={zw}:{zh}:{zx}:{zy},"
        f"scale={W-80}:{BROLL_H}:flags=lanczos,format=rgba[scr];"
        f"[2:v]format=gray[mb];[scr][mb]alphamerge[scrc];"
        f"[bg][1:v]overlay={40-pad}:{BROLL_Y-pad}[s1];"
        f"[s1][scrc]overlay=40:{BROLL_Y}[base]"
    )
    return fc, ins, len(ins) + 1, CAP_Y_BROLL


LAYOUTS = {"split": fc_split, "cam": fc_cam, "broll": fc_broll}


# ---- рендер одного под-шота -------------------------------------------------
def render_shot(src, words, t0, t1, layout, out, tmp, fps, zoom, hook, cues=None,
                pre=None, outro=None):
    """pre — готовая подложка 1080x1920 (например, от track_cam.py); тогда кроп
    внутри ffmpeg не нужен, видео берём из неё, звук — из исходника."""
    dur = t1 - t0
    if cues is None:
        cues = build_cues(words, t0, t1)
    if pre:
        fc_body, ins, cap_i, cap_y = "[0:v]null[base]", [], 2, CAP_Y_CAM
    else:
        fc_body, ins, cap_i, cap_y = LAYOUTS[layout](src, t0, t1, tmp, zoom)

    inputs = ins + [caption_layer(cues, dur, fps, tmp)]
    parts = [fc_body,
             f"[{cap_i}:v]format=rgba[cap];"
             f"[base][cap]overlay=0:{cap_y - STRIP_H // 2}[o1]"]
    last = "o1"
    for n, (text, is_outro) in enumerate(((hook, False), (outro, True))):
        if not text:
            continue
        inputs.append(hook_layer(text, layout, dur, fps, tmp, outro=is_outro))
        i = cap_i + len(inputs) - 1 - len(ins)     # субтитры — inputs[len(ins)] = cap_i
        parts.append(f"[{i}:v]format=rgba[hk{n}];[{last}][hk{n}]overlay=0:0[h{n}]")
        last = f"h{n}"
    parts.append(f"[{last}]format=yuv420p[v]")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if pre:
        cmd += ["-i", pre, "-ss", f"{t0}", "-t", f"{dur}", "-i", src]
        amap = "1:a"
    else:
        cmd += ["-ss", f"{t0}", "-t", f"{dur}", "-i", src]
        amap = "0:a"
    for p in inputs:
        cmd += ["-i", p]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", amap,
            "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", out]
    subprocess.run(cmd, check=True)
    return out


# ---- разметка типов кадра — во frames.py, чтобы её же читал планировщик ------
def shot_types(src, t0, t1, **kw):
    return frames.shot_types(src, t0, t1, CALIB, **kw)


# ---- сборка -----------------------------------------------------------------
def check_cam_source(src, t0, t1, need=0.5):
    """`cam` берёт центральные 9:16 ПОЛНОГО кадра, а не вставку.

    На скринкасте по центру кадра находится экран, а человек живёт только
    внутри PiP — и `cam` честно соберёт отрезок без единого кадра с лицом.
    Взять кадр из вставки вместо этого нельзя: окно 9:16 упирается в её высоту,
    апскейл до 1080x1920 выходит примерно шестикратным, и лицо превращается в
    восковую заливку. То есть `cam` годится только там, где исходник и есть
    камера на весь кадр; если слот в `split` мёртвый, лечится это выбором
    другого момента, а не сменой раскладки.

    Молча собирать кадр без человека хуже, чем упасть: файл выглядит готовым."""
    labs = frames.read_labels(src, t0, min(t1 - t0, 20.0), 2.0, CALIB)
    if not labs:
        return
    share = sum(l == "cam" for l in labs) / len(labs)
    if share >= need:
        return
    got = {l: f"{sum(x == l for x in labs) / len(labs) * 100:.0f}%"
           for l in ("cam", "split", "broll") if l in labs}
    raise SystemExit(
        f"раскладка cam на отрезке {t0:.2f}-{t1:.2f}, где исходник — не камера "
        f"на весь кадр: {got}. cam режет центральные 9:16 полного кадра, а там "
        f"экран — в шортсе не будет лица. Возьми split (вебка в нижней "
        f"карточке) или выбери другой момент.")


def cam_track_kw():
    """Диапазон «дыхания» окна `cam` — из калибровки, а не константой в трекере.

    Штатные 0.84–1.0 посчитаны на записи, где камера снимает человека средним
    планом в комнате: окну есть куда наезжать, и наезд читается как акцент.
    Если исходник сам по себе крупный план (селфи с руки во весь кадр), тот же
    наезд режет голову по волосам и ушам — ширины 9:16 из 16:9 и так впритык.
    Тогда в calib.json кладут "cam_zoom": [1.0, 1.0], и окно только ведёт за
    головой, ничего не поджимая."""
    z = (CALIB or {}).get("cam_zoom")
    return {"tight": float(z[0]), "wide": float(z[1])} if z else {}


def build(src, words_json, t0, t1, out, tmp, layout="split", fps=30, zoom=True,
          hook=None, cues=None, track=False, layout_overrides=None, outro=None):
    os.makedirs(tmp, exist_ok=True)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    words = json.load(open(words_json)) if words_json else []

    if layout != "auto":
        print(f"[{layout}] {t0:.2f}-{t1:.2f} ({t1-t0:.1f}s)")
        if layout == "cam":
            check_cam_source(src, t0, t1)
        pre = None
        if track and layout == "cam":
            import track_cam
            pre = os.path.join(tmp, "tracked.mp4")
            print("    трекинг головы/рук (OpenCV)...")
            track_cam.render(src, t0, t1, pre, fps, **cam_track_kw())
        render_shot(src, words, t0, t1, layout, out, tmp, fps, zoom, hook,
                    cues=cues, pre=pre, outro=outro)
        print("[ok]", out)
        return

    # auto собирается покадрово — там геометрия карточек анимируется на швах
    import compose
    if cues is None:
        cues = build_cues(words, t0, t1)
    compose.build(src, t0, t1, cues, hook, out, fps=fps, zoom=zoom,
                  shots_override=layout_overrides, outro=outro)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--words", default=None)
    ap.add_argument("--cues", default=None,
                    help="JSON [[start,end,text],...] — вручную поправленные субтитры")
    ap.add_argument("--track", action="store_true",
                    help="OpenCV-трекинг головы/рук для раскладки cam")
    ap.add_argument("--calib", required=True, help="calib.json от shorts-plan")
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--layout", default="split", choices=["split", "cam", "broll", "auto"])
    ap.add_argument("--hook", default=None, help="текст плашки; *слово* — жёлтый маркер, | — перенос строки")
    ap.add_argument("--outro", default=None,
                    help="финальная плашка тизера, формат как у --hook")
    ap.add_argument("--tmp", default=None)
    ap.add_argument("--no-zoom", action="store_true")
    a = ap.parse_args()
    tmp = a.tmp or os.path.join("/tmp/montage/work",
                                os.path.splitext(os.path.basename(a.out))[0])
    use_calib(a.calib)
    cues = json.load(open(a.cues)) if a.cues else None
    build(a.src, a.words, a.start, a.end, a.out, tmp,
          layout=a.layout, zoom=not a.no_zoom, hook=a.hook,
          cues=cues, track=a.track, outro=a.outro)
