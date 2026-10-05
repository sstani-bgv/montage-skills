#!/usr/bin/env python3
"""
Калибровка исходного лонга: где окно экрана, где PiP-вебка, по каким порогам
различать типы кадра. Всё, что раньше было прибито константами под конкретную
запись, теперь считается из самого видео.

  uv run --with 'opencv-python-headless<5' --with numpy \
      python3 calibrate.py --src ~/Desktop/video.mov --out /tmp/shorts/calib.json

Пишет два файла:
  calib.json  — геометрия и пороги, их читают скрипты сборки
  calib.jpg   — калибровочный лист: рамки поверх кадра + по кадру на каждый
                найденный тип + неуверенные кадры

Калибровочный лист обязательно показать пользователю до сборки. Автодетект
ошибается тихо: сдвинутый на 20px прямоугольник PiP заметен только глазами,
а стоит он криво обрезанной вебки во всех шести шортсах.

Типы кадра:
  split  экран + PiP-вебка в углу
  cam    камера на весь кадр
  broll  экран без вебки

Кадры, которые не легли ни в один тип уверенно, попадают в `unknown` — их надо
показать пользователю и решить, что это, а не подгонять под известные типы.
"""
import argparse, json, os, subprocess
import cv2
import numpy as np

FEAT_W, FEAT_H = 480, 270 # на кадре такого размера считаются признаки
SAMPLE_FPS = 2.0          # частота опроса для статистики по всему ролику
STD_WIN = 12              # сколько подряд идущих кадров берём на карту движения
STD_STEP = 0.20           # шаг между ними, сек — слайд не должен успеть смениться
PIP_INSET = 0.045         # внутрь от границ PiP: скруглённые углы и светлая кромка
PATCH_GROW = 90           # на сколько расширить PiP, чтобы замазать его в слайде
MARGIN = 0.12             # полоса неуверенности: доля зазора между кластерами


# ---- утилиты ----------------------------------------------------------------
def probe(src):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", src],
        capture_output=True, text=True).stdout
    d = json.loads(out)
    return (int(d["streams"][0]["width"]), int(d["streams"][0]["height"]),
            float(d["format"]["duration"]))


def read_frames(src, t0, dur, fps, w, h, gray=False):
    fmt = "gray" if gray else "rgb24"
    ch = w * h * (1 if gray else 3)
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t0}", "-t", f"{dur}", "-i", src,
         "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", fmt, "-"],
        capture_output=True)
    n = len(p.stdout) // ch
    shape = (h, w) if gray else (h, w, 3)
    return [np.frombuffer(p.stdout[i * ch:(i + 1) * ch], np.uint8).reshape(shape)
            for i in range(n)]


def otsu(x, bins=96):
    """Порог между двумя модами. Возвращает (порог, насколько моды разделены 0..1)."""
    x = np.asarray(x, float)
    lo, hi = x.min(), x.max()
    if hi - lo < 1e-6:
        return float(lo), 0.0
    hist, edges = np.histogram(x, bins=bins, range=(lo, hi))
    p = hist / hist.sum()
    centres = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(p)
    m0 = np.cumsum(p * centres)
    mt = m0[-1]
    denom = w0 * (1 - w0)
    with np.errstate(divide="ignore", invalid="ignore"):
        between = np.where(denom > 1e-9, (mt * w0 - m0) ** 2 / denom, 0)
    i = int(np.argmax(between))
    # разделимость: межклассовая дисперсия к общей — 1 значит две чистые моды
    sep = float(between[i] / max(x.var(), 1e-9))
    return float(centres[i]), min(sep, 1.0)


