#!/usr/bin/env python3
"""QA всей пачки с одним отчётом и готовой командой адресной пересборки."""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--renders", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("--only", default=None, help="id через запятую")
    a = ap.parse_args()

    with open(a.plan) as f:
        plan = json.load(f)
    selected = {x.strip() for x in a.only.split(",") if x.strip()} if a.only else None
    report_path = a.report or os.path.join(a.renders, "qa-report.json")
    report_dir = os.path.splitext(report_path)[0] + ".d"
    os.makedirs(report_dir, exist_ok=True)
    results = []

    for item in plan["shorts"]:
        sid = item["id"]
        if selected and sid not in selected:
            continue
        video = os.path.join(a.renders, sid + ".mp4")
        one = os.path.join(report_dir, sid + ".json")
        if not os.path.exists(video):
            results.append({"id": sid, "video": video, "ok": False,
                            "error": "рендер отсутствует"})
            continue
        cmd = [sys.executable, os.path.join(HERE, "qa.py"),
               "--video", video, "--plan", a.plan, "--id", sid, "--json", one]
        proc = subprocess.run(cmd)
        if os.path.exists(one):
            with open(one) as f:
                result = json.load(f)
        else:
            result = {"id": sid, "video": video, "ok": False,
                      "error": f"qa.py завершился с кодом {proc.returncode}"}
        results.append(result)

    failed = [r["id"] for r in results if not r.get("ok")]
    summary = {"ok": not failed, "failed_ids": failed, "shorts": results}
    os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n[qa-batch] отчёт: {report_path}")
    if failed:
        ids = ",".join(failed)
        print(f"[qa-batch] провалились: {ids}")
        print("[qa-batch] после исправления пересобери только их:")
        print(f"  render_all.py --plan {a.plan} --only {ids} --out {a.renders}")
        raise SystemExit(1)
    print(f"[qa-batch] все {len(results)} шортсов прошли автоматические проверки")


if __name__ == "__main__":
    main()
