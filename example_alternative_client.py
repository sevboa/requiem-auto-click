"""Пример альтернативной реализации клиента мыши для демонстрации заменяемости."""
from typing import Tuple
from mouse_client_base import MouseClient


class ExampleAlternativeMouseClient(MouseClient):
    """
    Пример альтернативной реализации клиента мыши.
    Это демонстрация того, как можно легко заменить клиент на другой.
    Например, можно создать клиент для Linux (xdotool), macOS (pyautogui) и т.д.
    """
    
    def click_at(self, screen_x: int, screen_y: int) -> None:
        """Выполняет клик по указанным экранным координатам."""
        # Здесь может быть реализация через pyautogui, xdotool, или другой API
        print(f"[Пример] Клик по координатам ({screen_x}, {screen_y})")
        # Например: pyautogui.click(screen_x, screen_y)
    
    def drag_screen(self, start_xy: Tuple[int, int], end_xy: Tuple[int, int], 
                   steps: int = 40, step_delay: float = 0.005) -> None:
        """Выполняет перетаскивание от start_xy до end_xy."""
        x1, y1 = start_xy
        x2, y2 = end_xy
        print(f"[Пример] Перетаскивание от ({x1}, {y1}) до ({x2}, {y2})")
        # Например: pyautogui.drag(x1, y1, x2-x1, y2-y1, duration=step_delay*steps)


# Пример использования:
# from clicker import Clicker
# from example_alternative_client import ExampleAlternativeMouseClient
# 
# alternative_client = ExampleAlternativeMouseClient()
# clicker = Clicker(alternative_client)
# clicker.click_at(100, 200)

