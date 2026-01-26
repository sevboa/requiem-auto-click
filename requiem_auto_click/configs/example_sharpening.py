"""
Пример конфига для команды `sharpening_items_to`.

Запуск:
  requiem-auto-click sharpening_items_to --config ./sharpening.py
"""

# Обязательное:
# targets[backpack][row][col] -> int (целевой уровень)
# 0 = пропустить ячейку
targets = [
    [  # backpack 0
        [0, 10, 0, 0, 12],
        [0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0],
    ],
]

# Опционально: если внешний список targets не соответствует реальным индексам мешков
# (например targets описывает мешки 1 и 3, а мешок 2 пропускаем).
# backpack_indices = [0, 2]
backpack_indices = None

# Опционально: подстрока заголовка окна игры
window_title_substring = "Requiem"

# Опционально:
# - True: при старте попросит нажать Backspace (в конструкторе) и затем ']' (перед началом действий)
# - False: пропустит ожидания (удобно для полностью консольного запуска)
wait_for_backspace_on_init = True
confirm_with_bracket = False


