#!/usr/bin/env python3
"""Вертикальная (1080x1920) сборка сцены v4: mk_scene.py + перевод канваса в портрет.
    python3 mk_scene_v.py <V> NN [NN...]
Зона контента — верх 1080x940 (y 0..940); ниже шортс кладёт субтитры (y~1000)
и карточку камеры (y 1100..1800), поэтому фон сцены должен быть спокойным там."""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mk_scene
V = os.path.abspath(sys.argv[1])
for n in sys.argv[2:]:
    mk_scene.build(V, n)
    p = f"{V}/{n}/index.html"; s = open(p).read()
    s = s.replace('data-resolution="landscape"', 'data-resolution="portrait"')
    s = s.replace('content="width=1920, height=1080"', 'content="width=1080, height=1920"')
    s = s.replace('data-width="1920" data-height="1080"', 'data-width="1080" data-height="1920"')
    open(p, "w").write(s)
    c = f"{V}/{n}/assets/common.css"; s = open(c).read()
    s = s.replace("width: 1920px; height: 1080px", "width: 1080px; height: 1920px")
    # портретные умолчания: пятна и герой под вертикаль (сцена может переопределить)
    s += """
/* --- портрет 1080x1920 (mk_scene_v) --- */
.blob { left: -200px; top: -300px; width: 1500px; height: 1500px; }
.blob2 { left: -300px; top: 900px; width: 1300px; height: 1100px; }
.paper-grid { -webkit-mask-image: radial-gradient(ellipse 80% 45% at 50% 25%, #000 20%, transparent 75%);
                      mask-image: radial-gradient(ellipse 80% 45% at 50% 25%, #000 20%, transparent 75%); }
#stage { transform-origin: 50% 25%; }
.kicker { left: 80px; top: 110px; }
.h1 { left: 80px; width: 920px; }
.sub { left: 80px; width: 920px; }
.card, .steps { left: 80px; }
.hero { left: 430px; top: 330px; width: 620px; height: 620px; }
"""
    open(c, "w").write(s)
    print("portrait", n)
