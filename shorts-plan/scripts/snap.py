"""Подтянуть границы отрезка к паузам в речи. Без зависимостей — его зовут и
make_plan.py, и candidates.py, которому OpenCV ни к чему.

Граница годится, если на ней пауза ИЛИ конец предложения. Второе не
перестраховка: локальный whisper.cpp (`hyperframes transcribe`) пишет слова
впритык, без пауз — на 34-минутной записи пауза длиннее 0.3s нашлась в 159
стыках из 6665. По одним паузам граница тогда оставалась там, куда её ткнули,
то есть посреди фразы, и судья резал за это почти каждого кандидата.
"""

SNAP = 2.5          # насколько далеко ищем паузу, сек
MIN_GAP = 0.30      # что считаем паузой между фразами
SENT_WINDOW = 8.0   # насколько далеко ищем конец предложения, если пауз нет
SENT_END = (".", "?", "!", "…")


def _ends_sentence(w):
    return w["word"].strip().rstrip("»\"')").endswith(SENT_END)


def snap_start(words, t, window=SNAP, wide=SENT_WINDOW):
    """Начало — на первое слово после паузы или конца предложения, ближайшее к
    запрошенному моменту. Если в `window` такого нет, ищем начало предложения
    шире: лучше сдвинуть границу на пять секунд, чем начать с полуфразы."""
    best, bd = None, 1e9
    for i, w in enumerate(words):
        d = abs(w["start"] - t)
        if d > wide:
            continue
        gap = w["start"] - words[i - 1]["end"] if i else 99
        pause = gap >= MIN_GAP and d <= window
        sent = i == 0 or _ends_sentence(words[i - 1])
        if (pause or sent) and d < bd:
            lo = words[i - 1]["end"] if i else 0.0
            # min(lo, start): при перехлёсте таймингов (см. snap_end) конец
            # прошлого слова позже начала этого — не режем первое слово.
            best, bd = max(w["start"] - 0.06, min(lo, w["start"])), d
    return round(t if best is None else best, 2)


def snap_end(words, t, window=SNAP, wide=SENT_WINDOW):
    """Конец — на последнее слово перед паузой или концом предложения. Запас
    после слова не залезает на следующее: у слов впритык +0.18s иначе
    проглатывает начало следующей фразы («вот. И»).

    Но и само слово не режем: Groq часто ставит следующему слову начало
    РАНЬШЕ конца текущего (перехлёст 0.1–0.4s), и `nxt - 0.02` уводил границу
    внутрь последнего слова. Оно выпадало из текста судьи и из шортса — на vsl
    так обрубились «20 баксов», «5 клиентов», «этими отзывами» у половины
    кандидатов. Поэтому граница никогда не раньше конца слова."""
    best, bd = None, 1e9
    for i, w in enumerate(words):
        d = abs(w["end"] - t)
        if d > wide:
            continue
        nxt = words[i + 1]["start"] if i + 1 < len(words) else w["end"] + 1
        pause = nxt - w["end"] >= MIN_GAP and d <= window
        if (pause or _ends_sentence(w)) and d < bd:
            best, bd = max(w["end"], min(w["end"] + 0.18, nxt - 0.02)), d
    return round(t if best is None else best, 2)
