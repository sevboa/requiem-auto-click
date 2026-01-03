# requiem-auto-click

Скрипт для автоматизации действий в игре **Requiem** (Windows).

Сейчас поддерживаются консольные команды:
- `sharpening_items_to` — заточка предметов до заданного уровня (по фактическому значению заточки).
- `disassemble_items` — разбор предметов.

## Требования

- Windows
- Python 3.10+ (желательно)

## Установка (через pip)

Установка через pip с GitHub:

```bash
pip install "requiem-auto-click @ https://github.com/sevboa/requiem-auto-click/archive/refs/heads/master.zip"
```

После установки появится консольная команда:

```bash
requiem-auto-click --help
```

## Установка (локально из репы)

Установка зависимостей:

```bash
pip install -r requirements.txt
```

## Быстрый старт (CLI)

Все входные данные передаются через **дополнительный `.py` файл-конфиг**, путь к которому указывается параметром `--config`.
Этот файл можно хранить **где угодно** (хоть на рабочем столе) — главное передать путь.

### Заточка до уровня

1) Отредактируй конфиг `configs/example_sharpening.py` под себя (матрица `targets`).

2) Запусти:

```bash
requiem-auto-click sharpening_items_to --config configs/example_sharpening.py
```

### Разбор предметов

1) Отредактируй конфиг `configs/example_disassemble.py` под себя (матрица `retries`).

2) Запусти:

```bash
requiem-auto-click disassemble_items --config configs/example_disassemble.py
```

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

## Локальные конфиги (чтобы не коммитить свои настройки)

Удобный подход:
- скопируй пример в `configs/local_sharpening.py` или `configs/local_disassemble.py`
- не коммить эти файлы (можно добавить их в `.gitignore` вручную под себя)

Если ставишь через `pip install`, примеры конфигов можно найти так:

```bash
python -c "import configs, pathlib; print(pathlib.Path(configs.__file__).parent)"
```


