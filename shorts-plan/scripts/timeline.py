#!/usr/bin/env python3
"""
Таймлайн лонга для выбора моментов: что говорится, когда и что при этом в кадре.

  uv run --with 'opencv-python-headless<5' --with numpy python3 timeline.py \
      --calib /tmp/shorts/calib.json --words /tmp/shorts/transcript/raw.words.json \
      --out /tmp/shorts/timeline.md

Скрипт не решает, какие моменты брать — это редакторская работа, её делает
модель, читая timeline.md. Задача скрипта — дать для этого честную фактуру:
границы фраз по паузам в речи, длительность и раскладку кадра в каждый момент.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir, "shorts-build", "scripts"))
import frames as F

GAP = 0.55          # пауза, по которой режем речь на фразы
MAX_CHARS = 240     # длинную фразу всё равно разрываем, чтобы таблица читалась


def sentences(words, gap=GAP):
    out, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > gap or
                    len(" ".join(x["word"] for x in cur)) > MAX_CHARS or
                    cur[-1]["word"].strip().endswith((".", "?", "!"))):
            out.append(cur); cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    # Фразу, порванную по длине, а не по паузе или точке, помечаем «…»: без
    # этого охотник принимает обрезок за начало мысли и режет шортс посреди
    # фразы. Особенно на транскрипте whisper.cpp, где пауз между словами нет.
    res = []
    for i, c in enumerate(out):
        txt = " ".join(x["word"].strip() for x in c)
        prev = out[i - 1][-1] if i else None
        if prev and c[0]["start"] - prev["end"] <= gap and \
                not prev["word"].strip().endswith((".", "?", "!")):
            txt = "… " + txt
        res.append({"start": round(c[0]["start"], 2), "end": round(c[-1]["end"], 2),
                    "text": txt})
    return res


def layout_at(runs, t0, t1):
    """Какие раскладки встречаются на отрезке и какая главная."""
    hit = {}
    for s, e, lab in runs:
        ov = min(e, t1) - max(s, t0)
        if ov > 0:
            hit[lab] = hit.get(lab, 0) + ov
    if not hit:
        return "?", {}
    return max(hit, key=hit.get), hit


def tc(v):
    return f"{int(v)//60:02d}:{v - (int(v)//60)*60:05.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", required=True)
    ap.add_argument("--words", required=True)
    ap.add_argument("--out", default="/tmp/shorts/timeline.md")
    ap.add_argument("--runs-out", default=None)
    a = ap.parse_args()

    calib = F.load_calib(a.calib)
    src = calib["src"]
    print("[timeline] размечаю кадры...")
    runs = F.scan(src, calib)
    if a.runs_out:
        json.dump(runs, open(a.runs_out, "w"), ensure_ascii=False, indent=1)

    sents = sentences(json.load(open(a.words)))
    lines = [
        f"# Таймлайн — {os.path.basename(src)}",
        "",
        f"Длительность {calib['duration']:.0f}s. Доли кадров: " +
        ", ".join(f"{k} {v*100:.0f}%" for k, v in calib["type_share"].items()) + ".",
        "",
        "Раскладка в колонке — что в кадре В ЭТОТ момент исходника, а не что",
        "надо собрать: `split` экран с вебкой, `cam` камера целиком, `broll`",
        "экран без вебки. Отрезок, попавший на несколько раскладок, собирается",
        "как `auto` — он сам переключит их со швами.",
        "",
        "Текст, начинающийся с «…», — продолжение предыдущей строки, порванной",
        "по длине: это середина предложения, шортс с неё начинать нельзя.",
        "",
        "## Длинные однородные куски",
        "",
    ]
    for s, e, lab in runs:
        if e - s >= 8:
            lines.append(f"- `{tc(s)}–{tc(e)}` **{lab}** ({e-s:.0f}s)")

    lines += ["", "## Речь по фразам", "",
              "| начало | длит | кадр | текст |", "|---|---|---|---|"]
    for s in sents:
        main_lab, hit = layout_at(runs, s["start"], s["end"])
        mix = "+".join(sorted(hit, key=hit.get, reverse=True)) if len(hit) > 1 else main_lab
        txt = s["text"].replace("|", "/")
        lines.append(f"| `{tc(s['start'])}` | {s['end']-s['start']:.1f} | {mix} | {txt} |")

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    open(a.out, "w").write("\n".join(lines) + "\n")
    print("[ok]", a.out, f"— {len(sents)} фраз, {len(runs)} прогонов")


if __name__ == "__main__":
    main()
