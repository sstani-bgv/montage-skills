#!/usr/bin/env python3
"""
OpenCV-трекинг для фул-бли́д кадров с камерой.

Считает по кадрам: где голова (Haar) и сколько движения в кадре (руки, повороты),
из этого строит плавную траекторию окна 9:16 —

  спокойно говорит          -> окно сжимается, кадр наезжает
  поднял руки / активно     -> окно расширяется, кадр отъезжает
  повернул голову / махнул  -> центр окна немного едет в ту же сторону

и рендерит 1080x1920 без звука. Результат подставляется в make_short.py
как готовая подложка (`--track`), звук берётся из исходника.

Отдельно можно посмотреть, что натрекалось:  --debug out.mp4
"""
import argparse, os, subprocess
import cv2
import numpy as np

SRC_W, SRC_H = 1920, 1080               # перезаписывается probe_dims() по факту исходника
OUT_W, OUT_H = 1080, 1920
AR = OUT_W / OUT_H                      # 0.5625

# насколько окно «дышит» (доля высоты исходника)
ZOOM_TIGHT = 0.84                       # спокойно — ближе
ZOOM_WIDE = 1.00                        # активно — шире
# как далеко центр окна уезжает за головой/движением, в долях ширины окна
PAN_FACE = 1.00                         # за головой — полностью
PAN_MOTION = 0.16                       # за центром движения — чуть-чуть
HEAD_ROOM = 0.42                        # голова на этой высоте окна (0 — вверху)
FACE_KEEP = 0.40                        # полуширина лица в долях Haar-бокса,
                                        # которую окно обязано удержать в кадре

SMOOTH_POS = 0.55                       # сек, сглаживание центра
SMOOTH_ZOOM = 1.20                      # сек, сглаживание зума (медленнее — дышит)
DETECT_EVERY = 2


def probe_dims(src):
    """Реальные размеры исходника. Хардкод 1920x1080 не годится: запись с экрана
    часто отдаёт нестандартную ширину (1914 и т.п.), а rawvideo-поток читается
    построчно — расхождение в ширине превращает кадр в диагональную кашу."""
    global SRC_W, SRC_H
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", src],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h = (int(v) for v in out.split("x")[:2])
    SRC_W, SRC_H = w, h
    return w, h


def gaussian_smooth(x, sigma_frames):
    if sigma_frames < 0.6:
        return x
    r = int(sigma_frames * 3)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma_frames) ** 2)
    k /= k.sum()
    pad = np.pad(x, r, mode="edge")
    return np.convolve(pad, k, mode="valid")


def analyse(src, t0, t1, fps):
    """-> (face_cx, face_cy, face_h, motion, motion_cx) по кадрам, в пикселях исходника."""
    probe_dims(src)
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", f"{t0}", "-t", f"{t1-t0}", "-i", src,
         "-vf", f"fps={fps},scale=480:270", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE)
    CH = 480 * 270
    fx, fy, fh, mot, mcx = [], [], [], [], []
    prev = None
    last = None
    i = 0
    assert proc.stdout is not None
    while True:
        b = proc.stdout.read(CH)
        if len(b) < CH:
            break
        g = np.frombuffer(b, dtype=np.uint8).reshape(270, 480)

        if i % DETECT_EVERY == 0 or last is None:
            faces = cascade.detectMultiScale(g, 1.15, 5, minSize=(40, 40))
            if len(faces):
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                last = (x + w / 2, y + h / 2, h)
        if last is None:
            last = (240.0, 110.0, 70.0)
        fx.append(last[0]); fy.append(last[1]); fh.append(last[2])

        if prev is None:
            mot.append(0.0); mcx.append(last[0])
        else:
            d = cv2.absdiff(g, prev)
            m = (d > 18).astype(np.float32)
            s = m.sum()
            mot.append(s / m.size)
            mcx.append(float((m.sum(axis=0) * np.arange(480)).sum() / s) if s > 60 else last[0])
        prev = g
        i += 1
    proc.wait()
    k = SRC_W / 480.0
    return (np.array(fx) * k, np.array(fy) * k, np.array(fh) * k,
            np.array(mot), np.array(mcx) * k)


