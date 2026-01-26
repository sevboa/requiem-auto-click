"""Ненавязчивые, но узнаваемые звуки для сигналов старта/финиша (Windows).

Используем winsound.Beep (частота/длительность), чтобы не путаться с системными MessageBeep.
"""

from __future__ import annotations


def _beep_sequence(seq: list[tuple[int, int]], pause_ms: int = 25) -> None:
    try:
        import winsound
        for freq, dur in seq:
            # Beep ограничен примерно 37..32767 Гц
            winsound.Beep(int(freq), int(dur))
            if pause_ms > 0:
                winsound.Beep(37, int(pause_ms))  # очень тихий "пустой" промежуток (минимальная частота)
    except Exception:
        # Если звук недоступен (нет winsound/запрещено политиками) — просто молчим.
        return


def play_start_sound() -> None:
    # Мягкое восходящее арпеджио (C5-E5-G5), коротко
    _beep_sequence([(523, 70), (659, 70), (784, 90)], pause_ms=20)


def play_finish_sound() -> None:
    # Мягкое "подтверждение": вниз-вверх (G5-E5-C6)
    _beep_sequence([(784, 70), (659, 70), (1046, 120)], pause_ms=20)


def play_error_sound() -> None:
    # Низкий двойной сигнал
    _beep_sequence([(220, 140), (196, 160)], pause_ms=40)


