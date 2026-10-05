#!/usr/bin/env python3
"""Белый фон сгенерированной картинки → альфа (тени становятся полупрозрачными).

    uv run --with numpy --with pillow python alpha.py <папка с hNN.png> <W/img>

mix-blend-mode: multiply внутри трансформированного #stage не работает (изолированный контекст),
поэтому фон убирается заранее: alpha = max(1−r,1−g,1−b), rgb = (c − (1−alpha)) / alpha.
Уже сконвертированные файлы пропускает.
"""
import glob, os, sys
import numpy as np
from PIL import Image

src, dst = sys.argv[1], sys.argv[2]
os.makedirs(dst, exist_ok=True)
for p in sorted(glob.glob(os.path.join(src, "h*.png"))):
    out = os.path.join(dst, os.path.basename(p))
    if os.path.exists(out):
        continue
    a = np.asarray(Image.open(p).convert("RGB")).astype(np.float32) / 255
    alpha = np.clip((1 - a).max(axis=2) * 1.08, 0, 1)
    alpha[alpha < 0.02] = 0
    safe = np.where(alpha > 0, alpha, 1)[..., None]
    rgb = np.clip((a - (1 - alpha[..., None])) / safe, 0, 1)
    Image.fromarray((np.dstack([rgb, alpha]) * 255).astype(np.uint8), "RGBA").save(out)
    print(out)
