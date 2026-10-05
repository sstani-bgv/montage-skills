#!/usr/bin/env python3
"""Нативный вертикальный шортс 1080x1920 из исходных дорожек «Второго мозга».

Не кропает горизонтальный монтаж: каждый кадр собирается заново из
скринкаста, камеры iPhone (2560x1440, карта дрейфа camoff.json) и
вертикально перевёрстанных анимаций.

Раскладки (по типу кадра горизонтального EDL в этот момент):
  cam    камера на весь кадр 9:16, лицо по трекингу; субтитры y~1240
  screen скринкаст карточкой сверху (кроп по активности курсора), камера
         карточкой снизу, субтитры в зазоре
  anim   вертикальная анимация на весь кадр (контент сверху), камера
         карточкой снизу, субтитры в зазоре
Швы 0.4 с: прямоугольники карточек и кроп камеры — функции времени.

  python native_short.py plan.json <id> out.mp4 [--preview t1,t2,...] [--mux-only]

Источники ниже (блок SOURCES) — от ролика «Второй мозг» (sb28): скринкаст,
камера с картой дрейфа camoff.json, face.json, edl.json (типы кадров и зумы),
scrbox.json (активность курсора), W/scenes.json. Для нового ролика поправь блок
SOURCES; логика раскладок, швов, субтитров и хука от них не зависит.
plan.json: {"anim_v": {"NN": "<вертикальный рендер сцены>.mp4"},
            "shorts": [{"id","parts":[[T0,T1],...],"hook","cues":[[u0,u1,txt],...],
                        "overrides":[[u0,u1,"cam|screen|anim"]]?}]}
Подробно — references/layouts.md, раздел «Нативная сборка из раздельных дорожек».
"""
import json, os, subprocess, sys, functools, math
import numpy as np, cv2
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_short as MS
from PIL import ImageFont

FPS = 30
W, H = 1080, 1920
# ---- SOURCES (под конкретный ролик) ----
D = "/tmp/montage/sb28"
SCREEN = os.environ.get("SCREEN", "screen.mp4")  # путь к экранной записи
CAM = f"{D}/cam.mp4"
VOICE = f"{D}/voice_norm.wav"
CW, CH = 2560, 1440
SW, SH = 1920, 1080
HERE = os.path.dirname(os.path.abspath(__file__))

# ---- геометрия --------------------------------------------------------------
FULL = (0.0, 0.0, float(W), float(H))
TOP = (40.0, 150.0, 1000.0, 750.0)          # карточка экрана 4:3
CAMC = (40.0, 1100.0, 1000.0, 700.0)        # карточка камеры
CAP_SPLIT = 1000                            # центр субтитров в зазоре
CAP_CAM = 1400
R = 34
TR = 0.40                                   # длительность шва

def ease(k):
    k = min(max(k, 0.0), 1.0)
    return 4 * k * k * k if k < .5 else 1 - (-2 * k + 2) ** 3 / 2

lerp = lambda a, b, k: a + (b - a) * k
lerp4 = lambda a, b, k: tuple(lerp(x, y, k) for x, y in zip(a, b))

# ---- камера: дрейф и лицо ---------------------------------------------------
CO = json.load(open(f"{D}/camoff.json")); COT, COO = np.array(CO["t"]), np.array(CO["off"])
def cam_t(t):
    i = max(0, int(np.searchsorted(COT, t + 1e-6)) - 1)
    return t + float(COO[i])
FJ = json.load(open(f"{D}/face.json")); FT, FX, FY = map(np.array, (FJ["t"], FJ["x"], FJ["y"]))
def face_at(tc):
    # сглаживание ±0.5 с, чтобы окно не дрожало за детектором
    ts = np.linspace(tc - 0.5, tc + 0.5, 9)
    return float(np.interp(ts, FT, FX).mean()), float(np.interp(ts, FT, FY).mean())

def cam_crop(T, rect, push=0.0):
    x, y, w, h = rect
    a = w / h
    if a < 1:          # портрет (весь кадр)
        ch = 1120 * (1 - push); fy = 0.40
    else:              # карточка
        ch = 800 * (1 - push); fy = 0.44
    cw = ch * a
    if cw > CW: cw = CW; ch = cw / a
    fx, fyy = face_at(cam_t(T))
    cx = min(max(fx - cw / 2, 0), CW - cw)
    cy = min(max(fyy - fy * ch, 0), CH - ch)
    return (cx, cy, cw, ch)