def flatness(f):
    """Доля пикселей в идеально ровной заливке.

    Самый устойчивый признак «это экран, а не комната»: у слайда и UI огромные
    однотонные площади, у снятой камерой комнаты их нет вообще — там всюду шум
    матрицы и фактура. В отличие от цветовых признаков не зависит от того,
    белый слайд или синий: на memory.mov камера даёт 0.10-0.14, экран 0.49-0.97."""
    g = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY).astype(np.float32)
    mean = cv2.boxFilter(g, -1, (7, 7))
    sq = cv2.boxFilter(g * g, -1, (7, 7))
    return float((np.sqrt(np.maximum(sq - mean * mean, 0)) < 2.0).mean())


def low_cluster(x, k=4.0, lo_clamp=0.0, hi_clamp=1.0):
    """Граница сразу за самым нижним плотным кластером.

    Универсальные методы (Otsu, впадина плотности) здесь врут, потому что
    кластеры разной ширины: «камера» тесная (плоскостность 0.07-0.14), «экран»
    размазан от 0.22 до 0.99 и внутри сам многогорбый. Но нам и не нужна
    граница «посередине» — нужна граница сразу над камерой. Поэтому робастно
    подгоняем нижний кластер: берём нижние 2% как затравку и несколько раз
    пересчитываем среднее и разброс по тому, что попало в +-3 сигмы."""
    x = np.asarray(x, float)
    seed = x[x <= np.percentile(x, 2)]
    m, sd = float(seed.mean()), float(max(seed.std(), 1e-6))
    for _ in range(6):
        sel = x[np.abs(x - m) < 3 * sd + 1e-6]
        if sel.size < 3:
            break
        m, sd = float(sel.mean()), float(max(sel.std(), 1e-6))
    thr = float(np.clip(m + k * sd, lo_clamp, hi_clamp))
    share = float((x <= thr).mean())
    return thr, share, sd


def valley(x, bins=64, smooth=3):
    """Порог между двумя модами — по впадине плотности, а не по Otsu.

    Otsu кладёт границу между центрами мод и разваливается, когда моды разной
    ширины: у камеры плоскостность тесная (0.07-0.14), у экрана размазана от
    0.22 до 0.99, и Otsu уезжает на 0.50, отправляя тёмную IDE в «камеру».
    Впадина между пиками этого не боится. Возвращает (порог, глубина впадины)."""
    x = np.asarray(x, float)
    lo, hi = x.min(), x.max()
    if hi - lo < 1e-6:
        return float(lo), 0.0
    h, edges = np.histogram(x, bins=bins, range=(lo, hi))
    h = np.convolve(h.astype(float), np.ones(smooth) / smooth, "same")
    c = (edges[:-1] + edges[1:]) / 2
    p1 = int(np.argmax(h))
    far = max(bins // 8, 3)
    cand = [i for i in range(bins) if abs(i - p1) >= far]
    if not cand:
        return otsu(x)
    p2 = max(cand, key=lambda i: h[i])
    a, b = sorted((p1, p2))
    if b - a < 2:
        return otsu(x)
    v = a + int(np.argmin(h[a:b + 1]))
    depth = 1.0 - h[v] / max(min(h[p1], h[p2]), 1e-9)
    return float(c[v]), float(np.clip(depth, 0, 1))


def mmss(t):
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def warm(f, box=None):
    """Тёплость области: комната тёплая (R>B), экран холодный. Устойчиво к яркости."""
    r = f if box is None else f[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
    return float(r[..., 0].mean() - r[..., 2].mean())


def widest_gap(x, lo=0.35, hi=0.97, min_width=0.12, min_share=0.004):
    """Порог посреди самой широкой пустой щели — если щель вообще есть.

    Так разводятся split и broll: в прямоугольнике вебки либо живая комната
    (фактура, плоскостность низкая), либо пустой угол слайда (ровная заливка,
    плоскостность под единицу). Между кластерами на проверенном материале
    буквально пусто: 0.00-0.51 против 0.85-1.00, в середине ни одного кадра.

    Щель ищется, а не назначается, потому что её может не быть вовсе: если в
    записи вебка не уезжает ни разу, broll отсутствует как тип, и любой broll —
    ошибка классификации, из-за которой сборка замажет живое лицо. Раньше это
    приходилось угадывать руками флагом --no-broll, и на первой же записи
    угадали неверно: b-roll в ней был, но только в интро.

    Возвращает (порог, есть ли broll)."""
    x = np.sort(np.asarray(x, float))
    band = x[(x >= lo) & (x <= hi)]
    best = (0.0, None)
    edges = np.concatenate(([lo], band, [hi]))
    for a, b in zip(edges[:-1], edges[1:]):
        if b - a > best[0]:
            best = (b - a, (a + b) / 2)
    width, mid = best
    if mid is None or width < min_width:
        return 1.01, False                    # щели нет — всё экранное это split
    if float((x > mid).mean()) < min_share:
        return 1.01, False                    # «кластер» broll — считанные кадры
    return float(mid), True


# ---- геометрия --------------------------------------------------------------
def find_screen_rect(frames, W, H):
    """Окно захвата экрана: рамка вокруг него — гладкие обои, внутри — UI и текст."""
    acc = None
    for f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)
        mag = (np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, 3)) +
               np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, 3)))
        m = (mag > max(40.0, float(np.percentile(mag, 99)) * 0.20)).astype(np.uint8)
        acc = m if acc is None else np.maximum(acc, m)
    if acc is None or not acc.any():
        return (0, 0, W, H)
    acc = cv2.morphologyEx(acc, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))

    # по профилям, а не по общему bbox: одна случайная засветка на краю кадра
    # растянула бы bbox на весь экран, а профиль её просто не заметит
    def span(prof):
        thr = prof.max() * 0.08
        idx = np.where(prof > thr)[0]
        return (int(idx[0]), int(idx[-1]) + 1) if len(idx) else (0, len(prof))

    x0, x1 = span(acc.mean(axis=0))
    y0, y1 = span(acc.mean(axis=1))
    if (x1 - x0) * (y1 - y0) < W * H * 0.35:        # ничего осмысленного не нашли
        return (0, 0, W, H)
    return (x0 & ~1, y0 & ~1, (x1 - x0) & ~1, (y1 - y0) & ~1)


