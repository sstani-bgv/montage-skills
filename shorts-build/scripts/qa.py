#!/usr/bin/env python3
"""
Приёмка собранного шортса: контактный лист плюс автоматические проверки.

  uv run --with 'opencv-python-headless<5' --with numpy python3 qa.py \
      --video /tmp/shorts/renders/s03.mp4 --plan /tmp/shorts/plan.json --id s03

Контактный лист нужен глазам, но глаза устают и пропускают. Две поломки, на
которых этот пайплайн уже стоял, ловятся машиной — они здесь и проверяются:

  вебка живая      В карточке вебки должна быть комната. Если туда попала
                   замазка PiP (её накладывали на кадр, а не на копию), в
                   карточке окажется размытая заливка слайда — холодная по
                   цвету и без деталей.

  субтитры видны   Реплики должны читаться: если полоса субтитров почти не
                   отличается от фона, значит слой не наложился.

  угол карточки    Совет, а не приговор. Замазка PiP, ошибочно наложенная на
                   живой слайд, оставляет в правом нижнем углу вялый участок.
                   Надёжно по выходному файлу это НЕ ловится — компрессия
                   возвращает высокие частоты, и на проверке синтетический
                   баг проходил порог. Поэтому здесь только «посмотри сюда»,
                   а настоящая защита — инвариант в compose.py, который
                   считает, на скольких кадрах замазка сработала, и падает,
                   если она сработала не на split-кадре.

Проверки намеренно грубые: их дело — поймать «собралось не то», а не оценить
монтаж. Тонкое смотри на контактном листе.
"""
import argparse, json, math, os, subprocess, sys
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_short as M
import frames

FLAT_FILL = 0.60       # выше — в карточке вебки ровная заливка, а не комната
# Выброс кадра над медианой САМОГО шортса. Замерено: на шести чистых шортсах
# выброс 0.00-0.01, на отрезке с уехавшей вставкой — 0.25. Порог посередине.
FLAT_DRIFT = 0.15
FACE_MIN = 0.25        # доля кадров с лицом, ниже которой cam — брак
EDGE_DROP = 0.60        # ниже — угол стоит глянуть глазами (совет, не проверка)
CAP_CONTRAST = 18.0     # разброс яркости в полосе субтитров
SAMPLES = 24            # кадров на проверку: дефект «вставка уехала» живёт 2-3 с
SEAM_OFFSETS = (-0.22, -0.04, 0.12, 0.52)  # до реза, на резе, внутри и после шва


def sample_times(dur, item=None):
    """Равномерные кадры плюс обязательные пробы каждого ручного шва."""
    times = list(np.linspace(dur * 0.04, dur * 0.96, SAMPLES))
    if item:
        start = float(item["start"])
        overrides = item.get("layout_overrides") or []
        for left, right in zip(overrides, overrides[1:]):
            boundary = (float(left["end"]) + float(right["start"])) / 2 - start
            times.extend(boundary + off for off in SEAM_OFFSETS)
        if item.get("hook"):
            times.extend((0.40, 1.30, min(M.HOOK_DUR - 0.08, dur * 0.90)))
    return sorted({round(min(max(float(t), 0.0), max(dur - 0.01, 0.0)), 3)
                   for t in times if math.isfinite(float(t))})


def seam_times(item):
    """Локальные таймкоды ручных переходов для отчёта и приёмки."""
    if not item:
        return []
    start = float(item["start"])
    overrides = item.get("layout_overrides") or []
    return [round((float(a["end"]) + float(b["start"])) / 2 - start, 3)
            for a, b in zip(overrides, overrides[1:])]


def frames_at(video, times, w=None, h=None):
    """Возвращает [(t, кадр)] — таймкод нужен, чтобы сказать, КУДА смотреть."""
    out = []
    for t in times:
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", video, "-frames:v", "1",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
        n = M.W * M.H * 3
        if len(p.stdout) >= n:
            out.append((float(t),
                        np.frombuffer(p.stdout[:n], np.uint8).reshape(M.H, M.W, 3)))
    return out


