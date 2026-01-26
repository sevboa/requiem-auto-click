"""Контроллер для управления запуском и остановкой скрипта по Backspace."""
import threading
import time
import ctypes
import sys
from ctypes import wintypes

user32 = ctypes.windll.user32

VK_BACKSPACE = 0x08

class Controller:
    def __init__(self, execution_func):
        """
        Инициализирует контроллер.
        
        Args:
            execution_func: Функция, которая будет выполняться при запуске скрипта
        """
        self.execution_func = execution_func
        # Состояния:
        # - idle: скрипт не запущен
        # - running: скрипт выполняется
        # - stopping: запрошена остановка, ждём завершения потока
        self.state = "idle"
        self.script_thread: threading.Thread | None = None
        self.stop_flag: threading.Event | None = None
        self._lock = threading.Lock()
    
    def _monitor_backspace(self, **kwargs):
        """Мониторит нажатие Backspace и переключает состояние скрипта."""
        last_state = False
        while True:
            # Если поток завершился сам — приводим состояние в idle и печатаем один раз.
            with self._lock:
                if self.state in ("running", "stopping"):
                    if self.script_thread and (not self.script_thread.is_alive()):
                        self.state = "idle"
                        self.script_thread = None
                        self.stop_flag = None
                        print("Скрипт остановлен. Нажмите Backspace для запуска.")

            current_state = (user32.GetAsyncKeyState(VK_BACKSPACE) & 0x8000) != 0
            
            # Edge detection: срабатываем только при переходе с False на True
            if current_state and not last_state:
                with self._lock:
                    if self.state == "idle":
                        # Запуск скрипта
                        self._start_script(**kwargs)
                    elif self.state == "running":
                        # Запрос остановки скрипта
                        self._stop_script()
                    else:
                        # stopping: игнорируем повторные нажатия, пока поток реально не остановится
                        pass
                
                # Ждем, пока клавиша будет отпущена
                while (user32.GetAsyncKeyState(VK_BACKSPACE) & 0x8000) != 0:
                    time.sleep(0.05)
                # Дополнительная задержка чтобы избежать множественных срабатываний
                time.sleep(0.3)
            
            last_state = current_state
            time.sleep(0.05)
    
    def _start_script(self, **kwargs):
        """Запускает выполнение скрипта в отдельном потоке."""
        if self.state != "idle":
            return

        self.state = "running"
        self.stop_flag = threading.Event()
        self.script_thread = threading.Thread(target=self._run_script, kwargs=kwargs, daemon=True)
        print("Запускаем скрипт. Нажмите Backspace для остановки.\n")
        self.script_thread.start()
    
    def _stop_script(self):
        """Останавливает выполнение скрипта."""
        if self.state != "running":
            return

        self.state = "stopping"
        if self.stop_flag:
            self.stop_flag.set()
        # В мониторинге координат используется '\r' без '\n', поэтому перед сообщением — '\n'
        # и завершаем сообщение на '\r', чтобы не ломать вывод строки.
        #sys.stdout.write("Остановка скрипта...")
        sys.stdout.flush()
    
    def _run_script(self, **kwargs):
        """Выполняет скрипт в отдельном потоке."""
        try:
            # Всегда ПЫТАЕМСЯ передать stop_flag. Если функция не принимает — fallback.
            stop_flag = self.stop_flag
            try:
                self.execution_func(stop_flag=stop_flag, **kwargs)
            except TypeError:
                self.execution_func(**kwargs)
        except Exception as e:
            print(f"Ошибка при выполнении скрипта: {e}")
        finally:
            # Не печатаем "остановлен" здесь: это делает монитор (один раз, когда поток реально завершился).
            # Просто выходим из потока.
            pass
    
    def run(self, **kwargs):
        """Запускает контроллер (мониторинг Backspace)."""
        print("Контроллер запущен. Нажмите Backspace для запуска/остановки скрипта.")
        print("Для выхода нажмите Ctrl+C")
        try:
            self._monitor_backspace(**kwargs)
        except KeyboardInterrupt:
            print("\nВыход...")
            with self._lock:
                if self.state == "running":
                    self._stop_script()
            # Даём потоку шанс завершиться
            if self.script_thread:
                self.script_thread.join(timeout=2.0)