def find_pip(src, t, W, H):
    """PiP ищем по временной дисперсии: вебка живёт, слайд под ней стоит.

    Главный признак — движение ЛОКАЛЬНО. На кадре, где камера занята весь экран,
    тёплая движущаяся область тоже есть (это сам человек), но она размазана по
    всему кадру; у вставки движение заперто в маленький прямоугольник. Поэтому
    сначала отбрасываем кадры, где шевелится слишком много, и только потом ищем
    прямоугольного тёплого кандидата.

    Возвращает (rect, score) либо (None, 0)."""
    fs = read_frames(src, t, STD_WIN * STD_STEP, 1 / STD_STEP, W, H)
    if len(fs) < 4:
        return None, 0.0
    stack = np.stack([f.astype(np.float32) for f in fs])
    std = stack.std(axis=0).mean(axis=2)
    thr = max(4.0, float(np.percentile(std, 99.3)) * 0.30)
    m = (std > thr).astype(np.uint8)
    if m.mean() > 0.30:                      # шевелится весь кадр — это не вставка
        return None, 0.0
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    med = np.median(stack, axis=0).astype(np.uint8)
    best, best_score = None, 0.0
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        frac = a / (W * H)
        if not (0.004 < frac < 0.25) or w < 60 or h < 40:
            continue
        if fill_edges(x, y, w, h, W, H) >= 3:  # прилипло к трём краям — не вставка
            continue
        fill = a / (w * h)                     # вставка прямоугольная, а не клякса
        if fill < 0.55:
            continue
        wm = warm(med, (x, y, w, h))
        if wm < 5:
            continue
        score = fill * wm
        if score > best_score:
            best, best_score = (int(x), int(y), int(w), int(h)), score
    return best, best_score