def duration(video):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", video],
        capture_output=True, text=True).stdout.strip() or 0)


def edges(a):
    g = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).astype(np.float32)
    return float((np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, 3)) +
                  np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, 3))).mean())


def card_flatness(f):
    """Плоскостность карточки вебки, приведённая к масштабу признаков frames.py.

    Плоскостность зависит от разрешения (окно box-фильтра фиксированное),
    поэтому карточку 1000x550 ужимаем до размера PiP-рамки на калибровочном
    масштабе — только тогда числа сравнимы с порогами из calib.json."""
    x, y, w, h = 40, M.BOT_Y, M.CARD_W, M.BOT_H
    card = f[y + 40:y + h - 40, x + 40:x + w - 40]
    bw, bh = (M.CALIB["_pip_box"][2], M.CALIB["_pip_box"][3]) if M.CALIB else (146, 80)
    return frames.flatness(cv2.resize(card, (max(bw, 8), max(bh, 8))))


def check_webcam(fs, layout, item=None):
    """В карточке вебки должна быть комната — и на КАЖДОМ кадре, а не хоть на одном.

    Прошлая версия брала максимум тёплости по девяти кадрам и этим прятала
    настоящий дефект: когда исходник смонтирован, вставка на пост-наездах
    уезжает из фиксированной рамки, и в карточку на две-три секунды попадает
    интерфейс приложения. В максимум такие кадры не попадают — проверка была
    зелёной на двух шортсах, где это происходило, нашли глазами.

    Тёплость R−B для приговора не годится: она меряет освещение комнаты, а не
    её наличие. На шортсе с дневным светом в полкадра комната давала 5.0 при
    пороге 6.0 — две ложные тревоги подряд. Плоскостность к цвету света
    равнодушна: комната фактурна (0.00–0.23 на проверенном материале), заливка
    замазки ровная (0.88).

    Абсолютный порог тут только один и намеренно грубый — на ту поломку, ради
    которой проверка заводилась (в карточке заливка вместо комнаты). Дрейф
    рамки ловится иначе: внутри одного шортса комната однородна, поэтому кадр,
    сильно выпавший из медианы САМОГО шортса, и есть подозрительный. Порога из
    воздуха это не требует."""
    if layout not in ("split", "auto"):
        return None
    if layout == "auto" and item and item.get("layout_overrides") and not any(
            shot.get("layout") == "split" for shot in item["layout_overrides"]):
        return None
    vals = [(t, card_flatness(f)) for t, f in fs]
    if not vals:
        return None
    flat = np.array([v for _, v in vals])
    worst_t, worst = max(vals, key=lambda p: p[1])
    med = float(np.median(flat))

    if med > FLAT_FILL:
        return ("вебка живая", False,
                f"плоскостность карточки {med:.2f} (порог {FLAT_FILL}) — "
                f"в карточке ровная заливка, а не комната; похоже, туда попала "
                f"замазка PiP, см. references/gotchas.md")
    if layout == "auto" and worst > FLAT_FILL:
        return ("вебка живая", True,
                f"на {worst_t:.1f}s карточки вебки нет — для auto это допустимо, "
                f"проверь шов на контактном листе")
    drift = worst - med
    ok = drift < FLAT_DRIFT
    return ("вебка живая", ok,
            f"плоскостность медиана {med:.2f}, худший кадр {worst:.2f} на "
            f"{worst_t:.1f}s"
            + ("; комната ровно держится весь шортс" if ok else
               f" — выброс на {drift:.2f}: посмотри этот таймкод, обычно это "
               f"вставка, уехавшая из рамки на пост-наезде"))


