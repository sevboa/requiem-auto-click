"""
Максимально простой пример:
1) находит все окна, где в заголовке есть "requiem"
2) печатает список
3) спрашивает номер окна (1..n)
4) ждёт 5 секунд
5) пытается активировать окно
6) отправляет клавишу C через SendInput (как “реальная” клавиатура)

Важно: для DirectX игр/приложений отправка “в фоне” почти всегда НЕ работает — им нужен
глобальный ввод в активное окно (Raw Input / DirectInput).
"""

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- WinAPI constants ---
SW_RESTORE = 9

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

# Сообщения клавиатуры (для попытки “в фоне” через PostMessage/SendMessage)
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
VK_C = 0x43  # virtual-key 'C'

# “Псевдо-активация” окна через сообщения (без реального фокуса)
WM_ACTIVATE = 0x0006
WM_SETFOCUS = 0x0007
WM_KILLFOCUS = 0x0008
WM_ACTIVATEAPP = 0x001C
WA_ACTIVE = 1

# Скан-код физической клавиши 'C' на стандартной клавиатуре (US/ru раскладка — одна и та же клавиша)
SC_C = 0x2E

# Прототипы (чтобы ctypes корректно маршалил параметры)
user32.SendInput.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostMessageW.restype = wintypes.BOOL


def _win_err() -> str:
    code = ctypes.get_last_error()
    return f"GetLastError={code}"


def enum_windows():
    results = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @EnumWindowsProc
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if title:
            results.append((int(hwnd), title))
        return True

    user32.EnumWindows(cb, 0)
    return results


def find_requiem_windows():
    out = []
    for hwnd, title in enum_windows():
        if "requiem" in title.lower():
            out.append((hwnd, title))
    return out


def _force_foreground(hwnd: int) -> None:
    """Пытается активировать окно (Windows может запретить, но часто помогает)."""
    user32.ShowWindow(hwnd, SW_RESTORE)

    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return

    # AttachThreadInput workaround: цепляемся к input очередям потоков
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    GetWindowThreadProcessId.restype = wintypes.DWORD

    pid = wintypes.DWORD()
    fg_tid = GetWindowThreadProcessId(fg, ctypes.byref(pid))
    target_tid = GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    # GetCurrentThreadId находится в kernel32, не в user32
    kernel32.GetCurrentThreadId.argtypes = ()
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    cur_tid = kernel32.GetCurrentThreadId()

    user32.AttachThreadInput(cur_tid, fg_tid, True)
    user32.AttachThreadInput(cur_tid, target_tid, True)
    try:
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        user32.AttachThreadInput(cur_tid, target_tid, False)
        user32.AttachThreadInput(cur_tid, fg_tid, False)


def _send_input_scancode(sc: int, keyup: bool = False) -> None:
    # На разных версиях Python нет wintypes.ULONG_PTR, но почти всегда есть pointer-sized WPARAM.
    ULONG_PTR = getattr(wintypes, "ULONG_PTR", wintypes.WPARAM)

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class INPUT(ctypes.Structure):
        class _I(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        _anonymous_ = ("i",)
        _fields_ = [("type", wintypes.DWORD), ("i", _I)]

    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if keyup else 0)
    inp = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0, wScan=sc, dwFlags=flags, time=0, dwExtraInfo=0))
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if sent != 1:
        # Не кидаем исключение "наружу" — пусть вызывающий код спокойно попробует fallback.
        raise OSError(f"SendInput failed ({_win_err()})")


def _keybd_event_scancode(sc: int, keyup: bool = False) -> None:
    """Альтернатива SendInput: старый API keybd_event (иногда работает лучше в играх)."""
    ULONG_PTR = getattr(wintypes, "ULONG_PTR", wintypes.WPARAM)
    user32.keybd_event.argtypes = (wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ULONG_PTR)
    user32.keybd_event.restype = None

    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if keyup else 0)
    # bVk=0 => используем именно сканкод
    user32.keybd_event(0, sc, flags, 0)


def _press_c_scancode(hold_sec: float = 0.08) -> None:
    """Нажать/отпустить физическую клавишу C (сканкод)."""
    try:
        _send_input_scancode(SC_C, keyup=False)
        time.sleep(hold_sec)
        _send_input_scancode(SC_C, keyup=True)
    except (OSError, ctypes.ArgumentError) as e:
        print(f"Не получилось отправить SendInput: {e}")
        print("Пробую альтернативу: keybd_event (сканкод C)...")
        try:
            _keybd_event_scancode(SC_C, keyup=False)
            time.sleep(hold_sec)
            _keybd_event_scancode(SC_C, keyup=True)
            print("keybd_event: отправлено.")
        except (OSError, ctypes.ArgumentError) as e2:
            print(f"keybd_event тоже не сработал: {e2}")
            print("Подсказки: не запускай скрипт на UAC-экране/secure desktop;")
            print("если игра запущена от админа — скрипт тоже должен быть от админа (уровни привилегий должны совпадать).")


