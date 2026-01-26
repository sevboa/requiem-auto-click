"""Точка входа в программу."""
import time
from requiem_auto_click.modules.requiem_clicker import RequiemClicker
from requiem_auto_click.modules.windows_mouse_client import WindowsMouseClient
from requiem_auto_click.modules.sharpening_manager import SharpeningManager

if __name__ == "__main__":

    cost = 6
    retries = [
        [1, 0, 0, 0, 0],
        [ 0, 0, 0, 0, 0],
    ]

    items_to_sharpen = [
        [
            [30,],
        ]
    ]        

    retries_disassemble = [
        [
            [0,0,0,0,0],
        ],
        [
            [0,0,0,0,0],
        ],
        [
            [0,0,0,0,0],
            [0,0,0,0,0],
            [10,0,0,0,0],
        ],
    ]

    # Создаем клиент мыши и RequiemClicker
    mouse_client = WindowsMouseClient()
    # Важно: RequiemClicker при инициализации ждёт нажатие Backspace (до любых проверок).
    requiem_clicker = RequiemClicker(mouse_client, window_title_substring="Requiem")

    # Пример: получить состояние рюкзака(ов) по шаблонам в ROI (opened/closed/unknown)
    
    # Дальше все методы запускаются напрямую (без Controller/декораторов)
    requiem_clicker.sharpening_items_to(targets=items_to_sharpen)
    requiem_clicker.disassemble_items(retries=retries_disassemble)
    requiem_clicker.sharpening = SharpeningManager(clicker=requiem_clicker.clicker, image_finder=requiem_clicker.image_finder, backpacks=requiem_clicker.backpacks)
    requiem_clicker.sharpening.top_left_in_client = tuple(requiem_clicker.sharpening.DEFAULT_WINDOW_TOP_LEFT_IN_CLIENT)  
    started = time.perf_counter()
    # Вручную задаём variant, если не делали detect '+' через ensure_item_is_sharpenable()
    value = requiem_clicker.sharpening.get_current_sharpening_value(variant="a1")
    elapsed = time.perf_counter() - started
    print(f"Время исполнения функции: {elapsed:.3f} сек")
    print(value)
    
    
    requiem_clicker.backpacks.close_all_opened_backpacks()
    #requiem_clicker.backpacks.open_backpack(index=0)
    print(requiem_clicker.backpacks.get_backpack_cell_info(backpack_index=1, row=4, col=2))
    requiem_clicker.save_roi_image_interactive(output_filename="cell.png")
    
    print(requiem_clicker.find_image_in_roi(template_png_path="sharpening_window.png", roi_top_left_client=(0, 0), roi_size=(1024, 276)))
    requiem_clicker.find_coords(short_mode=True)