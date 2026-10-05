#!/usr/bin/env python3
"""
Слить находки охотников в один список кандидатов и подготовить судьям тексты.

  # охотник проверяет свой файл
  python3 candidates.py --validate /tmp/shorts/p/hunt/hook.json

  # оркестратор сливает всё
  python3 candidates.py --hunt-dir /tmp/shorts/p/hunt \
      --words /tmp/shorts/p/transcript/raw.words.json \
      --out /tmp/shorts/p/candidates.json --texts /tmp/shorts/p/cands

Каждому кандидату даётся cid вида `hook-03`, границы частей подтягиваются к
паузам тем же кодом, что и в make_plan.py, а текст берётся из пословного
транскрипта — ровно то, что услышит зритель. Судья получит только файл
`<texts>/<cid>.txt`: текст и длительность, без таймкодов, без имени охотника и
без его обоснования, чтобы оценка шла глазами постороннего.

У тизеров (`teaser-NN`) в конце текста судьи дописана финальная плашка — её
зритель тоже увидит. Кусок лонга, который охотник назвал ответом, уходит
отдельно в `<answers>/<cid>.txt` вместе с вопросом: его читает проверяющий
ответа, а не судья шортса.
"""
import argparse, glob, json, os, re, sys

HUNTERS = ("hook", "contra", "story", "payoff", "teaser")
MIN_DUR, MAX_DUR = 12.0, 62.0
ANSWER_MIN, ANSWER_MAX = 5.0, 90.0