# ---- экран: фокус по активности курсора ------------------------------------
SB = json.load(open(f"{D}/scrbox.json"))
ZOOMS = json.load(open(f"{D}/edl.json"))["zooms"]
SCR_CW = 1060                                 # ширина кропа экрана (4:3)
def screen_focus(t0, t1):
    for z in ZOOMS:
        if min(t1, z["t1"]) - max(t0, z["t0"]) > 0.5 * (t1 - t0) or (z["t0"] <= (t0 + t1) / 2 <= z["t1"]):
            return float(z["cx"]), float(z["cy"])
    xs, ys, ws = [], [], []
    for i in range(int(t0 * 4), min(int(t1 * 4) + 1, len(SB))):
        b = SB[i]
        if not b or (b[0], b[1], b[2], b[3]) == (556, 217, 556, 247):
            continue
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        area = max((b[2] - b[0]) * (b[3] - b[1]), 1)
        if area > 0.35 * SW * SH:   # смена экрана целиком — не фокус
            continue
        xs.append(cx); ys.append(cy); ws.append(1.0)
    if not xs:
        return SW / 2, SH / 2
    return float(np.median(xs)), float(np.median(ys))

def grab_screen(t, scale=4):
    w, h = SW // scale, SH // scale
    b = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", SCREEN, "-frames:v", "1", "-vf", f"scale={w}:{h},format=gray",
                        "-f", "rawvideo", "-"], capture_output=True).stdout
    if len(b) < w * h:
        return None
    return np.frombuffer(b[:w * h], np.uint8).reshape(h, w)

