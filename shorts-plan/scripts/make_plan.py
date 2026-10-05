#!/usr/bin/env python3
"""
Собрать plan.json из выбранных моментов: подрезать границы по речи, определить
раскладку, разложить субтитры.

  uv run --with 'opencv-python-headless<5' --with numpy python3 make_plan.py \
      --calib /tmp/shorts/calib.json --words /tmp/shorts/transcript/raw.words.json \
      --picks /tmp/shorts/picks.json --out /tmp/shorts/plan.json

picks.json — то, что выбрала модель (или shortlist.py после охотников и судьи):
  [{"id": "s01", "title": "...", "hook": "Одна задача —|*одна сессия*",
    "start": 136.0, "end": 166.0}]
  или со склейкой нескольких кусков исходника:
  [{"id": "s02-story", "parts": [[190.0, 214.5], [1302.0, 1321.0]], ...}]

Склейку сборка не умеет — она режет один непрерывный отрезок. Поэтому части
здесь же склеиваются в промежуточный файл `<out>/stitch/<id>.mp4` (той же
геометрии, что исходник, так что calib.json к нему подходит), слова
перекладываются на его время, и запись плана получает свои `src` и `words`.
Дальше она собирается как обычный отрезок 0..длительность.

Границы почти всегда стоит подвинуть: на слух «примерно с 136-й» означает
«с начала ближайшей фразы». Скрипт двигает их к ближайшей паузе в речи, чтобы
шортс не начинался с обрубка слова и не обрывался на полуслове.
"""
import argparse, json, os, subprocess, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir, "shorts-build", "scripts"))
import frames as F
import make_short as M

from snap import snap_start, snap_end


def layout_for(runs, t0, t1, min_share=0.12):
    """Одна раскладка на весь отрезок или auto, если внутри есть переключения.

    Мелкие вкрапления игнорируем: полсекунды другого типа посреди отрезка —
    это моргание разметки, а не повод городить шов."""
    hit = {}
    for s, e, lab in runs:
        ov = min(e, t1) - max(s, t0)
        if ov > 0:
            hit[lab] = hit.get(lab, 0) + ov
    total = max(sum(hit.values()), 1e-6)
    big = {k: v for k, v in hit.items() if v / total >= min_share}
    if len(big) <= 1:
        return max(hit, key=lambda k: hit[k]) if hit else "split"
    return "auto"


def stitch(src, words, runs, parts, out_mp4):
    """Склеить куски исходника в один файл, переложить слова и разметку кадров."""
    os.makedirs(os.path.dirname(out_mp4), exist_ok=True)
    chains, labels = [], ""
    for i, (s, e) in enumerate(parts):
        chains.append(f"[0:v]trim={s}:{e},setpts=PTS-STARTPTS[v{i}];"
                      f"[0:a]atrim={s}:{e},asetpts=PTS-STARTPTS[a{i}]")
        labels += f"[v{i}][a{i}]"
    fc = ";".join(chains) + f";{labels}concat=n={len(parts)}:v=1:a=1[v][a]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-filter_complex", fc,
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "12",
                    "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "256k", out_mp4], check=True)
    new_words, new_runs, off = [], [], 0.0
    for s, e in parts:
        for w in words:
            if w["start"] >= s - 0.05 and w["end"] <= e + 0.05:
                new_words.append({**w, "start": round(max(w["start"] - s, 0) + off, 3),
                                  "end": round(min(w["end"], e) - s + off, 3)})
        for rs, re_, lab in runs:
            a, b = max(rs, s), min(re_, e)
            if b > a:
                new_runs.append([a - s + off, b - s + off, lab])
        off += e - s
    wpath = os.path.splitext(out_mp4)[0] + ".words.json"
    json.dump(new_words, open(wpath, "w"), ensure_ascii=False)
    return wpath, new_words, new_runs, round(off, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", required=True)
    ap.add_argument("--words", required=True)
    ap.add_argument("--picks", required=True)
    ap.add_argument("--out", default="/tmp/shorts/plan.json")
    ap.add_argument("--runs", default=None, help="кэш разметки от timeline.py")
    a = ap.parse_args()

    calib = F.load_calib(a.calib)
    words = json.load(open(a.words))
    picks = json.load(open(a.picks))
    runs = (json.load(open(a.runs)) if a.runs and os.path.exists(a.runs)
            else F.scan(calib["src"], calib))

    M.use_calib(calib)
    shorts = []
    for p in picks:
        parts = p.get("parts") or [[p["start"], p["end"]]]
        # "exact": true — границы выставлены руками по паузам в аудио (обрезка
        # рыхлого хвоста посреди предложения); snap иначе утянет конец к
        # ближайшей точке и вернёт вырезанное или отрежет нужное.
        if not p.get("exact"):
            parts = [[snap_start(words, s), snap_end(words, e)] for s, e in parts]
        item = {"id": p["id"], "title": p.get("title", p["id"])}
        for k in ("hunter", "found_by", "score", "question", "answer"):
            if k in p:
                item[k] = p[k]
        if len(parts) == 1:
            (t0, t1), iw, ir = parts[0], words, runs
        else:
            out_mp4 = os.path.join(os.path.dirname(os.path.abspath(a.out)),
                                   "stitch", p["id"] + ".mp4")
            wpath, iw, ir, dur = stitch(calib["src"], words, runs, parts, out_mp4)
            t0, t1 = 0.0, dur
            item.update({"src": out_mp4, "words": wpath, "parts": parts,
                         "seams": [round(sum(e - s for s, e in parts[:i]), 2)
                                   for i in range(1, len(parts))]})
        lay = p.get("layout") or layout_for(ir, t0, t1)
        item.update({"layout": lay, "start": t0, "end": t1,
                     "track": p.get("track", lay in ("cam", "auto")),
                     "hook": p.get("hook"), "outro": p.get("outro"),
                     "cues": M.build_cues(iw, t0, t1)})
        shorts.append(item)
        where = (f"{t0:7.2f}-{t1:7.2f}" if len(parts) == 1 else
                 " + ".join(f"{s:.0f}-{e:.0f}" for s, e in parts))
        print(f"  {p['id']:10s} {where} ({t1-t0:5.1f}s) {lay:6s} "
              f"«{p.get('title','')}»")
        if t1 - t0 < 12:
            print("         ! короче 12s — для шортса маловато")
        if t1 - t0 > 60:
            print("         ! длиннее 60s — Shorts обрежет")

    plan = {"src": calib["src"], "calib": os.path.abspath(a.calib),
            "words": os.path.abspath(a.words), "shorts": shorts}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(plan, open(a.out, "w"), ensure_ascii=False, indent=1)
    print("[ok]", a.out)


if __name__ == "__main__":
    main()