def plan(src, t0, t1, fps, tight=ZOOM_TIGHT, wide=ZOOM_WIDE, head=HEAD_ROOM):
    fx, fy, fh, mot, mcx = analyse(src, t0, t1, fps)
    n = len(fx)
    if n == 0:
        raise SystemExit("не удалось прочитать кадры")

    # активность 0..1 — по перцентилям самого отрезка, чтобы не подбирать пороги руками
    lo, hi = np.percentile(mot, 20), np.percentile(mot, 92)
    act = np.clip((mot - lo) / max(hi - lo, 1e-6), 0, 1)
    act = gaussian_smooth(act, SMOOTH_ZOOM * fps)

    zoom = tight + (wide - tight) * act
    crop_h = np.clip(zoom * SRC_H, 200, SRC_H)
    crop_w = crop_h * AR

    cx = fx * PAN_FACE + (mcx - fx) * PAN_MOTION
    cy = fy + (0.5 - head) * crop_h          # держим голову в верхней трети окна

    cx = gaussian_smooth(cx, SMOOTH_POS * fps)
    cy = gaussian_smooth(cy, SMOOTH_POS * fps)
    crop_w = gaussian_smooth(crop_w, SMOOTH_ZOOM * fps)
    crop_h = crop_w / AR

    x0 = np.clip(cx - crop_w / 2, 0, SRC_W - crop_w)

    # Сглаживание ведёт окно, но не имеет права отпустить лицо за край.
    # На резком повороте головы центр отстаёт на десятые доли секунды, и
    # краем кадра срезает нос и рот — на крупном плане это видно сразу, а
    # автопроверкам незаметно: лицо в кадре «есть», просто неполное.
    # Поэтому после сглаживания окно дожимается минимальным сдвигом, а
    # сам сдвиг ещё раз приглаживается, чтобы догон не читался ступенькой.
    half = fh * FACE_KEEP
    room = np.minimum(half, crop_w / 2 - 1)      # лицо шире окна — держим центр
    lo = np.clip(fx + room - crop_w, 0, None)
    hi = fx - room
    x0 = np.clip(x0, np.minimum(lo, hi), np.maximum(lo, hi))
    x0 = gaussian_smooth(x0, 0.12 * fps)
    x0 = np.clip(x0, np.minimum(lo, hi), np.maximum(lo, hi))
    x0 = np.clip(x0, 0, SRC_W - crop_w)

    y0 = np.clip(cy - crop_h / 2, 0, SRC_H - crop_h)
    return np.stack([x0, y0, crop_w, crop_h], axis=1)


def render(src, t0, t1, out, fps=30, debug=None, **kw):
    probe_dims(src)
    path = plan(src, t0, t1, fps, **kw)
    rd = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", f"{t0}", "-t", f"{t1-t0}", "-i", src,
         "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE)
    wr = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{OUT_W}x{OUT_H}", "-r", str(fps), "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", "16",
         "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    dbg = None
    if debug:
        dbg = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-s", f"{SRC_W//2}x{SRC_H//2}", "-r", str(fps), "-i", "-",
             "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", debug],
            stdin=subprocess.PIPE)

    CH = SRC_W * SRC_H * 3
    i = 0
    assert rd.stdout is not None and wr.stdin is not None
    while i < len(path):
        b = rd.stdout.read(CH)
        if len(b) < CH:
            break
        f = np.frombuffer(b, dtype=np.uint8).reshape(SRC_H, SRC_W, 3)
        x0, y0, cw, ch = path[i]
        x0, y0 = int(round(x0)), int(round(y0))
        cw, ch = int(round(cw)), int(round(ch))
        sub = f[y0:y0 + ch, x0:x0 + cw]
        wr.stdin.write(cv2.resize(sub, (OUT_W, OUT_H), interpolation=cv2.INTER_LANCZOS4)
                       .tobytes())
        if dbg:
            v = cv2.resize(f, (SRC_W // 2, SRC_H // 2))
            cv2.rectangle(v, (x0 // 2, y0 // 2), ((x0 + cw) // 2, (y0 + ch) // 2),
                          (0, 0, 255), 2)
            assert dbg.stdin is not None
            dbg.stdin.write(np.ascontiguousarray(v).tobytes())
        i += 1
    wr.stdin.close(); wr.wait()
    if dbg:
        assert dbg.stdin is not None
        dbg.stdin.close(); dbg.wait()
    rd.wait()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--debug", default=None, help="mp4 с нарисованным окном кадрирования")
    ap.add_argument("--tight", type=float, default=ZOOM_TIGHT)
    ap.add_argument("--wide", type=float, default=ZOOM_WIDE)
    ap.add_argument("--head", type=float, default=HEAD_ROOM)
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    render(a.src, a.start, a.end, a.out, a.fps, a.debug,
           tight=a.tight, wide=a.wide, head=a.head)
    print("[ok]", a.out)
