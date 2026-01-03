"""Базовый абстрактный класс для клиента мыши."""
from abc import ABC, abstractmethod
from typing import Tuple


class MouseClient(ABC):
    """Абстрактный интерфейс для клиента мыши."""
    
    @abstractmethod
    def click_at(self, screen_x: int, screen_y: int) -> None:
        """
        Выполняет клик по указанным экранным координатам.
        
        Args:
            screen_x: X координата на экране
            screen_y: Y координата на экране
        """
        pass
    
    @abstractmethod
    def drag_screen(self, start_xy: Tuple[int, int], end_xy: Tuple[int, int], 
                   steps: int = 40, step_delay: float = 0.005) -> None:
        """
        Выполняет перетаскивание от start_xy до end_xy.
        
        Args:
            start_xy: Начальные координаты (x, y)
            end_xy: Конечные координаты (x, y)
            steps: Количество шагов для перетаскивания
            step_delay: Задержка между шагами в секундах
        """
        pass