def fill_edges(x, y, w, h, W, H, tol=8):
    return sum([x <= tol, y <= tol, x + w >= W - tol, y + h >= H - tol])


def locate_pip(src, dur, W, H, small=None, probes=9):
    """Прогоняем детектор по нескольким моментам и выбираем стойкого кандидата.

    Одного согласия мало. Если на экране проигрывается ЧУЖОЕ видео, в котором
    есть человек, у настоящей вставки появляется конкурент — вторая тёплая
    движущаяся область, и на реакционном ролике детектор ушёл именно к ней.

    Различает их постоянство. Настоящая вставка тёплая почти на всех экранных
    кадрах записи, потому что она поверх всего и всегда; человек внутри
    проигрываемого видео тёплый только пока идёт этот кусок. Поэтому кандидатов
    сначала собираем, а потом меряем каждого по всей записи и берём того, кто
    держится дольше.

    Возвращает (rect, доля тёплых кадров, сколько замеров сошлось)."""
    hits = []
    for t in np.linspace(dur * 0.08, dur * 0.92, probes):
        r, sc = find_pip(src, float(t), W, H)
        if r:
            hits.append((r, sc, float(t)))
            print(f"    {t:6.0f}s -> {r} ({sc:.1f})")
    if not hits:
        return None, 0.0, 0

    # кластеризуем по центру: совпавшие прямоугольники усредняем
    cent = np.array([[r[0] + r[2] / 2, r[1] + r[3] / 2] for r, _, _ in hits])
    tol = max(W, H) * 0.04
    groups, taken = [], set()
    for i, c in enumerate(cent):
        if i in taken:
            continue
        g = [j for j, cc in enumerate(cent) if np.hypot(*(cc - c)) < tol]
        taken.update(g)
        rects = np.array([hits[j][0] for j in g])
        groups.append((tuple(int(v) & ~1 for v in np.median(rects, axis=0)), len(g)))

    if small is None or len(groups) == 1:
        rect, n = max(groups, key=lambda g: g[1])
        return rect, 1.0, n

    k = np.array([FEAT_W / W, FEAT_H / H, FEAT_W / W, FEAT_H / H])
    best, best_score = None, -1.0
    for rect, n in groups:
        box = tuple((np.array(rect) * k).astype(int))
        hot = float(np.mean([warm(f, box) > 8 for f in small]))
        print(f"    кандидат {rect}: тёплый на {hot*100:.0f}% кадров, "
              f"замеров {n}")
        score = hot + 0.05 * n
        if score > best_score:
            best, best_score = (rect, hot, n), score
    return best