def send_key_c_active(hwnd: int, delay_before_sec: float = 5.0, hold_sec: float = 0.08) -> None:
    """Рабочий вариант для DirectX: активируем окно и шлём SendInput."""
    time.sleep(delay_before_sec)
    _force_foreground(hwnd)
    _press_c_scancode(hold_sec=hold_sec)


def send_key_c_background_postmessage(hwnd: int, delay_before_sec: float = 5.0) -> None:
    """
    Попытка отправить 'C' в неактивное окно сообщениями.
    Для DirectX-игр обычно НЕ работает, но для обычных GUI — может.
    """
    time.sleep(delay_before_sec)
    # KEYDOWN/UP + на всякий WM_CHAR (латинская 'c')
    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_C, 0)
    user32.PostMessageW(hwnd, WM_KEYUP, VK_C, 0)
    user32.PostMessageW(hwnd, WM_CHAR, ord("c"), 0)


def send_key_c_background_fakeactivate(hwnd: int, delay_before_sec: float = 5.0) -> None:
    """
    Ещё одна попытка “в фоне”:
    1) шлём сообщения, как будто окно активировали (WM_ACTIVATEAPP/WM_ACTIVATE/WM_SETFOCUS)
    2) шлём 'C' через PostMessage (WM_KEYDOWN/UP + WM_CHAR)
    3) опционально “деактивируем” (WM_KILLFOCUS)

    Иногда помогает приложениям, которые сами блокируют ввод, если “не активны”.
    В DirectX-играх чаще всего всё равно не сработает, но попробовать стоит.
    """
    time.sleep(delay_before_sec)
    user32.PostMessageW(hwnd, WM_ACTIVATEAPP, 1, 0)
    user32.PostMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
    user32.PostMessageW(hwnd, WM_SETFOCUS, 0, 0)

    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_C, 0)
    user32.PostMessageW(hwnd, WM_KEYUP, VK_C, 0)
    user32.PostMessageW(hwnd, WM_CHAR, ord("c"), 0)

    user32.PostMessageW(hwnd, WM_KILLFOCUS, 0, 0)


def send_key_c_focus_blink(hwnd: int, delay_before_sec: float = 5.0, hold_sec: float = 0.08, restore_delay_sec: float = 0.15) -> None:
    """
    Компромисс: на мгновение забираем фокус, жмём C через SendInput и возвращаем фокус назад.
    Со стороны это выглядит как “почти в фоне”, но технически окно становится активным на короткое время.
    """
    time.sleep(delay_before_sec)
    prev = user32.GetForegroundWindow()
    _force_foreground(hwnd)
    _press_c_scancode(hold_sec=hold_sec)
    time.sleep(restore_delay_sec)
    if prev and prev != hwnd:
        _force_foreground(prev)


def main():
    print(f"Запущен скрипт: {__file__}")
    wins = find_requiem_windows()
    print(f'Найдено окон по маске "requiem": {len(wins)}')
    for i, (hwnd, title) in enumerate(wins, start=1):
        print(f"{i}) hwnd=0x{hwnd:08X}  title={title!r}")

    if not wins:
        return

    while True:
        s = input("Выбери номер окна (1..n), или Enter чтобы выйти: ").strip()
        if s == "":
            return
        if s.isdigit() and 1 <= int(s) <= len(wins):
            idx = int(s) - 1
            hwnd = wins[idx][0]
            mode = input(
                "Режим: 1=активное окно(SendInput), 2=фон(PostMessage), 4=фон(fake activate + key). Enter=1: "
            ).strip()
            if mode == "":
                mode = "1"
            if mode not in {"1", "2", "4"}:
                print("Неизвестный режим, использую 1.")
                mode = "1"

            if mode == "1":
                print(f"Жду 5 секунд, затем отправляю C в активное окно (SendInput) hwnd=0x{hwnd:08X} ...")
                send_key_c_active(hwnd)
            elif mode == "2":
                print(f"Жду 5 секунд, затем пытаюсь отправить C в НЕактивное окно (PostMessage) hwnd=0x{hwnd:08X} ...")
                send_key_c_background_postmessage(hwnd)
            else:
                print(f"Жду 5 секунд, затем пытаюсь отправить C в фоне (fake activate + key) hwnd=0x{hwnd:08X} ...")
                send_key_c_background_fakeactivate(hwnd)

            print("Готово.")
            return
        print("Неверный ввод. Попробуй ещё раз.")


if __name__ == "__main__":
    main()


