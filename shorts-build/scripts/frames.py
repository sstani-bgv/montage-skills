#!/usr/bin/env python3
"""
Разметка кадров лонга по типам — общая для планирования и сборки.

Все пороги и прямоугольники приходят из calib.json (см. shorts-plan/scripts/
calibrate.py). Ничего про конкретную запись здесь не зашито: поменялся угол
съёмки или размер PiP — перекалибровал, код тот же.

Типы:
  split  экран + PiP-вебка в углу
  cam    камера на весь кадр
  broll  экран без вебки
"""
import json, subprocess
import cv2
import numpy as np


# Признаки считаются на кадре такого размера — ровно на таком их считала
# калибровка. Масштаб менять нельзя: плоскостность зависит от разрешения
# (окно box-фильтра фиксированное), и на полном кадре пороги поедут.
# Заодно разметка всего ролика становится в разы быстрее.
SCALE_W, SCALE_H = 480, 270


def load_calib(path):
    c = json.load(open(path)) if isinstance(path, str) else dict(path)
    fs = c.get("feature_scale")
    if fs and tuple(fs) != (SCALE_W, SCALE_H):
        raise SystemExit(
            f"calib.json посчитан на кадре {fs[0]}x{fs[1]}, а разметка считает "
            f"на {SCALE_W}x{SCALE_H}. Пороги при другом масштабе не действуют "
            f"(плоскостность зависит от разрешения) — перекалибруй исходник.")
    px, py, pw, ph = (c["pip_outer"][2], c["pip_outer"][3],
                      c["pip_outer"][0], c["pip_outer"][1])
    kx, ky = SCALE_W / c["width"], SCALE_H / c["height"]
    c["_pip_box"] = (int(px * kx), int(py * ky), int(pw * kx), int(ph * ky))
    return c


def flatness(f):
    """Доля пикселей в ровной заливке — главный признак «это экран, а не комната».

    У слайдов и UI огромные однотонные площади, у снятой камерой комнаты их нет:
    там всюду шум матрицы и фактура. Признак не зависит от цвета слайда, поэтому
    синяя плашка и белый документ считаются одинаково экраном."""
    g = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY).astype(np.float32)
    mean = cv2.boxFilter(g, -1, (7, 7))
    sq = cv2.boxFilter(g * g, -1, (7, 7))
    return float((np.sqrt(np.maximum(sq - mean * mean, 0)) < 2.0).mean())


def warm(f, box=None):
    r = f if box is None else f[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
    return float(r[..., 0].mean() - r[..., 2].mean())


def pip_flatness(f, box):
    """Доля ровной заливки внутри прямоугольника PiP.

    Живая вставка — это снятая камерой комната: шум матрицы, фактура, ровных
    площадей почти нет. Пустой угол слайда, наоборот, ровный целиком."""
    x, y, w, h = box
    return flatness(np.ascontiguousarray(f[y:y + h, x:x + w]))


def frame_label(f, calib):
    """f — RGB-кадр, приведённый к SCALE_W x SCALE_H."""
    if flatness(f) <= calib["flat_threshold"]:
        return "cam"
    # split vs broll — есть ли в углу живая вставка. Признак «тёплость угла»
    # (pip_threshold) здесь не работает: белый угол слайда даёт R-B около нуля,
    # ровно как вставка на светлом фоне комнаты, и слайд без вебки уезжает в
    # split — в карточке вебки оказывается пустая белая заливка. Плоскостность
    # угла разводит эти два случая с большим запасом (вставка ≤0.51, пустой
    # угол ≥0.87 на проверенном исходнике).
    thr = calib.get("pip_flat_threshold", 0.7)
    return "broll" if pip_flatness(f, calib["_pip_box"]) > thr else "split"


def read_labels(src, t0, dur, fps, calib):
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t0}", "-t", f"{dur}", "-i", src,
         "-vf", f"fps={fps},scale={SCALE_W}:{SCALE_H}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    ch = SCALE_W * SCALE_H * 3
    n = len(p.stdout) // ch
    return [frame_label(np.frombuffer(p.stdout[i * ch:(i + 1) * ch], np.uint8)
                        .reshape(SCALE_H, SCALE_W, 3), calib) for i in range(n)]


def refine_boundary(src, tb, new_lab, calib, span=0.6, fps=30, run=3,
                    old_lab=None):
    """Уточнить рез до кадра.

    Грубая разметка идёт с шагом в полсекунды, и этого мало: последние доли
    секунды старого куска источник уже показывает новую сцену, а раскладка ещё
    старая — в кадре оказывается чужой контент. Досматриваем окно вокруг реза
    покадрово и берём момент, с которого новый тип держится подряд.

    Если известен уходящий тип (`old_lab`), рез ставится не туда, где новая
    сцена устоялась, а туда, где **кончилась старая**: в исходнике рез сделан
    кроссфейдом на 3–7 кадров, и эти кадры наплыва ещё считаются старой
    раскладкой. В `cam` это заметно как вспышка — слайд в момент наплыва
    раздувается на весь кадр 9:16. Шов всё равно идёт после реза, а уходящий
    слот доигрывает на замороженном кадре до наплыва, так что наплыв уезжает
    внутрь карточки, где он читается как обычный переход."""
    labs = read_labels(src, max(tb - span, 0), span * 2, fps, calib)
    if not labs:
        return tb
    for i in range(len(labs) - run):
        if all(l == new_lab for l in labs[i:i + run]):
            if old_lab is not None:
                # отмотать к первому кадру, который перестал быть старым типом
                j = i
                while j - 1 >= 0 and labs[j - 1] != old_lab:
                    j -= 1
                if j < i:
                    i = j
            return tb - span + i / fps
    return tb


def shot_types(src, t0, t1, calib, step=0.5, min_shot=2.0, refine=True):
    """Отрезок -> под-шоты [start, end, тип]."""
    labs = read_labels(src, t0, t1 - t0, 1 / step, calib)
    n = len(labs)
    if not n:
        return [[t0, t1, "split"]]
    shots, cur, st = [], labs[0], 0
    for i in range(1, n):
        if labs[i] != cur:
            shots.append([st * step, i * step, cur]); cur = labs[i]; st = i
    shots.append([st * step, n * step, cur])

    out = []
    for s, e, t in shots:
        if out and e - s < min_shot:     # мигание в один-два кадра — не под-шот
            out[-1][1] = e
        else:
            out.append([s, e, t])
    if len(out) > 1 and out[0][1] - out[0][0] < min_shot:
        out[1][0] = out[0][0]; out.pop(0)
    out = [[t0 + s, t0 + e, t] for s, e, t in out]

    if refine:
        for i in range(1, len(out)):
            tb = refine_boundary(src, out[i][0], out[i][2], calib,
                                 old_lab=out[i - 1][2])
            tb = min(max(tb, out[i - 1][0] + 0.5), out[i][1] - 0.5)
            out[i - 1][1] = out[i][0] = tb
    return out


def scan(src, calib, step=0.5, min_run=3.0):
    """Разметка всего ролика — для планирования. Возвращает список прогонов."""
    labs = read_labels(src, 0, calib["duration"], 1 / step, calib)
    runs, cur, st = [], labs[0], 0
    for i in range(1, len(labs)):
        if labs[i] != cur:
            runs.append([st * step, i * step, cur]); cur = labs[i]; st = i
    runs.append([st * step, len(labs) * step, cur])
    merged = []
    for s, e, t in runs:
        if merged and e - s < min_run:
            merged[-1][1] = e
        else:
            merged.append([s, e, t])
    return merged