def grow_pip(src, seed, dur, W, H, samples=7):
    """Дорастить рамку вставки от найденного зерна до настоящих границ.

    Детектор движения находит вставку по тому, что в ней шевелится, — а
    шевелится там человек, не весь прямоугольник. Стена и мебель внутри вставки
    стоят неподвижно, и рамка получается заметно меньше реальной: на проверке
    190x134 вместо 596x350.

    Но вставка — это тёплый прямоугольник на холодном экране целиком, включая
    неподвижный фон комнаты. Поэтому берём медианный кадр, отделяем тёплое от
    холодного и разрастаемся от зерна по связной области. Человек внутри
    ЧУЖОГО видео на экране в ту же область не попадёт: их разделяет холодный
    интерфейс."""
    fs = []
    for t in np.linspace(dur * 0.08, dur * 0.92, samples):
        f = read_frames(src, float(t), 0.05, 30, W, H)
        if f and warm(f[0], seed) > 8:
            fs.append(f[0])
    if len(fs) < 2:
        return seed
    med = np.median(np.stack(fs), axis=0).astype(np.uint8)
    hot = ((med[..., 0].astype(int) - med[..., 2].astype(int)) > 6).astype(np.uint8)
    hot = cv2.morphologyEx(hot, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(hot, 8)
    cy, cx = seed[1] + seed[3] // 2, seed[0] + seed[2] // 2
    i = int(lab[cy, cx])
    if i == 0:
        return seed
    x, y, w, h, a = stats[i]
    if not (0.004 < a / (W * H) < 0.30) or a / max(w * h, 1) < 0.5:
        return seed                      # разрослось во что-то не прямоугольное
    grown = (int(x) & ~1, int(y) & ~1, int(w) & ~1, int(h) & ~1)
    if grown[2] * grown[3] < seed[2] * seed[3]:
        return seed
    print(f"    рамка доращена: {seed} -> {grown}")
    return grown


def empty_spot(frames, screen, size):
    """Самый пустой кусок слайда такого же размера — им замазываем PiP."""
    sx, sy, sw, sh = screen
    pw, ph = size
    g = cv2.cvtColor(np.median(np.stack(frames), axis=0).astype(np.uint8),
                     cv2.COLOR_RGB2GRAY)[sy:sy + sh, sx:sx + sw]
    mag = (np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, 3)) +
           np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, 3)))
    ii = cv2.integral(mag)
    best, best_e = (0, 0), None
    for y in range(0, max(sh - ph, 1), 24):
        for x in range(0, max(sw - pw, 1), 24):
            e = (ii[y + ph, x + pw] - ii[y, x + pw] - ii[y + ph, x] + ii[y, x])
            if best_e is None or e < best_e:
                best, best_e = (x, y), e
    return best


# ---- калибровочный лист -----------------------------------------------------
def label_strip(imgs, titles, width=1600, cell_h=300):
    cells = []
    for im, t in zip(imgs, titles):
        h, w = im.shape[:2]
        c = cv2.resize(im, (int(w * cell_h / h), cell_h))
        cv2.rectangle(c, (0, cell_h - 34), (c.shape[1], cell_h), (16, 18, 22), -1)
        cv2.putText(c, t, (10, cell_h - 11), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                    (255, 255, 255), 1, cv2.LINE_AA)
        cells.append(c)
    strip = np.hstack(cells) if cells else np.zeros((cell_h, width, 3), np.uint8)
    s = width / strip.shape[1]
    return cv2.resize(strip, (width, max(int(strip.shape[0] * s), 1)))


def proof_sheet(path, hero, rows):
    parts = [cv2.resize(hero, (1600, int(hero.shape[0] * 1600 / hero.shape[1])))]
    for imgs, titles in rows:
        if imgs:
            parts.append(label_strip(imgs, titles))
    cv2.imwrite(path, np.vstack(parts), [cv2.IMWRITE_JPEG_QUALITY, 88])


