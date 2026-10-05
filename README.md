# montage-skills

Скилы для Claude Code / Codex: моушн-графика и нарезка шортсов на [HyperFrames](https://github.com/heygen-com/hyperframes) + GSAP.

## Что внутри

| Скил | Что делает |
|---|---|
| `motion-run` | Точка входа: из записи или транскрипта делает все анимационные вставки и отдаёт черновой монтаж |
| `motion-plan` | Размечает, где в ролике нужна графика, какими приёмами, с таймкодами. На выходе `MOTION.md` |
| `motion-build` | Собирает одну анимационную вставку по спеке. Внутри дизайн-система, каталог приёмов, рецепты и грабли |
| `shorts-run` | Точка входа: нарезает длинный ролик на вертикальные шортсы |
| `shorts-plan` | Ищет моменты для шортсов (пять «охотников» и судья), собирает `plan.json` |
| `shorts-build` | Собирает один шортс 1080x1920: раскладка, хук, субтитры, камера |
| `watch` | Даёт агенту «посмотреть» видео по ссылке или файлу: кадры по сменам сцен, контактные листы, транскрипт (субтитры или Whisper через Groq). Форк [bradautomates/claude-video](https://github.com/bradautomates/claude-video), лицензия MIT (`watch/LICENSE`) |

## Как пользоваться

1. Положи нужные папки в `skills/` своего проекта (или в `~/.claude/skills/`).
2. Для вставок вызывай `/motion-run`, для шортсов `/shorts-run`.
3. Нужны HyperFrames, ffmpeg, Python с OpenCV, NumPy и Pillow.
4. Для `watch` нужны `yt-dlp` и `ffmpeg`; ключ Groq кладётся в `~/.config/watch/.env` (подробно в `watch/SETUP.md`). Вызов: `/watch <ссылка или путь> <вопрос>`.

## Что поменять под себя

- Дизайн-система в `motion-build/references/design-system.md` и `assets/`: цвета, шрифты, тон. Там стоят заглушки вместо имени и темы канала.
- Плашки с именем в `motion-build/references/recipes/pip-morph.html` и `shorts-build/assets/outro/cta-comment-link.html`.
- Пути в `shorts-build/scripts/native_short.py`: переменные окружения `SCREEN` (экранная запись) и `SFX_BUNDLE` (звуки media-use).

Скилы написаны по-русски и под русскоязычный канал.

## Про `watch`

`watch` — доработанный форк скилла [bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT, © Bradley Bonanno): русская инструкция установки `SETUP.md`, режим `--hook` для разбора первых секунд и формат хронологического отчёта (`references/source-report.md`). Оригинальная лицензия сохранена в `watch/LICENSE`.
