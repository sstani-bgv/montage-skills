#!/usr/bin/env python3
"""
Свести оценки судей: отсечь по порогу, схлопнуть дубли, собрать picks.json.

  python3 shortlist.py --candidates /tmp/shorts/p/candidates.json \
      --judged /tmp/shorts/p/judged --min 7 \
      --out /tmp/shorts/p/picks.json --report /tmp/shorts/p/selection.md

Дубли — это разные кандидаты про один и тот же кусок исходника (часто их
находят два охотника независимо). Из группы остаётся кандидат с высшим баллом,
а в `found_by` записываются все охотники, которые его нашли: совпадение двух
слепых охотников — полезный сигнал для аналитики.

Сколько шортсов получится, не задаётся: это решают охотники и судья. Бывает
тридцать, бывает три, бывает ноль.

Тизер (`teaser-NN`) проходит, только если проверяющий ответа подтвердил, что
лонг отвечает на открытый вопрос (`judged/<cid>.answer.json`, `delivers`).
Сильный шортс, который зовёт за несуществующим ответом, хуже, чем никакой.
"""
import argparse, json, os


def overlap(a, b):
    """Доля пересечения по времени исходника от более короткого кандидата."""
    inter = sum(max(0.0, min(e1, e2) - max(s1, s2))
                for s1, e1 in a["parts"] for s2, e2 in b["parts"])
    return inter / max(min(a["duration"], b["duration"]), 1e-6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--judged", required=True, help="папка <cid>.json от судей")
    ap.add_argument("--min", type=float, default=7)
    ap.add_argument("--dup", type=float, default=0.5,
                    help="доля пересечения, с которой два кандидата — один момент")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    a = ap.parse_args()

    cands = json.load(open(a.candidates))
    missing = []
    for c in cands:
        p = os.path.join(a.judged, c["cid"] + ".json")
        try:
            j = json.load(open(p))
            c["score"] = float(j["score"])
            c["judge"] = j
        except Exception:
            missing.append(c["cid"])
            c["score"] = None
        if c["hunter"] == "teaser":
            try:
                c["answer_check"] = json.load(open(
                    os.path.join(a.judged, c["cid"] + ".answer.json")))
            except Exception:
                missing.append(c["cid"] + " (ответ)")
                c["answer_check"] = None
    if missing:
        print("! без оценки (перезапусти судей на них):", ", ".join(missing))

    def answer_ok(c):
        chk = c.get("answer_check")
        return c["hunter"] != "teaser" or bool(chk and chk.get("delivers"))

    ranked = sorted((c for c in cands if c["score"] is not None),
                    key=lambda c: (-c["score"], c["duration"]))
    kept, groups = [], {}
    for c in ranked:
        twin = next((k for k in kept if overlap(k, c) >= a.dup), None)
        if twin:
            groups[twin["cid"]].append(c)
        else:
            kept.append(c)
            groups[c["cid"]] = [c]

    winners = sorted((k for k in kept if k["score"] >= a.min and answer_ok(k)),
                     key=lambda c: c["parts"][0][0])
    picks = []
    for n, c in enumerate(winners, 1):
        found_by = sorted({g["hunter"] for g in groups[c["cid"]]})
        picks.append({
            "id": f"s{n:02d}-{c['hunter']}", "cid": c["cid"], "hunter": c["hunter"],
            "found_by": found_by, "score": c["score"], "title": c["title"],
            "hook": c["hook"], "parts": c["parts"],
            "judge_reason": c["judge"].get("reason", ""),
        })
        if c["hunter"] == "teaser":
            picks[-1].update({"question": c["question"], "outro": c["outro"],
                              "answer": c["answer"],
                              "answer_reason": c["answer_check"].get("reason", "")})
    json.dump(picks, open(a.out, "w"), ensure_ascii=False, indent=1)

    def tc(v):
        return f"{int(v)//60:02d}:{v % 60:04.1f}"

    by_hunter = {}
    for c in cands:
        h = by_hunter.setdefault(c["hunter"], [0, 0, []])
        h[0] += 1
        if c["score"] is not None:
            h[2].append(c["score"])
    for p in picks:
        by_hunter[p["hunter"]][1] += 1

    L = [f"# Отбор шортсов — порог {a.min:g}", "",
         f"Кандидатов {len(cands)}, оценено {len(ranked)}, уникальных моментов "
         f"{len(kept)}, прошло порог **{len(picks)}**.", "",
         "| охотник | нашёл | прошло | средний балл |", "|---|---|---|---|"]
    for h, (n, k, sc) in sorted(by_hunter.items()):
        avg = f"{sum(sc)/len(sc):.1f}" if sc else "—"
        L.append(f"| {h} | {n} | {k} | {avg} |")
    L += ["", "## Прошли", "",
          "| id | балл | где | длит | нашли | хук | почему судья так решил |",
          "|---|---|---|---|---|---|---|"]
    for p in picks:
        where = " + ".join(f"{tc(s)}–{tc(e)}" for s, e in p["parts"])
        dur = sum(e - s for s, e in p["parts"])
        L.append(f"| {p['id']} | {p['score']:g} | {where} | {dur:.0f}s | "
                 f"{', '.join(p['found_by'])} | {p['hook'].replace('|', ' / ')} | "
                 f"{p['judge_reason'].replace('|', '/')} |")
    teasers = [p for p in picks if p["hunter"] == "teaser"]
    if teasers:
        L += ["", "## Тизеры — куда ведут", "",
              "| id | вопрос | плашка | ответ в лонге | проверка ответа |",
              "|---|---|---|---|---|"]
        for p in teasers:
            where = " + ".join(f"{tc(s)}–{tc(e)}" for s, e in p["answer"])
            L.append(f"| {p['id']} | {p['question']} | "
                     f"{p['outro'].replace('|', ' / ')} | {where} | "
                     f"{p['answer_reason'].replace('|', '/')} |")
    L += ["", "## Не прошли (уникальные, по убыванию балла)", "",
          "| cid | балл | название | слабое место |", "|---|---|---|---|"]
    for c in kept:
        if c["score"] < a.min:
            L.append(f"| {c['cid']} | {c['score']:g} | {c['title']} | "
                     f"{c['judge'].get('weak_spot', '').replace('|', '/')} |")
        elif not answer_ok(c):
            chk = c.get("answer_check") or {}
            L.append(f"| {c['cid']} | {c['score']:g} | {c['title']} | "
                     f"лонг не отвечает на вопрос ({chk.get('score', '—')}): "
                     f"{chk.get('reason', 'нет проверки ответа').replace('|', '/')} |")
    open(a.report, "w").write("\n".join(L) + "\n")
    print(f"[ok] прошло {len(picks)} из {len(kept)} уникальных "
          f"({len(cands)} кандидатов) → {a.out}, отчёт → {a.report}")


if __name__ == "__main__":
    main()