# ---- основное ---------------------------------------------------------------
def calibrate(src, out_json, out_jpg, pip_override=None, no_broll=False,
              screen_override=None, no_cam=False):
    W, H, dur = probe(src)
    print(f"[calib] {os.path.basename(src)} {W}x{H} {dur:.0f}s")

    small = read_frames(src, 0, dur, SAMPLE_FPS, FEAT_W, FEAT_H)
    times = [i / SAMPLE_FPS for i in range(len(small))]
    warm_all = np.array([warm(f) for f in small])

    # Геометрию ищем на кадрах с экраном. Отличить экран от комнаты по абсолютной
    # «тёплости» нельзя: синий слайд даёт R-B около -90, белый около -12, комната
    # около +30 — три моды, и Otsu режет между синими слайдами и остальным.
    # Поэтому сначала находим сам PiP (он не зависит от цвета слайда), а типы
    # кадра различаем уже относительными признаками.
    if pip_override is not None:
        pip, agree = tuple(pip_override), -1     # -1 = задано руками, не найдено
        print(f"[calib] PiP задан вручную: {pip}")
    else:
        print("[calib] ищу PiP:")
        pip, hot, agree = locate_pip(src, dur, W, H, small=small)
        if pip is not None:
            pip = grow_pip(src, pip, dur, W, H)
            print(f"[calib] PiP {pip} — сошлись {agree} замеров из 9, "
                  f"тёплый на {hot*100:.0f}% кадров")
            if agree < 3 and hot < 0.5:
                # Один сбойный замер способен перевесить два верных: на проверке
                # мусорный кандидат набрал оценку 26 против 14 и 17 у правильных,
                # и спасло только то, что правильных оказалось больше.
                print("[calib] ! мало согласия. Посмотри рамку на листе особенно "
                      "внимательно, а если она мимо — задай её руками: "
                      "--pip x,y,w,h")
    if pip is None:
        raise SystemExit("Не нашёл PiP-вебку. Либо её нет (тогда типы только "
                         "cam/broll), либо она нигде не двигалась — проверь запись.")
    px, py, pw, ph = pip

    # экран против комнаты — по плоским заливкам, тут признак чистый и бимодальный
    flat = np.array([flatness(f) for f in small])
    flat_t, flat_sep, flat_sd = low_cluster(flat, k=4.0, lo_clamp=0.15, hi_clamp=0.45)
    is_screen = flat > flat_t
    print(f"[calib] порог экран/комната flat={flat_t:.2f} "
          f"(камеры {flat_sep*100:.0f}%, экрана {is_screen.mean()*100:.0f}%)")
    if no_cam:
        # Смонтированный лонг без полноэкранной камеры: тёмные слайды и IDE по
        # плоскостности похожи на комнату, и на vsl в cam уехало 55% кадров —
        # при том что камеры во весь кадр там не было ни секунды.
        flat_t, is_screen = -1.0, np.ones_like(flat, dtype=bool)
        print("[calib] cam отключён вручную: все кадры — экран")

    # split против broll — по тёплости прямоугольника вебки на экранных кадрах
    k = np.array([FEAT_W / W, FEAT_H / H, FEAT_W / W, FEAT_H / H])
    pbox = tuple((np.array([px, py, pw, ph]) * k).astype(int))
    pip_warm = np.array([warm(f, pbox) for f in small])
    # тот же приём: нижний кластер здесь — broll (в прямоугольнике слайд,
    # холодный), split сидит заметно выше, а между ними кадры перехода
    # Здесь оба кластера умеренной ширины, но между ними много кадров перехода
    # (вебка выезжает), поэтому не подгоняем кластер, а берём точку между
    # робастными центрами, чуть ближе к холодному: спорный кадр лучше отдать в
    # split — там вебка просто есть, а ошибочный broll замажет живой контент.
    ps = pip_warm[is_screen]
    lo_c = float(np.median(ps[ps <= np.percentile(ps, 20)]))
    hi_c = float(np.median(ps[ps >= np.percentile(ps, 60)]))
    pip_t = lo_c + 0.45 * (hi_c - lo_c)

    # Разводит split и broll не тёплость, а плоскостность прямоугольника вебки.
    # Тёплость здесь врёт: у живой вставки R-B около +20, у синего слайда -90,
    # но у БЕЛОГО угла слайда -3 — с той же стороны, что и вставка. Слайд без
    # вебки уезжал в split, и карточка вебки в шортсе получалась пустой белой
    # заливкой. `pip_threshold` остаётся в calib.json как справка, разметка
    # живёт на `pip_flat_threshold`.
    pip_flat = np.array([flatness(np.ascontiguousarray(
        f[pbox[1]:pbox[1] + pbox[3], pbox[0]:pbox[0] + pbox[2]])) for f in small])
    pflat_t, has_broll = widest_gap(pip_flat[is_screen])
    if no_broll:
        pflat_t, has_broll = 1.01, False
        print("[calib] broll отключён вручную: все экранные кадры -> split")
    elif not has_broll:
        print("[calib] broll в записи не найден: вебка не уезжает ни разу, "
              "все экранные кадры -> split")
    else:
        print(f"[calib] порог PiP flat={pflat_t:.2f} "
              f"(со вставкой {(pip_flat[is_screen] <= pflat_t).mean()*100:.0f}% экранных)")

    lab = np.where(~is_screen, "cam",
                   np.where(pip_flat <= pflat_t, "split", "broll"))

    if (lab == "split").mean() < 0.02:
        raise SystemExit("Кадров «экран + вебка» почти нет — либо это чистая "
                         "говорящая голова, либо PiP найден неверно. Посмотри calib.jpg.")

    a = int(np.argmax(np.convolve((lab == "split").astype(float),
                                  np.ones(int(SAMPLE_FPS * 10)), "valid")))
    geo_idx = np.linspace(a, a + int(SAMPLE_FPS * 10) - 1, 6).astype(int)
    geo_idx = np.clip(geo_idx, 0, len(small) - 1)
    geo_frames = [read_frames(src, times[i], 0.05, 30, W, H)[0] for i in geo_idx]
    hero_t = times[geo_idx[len(geo_idx) // 2]]

    screen = (tuple(screen_override) if screen_override
              else find_screen_rect(geo_frames, W, H))
    print(f"[calib] экран {screen}"
          + (" (задан руками)" if screen_override else ""))

    ins = int(min(pw, ph) * PIP_INSET)
    pip_crop = (pw - ins * 2, ph - ins * 2, px + ins, py + ins)     # (w,h,x,y)

    sx, sy, sw, sh = screen
    gx0 = max(px - PATCH_GROW - sx, 0)
    gy0 = max(py - PATCH_GROW - sy, 0)
    gx1 = min(px + pw + PATCH_GROW - sx, sw)
    gy1 = min(py + ph + PATCH_GROW - sy, sh)
    patch = (gx1 - gx0, gy1 - gy0, gx0, gy0)                        # (w,h,x,y) в слайде
    patch_src = empty_spot(geo_frames, screen, (patch[0], patch[1]))

    # Неуверенные: признак сидит вплотную к порогу — их показываем пользователю,
    # а не подгоняем под известный тип.
    #
    # Полосу меряем в масштабе САМИХ КЛАСТЕРОВ, а не полного разброса признака.
    # Через разброс не работает: порог по построению стоит вплотную к нижнему
    # кластеру (среднее плюс 4 сигмы), а полный разброс задаётся выбросами, и
    # полоса накрывала весь нижний кластер целиком. На проверке это давало 22%
    # неуверенных на одной записи и 81% на другой — то есть метрика мерила не
    # уверенность, а форму гистограммы.
    mf = max(flat_sd, 1e-4)                       # сигма камерного кластера
    mp = MARGIN                                   # доля зазора между кластерами
    unsure = (np.abs(flat - flat_t) < mf) | (
        is_screen & has_broll & (np.abs(pip_flat - pflat_t) < mp))
    share = {t: float((lab == t).mean()) for t in ("split", "cam", "broll")}
    print("[calib] доли типов:", {k2: f"{v*100:.0f}%" for k2, v in share.items()},
          f"| неуверенных {unsure.mean()*100:.0f}%")

    calib = {
        "src": os.path.abspath(src), "width": W, "height": H, "duration": dur,
        "screen_crop": [sw, sh, sx, sy],
        "pip_crop": list(pip_crop),
        "pip_outer": [pw, ph, px, py], "pip_agreement": agree,
        "patch": list(patch),
        "patch_src": list(patch_src),
        # пороги действительны только на этом масштабе: плоскостность
        # зависит от разрешения, окно box-фильтра фиксированное
        "feature_scale": [FEAT_W, FEAT_H],
        "flat_threshold": flat_t, "pip_threshold": pip_t,
        "pip_flat_threshold": pflat_t, "has_broll": bool(has_broll),
        "cam_share": flat_sep,
        "split_share_of_screen": float((pip_flat[is_screen] <= pflat_t).mean()),
        "type_share": share, "unsure_share": float(unsure.mean()),
    }
    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    json.dump(calib, open(out_json, "w"), ensure_ascii=False, indent=1)

    # ---- лист на согласование
    hero = cv2.cvtColor(read_frames(src, hero_t, 0.05, 30, W, H)[0], cv2.COLOR_RGB2BGR)
    cv2.rectangle(hero, (sx, sy), (sx + sw, sy + sh), (255, 140, 0), 4)
    cv2.rectangle(hero, (px, py), (px + pw, py + ph), (60, 220, 60), 4)
    ic = (pip_crop[2], pip_crop[3], pip_crop[0], pip_crop[1])
    cv2.rectangle(hero, (ic[0], ic[1]), (ic[0] + ic[2], ic[1] + ic[3]), (60, 220, 255), 2)
    cv2.putText(hero, "screen", (sx + 10, sy + 42), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
                (255, 140, 0), 3, cv2.LINE_AA)
    cv2.putText(hero, "pip / crop", (px, py - 14), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                (60, 220, 60), 3, cv2.LINE_AA)

    rows = []
    imgs, titles = [], []
    for t in ("split", "cam", "broll"):
        idx = np.where((lab == t) & ~unsure)[0]
        if not len(idx):
            continue
        i = int(idx[len(idx) // 2])
        imgs.append(cv2.cvtColor(small[i], cv2.COLOR_RGB2BGR))
        # Раньше тут стояло «~920s» — таймкод кадра-примера, но читался он как
        # длительность типа, и подписи не сходились с длиной ролика.
        # Подписи только ASCII: лист рисуется шрифтом Hershey из OpenCV, он
        # кириллицу не умеет и молча выводит вместо неё «?????».
        titles.append(f"{t}  {share[t]*100:.0f}%  total {mmss(share[t] * dur)}"
                      f"  |  frame @ {mmss(times[i])}")
    rows.append((imgs, titles))

    ui = np.where(unsure)[0]
    if len(ui):
        pick = ui[np.linspace(0, len(ui) - 1, min(5, len(ui)), dtype=int)]
        rows.append(([cv2.cvtColor(small[i], cv2.COLOR_RGB2BGR) for i in pick],
                     [f"UNKNOWN? @ {mmss(times[i])}" for i in pick]))

    proof_sheet(out_jpg, hero, rows)
    print("[ok]", out_json, "|", out_jpg)
    return calib


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default="/tmp/shorts/calib.json")
    ap.add_argument("--jpg", default=None)
    ap.add_argument("--no-broll", action="store_true",
                    help="В записи вебка стоит всегда: считать все экранные кадры split. "
                         "Проверить можно так: если в прямоугольнике вебки нет ни одного "
                         "участка без движения длиннее 4 с, broll в записи отсутствует.")
    ap.add_argument("--pip", default=None,
                    help="Прямоугольник вебки вручную: x,y,w,h. Нужен, когда автодетект "
                         "промахнулся (смотри calib.jpg). Пороги и заплатка пересчитываются "
                         "от заданной рамки.")
    ap.add_argument("--screen", default=None,
                    help="Окно экрана вручную: x,y,w,h. Нужен, когда рамка на calib.jpg "
                         "села мимо окна захвата.")
    ap.add_argument("--no-cam", action="store_true",
                    help="В записи нет камеры во весь кадр (смонтированный лонг: "
                         "экран + вставка всё время). Без флага тёмные слайды и IDE "
                         "уезжают в cam. Проверяй контактным листом до калибровки.")
    a = ap.parse_args()

    def rect(v, flag):
        if not v:
            return None
        r = tuple(int(x) for x in v.split(","))
        if len(r) != 4:
            raise SystemExit(f"{flag} ждёт четыре числа: x,y,w,h")
        return r

    calibrate(a.src, a.out, a.jpg or a.out.replace(".json", ".jpg"),
              rect(a.pip, "--pip"), a.no_broll, rect(a.screen, "--screen"),
              a.no_cam)