def check_cam_face(fs, layout):
    """В раскладке `cam` в кадре обязан быть человек.

    `cam` режет центральные 9:16 полного кадра. Если отрезок на самом деле
    скринкаст, соберётся шортс без единого кадра с лицом — и ни одна прежняя
    проверка этого не замечала, потому что субтитры и хук рисуются исправно.
    Защита стоит и на входе (make_short.check_cam_source), но исходник мог быть
    размечен неверно, так что смотрим и на результат."""
    if layout != "cam":
        return None
    cas = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if cas.empty():
        return None
    hits = 0
    for _, f in fs:
        g = cv2.cvtColor(cv2.resize(f, (M.W // 4, M.H // 4)), cv2.COLOR_RGB2GRAY)
        if len(cas.detectMultiScale(g, 1.15, 5, minSize=(30, 30))):
            hits += 1
    share = hits / len(fs)
    ok = share >= FACE_MIN
    return ("в кадре человек", ok,
            f"лицо найдено на {share*100:.0f}% кадров (порог {FACE_MIN*100:.0f}%)"
            + ("" if ok else " — для cam это брак: похоже, отрезок не камера, "
                             "а экран, см. references/gotchas.md"))


def check_corner(fs, layout):
    """Совет: не выглядит ли правый нижний угол карточки подозрительно вялым.

    Это НЕ надёжная проверка замазки. На синтетическом тесте размытый угол дал
    0.81 против чистых 1.50 — то есть прошёл бы любой разумный порог, потому
    что компрессия возвращает в размытую область высокие частоты. Оставлено как
    подсказка, куда посмотреть глазами; жёсткая защита живёт в compose.py."""
    if layout not in ("broll", "auto"):
        return None
    ratios = []
    for _, f in fs:
        x, y, w, h = 40, M.BROLL_Y, M.W - 80, M.BROLL_H
        card = f[y:y + h, x:x + w]
        if card.size == 0:
            continue
        ch, cw = card.shape[:2]
        e_rest = edges(card[:int(ch * 0.55), :])
        if e_rest < 1.0:                     # пустой слайд — сравнивать не с чем
            continue
        ratios.append(edges(card[int(ch * 0.55):, int(cw * 0.55):]) / e_rest)
    if not ratios:
        return None
    worst = min(ratios)
    return ("угол карточки", True,
            f"резкость угла к остальному кадру {worst:.2f}"
            + ("" if worst > EDGE_DROP else
               " — заметно ниже кадра, глянь угол на листе"))


def check_captions(fs, cues, dur):
    """Полоса субтитров должна отличаться от фона — иначе слой не наложился."""
    if not cues:
        return None
    best = 0.0
    for yy in (M.CAP_Y_CAM, M.CAP_Y_SPLIT, M.CAP_Y_BROLL):
        for _, f in fs:
            strip = f[max(yy - 70, 0):min(yy + 70, M.H)]
            best = max(best, float(cv2.cvtColor(strip, cv2.COLOR_RGB2GRAY).std()))
    ok = best > CAP_CONTRAST
    return ("субтитры наложились", ok,
            f"контраст полосы {best:.1f} (порог {CAP_CONTRAST})")


def check_overlay_geometry(item):
    """Статическая защита от хука поверх важной b-roll карточки."""
    if not item or not item.get("hook"):
        return None
    start = float(item["start"])
    shots = item.get("layout_overrides") or []
    first = shots[0]["layout"] if shots else item.get("layout", "auto")
    if first == "auto":
        return ("хук не закрывает вставку", True,
                "без ручной разметки геометрию auto оцениваем по контактному листу")

    # hook_card включает по 60 px прозрачного поля/тени вокруг самой плашки.
    card_h = M.hook_card(item["hook"], first).height - 120
    hook_bottom = M.hook_top(first) + card_h
    hook_end = min(M.HOOK_DUR + M.HOOK_OUT, float(item["end"]) - start)
    broll_while_hook = []
    for shot in shots:
        a = max(float(shot["start"]) - start, 0.0)
        b = min(float(shot["end"]) - start, hook_end)
        if shot["layout"] == "broll" and b > a:
            broll_while_hook.append((a, b))
    if not shots and first == "broll":
        broll_while_hook.append((0.0, hook_end))
    if not broll_while_hook:
        return ("хук не закрывает вставку", True,
                "пока хук виден, b-roll не показывается")
    ok = hook_bottom <= M.BROLL_Y - 12
    spans = ", ".join(f"{a:.2f}-{b:.2f}s" for a, b in broll_while_hook)
    return ("хук не закрывает вставку", ok,
            f"низ плашки y={hook_bottom}, b-roll начинается y={M.BROLL_Y}; "
            f"совместно видны на {spans}" +
            ("" if ok else " — сократи хук, подними его или начни b-roll позже"))


def sheet(video, out, times, cols=6):
    """Контактный лист из тех же кадров, которыми QA реально проверял видео."""
    selected = frames_at(video, times[:24])
    if not selected:
        return out
    tw, th, label_h = 180, 320, 26
    rows = int(math.ceil(len(selected) / cols))
    canvas = np.full((rows * (th + label_h), cols * tw, 3), 24, np.uint8)
    for i, (t, frame) in enumerate(selected):
        thumb = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
        row, col = divmod(i, cols)
        y, x = row * (th + label_h), col * tw
        canvas[y:y + th, x:x + tw] = cv2.cvtColor(thumb, cv2.COLOR_RGB2BGR)
        cv2.putText(canvas, f"{t:.2f}s", (x + 7, y + th + 19),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.imwrite(out, canvas)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--id", default=None)
    ap.add_argument("--layout", default=None)
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--json", default=None, help="машинный JSON-отчёт")
    a = ap.parse_args()

    layout, cues, item = a.layout, None, None
    if a.plan and a.id:
        plan = json.load(open(a.plan))
        M.use_calib(plan["calib"])
        item = next(s for s in plan["shorts"] if s["id"] == a.id)
        layout, cues = item["layout"], item.get("cues")
    layout = layout or "auto"

    d = duration(a.video)
    times = sample_times(d, item)
    fs = frames_at(a.video, times)
    if not fs:
        print("[qa] не смог прочитать кадры — файл битый?")
        raise SystemExit(1)

    print(f"[qa] {os.path.basename(a.video)} {d:.1f}s, раскладка {layout}")
    results = [r for r in (check_webcam(fs, layout, item),
                           check_cam_face(fs, layout),
                           check_corner(fs, layout),
                           check_captions(fs, cues, d),
                           check_overlay_geometry(item)) if r]
    bad = 0
    for name, ok, detail in results:
        print(f"  {'ok  ' if ok else 'ПЛОХО'} {name}: {detail}")
        bad += not ok

    out = a.sheet or os.path.splitext(a.video)[0] + "_qa.jpg"
    priority = []
    for t in seam_times(item):
        priority.extend(t + off for off in SEAM_OFFSETS)
    if item and item.get("hook"):
        priority.extend((0.40, 1.30, min(M.HOOK_DUR - 0.08, d * 0.90)))
    sheet_times = sorted({round(min(max(t, 0.0), max(d - 0.01, 0.0)), 3)
                          for t in priority})
    sheet_times += [t for t in times if t not in sheet_times]
    sheet(a.video, out, sheet_times)
    print(f"[qa] контактный лист {out} — посмотри его глазами, автопроверки "
          f"ловят только грубые поломки")
    if a.json:
        report = {
            "id": a.id, "video": os.path.abspath(a.video), "duration": d,
            "layout": layout, "ok": bad == 0, "sheet": os.path.abspath(out),
            "seams": seam_times(item),
            "checks": [{"name": name, "ok": bool(ok), "detail": detail}
                       for name, ok, detail in results],
        }
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"[qa] JSON {a.json}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