def content_focus(t, cursor):
    """Окно 4:3 с максимумом «чернил» (краёв) рядом с курсором: на тёмном UI
    курсор часто стоит в пустом поле, а текст — сбоку."""
    g = grab_screen(t)
    if g is None:
        return cursor
    sc = 4
    e = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1))
    e = (e > max(np.percentile(e, 97), 20)).astype(np.float32)
    cw, ch = SCR_CW // sc, int(SCR_CW * 3 / 4) // sc
    ii = cv2.integral(e)
    best, bxy = -1, None
    for y in range(20 // sc, (SH - 20) // sc - ch, 6):
        for x in range(60 // sc, (SW - 60) // sc - cw, 6):
            ink = ii[y + ch, x + cw] - ii[y, x + cw] - ii[y + ch, x] + ii[y, x]
            cx, cy = (x + cw / 2) * sc, (y + ch / 2) * sc
            d = math.hypot(cx - cursor[0], cy - cursor[1]) / SW
            score = ink * (1.0 - 0.9 * min(d, 1.0))
            if score > best:
                best, bxy = score, (cx, cy)
    return bxy or cursor

def screen_crop(f):
    cw = SCR_CW; ch = cw * 3 / 4
    x = min(max(f[0] - cw / 2, 60), SW - 60 - cw)
    y = min(max(f[1] - ch / 2, 20), SH - 20 - ch)
    return (x, y, cw, ch)

# ---- отрисовка --------------------------------------------------------------
@functools.lru_cache(maxsize=64)
def rmask(w, h, r):
    m = np.zeros((h, w), np.uint8)
    r = int(max(0, min(r, w // 2, h // 2)))
    if r == 0:
        m[:] = 255
    else:
        cv2.rectangle(m, (r, 0), (w - 1 - r, h - 1), 255, -1)
        cv2.rectangle(m, (0, r), (w - 1, h - 1 - r), 255, -1)
        for cx, cy in ((r, r), (w - 1 - r, r), (r, h - 1 - r), (w - 1 - r, h - 1 - r)):
            cv2.circle(m, (cx, cy), r, 255, -1, cv2.LINE_AA)
    return (m.astype(np.float32) / 255.0)

@functools.lru_cache(maxsize=16)
def shadow(w, h, r):
    pad = 60
    s = np.zeros((h + 2 * pad, w + 2 * pad), np.float32)
    s[pad + 14:pad + 14 + h, pad:pad + w] = rmask(w, h, r)
    return cv2.GaussianBlur(s, (0, 0), 26) * 0.55, pad

def blit(dst, src, a, x, y):
    h, w = src.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    s = src[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    aa = a[y0 - y:y1 - y, x0 - x:x1 - x][..., None]
    d = dst[y0:y1, x0:x1].astype(np.float32)
    dst[y0:y1, x0:x1] = (s * aa + d * (1 - aa)).astype(np.uint8)

def card(dst, img, rect, r, alpha=1.0, sh=True):
    x, y, w, h = [int(round(v)) for v in rect]
    if w < 4 or h < 4 or alpha <= 0.01:
        return
    if sh and w < W - 4:
        s, pad = shadow(w, h, r)
        x0, y0 = x - pad, y - pad
        xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + s.shape[1]), min(H, y0 + s.shape[0])
        if xb > xa and yb > ya:
            aa = s[ya - y0:yb - y0, xa - x0:xb - x0][..., None] * alpha
            dst[ya:yb, xa:xb] = (dst[ya:yb, xa:xb].astype(np.float32) * (1 - aa)).astype(np.uint8)
    im = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA if img.shape[1] > w else cv2.INTER_CUBIC)
    blit(dst, im, rmask(w, h, int(r)) * alpha, x, y)

def crop(img, rect):
    x, y, w, h = rect
    x0, y0 = int(round(x)), int(round(y))
    return img[y0:y0 + int(round(h)), x0:x0 + int(round(w))]

def blur_bg(img):
    b = cv2.resize(img, (W // 10, H // 10), interpolation=cv2.INTER_AREA)
    b = cv2.GaussianBlur(b, (0, 0), 3)
    b = cv2.resize(b, (W, H), interpolation=cv2.INTER_LINEAR)
    return cv2.convertScaleAbs(b, alpha=0.42, beta=4)

# ---- чтение видео -----------------------------------------------------------
class Reader:
    def __init__(self, path, t0, w, h):
        self.w, self.h = w, h
        self.t0 = t0
        self.p = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(t0, 0):.4f}", "-i", path, "-vf", f"fps={FPS},scale={w}:{h}",
             "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE, bufsize=10 ** 8)
        self.n = w * h * 3
        self.last = None
        self.idx = int(round(max(t0, 0) * FPS)) - 1

    def read(self):
        b = self.p.stdout.read(self.n)
        if len(b) == self.n:
            self.last = np.frombuffer(b, np.uint8).reshape(self.h, self.w, 3)
        return self.last

    def read_at(self, t):
        target = int(round(t * FPS))
        if target < self.idx - 1 or target > self.idx + 90:
            raise RuntimeError(f"reader seek {self.idx}->{target}")
        while self.idx < target:
            self.read(); self.idx += 1
        return self.last

    def close(self):
        try: self.p.kill()
        except Exception: pass

# ---- план -------------------------------------------------------------------
E = json.load(open(f"{D}/edl.json"))
SCENES = json.load(open(f"{D}/W/scenes.json"))
def edl_kind(T):
    for s in E["shots"]:
        if s["t0"] <= T < s["t1"]:
            if s["type"] == "insert":
                return "anim", s.get("scene")
            if s["type"] == "screen":
                return "screen", None
            return "cam", None
    return "cam", None

def build_segments(parts, anim_src, overrides):
    """Список сегментов в выходном времени: (u0,u1,kind,scene,T0)."""
    segs = []
    u = 0.0
    for pi, (a, b) in enumerate(parts):
        # точки смены типа внутри части
        cuts = sorted({a, b} | {s["t0"] for s in E["shots"] if a < s["t0"] < b} | {s["t1"] for s in E["shots"] if a < s["t1"] < b})
        for x, y in zip(cuts, cuts[1:]):
            k, sc = edl_kind((x + y) / 2)
            if k == "anim" and sc not in anim_src:
                k, sc = "cam", None
            segs.append([u + x - a, u + y - a, k, sc, pi])
        u += b - a
    for o in overrides or []:           # [u0,u1,kind] в выходном времени
        new = []
        for s in segs:
            if s[1] <= o[0] or s[0] >= o[1]:
                new.append(s); continue
            if s[0] < o[0]: new.append([s[0], o[0]] + s[2:])
            new.append([max(s[0], o[0]), min(s[1], o[1]), o[2], None if o[2] != "anim" else s[3], s[4]])
            if s[1] > o[1]: new.append([o[1], s[1]] + s[2:])
        segs = new
    # склеить одинаковые соседние и поглотить короткие (< 1.2 с)
    def merge(segs):
        out = []
        for s in segs:
            if out and out[-1][2] == s[2] and out[-1][3] == s[3]:
                out[-1][1] = s[1]
            else:
                out.append(list(s))
        return out
    segs = merge(segs)
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, s in enumerate(segs):
            if s[1] - s[0] < 1.2:
                j = i - 1 if i > 0 else i + 1
                if i + 1 < len(segs) and i > 0 and (segs[i + 1][1] - segs[i + 1][0]) > (segs[i - 1][1] - segs[i - 1][0]):
                    j = i + 1
                segs[i][2], segs[i][3] = segs[j][2], segs[j][3]
                segs = merge(segs); changed = True
                break
    return segs

def main():
    plan = json.load(open(sys.argv[1]))
    item = next(x for x in plan["shorts"] if x["id"] == sys.argv[2])
    OUT = sys.argv[3]
    preview = None
    if "--preview" in sys.argv:
        preview = [float(v) for v in sys.argv[sys.argv.index("--preview") + 1].split(",")]
    parts = item["parts"]
    anim_src = {k: v for k, v in (plan.get("anim_v") or {}).items() if os.path.exists(v)}
    segs = build_segments(parts, anim_src, item.get("overrides"))
    dur = sum(b - a for a, b in parts)
    print("segments:", [(round(s[0], 2), round(s[1], 2), s[2], s[3]) for s in segs])
    starts = [0.0]
    for a, b in parts[:-1]:
        starts.append(starts[-1] + b - a)

    def u2T(u):
        for pi in range(len(parts) - 1, -1, -1):
            if u >= starts[pi] - 1e-6:
                return pi, parts[pi][0] + (u - starts[pi])
        return 0, parts[0][0] + u

    # фокус экрана на сегмент
    for s in segs:
        if s[2] == "screen":
            pi = s[4]; a = parts[pi][0] + s[0] - starts[pi]; b = parts[pi][0] + s[1] - starts[pi]
            keys = []
            t = a
            while True:
                f = content_focus(t, screen_focus(max(a, t - 2.5), min(b, t + 2.5)))
                keys.append((t - a + s[0], screen_crop(f)))
                if t >= b: break
                t = min(b, t + 2.0)
            s.append(keys)
        else:
            s.append(None)

    def seg_at(u):
        for i, s in enumerate(segs):
            if s[0] - 1e-6 <= u < s[1]:
                return i
        return len(segs) - 1

    def layout_state(i, u, T):
        s = segs[i]
        if s[2] == "cam":
            k = min(1.0, (u - s[0]) / max(s[1] - s[0], 0.1))
            return dict(cam=FULL, cap=CAP_CAM, push=0.05 * k, top=None, bgk=0.0)
        return dict(cam=CAMC, cap=CAP_SPLIT, push=0.0, top=s[2], bgk=1.0 if s[2] == "anim" else 0.0)

    # ---- субтитры и хук ----
    font = ImageFont.truetype(MS.FONT_BLACK, MS.FONT_SIZE)
    cues = item["cues"]
    cue_imgs = [MS.render_text(t, font) for _, _, t in cues]
    cue_np = [(np.array(im)) if im is not None else None for im in cue_imgs]
    hook_img = np.array(MS.hook_card(item["hook"], "cam")) if item.get("hook") else None
    outro_img = np.array(MS.hook_card(item["outro"], "cam")) if item.get("outro") else None

    def paste_rgba(dst, rgba, x, y, alpha=1.0):
        a = rgba[..., 3].astype(np.float32) / 255.0 * alpha
        blit(dst, rgba[..., 2::-1].copy(), a, int(x), int(y))

    # ---- чтение ----
    readers = {}
    def get_reader(key, path, t0, w, h):
        r = readers.get(key)
        if r is None:
            r = readers[key] = Reader(path, t0, w, h)
        return r

    if "--mux-only" in sys.argv:
        return mux_audio(item, parts, starts, dur, OUT, anim_src, segs)
    frames_idx = range(int(round(dur * FPS)))
    if preview:
        frames_idx = [int(round(p * FPS)) for p in preview]
    enc = None
    if not preview:
        enc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
             "-c:v", "h264_videotoolbox", "-b:v", "16M", "-pix_fmt", "yuv420p", OUT + ".v.mp4"], stdin=subprocess.PIPE)
    last_cf = None; last_sf = None; last_af = {}
    for n in frames_idx:
        u = n / FPS
        pi, T = u2T(u)
        i = seg_at(u)
        s = segs[i]
        if preview:  # в режиме превью — новые ридеры на каждый кадр
            for r in readers.values(): r.close()
            readers.clear()
        cr = get_reader(("cam", pi), CAM, cam_t(T), CW, CH)
        cf = cr.read_at(cam_t(T)) if not preview else cr.read()
        if cf is None: cf = last_cf
        last_cf = cf
        need_scr = s[2] == "screen" or (i > 0 and segs[i - 1][2] == "screen" and u - s[0] < TR)
        sf = None
        if need_scr:
            sr = get_reader(("scr", pi), SCREEN, T, SW, SH)
            sf = sr.read_at(T) if not preview else sr.read()
            last_sf = sf
        # анимация
        def anim_frame(seg):
            sc = seg[3]
            ta = parts[seg[4]][0] + (u - starts[seg[4]]) - SCENES[sc]["start"]
            key = ("anim", sc, seg[4])
            if preview:
                r = Reader(anim_src[sc], max(ta, 0), W, H); f = r.read(); r.close(); return f
            r = readers.get(key)
            if r is None:
                r = readers[key] = Reader(anim_src[sc], max(ta, 0), W, H)
            f = r.read_at(max(ta, 0) + r.t0 * 0)  # поток идёт от t0
            return f
        # состояние с учётом шва
        st = layout_state(i, u, T)
        prev = None
        k = 1.0
        if i > 0 and u - s[0] < TR and segs[i - 1][4] == s[4] or (i > 0 and u - s[0] < TR):
            prev = layout_state(i - 1, s[0] - 1e-3, T)
            k = ease((u - s[0]) / TR)
        cam_rect = lerp4(prev["cam"], st["cam"], k) if prev else st["cam"]
        push = lerp(prev["push"], st["push"], k) if prev else st["push"]
        cap_y = lerp(prev["cap"], st["cap"], k) if prev else st["cap"]
        crop_a = cam_crop(T, prev["cam"] if prev else st["cam"], push)
        crop_b = cam_crop(T, st["cam"], push)
        # кроп камеры лерпится в согласии с прямоугольником: берём кроп целевого аспекта
        ar = cam_rect[2] / cam_rect[3]
        cx, cy, cw, ch = lerp4(crop_a, crop_b, k)
        # поправка аспекта: держим высоту, ширину по аспекту рамки
        ncw = ch * ar
        if ncw > CW: ncw = CW; ch = ncw / ar
        ccx = cx + cw / 2
        cx = min(max(ccx - ncw / 2, 0), CW - ncw); cw = ncw
        cy = min(max(cy, 0), CH - ch)
        cam_img = crop(cf, (cx, cy, cw, ch))

        # фон
        frame = blur_bg(crop(cf, cam_crop(T, FULL)))
        # анимационный фон/контент
        def draw_top(frame, kind, seg, alpha):
            if kind == "anim":
                af = anim_frame(seg)
                if af is not None:
                    last_af[seg[3]] = af
                af = last_af.get(seg[3])
                if af is not None:
                    if alpha >= 0.999:
                        frame[:] = af
                    else:
                        frame[:] = cv2.addWeighted(af, alpha, frame, 1 - alpha, 0)
            elif kind == "screen":
                img = sf if sf is not None else last_sf
                if img is not None:
                    keys = seg[5]
                    us = [k_[0] for k_ in keys]
                    scr_rect = tuple(float(np.interp(u, us, [k_[1][j] for k_ in keys])) for j in range(4))
                    rect = TOP
                    if alpha < 0.999:  # въезд карточки
                        rect = (TOP[0], TOP[1] + 60 * (1 - alpha), TOP[2], TOP[3])
                    card(frame, crop(img, scr_rect), rect, R, alpha)
        if prev and prev["top"] and prev["top"] != st["top"]:
            draw_top(frame, prev["top"], segs[i - 1], 1 - k)
        if st["top"]:
            draw_top(frame, st["top"], s, k if (prev and prev["top"] != st["top"]) else 1.0)
        full = cam_rect[2] >= W - 1
        card(frame, cam_img, cam_rect, 0 if full else R * min(1, (W - cam_rect[2]) / 80 + 0.001), 1.0, sh=not full)

        # субтитры
        for ci, (c0, c1, _) in enumerate(cues):
            if c0 <= u < c1 and cue_np[ci] is not None:
                im = cue_np[ci]
                age = (u - c0) * 1000
                if age < MS.POP_MS:
                    kk = age / MS.POP_MS
                    sc_ = MS.POP_FROM + (1 - MS.POP_FROM) * (1 - (1 - kk) ** 3)
                    im = cv2.resize(im, (max(int(im.shape[1] * sc_), 1), max(int(im.shape[0] * sc_), 1)), interpolation=cv2.INTER_AREA)
                paste_rgba(frame, im, (W - im.shape[1]) // 2, cap_y - im.shape[0] // 2)
                break
        # хук / outro
        for img, is_out in ((hook_img, False), (outro_img, True)):
            if img is None: continue
            m = MS.card_motion(u, dur, is_out)
            if m is None: continue
            dy, al = m
            if st["cam"] == FULL and not prev:
                top = MS.HOOK_TOP_CAM - 60
            elif st["top"] == "anim":
                top = 930 - img.shape[0] + 60   # низ зоны анимации: заголовок сцены остаётся видно
            else:
                top = int(TOP[1] + TOP[3] / 2 - img.shape[0] / 2)
            paste_rgba(frame, img, (W - img.shape[1]) // 2, top + dy, al)
        if preview:
            cv2.imwrite(f"{OUT}_{u:06.2f}.jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        else:
            enc.stdin.write(np.ascontiguousarray(frame).tobytes())
    for r in readers.values(): r.close()
    if preview:
        return
    enc.stdin.close(); enc.wait()
    mux_audio(item, parts, starts, dur, OUT, anim_src, segs)

# ---- звук -------------------------------------------------------------------
BUNDLE = os.environ.get("SFX_BUNDLE", "media-use/audio/assets/sfx")
V4 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../motion-build/assets/style-v4/sfx")
G = {"whoosh": -21, "whoosh-short": -21, "pop": -19, "click": -17, "click-soft": -18, "ping": -18, "stamp": -16,
     "neg": -17, "typing": -13, "key-press": -16}
def sfx_path(nm):
    p = f"{V4}/{nm}.wav" if nm in ("stamp", "neg") else f"{BUNDLE}/{nm}.mp3"
    return p if os.path.exists(p) else None

def mux_audio(item, parts, starts, dur, OUT, anim_src, segs):
    ins, fl = [], []
    for pi, (a, b) in enumerate(parts):
        ins += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", VOICE]
        fl.append(f"[{pi + 1}:a]aresample=48000,aformat=channel_layouts=stereo,afade=t=in:d=0.015,afade=t=out:st={b - a - 0.02:.3f}:d=0.02[v{pi}]")
    fl.append("".join(f"[v{pi}]" for pi in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[voice]")
    mix = ["[voice]"]
    n = len(parts) + 1
    cues = []
    for s in segs:
        if s[2] != "anim": continue
        sc = s[3]; pi = s[4]
        f = f"{D}/W/scenes/{sc}.sfx.json"
        if not os.path.exists(f): continue
        for t, nm in json.load(open(f)):
            T = SCENES[sc]["start"] + t
            u = starts[pi] + T - parts[pi][0]
            if s[0] - 0.05 <= u < s[1] and nm in G and sfx_path(nm):
                cues.append((u, nm, G[nm]))
    for u, nm, g in cues:
        ins += ["-i", sfx_path(nm)]
        ms = int(u * 1000)
        fl.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,volume={g + 3}dB,adelay={ms}|{ms}[s{n}]")
        mix.append(f"[s{n}]"); n += 1
    if len(mix) > 1:
        fl.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first,alimiter=limit=0.95[a]")
    else:
        fl.append("[voice]anull[a]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", OUT + ".v.mp4"] + ins +
                   ["-filter_complex", ";".join(fl).replace("[0:a]", "[0:a]"), "-map", "0:v", "-map", "[a]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.3f}", OUT], check=True)
    os.remove(OUT + ".v.mp4")

if __name__ == "__main__":
    main()