def parse_t(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().strip("`")
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    parts = s.split(":")
    if not 2 <= len(parts) <= 3:
        raise ValueError(f"не понимаю время {v!r}")
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    return sec


def validate(doc, where):
    """Список ошибок формата — пусто, если файл годен."""
    errs = []
    if not isinstance(doc, dict) or not isinstance(doc.get("candidates"), list):
        return [f"{where}: нужен объект с полем candidates: [...]"]
    if doc.get("hunter") not in HUNTERS:
        errs.append(f"{where}: hunter должен быть одним из {HUNTERS}")
    for i, c in enumerate(doc["candidates"]):
        tag = f"{where}#{i}"
        for k in ("title", "parts", "hook", "why"):
            if not c.get(k):
                errs.append(f"{tag}: нет поля {k}")
        parts = c.get("parts") or []
        prev_end = -1.0
        total = 0.0
        for j, p in enumerate(parts):
            try:
                s, e = parse_t(p["start"]), parse_t(p["end"])
            except Exception as ex:
                errs.append(f"{tag} part{j}: {ex}")
                continue
            if e <= s:
                errs.append(f"{tag} part{j}: end раньше start")
            if s < prev_end:
                errs.append(f"{tag} part{j}: части должны идти по порядку и не пересекаться")
            prev_end = e
            total += e - s
        if parts and not MIN_DUR <= total <= MAX_DUR:
            errs.append(f"{tag}: суммарно {total:.1f}s, нужно {MIN_DUR:.0f}–{MAX_DUR:.0f}")
        if len(parts) > 4:
            errs.append(f"{tag}: {len(parts)} частей — слишком рваный")
        if doc.get("hunter") == "teaser":
            errs += validate_teaser(c, tag, parts)
    return errs


def validate_teaser(c, tag, parts):
    """У тизера есть вопрос, финальная плашка и ответ где-то в другом месте лонга."""
    errs = [f"{tag}: у тизера нет поля {k}" for k in ("question", "outro", "answer")
            if not c.get(k)]
    try:
        spans = [(parse_t(p["start"]), parse_t(p["end"])) for p in parts]
        answer = [(parse_t(p["start"]), parse_t(p["end"])) for p in c.get("answer") or []]
    except Exception as ex:
        return errs + [f"{tag} answer: {ex}"]
    if any(e <= s for s, e in answer):
        errs.append(f"{tag} answer: end раньше start")
    total = sum(e - s for s, e in answer)
    if answer and not ANSWER_MIN <= total <= ANSWER_MAX:
        errs.append(f"{tag} answer: {total:.1f}s, нужно {ANSWER_MIN:.0f}–{ANSWER_MAX:.0f}")
    for s, e in answer:
        if any(s < pe and ps < e for ps, pe in spans):
            errs.append(f"{tag} answer: ответ попадает в сам шортс — тизеру нечего "
                        f"обещать")
    return errs


def text_of(words, t0, t1):
    return " ".join(w["word"].strip() for w in words
                    if w["start"] >= t0 - 0.05 and w["end"] <= t1 + 0.05).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", help="проверить один файл охотника и выйти")
    ap.add_argument("--hunt-dir")
    ap.add_argument("--words")
    ap.add_argument("--out")
    ap.add_argument("--texts", help="папка для текстов судьям")
    ap.add_argument("--answers", help="папка для ответов тизеров "
                    "(по умолчанию answers/ рядом с --texts)")
    a = ap.parse_args()

    if a.validate:
        errs = validate(json.load(open(a.validate)), os.path.basename(a.validate))
        print("\n".join(errs) if errs else
              f"[ok] {len(json.load(open(a.validate))['candidates'])} кандидатов")
        sys.exit(1 if errs else 0)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from snap import snap_start, snap_end

    words = json.load(open(a.words))
    os.makedirs(a.texts, exist_ok=True)
    answers_dir = a.answers or os.path.join(os.path.dirname(os.path.abspath(a.texts)),
                                            "answers")
    out, bad = [], 0
    for path in sorted(glob.glob(os.path.join(a.hunt_dir, "*.json"))):
        doc = json.load(open(path))
        errs = validate(doc, os.path.basename(path))
        if errs:
            # Файл охотника с ошибками не выкидываем целиком — берём годные
            # кандидаты, а про остальные кричим, чтобы оркестратор видел потерю.
            print("\n".join("  ! " + e for e in errs))
        hunter = doc.get("hunter") or os.path.splitext(os.path.basename(path))[0]
        n = 0
        for c in doc.get("candidates", []):
            try:
                parts = [[snap_start(words, parse_t(p["start"])),
                          snap_end(words, parse_t(p["end"]))] for p in c["parts"]]
            except Exception:
                bad += 1
                continue
            texts = [text_of(words, s, e) for s, e in parts]
            dur = sum(e - s for s, e in parts)
            if not all(texts) or not MIN_DUR <= dur <= MAX_DUR:
                bad += 1
                print(f"  - {hunter}: «{c.get('title')}» выброшен "
                      f"({dur:.1f}s{'' if all(texts) else ', часть без речи'})")
                continue
            teaser = None
            if hunter == "teaser":
                try:
                    answer = [[snap_start(words, parse_t(p["start"])),
                               snap_end(words, parse_t(p["end"]))] for p in c["answer"]]
                except Exception:
                    bad += 1
                    continue
                answer_text = "\n\n".join(text_of(words, s, e) for s, e in answer)
                if not answer_text.strip():
                    bad += 1
                    print(f"  - teaser: «{c.get('title')}» выброшен (в ответе нет речи)")
                    continue
                teaser = {"question": c["question"], "outro": c["outro"],
                          "answer": answer, "answer_text": answer_text}
            n += 1
            cid = f"{hunter}-{n:02d}"
            item = {"cid": cid, "hunter": hunter, "title": c["title"],
                    "hook": c["hook"], "why": c["why"], "parts": parts,
                    "duration": round(dur, 1), "text": "\n\n".join(texts)}
            if teaser:
                item.update(teaser)
            out.append(item)
            with open(os.path.join(a.texts, cid + ".txt"), "w") as f:
                f.write(f"Длительность: {dur:.0f} секунд"
                        f"{f', склеек: {len(parts) - 1}' if len(parts) > 1 else ''}\n\n")
                f.write("\n\n".join(texts) + "\n")
                if teaser:
                    # Плашку зритель видит, значит, и судья должен её видеть.
                    # Где ответ и что в нём — нет: этого зритель ленты не знает.
                    f.write("\n[В последние 2 секунды поверх кадра плашка: "
                            f"«{teaser['outro'].replace('|', ' ').replace('*', '')}»]\n")
            if teaser:
                os.makedirs(answers_dir, exist_ok=True)
                with open(os.path.join(answers_dir, cid + ".txt"), "w") as f:
                    f.write(f"Большой вопрос, который шортс оставляет открытым:\n"
                            f"{teaser['question']}\n\n"
                            f"Финальная плашка шортса: "
                            f"«{teaser['outro'].replace('|', ' ').replace('*', '')}»\n\n"
                            f"Кусок лонга, который должен на это ответить "
                            f"({sum(e - s for s, e in answer):.0f} секунд):\n\n"
                            f"{answer_text}\n")
        print(f"  {hunter:7s} {n} кандидатов")

    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(f"[ok] {len(out)} кандидатов → {a.out}, тексты → {a.texts}"
          + (f"; выброшено {bad}" if bad else ""))


if __name__ == "__main__":
    main()
