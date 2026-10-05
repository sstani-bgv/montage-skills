#!/usr/bin/env python3
"""
Покадровый сборщик для раскладки `auto`.

Раньше `auto` резал отрезок по типам кадра и склеивал куски встык — на швах
вебка исчезала рывком. Здесь кадр собирается покадрово, поэтому геометрия
карточек — это функция времени: вебка не пропадает, а сворачивается по высоте
и уезжает, презентация в тот же момент разворачивается на её место.

Два слота, оба живут весь ролик, у каждого свой прямоугольник и прозрачность:

  split   преза сверху (16:9), вебка снизу (кроп PiP)
  broll   преза во весь кадр (4:3 по контенту), вебка свёрнута в ноль
  cam     вебка на весь кадр (окно от track_cam), преза свёрнута в ноль

Переход split -> cam получается сам собой: окно вебки лерпится от PiP-кропа
к полному кадру, то есть PiP буквально разворачивается в камеру.

В b-roll камера не стоит: `kenburns_path` находит блоки текста на слайде и
ведёт окно по ним — подъехали к левому блоку, подержали, переехали к правому.

Вызывается из make_short.build() при layout="auto"; напрямую — для отладки,
предварительно настроив геометрию через make_short.use_calib(calib.json).
"""
import argparse, json, os, subprocess
import cv2
import numpy as np

import make_short as M
import track_cam

SRC_W, SRC_H = M.SRC_W, M.SRC_H     # перезаписывается в build() по калибровке
W, H = M.W, M.H

TR = 0.46                        # длительность шва между раскладками, сек
KB_MOVE = 0.70                   # переезд между блоками внутри b-roll, сек
KB_MIN_HOLD = 1.6                # меньше держать блок смысла нет
KB_PAD = 0.13                    # воздух вокруг блока
KB_MIN_W = 0.44                  # окно не уже этой доли ширины слайда

SLIDE_RECT = {"split": (40, M.TOP_Y, M.CARD_W, M.TOP_H),
              "broll": (40, M.BROLL_Y, W - 80, M.BROLL_H)}
CAM_RECT = {"split": (40, M.BOT_Y, M.CARD_W, M.BOT_H),
            "cam": (0, 0, W, H)}
CAP_Y = {"split": M.CAP_Y_SPLIT, "broll": M.CAP_Y_BROLL, "cam": M.CAP_Y_CAM}


def collapse(rect):
    """Прямоугольник, схлопнутый по высоте вокруг своего центра."""
    x, y, w, h = rect
    return (x + w * 0.10, y + h / 2, w * 0.80, 0.0)


def ease(k):
    k = min(max(k, 0.0), 1.0)
    return 3 * k * k - 2 * k * k * k          # smoothstep


def lerp(a, b, k):
    return a + (b - a) * k


# ---- маски и тени, кэшируются по размеру ------------------------------------
_mask_cache, _shadow_cache = {}, {}


def rounded_mask(w, h, r=M.RADIUS):
    key = (w, h)
    if key not in _mask_cache:
        m = np.zeros((h, w), np.float32)
        rr = min(r, w // 2, h // 2)
        cv2.rectangle(m, (rr, 0), (w - rr, h), 1.0, -1)
        cv2.rectangle(m, (0, rr), (w, h - rr), 1.0, -1)
        for cx, cy in ((rr, rr), (w - rr, rr), (rr, h - rr), (w - rr, h - rr)):
            cv2.circle(m, (cx, cy), rr, 1.0, -1)
        _mask_cache[key] = m
    return _mask_cache[key]


def shadow_sprite(w, h):
    """Мягкая тень под карточкой: (alpha, offset_x, offset_y)."""
    key = (w, h)
    if key not in _shadow_cache:
        pad = 54
        s = np.zeros((h + pad * 2, w + pad * 2), np.float32)
        s[pad:pad + h, pad:pad + w] = rounded_mask(w, h)
        s = cv2.GaussianBlur(s, (0, 0), 17) * 0.62
        _shadow_cache[key] = (s, pad)
    return _shadow_cache[key]


def blit(dst, img, alpha, x, y):
    """img — BGR uint8 (или None = чёрный), alpha — float32 (H,W), (x,y) — левый верх."""
    h, w = alpha.shape
    x, y = int(round(x)), int(round(y))
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, W), min(y + h, H)
    if x1 <= x0 or y1 <= y0:
        return
    a = alpha[y0 - y:y1 - y, x0 - x:x1 - x, None]
    roi = dst[y0:y1, x0:x1]
    if img is None:
        roi[:] = (roi * (1 - a)).astype(np.uint8)
    else:
        roi[:] = (roi * (1 - a) + img[y0 - y:y1 - y, x0 - x:x1 - x] * a).astype(np.uint8)


