# requiem-auto-click

Скрипт для автоматизации действий в игре **Requiem** (Windows).

Сейчас поддерживаются консольные команды:
- `init` — скопировать примеры конфигов в текущую папку (`./disassemble.py`, `./sharpening.py`).
- `sharpening_items_to` — заточка предметов до заданного уровня (по фактическому значению заточки).
- `disassemble_items` — разбор предметов.

## Требования

- Windows
- Python 3.10+ (желательно)

## Как пользоваться (по шагам)

### 0) Создать рабочую папку

Рекомендуется завести отдельную папку, из которой вы будете запускать команду и где будут лежать ваши конфиги:

```bash
mkdir requiem-run
cd requiem-run
```

### 1) Установка через pip (без git)

```bash
pip install "requiem-auto-click @ https://github.com/sevboa/requiem-auto-click/archive/refs/heads/master.zip"
```

Проверка:

```bash
requiem-auto-click --help
```

### 2) Создать конфиги (init)

```bash
requiem-auto-click init
```

Если файлы уже существуют — перезаписать:

```bash
requiem-auto-click init --force
```

### 3) Отредактировать конфиги

- `./sharpening.py` — правь матрицу `targets`
- `./disassemble.py` — правь матрицу `retries`

### 4) Запуск

Заточка:

```bash
requiem-auto-click sharpening_items_to --config ./sharpening.py
```

Разбор:

```bash
requiem-auto-click disassemble_items --config ./disassemble.py
```

### Если команда не находится

Обычно помогает:
- открыть **новый терминал** после установки
- убедиться, что ты запускаешь в том же окружении Python, куда ставил пакет (venv/глобальный Python)

## Формат конфигов

### `sharpening_items_to`

Обязательная переменная:
- `targets` — трёхмерный массив: `targets[backpack][row][col] -> int`
  - `0` означает “пропустить ячейку”
  - `>0` означает “точить, пока текущий уровень >= целевого”

Опциональные переменные:
- `backpack_indices`: `list[int] | None` — если внешний список `targets` не совпадает с реальными индексами мешков.
- `window_title_substring`: `str` — подстрока заголовка окна игры (по умолчанию `"Requiem"`).
- `wait_for_backspace_on_init`: `bool` — ждать ли одиночный Backspace перед стартом (по умолчанию `True`).
- `confirm_with_bracket`: `bool` — ждать ли нажатие `]` перед началом действий (по умолчанию `True`).

### `disassemble_items`

Обязательная переменная:
- `retries` — трёхмерный массив: `retries[backpack][row][col] -> int`
  - `0` означает “пропустить ячейку”
  - `>0` означает “сколько раз попытаться разобрать”

Опциональные переменные:
- `window_title_substring`: `str`
- `wait_for_backspace_on_init`: `bool`
- `confirm_with_bracket`: `bool`

## Примечания по управлению

- По умолчанию конструктор `RequiemClicker` **ждёт Backspace**, чтобы ты мог спокойно навести курсор/окно и только потом стартовать.
- По умолчанию перед началом сценария будет запрос на `]` (второе подтверждение).
- Во время выполнения можно останавливать сценарии через Backspace (актуально для CLI тоже).