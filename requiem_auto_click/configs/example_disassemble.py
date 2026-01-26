"""
Пример конфига для команды `disassemble_items`.

Запуск:
  requiem-auto-click disassemble_items --config ./disassemble.py
"""

# Обязательное:
# retries[backpack][row][col] -> int (сколько раз попытаться разобрать)
# 0 = пропустить ячейку
retries = [
    [  # backpack 0
        [0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0],
    ],
    [  # backpack 1
        [0, 0, 0, 0, 0],
    ],
    [  # backpack 2
        [0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0],
        [1, 0, 0, 0, 0],
    ],
]

# Опционально: подстрока заголовка окна игры
window_title_substring = "Requiem"

# Опционально:
wait_for_backspace_on_init = True
confirm_with_bracket = False


