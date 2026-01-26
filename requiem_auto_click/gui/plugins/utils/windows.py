from __future__ import annotations

from dataclasses import dataclass

# pylint: disable=broad-exception-caught
import win32api  # type: ignore
import win32con  # type: ignore
import win32gui  # type: ignore
import win32process  # type: ignore


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) в экранных координатах."""
    l, t, r, b = win32gui.GetWindowRect(int(hwnd))
    return int(l), int(t), int(r), int(b)


def list_visible_windows_with_exact_title(title: str) -> list[WindowInfo]:
    """Возвращает список видимых окон с точным заголовком title."""
    out: list[WindowInfo] = []
    want = str(title)

    def _enum(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        t = win32gui.GetWindowText(hwnd) or ""
        if t != want:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        out.append(WindowInfo(hwnd=int(hwnd), pid=int(pid), title=str(t)))

    win32gui.EnumWindows(_enum, None)
    return out


def find_hwnd_by_pid_and_exact_title(*, pid: int, title: str) -> int:
    """Ищет HWND видимого окна по PID и точному заголовку."""
    pid = int(pid or 0)
    if pid <= 0:
        return 0
    want = str(title)
    found_hwnd = 0

    def _enum(hwnd, _):
        nonlocal found_hwnd
        if found_hwnd:
            return
        if not win32gui.IsWindowVisible(hwnd):
            return
        t = win32gui.GetWindowText(hwnd) or ""
        if t != want:
            return
        try:
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        if int(wpid) == pid:
            found_hwnd = int(hwnd)

    win32gui.EnumWindows(_enum, None)
    return int(found_hwnd)


def window_available_for_pid(*, pid: int, title: str) -> bool:
    """Проверяет, что для PID есть видимое окно с точным заголовком."""
    return find_hwnd_by_pid_and_exact_title(pid=int(pid), title=str(title)) > 0


def focus_window_by_pid(*, pid: int, title: str) -> bool:
    """Фокус на окно по PID+title. Возвращает True/False."""
    hwnd = find_hwnd_by_pid_and_exact_title(pid=int(pid), title=str(title))
    if hwnd <= 0:
        return False
    focus_hwnd(hwnd)
    return True


def pid_exists(pid: int) -> bool:
    """
    Best-effort check that a process with PID exists.
    If access is denied, assume it exists (so we don't accidentally reset state).
    """
    pid = int(pid or 0)
    if pid <= 0:
        return False
    access = getattr(win32con, "PROCESS_QUERY_LIMITED_INFORMATION", None)
    if access is None:
        access = win32con.PROCESS_QUERY_INFORMATION
    try:
        h = win32api.OpenProcess(int(access), False, int(pid))
        try:
            return True
        finally:
            win32api.CloseHandle(h)
    except Exception as e:
        winerr = getattr(e, "winerror", None)
        if winerr == 5:  # Access denied
            return True
        return False


def focus_hwnd(hwnd: int) -> None:
    """Активирует окно (best-effort)."""
    hwnd = int(hwnd or 0)
    if hwnd <= 0:
        raise ValueError("hwnd is required")
    win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
    win32gui.SetForegroundWindow(hwnd)


def get_foreground_pid() -> int:
    """PID активного окна (best-effort)."""
    try:
        fg = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(fg)
        return int(pid)
    except Exception:
        return -1


def terminate_process(pid: int) -> None:
    """Принудительно завершает процесс по PID."""
    pid = int(pid or 0)
    if pid <= 0:
        raise ValueError("pid is required")
    h = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, int(pid))
    try:
        win32process.TerminateProcess(h, 1)
    finally:
        win32api.CloseHandle(h)