def card(dst, frame, win, rect, alpha, sharpen=0.0):
    """Вырезать окно win из исходного кадра и положить карточкой в rect."""
    rx, ry, rw, rh = rect
    rw, rh = int(round(rw)), int(round(rh))
    if alpha < 0.01 or rw < 8 or rh < 8:
        return
    wx, wy, ww, wh = [int(round(v)) for v in win]
    wx = max(0, min(wx, SRC_W - 2)); wy = max(0, min(wy, SRC_H - 2))
    ww = max(2, min(ww, SRC_W - wx)); wh = max(2, min(wh, SRC_H - wy))
    sub = frame[wy:wy + wh, wx:wx + ww]
    interp = cv2.INTER_AREA if ww > rw else cv2.INTER_LANCZOS4
    img = cv2.resize(sub, (rw, rh), interpolation=interp)
    if sharpen > 0:
        img = cv2.addWeighted(img, 1 + sharpen, cv2.GaussianBlur(img, (0, 0), 2),
                              -sharpen, 0)
    sh, pad = shadow_sprite(rw, rh)
    blit(dst, None, sh * alpha, rx - pad, ry - pad + 10)
    blit(dst, img, rounded_mask(rw, rh) * alpha, rx, ry)


# ---- фон: размытая тёмная подложка из самого кадра ---------------------------
def background(frame, from_slide):
    sw, sh, sx, sy = M.SLIDE_CROP
    src = frame[sy:sy + sh, sx:sx + sw] if from_slide else frame
    small = cv2.resize(src, (120, 214), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 9)
    bg = cv2.resize(small, (W, H), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    g = bg.mean(axis=2, keepdims=True)
    bg = g + (bg - g) * 0.30                       # почти обесцветить
    return np.clip(bg * 0.36, 0, 255).astype(np.uint8)


# ---- b-roll: блоки контента и проезд по ним ----------------------------------
def content_blocks(src, t0, t1, samples=6):
    """Блоки текста/картинок на слайде, в координатах SLIDE_CROP, в порядке чтения.

    Детект через OpenCV: карта краёв (Sobel) -> морфологическая склейка строк
    в абзацы -> связные компоненты -> отсев по плотности краёв внутри блока."""
    sw, sh, sx, sy = M.SLIDE_CROP
    SC = 4
    acc = None
    for i in range(samples):
        t = t0 + (t1 - t0) * (i + 0.5) / samples
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", src, "-frames:v", "1",
             "-vf", f"crop={sw}:{sh}:{sx}:{sy},scale={sw//SC}:{sh//SC}",
             "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
        a = np.frombuffer(p.stdout, np.uint8)
        if a.size != (sw // SC) * (sh // SC):
            continue
        a = a.reshape(sh // SC, sw // SC)
        # «чернила» = локальная энергия градиента, а не отличие от фона по яркости:
        # у текста и рамок UI края резкие, у заливок и градиентов — почти ноль,
        # поэтому цветной слайд целиком больше не считается блоком контента
        mag = (np.abs(cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3)) +
               np.abs(cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)))
        thr = max(28.0, float(np.percentile(mag, 99)) * 0.22)
        ink = (mag > thr).astype(np.uint8)
        acc = ink if acc is None else (acc | ink)
    if acc is None:
        return []
    mx, my = int(acc.shape[1] * 0.03), int(acc.shape[0] * 0.03)
    acc[:my] = 0; acc[-my:] = 0; acc[:, :mx] = 0; acc[:, -mx:] = 0
    raw = acc.copy()
    # склеиваем строки в абзацы, абзацы — в блоки
    acc = cv2.morphologyEx(acc, cv2.MORPH_CLOSE, np.ones((9, 27), np.uint8))
    acc = cv2.dilate(acc, np.ones((5, 9), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(acc, 8)
    out = []
    area_min = acc.size * 0.006
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a < area_min or w < 12 or h < 8:
            continue
        # плотность настоящих чернил внутри блока — иначе уедем в пустое поле
        dens = raw[y:y + h, x:x + w][lab[y:y + h, x:x + w] == i].mean()
        if dens < 0.06:
            continue
        out.append((x * SC, y * SC, w * SC, h * SC))
    if not out:
        return []
    # порядок чтения: блоки, у которых вертикали заметно перекрываются, — одна
    # полоса, внутри полосы идём слева направо; сами полосы — сверху вниз
    out.sort(key=lambda b: b[1])
    rows, cur = [], [out[0]]
    for b in out[1:]:
        top = max(min(r[1] for r in cur), b[1])
        bot = min(min(r[1] + r[3] for r in cur), b[1] + b[3])
        ov = max(bot - top, 0) / min(min(r[3] for r in cur), b[3])
        if ov > 0.4:
            cur.append(b)
        else:
            rows.append(cur); cur = [b]
    rows.append(cur)
    return [b for r in rows for b in sorted(r, key=lambda b: b[0])]


def _window(block, aspect, sw, sh, pad=KB_PAD, min_w=KB_MIN_W):
    x, y, w, h = block
    cx, cy = x + w / 2, y + h / 2
    w, h = w * (1 + pad * 2), h * (1 + pad * 2)
    w = max(w, h * aspect, sw * min_w)
    h = w / aspect
    if h > sh:
        h, w = sh, sh * aspect
    if w > sw:
        w, h = sw, sw / aspect
    return (min(max(cx - w / 2, 0), sw - w), min(max(cy - h / 2, 0), sh - h), w, h)


def kenburns_path(src, t0, t1, fps, aspect):
    """Траектория окна по слайду: подъехали к блоку, подержали, переехали к следующему."""
    sw, sh, _, _ = M.SLIDE_CROP
    n = max(int(round((t1 - t0) * fps)), 1)
    blocks = content_blocks(src, t0, t1)
    dur = t1 - t0

    if not blocks:
        full = _window((0, 0, sw, sh), aspect, sw, sh, pad=0, min_w=1.0)
        stops = [full, full]
    else:
        keep = max(1, min(len(blocks), int(dur // KB_MIN_HOLD)))
        if keep < len(blocks):                       # оставляем самые крупные
            big = sorted(blocks, key=lambda b: -b[2] * b[3])[:keep]
            blocks = [b for b in blocks if b in big]
        stops = [_window(b, aspect, sw, sh) for b in blocks]
        if len(stops) == 1:                          # один блок — медленный наезд
            x, y, w, h = stops[0]
            k = 1.10
            wide = _window((x + w / 2 - w * k / 2, y + h / 2 - h * k / 2, w * k, h * k),
                           aspect, sw, sh, pad=0)
            stops = [wide, stops[0]]

    # расписание: держим блок, переезжаем, держим следующий
    m = len(stops)
    move = min(KB_MOVE, dur / (2 * m)) if m > 1 else dur * 0.5
    hold = max((dur - move * (m - 1)) / m, 0.1)
    path = np.zeros((n, 4), np.float32)
    for f in range(n):
        t = f / fps
        seg = min(int(t // (hold + move)), m - 1)
        loc = t - seg * (hold + move)
        a = np.array(stops[seg], np.float32)
        if loc <= hold or seg + 1 >= m:
            path[f] = a
        else:
            b = np.array(stops[seg + 1], np.float32)
            path[f] = a + (b - a) * ease((loc - hold) / move)
    return path


# ---- параметры раскладки во времени ------------------------------------------
def shot_params(src, shots, t0, fps, zoom=True):
    """Для каждого под-шота — функция кадра -> набор параметров кадра."""
    out = []
    for a, b, mode in shots:
        n = max(int(round((b - a) * fps)), 1)
        p = {"mode": mode, "cap_y": CAP_Y[mode]}
        if mode == "broll":
            ar = SLIDE_RECT["broll"][2] / SLIDE_RECT["broll"][3]
            p["slide_rect"] = SLIDE_RECT["broll"]
            p["slide_path"] = kenburns_path(src, a, b, fps, ar)
            p["slide_a"] = 1.0
            p["cam_rect"] = collapse(CAM_RECT["split"]); p["cam_a"] = 0.0
            p["cam_path"] = np.tile(np.array(M.PIP_CROP, np.float32)[[2, 3, 0, 1]], (n, 1))
        elif mode == "split":
            zw, zh, zx, zy = (M.slide_zoom(src, a, b) if zoom
                              else (M.SLIDE_CROP[0], M.SLIDE_CROP[1], 0, 0))
            p["slide_rect"] = SLIDE_RECT["split"]
            p["slide_path"] = np.tile(np.array([zx, zy, zw, zh], np.float32), (n, 1))
            p["slide_a"] = 1.0
            p["cam_rect"] = CAM_RECT["split"]; p["cam_a"] = 1.0
            p["cam_path"] = np.tile(np.array(M.PIP_CROP, np.float32)[[2, 3, 0, 1]], (n, 1))
        else:                                        # cam
            p["slide_rect"] = collapse(SLIDE_RECT["split"]); p["slide_a"] = 0.0
            p["slide_path"] = np.tile(np.array([0, 0, M.SLIDE_CROP[0], M.SLIDE_CROP[1]],
                                               np.float32), (n, 1))
            p["cam_rect"] = CAM_RECT["cam"]; p["cam_a"] = 1.0
            p["cam_path"] = track_cam.plan(src, a, b, fps).astype(np.float32)
        p["t0"], p["t1"], p["n"] = a - t0, b - t0, n
        out.append(p)
    return out


def sample(p, t):
    """Параметры одного под-шота в момент t (от начала ролика), с клампом по краям."""
    i = int(round((t - p["t0"]) * p["fps"])) if "fps" in p else 0
    i = max(0, min(i, p["n"] - 1))
    return {"slide_rect": np.array(p["slide_rect"], np.float32),
            "slide_win": p["slide_path"][i], "slide_a": p["slide_a"],
            "cam_rect": np.array(p["cam_rect"], np.float32),
            "cam_win": p["cam_path"][i], "cam_a": p["cam_a"],
            "cap_y": float(p["cap_y"]), "mode": p["mode"]}


def blend(A, B, k):
    k = ease(k)
    return {"slide_rect": lerp(A["slide_rect"], B["slide_rect"], k),
            "slide_win": lerp(A["slide_win"], B["slide_win"], k),
            "slide_a": lerp(A["slide_a"], B["slide_a"], k),
            "cam_rect": lerp(A["cam_rect"], B["cam_rect"], k),
            "cam_win": lerp(A["cam_win"], B["cam_win"], k),
            "cam_a": lerp(A["cam_a"], B["cam_a"], k),
            "cap_y": lerp(A["cap_y"], B["cap_y"], k),
            "mode": B["mode"] if k > 0.5 else A["mode"]}


# ---- накладки: субтитры и хук ------------------------------------------------
def pil_to_np(img):
    a = np.array(img)
    return cv2.cvtColor(a[:, :, :3], cv2.COLOR_RGB2BGR), a[:, :, 3].astype(np.float32) / 255


def caption_sprites(cues):
    from PIL import ImageFont
    font = ImageFont.truetype(M.FONT_BLACK, M.FONT_SIZE)
    out = []
    for _, _, txt in cues:
        im = M.render_text(txt, font)
        out.append(pil_to_np(im) if im is not None else None)
    return out


# ---- сборка ------------------------------------------------------------------
def build(src, t0, t1, cues, hook, out, fps=30, zoom=True, first_layout=None,
          patch_pip=True, shots_override=None, outro=None):
    global SRC_W, SRC_H
    SRC_W, SRC_H = M.SRC_W, M.SRC_H          # геометрия исходника — из калибровки
    if shots_override:
        shots = []
        for raw in shots_override:
            a, b, mode = float(raw["start"]), float(raw["end"]), raw["layout"]
            if mode not in ("split", "cam", "broll"):
                raise ValueError(f"неизвестная ручная раскладка: {mode}")
            a, b = max(a, t0), min(b, t1)
            if b > a:
                shots.append([a, b, mode])
        shots.sort(key=lambda x: x[0])
        if not shots or shots[0][0] > t0 + 0.02 or shots[-1][1] < t1 - 0.02:
            raise ValueError("layout_overrides должны покрывать весь шортс")
        for left, right in zip(shots, shots[1:]):
            if abs(left[1] - right[0]) > 0.02:
                raise ValueError("в layout_overrides есть разрыв или пересечение")
        print("[compose] ручная разметка раскладок из plan.json")
    else:
        shots = M.shot_types(src, t0, t1)
    print(f"[compose] {len(shots)} под-шота:")
    for a, b, m in shots:
        print(f"  {a:.2f}-{b:.2f} ({b-a:.1f}s) -> {m}")

    params = shot_params(src, shots, t0, fps, zoom)
    for p in params:
        p["fps"] = fps

    sprites = caption_sprites(cues)
    hook_sp, hook_top = None, 0
    if hook:
        lay = first_layout or shots[0][2]
        hook_sp = pil_to_np(M.hook_card(hook, lay))
        hook_top = M.hook_top(lay) - 60
    outro_sp, outro_top = None, 0
    if outro:
        lay = shots[-1][2]
        outro_sp = pil_to_np(M.hook_card(outro, lay))
        outro_top = M.hook_top(lay) - 60

    # заплатка, которой прячем PiP внутри слайда
    pw, ph, px, py = M.PATCH
    pmask = np.array(M.Image.open(M.make_patch_mask("/tmp")), np.float32) / 255
    pmask = pmask[:, :, None]
    # PiP прижат к правому нижнему углу, поэтому заплатка вокруг него вылезает
    # за край кадра. Режем и её, и маску по границам исходника, иначе срез кадра
    # и маска расходятся по форме.
    _psx, _psy = M.SLIDE_CROP[2], M.SLIDE_CROP[3]
    pw = max(0, min(pw, SRC_W - (_psx + px)))
    ph = max(0, min(ph, SRC_H - (_psy + py)))
    pmask = pmask[:ph, :pw]

    # Инвариант: замазка PiP имеет право сработать РОВНО на split-кадрах.
    # Ошибка в этом условии однажды положила размытое пятно на живой слайд в
    # b-roll, и по готовому файлу это ловится плохо — поэтому считаем здесь.
    stats = {"patched": 0, "by_mode": {}}

    dur = t1 - t0
    total = int(round(dur * fps))
    rd = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", f"{t0}", "-t", f"{dur}", "-i", src,
         "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    plate = out + ".plate.mp4"
    wr = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", "17",
         "-pix_fmt", "yuv420p", plate], stdin=subprocess.PIPE)

    bounds = [p["t1"] for p in params[:-1]]
    CH = SRC_W * SRC_H * 3
    cue_i = 0
    # Морозим не последний кадр перед резом, а кадр за ~0.3 с до него: в исходнике
    # рез часто сделан кроссфейдом, и соседний кадр — уже наполовину новая сцена
    # (уходящая карточка презентации показала бы комнату).
    LAG = max(1, int(round(0.30 * fps)))
    hist = []                       # последние LAG пар (patched, clean)
    frozen_slide = frozen_cam = None
    prev_si = 0
    written = 0
    assert rd.stdout is not None and wr.stdin is not None
    for f in range(total):
        b = rd.stdout.read(CH)
        if len(b) < CH:
            break
        frame = np.frombuffer(b, np.uint8).reshape(SRC_H, SRC_W, 3).copy()
        t = f / fps

        si = next((i for i, p in enumerate(params) if t < p["t1"]), len(params) - 1)
        P = sample(params[si], t)

        # заплатка, прячущая PiP внутри слайда, живёт на ОТДЕЛЬНОЙ копии кадра:
        # карточке вебки нужен чистый кадр, иначе она вырежет замазанную область.
        # Вешаем её по типу ИСХОДНОГО кадра, а не по тому, видна ли преза: в b-roll
        # вебки в кадре нет, и замазка легла бы на живой контент слайда.
        patched = frame
        stats["by_mode"][params[si]["mode"]] = stats["by_mode"].get(params[si]["mode"], 0) + 1
        if patch_pip and params[si]["mode"] == "split":
            stats["patched"] += 1
            patched = frame.copy()
            sx, sy = M.SLIDE_CROP[2], M.SLIDE_CROP[3]
            reg = patched[sy + py:sy + py + ph, sx + px:sx + px + pw].astype(np.float32)
            src_reg = patched[sy + M.PATCH_SRC[1]:sy + M.PATCH_SRC[1] + ph,
                              sx + M.PATCH_SRC[0]:sx + M.PATCH_SRC[0] + pw].astype(np.float32)
            fill = cv2.GaussianBlur(cv2.resize(cv2.resize(src_reg, (14, 9)), (pw, ph)),
                                    (0, 0), 9)
            patched[sy + py:sy + py + ph, sx + px:sx + px + pw] = \
                (reg * (1 - pmask) + fill * pmask).astype(np.uint8)

        if si != prev_si:                                # только что перешли рез
            old_patched, old_clean = hist[0] if hist else (patched.copy(), frame.copy())
            frozen_slide, frozen_cam = old_patched, old_clean
            prev_si = si
        slide_src, cam_src = patched, frame
        for bi, tb in enumerate(bounds):
            # шов целиком после реза: до реза источник ещё старый, после — новый,
            # поэтому уходящий слот доигрывает на замороженном кадре
            if tb <= t < tb + TR:
                A = sample(params[bi], tb - 1e-3)
                B = sample(params[bi + 1], t)
                P = blend(A, B, (t - tb) / TR)
                if B["slide_a"] < 0.01:
                    P["slide_win"] = A["slide_win"]; slide_src = frozen_slide
                else:
                    P["slide_win"] = B["slide_win"]
                if B["cam_a"] < 0.01:
                    P["cam_win"] = A["cam_win"]; cam_src = frozen_cam
                else:
                    P["cam_win"] = B["cam_win"]
                break
        hist.append((patched, frame))
        if len(hist) > LAG:
            hist.pop(0)

        canvas = background(slide_src if P["slide_a"] > 0.5 else cam_src,
                            P["slide_a"] > 0.5)

        _, _, sx_, sy_ = M.SLIDE_CROP
        win = P["slide_win"]
        card(canvas, slide_src, (sx_ + win[0], sy_ + win[1], win[2], win[3]),
             P["slide_rect"], P["slide_a"])
        card(canvas, cam_src, P["cam_win"], P["cam_rect"], P["cam_a"], sharpen=0.45)

        while cue_i < len(cues) and t >= cues[cue_i][1]:
            cue_i += 1
        if cue_i < len(cues) and cues[cue_i][0] <= t and sprites[cue_i] is not None:
            img, al = sprites[cue_i]
            age = (t - cues[cue_i][0]) * 1000
            if age < M.POP_MS:
                k = age / M.POP_MS
                s = M.POP_FROM + (1 - M.POP_FROM) * (1 - (1 - k) ** 3)
                nw, nh = max(int(img.shape[1] * s), 1), max(int(img.shape[0] * s), 1)
                img = cv2.resize(img, (nw, nh)); al = cv2.resize(al, (nw, nh))
            blit(canvas, img, al, (W - img.shape[1]) / 2, P["cap_y"] - img.shape[0] / 2)

        if hook_sp is not None and t < M.HOOK_DUR + M.HOOK_OUT:
            img, al = hook_sp
            if t < M.HOOK_IN:
                k = ease(t / M.HOOK_IN); dy, a_ = int(46 * (1 - k)), k
            elif t < M.HOOK_DUR:
                dy, a_ = 0, 1.0
            else:
                k = ease((t - M.HOOK_DUR) / M.HOOK_OUT); dy, a_ = int(-52 * k), 1 - k
            blit(canvas, img, al * a_, (W - img.shape[1]) / 2, hook_top + dy)

        if outro_sp is not None:
            m = M.card_motion(t, dur, outro=True)
            if m is not None:
                img, al = outro_sp
                blit(canvas, img, al * m[1], (W - img.shape[1]) / 2, outro_top + m[0])

        wr.stdin.write(canvas.tobytes())
        written += 1

    # We intentionally consume exactly `total` output frames.  Depending on
    # ffmpeg's fps rounding, the source filter may still have one extra frame
    # buffered; leaving stdout open while waiting for it can deadlock the
    # decoder on a full pipe after the plate encoder has finished.
    wr.stdin.close(); wr.wait()
    if rd.stdout is not None:
        rd.stdout.close()
    rd.wait()
    if written != total:
        raise RuntimeError(
            f"декодер отдал {written} кадров вместо {total}; рендер неполный")

    split_frames = stats["by_mode"].get("split", 0)
    if patch_pip and stats["patched"] != split_frames:
        raise RuntimeError(
            f"замазка PiP сработала на {stats['patched']} кадрах при "
            f"{split_frames} split-кадрах — условие перепутано, см. "
            f"references/gotchas.md")
    print("    кадров по типам:",
          ", ".join(f"{k} {v}" for k, v in sorted(stats["by_mode"].items())),
          f"| замазка на {stats['patched']}")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", plate,
                    "-ss", f"{t0}", "-t", f"{dur}", "-i", src,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-movflags", "+faststart", out], check=True)
    os.remove(plate)
    print("[ok]", out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--cues", required=True)
    ap.add_argument("--hook", default=None)
    ap.add_argument("--outro", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()
    build(a.src, a.start, a.end, json.load(open(a.cues)), a.hook, a.out, a.fps,
          outro=a.outro)
