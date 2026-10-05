#!/usr/bin/env python3
"""
Рендер шортсов по плану.

  uv run --with pillow --with numpy --with 'opencv-python-headless<5' \
      python3 render_all.py --plan /tmp/shorts/plan.json [--only s03] [--out DIR]

plan.json:
  {
    "src":   "/path/to/long.mov",
    "calib": "/tmp/shorts/calib.json",
    "shorts": [
      {"id": "s01", "title": "...", "layout": "split|cam|broll|auto",
       "start": 136.25, "end": 165.95, "track": true,
       "hook": "Одна задача —|*одна сессия*",
       "layout_overrides": [
         {"start": 136.25, "end": 140.0, "layout": "cam"},
         {"start": 140.0, "end": 150.0, "layout": "broll", "size": "large"}
       ],
       "patch_src": [60, 630],
       "src": "/tmp/shorts/p/stitch/s02-story.mp4",   # только у склеек, см. ниже
       "words": "/tmp/shorts/p/stitch/s02-story.words.json",
       "cues": [[0.0, 0.4, "текст"], ...]}
    ]
  }

`cues: null` — субтитры соберутся из raw.words.json и допишутся обратно в план,
чтобы правки не терялись между прогонами. Один шортс — один вызов, поэтому
оркестратор может раздать их сабагентам параллельно.

`patch_src` (и, если совсем прижало, `patch`) переопределяют калибровку на один
шортс. Нужно это потому, что заливка замазки PiP — средний цвет куска кадра из
`patch_src`, а экран в разных местах лонга разный: один источник на весь ролик
где-то попадает в тон окружения, а где-то кладёт светлую кляксу на тёмный
интерфейс. Без этой ручки приходится плодить копии calib.json на каждый шортс.

`src`/`words` в записи переопределяют общие. Их ставит make_plan.py шортсам,
склеенным из нескольких кусков лонга: такой шортс собирается из уже склеенного
промежуточного файла той же геометрии, как обычный отрезок 0..длительность.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_short


def render_one(plan, item, out_dir, track=True):
    make_short.use_calib(plan["calib"], item)
    src = item.get("src", plan["src"])
    words = item.get("words", plan.get("words"))
    cues = item.get("cues")
    if cues is None:
        # Молча собрать шортс без субтитров — худший исход: файл выглядит
        # готовым, и брак всплывает уже у зрителя.
        if not words:
            raise SystemExit(
                f"{item['id']}: нет ни готовых cues, ни words в плане — "
                f"субтитров не будет. Добавь путь к raw.words.json в план "
                f"или впиши реплики в запись.")
        cues = make_short.build_cues(json.load(open(words)),
                                     item["start"], item["end"])
        if not cues:
            raise SystemExit(
                f"{item['id']}: на отрезке {item['start']}-{item['end']} нет "
                f"слов — проверь таймкоды, они, похоже, мимо речи.")
        item["cues"] = cues
    print(f"\n=== {item['id']}  «{item.get('title', '')}»")
    make_short.build(
        src, words, item["start"], item["end"],
        os.path.join(out_dir, item["id"] + ".mp4"),
        os.path.join("/tmp/shorts/work", item["id"]),
        layout=item["layout"], hook=item.get("hook"), outro=item.get("outro"),
        cues=cues,
        track=bool(item.get("track")) and track,
        layout_overrides=item.get("layout_overrides"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--only", default=None,
                    help="один id или список через запятую: s02,s05")
    ap.add_argument("--out", default="/tmp/shorts/renders")
    ap.add_argument("--no-track", action="store_true")
    a = ap.parse_args()

    plan = json.load(open(a.plan))
    os.makedirs(a.out, exist_ok=True)
    before = json.dumps(plan, sort_keys=True)

    selected = {x.strip() for x in a.only.split(",") if x.strip()} if a.only else None
    known = {item["id"] for item in plan["shorts"]}
    if selected and selected - known:
        raise SystemExit("неизвестные id: " + ", ".join(sorted(selected - known)))

    for item in plan["shorts"]:
        if selected and item["id"] not in selected:
            continue
        render_one(plan, item, a.out, track=not a.no_track)

    if json.dumps(plan, sort_keys=True) != before:
        json.dump(plan, open(a.plan, "w"), ensure_ascii=False, indent=1)
        print("\n[plan] реплики дописаны в", a.plan)


if __name__ == "__main__":
    main()
