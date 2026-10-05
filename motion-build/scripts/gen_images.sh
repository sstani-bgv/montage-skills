#!/bin/zsh
# Генерация картинок-героев через Codex пачками по 6 параллельно.
#   gen_images.sh prompts.tsv <outdir>
# prompts.tsv: "NN<TAB>объект-метафора одной фразой" (только объект; стиль-хвост добавляется здесь).
# Результат: <outdir>/hNN.png (белый фон) → дальше alpha.py → W/img/hNN.png.
# Модель: CODEX_MODEL=gpt-6-luna (модель из конфига codex может не поддерживаться ChatGPT-аккаунтом).
# ~50 с на картинку. Уже существующие файлы пропускаются — перезапуск догенерирует упавшие.
# Codex иногда отказывает (safety) на безобидный промпт — перефразируй объект и запусти снова.
TSV=${1:A}; OUT=${2:A}; mkdir -p $OUT
STYLE="Photorealistic 3D render, premium product-shot look, soft diffused studio lighting, gentle contact shadow, isolated on a pure white seamless background (#FFFFFF), subject centered with generous margins, clean and minimal, muted colors with a single cobalt-blue (#2E58EA) accent detail, absolutely no text, no letters, no logos, no numbers."
gen() {
  [[ -f $OUT/h$1.png ]] && return
  # < /dev/null обязателен: иначе codex съедает stdin цикла и пачка обрывается после первой волны
  codex exec --skip-git-repo-check ${CODEX_MODEL:+-m} ${CODEX_MODEL} --sandbox workspace-write -C $OUT \
    "Use your image generation tool to create ONE image (square, 1:1) and copy the generated file to $OUT/h$1.png. Prompt: '$2 $STYLE'. Reply only with the saved path." \
    < /dev/null > $OUT/h$1.log 2>&1
}
i=0
while IFS=$'\t' read -r n p; do
  [[ -z $n ]] && continue
  gen $n "$p" & i=$((i+1))
  (( i % 6 == 0 )) && wait
done < $TSV
wait
for n in $(cut -f1 $TSV); do [[ -f $OUT/h$n.png ]] || echo "НЕ СГЕНЕРИРОВАНО: $n (см. $OUT/h$n.log)"; done
