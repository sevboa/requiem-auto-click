"""Реализация клиента мыши для Windows через SendInput."""
import time
from typing import Tuple
from .mouse_client_base import MouseClient
from .mouse_utils import send_mouse, MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP


class WindowsMouseClient(MouseClient):
    """Конкретная реализация клиента мыши для Windows."""
    
    def click_at(self, screen_x: int, screen_y: int) -> None:
        """Выполняет клик по указанным экранным координатам."""
        send_mouse(MOUSEEVENTF_MOVE, screen_x, screen_y)
        time.sleep(0.03)
        send_mouse(MOUSEEVENTF_LEFTDOWN)
        time.sleep(0.03)
        send_mouse(MOUSEEVENTF_LEFTUP)
    
    def drag_screen(self, start_xy: Tuple[int, int], end_xy: Tuple[int, int], 
                   steps: int = 40, step_delay: float = 0.005) -> None:
        """Выполняет перетаскивание от start_xy до end_xy."""
        x1, y1 = start_xy
        x2, y2 = end_xy
        
        send_mouse(MOUSEEVENTF_MOVE, x1, y1)
        time.sleep(0.03)
        send_mouse(MOUSEEVENTF_LEFTDOWN)
        time.sleep(0.03)
        
        for i in range(1, steps + 1):
            xi = int(x1 + (x2 - x1) * i / steps)
            yi = int(y1 + (y2 - y1) * i / steps)
            send_mouse(MOUSEEVENTF_MOVE, xi, yi)
            time.sleep(step_delay)
        
        time.sleep(0.03)
        send_mouse(MOUSEEVENTF_LEFTUP)

