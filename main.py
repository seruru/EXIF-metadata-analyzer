import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import threading
import csv
import datetime
import time
import io  # <--- ВАЖНО: Нужно для чтения миниатюр из EXIF

# Импорт библиотек
import exifread
from PIL import Image, ImageTk, ImageFile

# Разрешаем загрузку "обрезанных" изображений (помогает с некоторыми jpg)
ImageFile.LOAD_TRUNCATED_IMAGES = True


# --- ХЕЛПЕРЫ ДЛЯ РАБОТЫ С GPS ---
def _convert_to_degrees(value):
    d = float(value.values[0].num) / float(value.values[0].den)
    m = float(value.values[1].num) / float(value.values[1].den)
    s = float(value.values[2].num) / float(value.values[2].den)
    return d + (m / 60.0) + (s / 3600.0)


def get_gps_coords(tags):
    try:
        if 'GPS GPSLatitude' in tags and 'GPS GPSLongitude' in tags:
            lat = _convert_to_degrees(tags['GPS GPSLatitude'])
            lon = _convert_to_degrees(tags['GPS GPSLongitude'])
            if tags.get('GPS GPSLatitudeRef', '').printable == 'S': lat = -lat
            if tags.get('GPS GPSLongitudeRef', '').printable == 'W': lon = -lon
            return round(lat, 6), round(lon, 6)
    except Exception:
        return None, None
    return None, None


def format_bytes(size):
    power = 2 ** 10
    n = 0
    power_labels = {0: '', 1: 'KB', 2: 'MB', 3: 'GB'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"


class PhotoAnalyzerApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("EXIF MetadataAnalyzer")
        self.geometry("1300x750")
        self.configure(bg="#2b2b2b")

        self.found_data = []
        self.map_data = {}
        self.is_processing = False
        self.current_image_ref = None

        self._init_styles()
        self._build_ui()

    def _init_styles(self):
        self.style = ttk.Style()
        self.style.theme_use('clam')

        self.colors = {
            "bg": "#2b2b2b", "fg": "#e0e0e0", "panel": "#333333",
            "accent": "#5c6bc0", "accent_hover": "#7986cb",
            "border": "#45475a", "success": "#66bb6a"
        }

        self.style.configure("TFrame", background=self.colors["bg"])
        self.style.configure("Panel.TFrame", background=self.colors["panel"], relief="flat")
        self.style.configure("TLabel", background=self.colors["panel"], foreground=self.colors["fg"],
                             font=("Segoe UI", 10))
        self.style.configure("Title.TLabel", font=("Segoe UI", 12, "bold"), foreground="#89b4fa")

        self.style.configure("TButton", background=self.colors["panel"], foreground=self.colors["fg"], borderwidth=1,
                             font=("Segoe UI", 10))
        self.style.map("TButton", background=[('active', self.colors["border"])])

        self.style.configure("Accent.TButton", background=self.colors["accent"], foreground="white",
                             font=("Segoe UI", 11, "bold"))
        self.style.map("Accent.TButton", background=[('active', self.colors["accent_hover"])])

        self.style.configure("TCheckbutton", background=self.colors["panel"], foreground=self.colors["fg"],
                             font=("Segoe UI", 10))
        self.style.map("TCheckbutton", background=[('active', self.colors["panel"])])

        self.style.configure("Horizontal.TProgressbar", background=self.colors["success"], troughcolor="#1e1e1e",
                             bordercolor=self.colors["border"])

        self.style.configure("Treeview", background="#1e1e1e", foreground="#ffffff", fieldbackground="#1e1e1e",
                             rowheight=25)
        self.style.configure("Treeview.Heading", background="#333333", foreground="#ffffff",
                             font=("Segoe UI", 10, "bold"))
        self.style.map("Treeview", background=[('selected', self.colors["accent"])])

    def _parse_date(self, date_str):
        if not date_str: return "-"
        try:
            clean_date = str(date_str).strip()
            dt_obj = datetime.datetime.strptime(clean_date, '%Y:%m:%d %H:%M:%S')
            return dt_obj.strftime('%d.%m.%Y %H:%M')
        except ValueError:
            return str(date_str)

    def _build_ui(self):
        # 1. ЛЕВАЯ ПАНЕЛЬ (SIDEBAR)
        sidebar = ttk.Frame(self, style="Panel.TFrame", padding=15)
        sidebar.pack(side="left", fill="y")

        # --- Выбор папки ---
        ttk.Label(sidebar, text="Источник данных", style="Title.TLabel").pack(anchor="w", pady=(0, 5))
        self.lbl_path = ttk.Label(sidebar, text="Папка не выбрана", wraplength=200, font=("Segoe UI", 9, "italic"))
        self.lbl_path.pack(anchor="w", pady=(0, 10))

        ttk.Button(sidebar, text="📂 Обзор...", command=self.select_folder).pack(fill="x", pady=(0, 10))

        # --- Галочка: Рекурсия (с испраленным видом) ---
        self.var_recursive = tk.BooleanVar(value=True)
        tk.Checkbutton(sidebar, text="Рекурсивный поиск", variable=self.var_recursive,
                       bg=self.colors["panel"],  # Фон (как у панели)
                       fg=self.colors["fg"],  # Текст (светлый)
                       selectcolor=self.colors["panel"],  # Цвет квадратика внутри (чтобы не был белым)
                       activebackground=self.colors["panel"],  # При наведении
                       activeforeground=self.colors["fg"],
                       font=("Segoe UI", 10),
                       cursor="hand2").pack(anchor="w")

        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", pady=20)

        # --- Фильтры форматов (с испраленным видом) ---
        ttk.Label(sidebar, text="Форматы файлов", style="Title.TLabel").pack(anchor="w", pady=(0, 5))
        self.filter_vars = {
            ".jpg": tk.BooleanVar(value=True),
            ".jpeg": tk.BooleanVar(value=True),
            ".png": tk.BooleanVar(value=False),
            ".tiff": tk.BooleanVar(value=False)
        }

        for ext, var in self.filter_vars.items():
            tk.Checkbutton(sidebar, text=f"Файлы {ext}", variable=var,
                           bg=self.colors["panel"],
                           fg=self.colors["fg"],
                           selectcolor=self.colors["panel"],
                           activebackground=self.colors["panel"],
                           activeforeground=self.colors["fg"],
                           font=("Segoe UI", 10),
                           cursor="hand2").pack(anchor="w")

        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", pady=20)

        # --- Кнопка старт ---
        self.btn_start = ttk.Button(sidebar, text="НАЧАТЬ АНАЛИЗ", style="Accent.TButton",
                                    command=self.start_analysis_thread)
        self.btn_start.pack(fill="x", pady=10)

        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", pady=20)

        # --- Экспорт ---
        ttk.Label(sidebar, text="Экспорт", style="Title.TLabel").pack(anchor="w", pady=(0, 5))
        self.btn_csv = ttk.Button(sidebar, text="💾 CSV", state="disabled", command=self.export_csv)
        self.btn_csv.pack(fill="x", pady=2)
        self.btn_html = ttk.Button(sidebar, text="🌐 HTML", state="disabled", command=self.export_html)
        self.btn_html.pack(fill="x", pady=2)

        # 2. ПРАВАЯ ПАНЕЛЬ (ПРЕДПРОСМОТР)
        info_panel = ttk.Frame(self, style="Panel.TFrame", width=320, padding=10)
        info_panel.pack(side="right", fill="y")
        info_panel.pack_propagate(False)  # Фиксируем ширину

        ttk.Label(info_panel, text="Предпросмотр", style="Title.TLabel").pack(pady=(0, 10))

        # Виджет для картинки
        self.lbl_preview = ttk.Label(info_panel, text="Нет изображения", anchor="center", background="#1e1e1e")
        self.lbl_preview.pack(fill="x", ipady=20)

        ttk.Separator(info_panel, orient="horizontal").pack(fill="x", pady=15)
        ttk.Label(info_panel, text="Подробные метаданные", style="Title.TLabel").pack(anchor="w", pady=(0, 5))

        # Текстовое поле для деталей
        self.txt_details = tk.Text(info_panel, height=20, bg="#1e1e1e", fg="#a6adc8",
                                   font=("Consolas", 9), bd=0, highlightthickness=0)
        self.txt_details.pack(fill="both", expand=True)
        self.txt_details.insert("1.0", "Выберите строку в таблице...")
        self.txt_details.config(state="disabled")

        # 3. ЦЕНТРАЛЬНАЯ ЧАСТЬ
        main_area = ttk.Frame(self, padding=10)
        main_area.pack(side="left", fill="both", expand=True)

        columns = ("filename", "size", "date", "camera", "lat", "lon")
        self.tree = ttk.Treeview(main_area, columns=columns, show="headings", selectmode="browse")

        headers = {
            "filename": "Имя файла",
            "size": "Размер",
            "date": "Дата съемки",
            "camera": "Камера",
            "lat": "Широта",
            "lon": "Долгота"
        }
        widths = [200, 80, 130, 150, 90, 90]

        for i, (col, name) in enumerate(headers.items()):
            self.tree.heading(col, text=name)
            self.tree.column(col, width=widths[i], anchor="w")

        self.tree.bind("<<TreeviewSelect>>", self.on_row_select)

        sb = ttk.Scrollbar(main_area, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=sb.set)
        self.tree.pack(side="top", fill="both", expand=True)
        sb.pack(side="right", fill="y", in_=self.tree)

        # Нижняя панель
        bottom_frame = ttk.Frame(main_area, padding=(0, 10, 0, 0))
        bottom_frame.pack(side="bottom", fill="x")

        self.lbl_status = ttk.Label(bottom_frame, text="Готов к работе", background=self.colors["bg"])
        self.lbl_status.pack(anchor="w")
        self.progress = ttk.Progressbar(bottom_frame, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", pady=(2, 5))

        self.log_text = tk.Text(bottom_frame, height=5, bg="#1e1e1e", fg="#a6adc8",
                                font=("Consolas", 9), bd=1, relief="solid")
        self.log_text.pack(fill="x")
        self.log_text.config(state="disabled")

    # --- ЛОГИКА ---
    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.lbl_path.config(text=folder)
            self.log(f"Выбрана папка: {folder}")
            self.selected_folder = folder

    def get_target_extensions(self):
        exts = []
        for ext, var in self.filter_vars.items():
            if var.get(): exts.append(ext)
        return tuple(exts)

    def start_analysis_thread(self):
        if not hasattr(self, 'selected_folder'):
            messagebox.showwarning("Внимание", "Пожалуйста, выберите папку.")
            return
        if self.is_processing: return

        self.is_processing = True
        self.btn_start.config(state="disabled")
        self.btn_csv.config(state="disabled")
        self.btn_html.config(state="disabled")

        for item in self.tree.get_children(): self.tree.delete(item)
        self.found_data = []
        self.map_data = {}

        threading.Thread(target=self.run_analysis, daemon=True).start()

    def run_analysis(self):
        target_exts = self.get_target_extensions()
        files_to_process = []
        self.log("Сканирование...")

        if self.var_recursive.get():
            for root, _, files in os.walk(self.selected_folder):
                for file in files:
                    if file.lower().endswith(target_exts):
                        files_to_process.append(os.path.join(root, file))
        else:
            for file in os.listdir(self.selected_folder):
                full = os.path.join(self.selected_folder, file)
                if os.path.isfile(full) and file.lower().endswith(target_exts):
                    files_to_process.append(full)

        total = len(files_to_process)
        self.log(f"Найдено изображений: {total}")
        self.progress['maximum'] = total
        self.progress['value'] = 0

        for i, filepath in enumerate(files_to_process):
            try:
                meta = self.process_image(filepath)
                self.found_data.append(meta)
                self.after(1, self.add_row_to_table, meta)
            except Exception as e:
                print(f"Error {filepath}: {e}")
            self.after(1, self.update_progress, i + 1, total)

        self.after(0, self.finish_analysis)

    def process_image(self, filepath):
        res = {
            "path": filepath,
            "filename": os.path.basename(filepath),
            "date": "-", "lat": "", "lon": "", "camera": "-", "size": "0 KB",
            "details": {}
        }
        try:
            res['size'] = format_bytes(os.path.getsize(filepath))

            with open(filepath, 'rb') as f:
                tags = exifread.process_file(f, details=False)

                dt = tags.get('EXIF DateTimeOriginal') or tags.get('Image DateTime')
                if dt: res['date'] = self._parse_date(dt)

                make = str(tags.get('Image Make', '')).strip()
                model = str(tags.get('Image Model', '')).strip()
                if make or model: res['camera'] = f"{make} {model}".strip()

                lat, lon = get_gps_coords(tags)
                if lat: res['lat'], res['lon'] = lat, lon

                for k in ['Image Software', 'EXIF ISOSpeedRatings', 'EXIF ExposureTime',
                          'EXIF FNumber', 'EXIF FocalLength', 'EXIF Flash']:
                    if k in tags:
                        clean_key = k.replace('EXIF ', '').replace('Image ', '')
                        res['details'][clean_key] = str(tags[k])
        except Exception:
            pass
        return res

    def add_row_to_table(self, meta):
        lat_str = f"{meta['lat']:.5f}" if meta['lat'] else "-"
        lon_str = f"{meta['lon']:.5f}" if meta['lon'] else "-"

        # --- ИСПРАВЛЕНИЕ ЗДЕСЬ: Добавили meta['camera'] в values ---
        item_id = self.tree.insert("", "end", values=(
            meta['filename'],
            meta['size'],
            meta['date'],
            meta['camera'],  # <--- Вот оно!
            lat_str,
            lon_str
        ))
        self.map_data[item_id] = meta

    def update_progress(self, current, total):
        self.progress['value'] = current
        self.lbl_status.config(text=f"Обработка: {current}/{total}")

    def finish_analysis(self):
        self.is_processing = False
        self.btn_start.config(state="normal")
        self.btn_csv.config(state="normal")
        self.btn_html.config(state="normal")
        self.lbl_status.config(text="Готово")
        messagebox.showinfo("Готово", "Анализ завершен!")

    # --- ОБРАБОТЧИК КЛИКА ПО СТРОКЕ (С фиксом для iPhone) ---
    def on_row_select(self, event):
        selected_items = self.tree.selection()
        if not selected_items: return

        item_id = selected_items[0]
        meta = self.map_data.get(item_id)
        if not meta: return

        # 1. Текст
        self.txt_details.config(state="normal")
        self.txt_details.delete("1.0", "end")

        info = f"Файл: {meta['filename']}\nПуть: {meta['path']}\n"
        info += f"Камера: {meta['camera']}\n"
        info += f"Размер: {meta['size']}\n"
        if meta['lat']: info += f"GPS: {meta['lat']}, {meta['lon']}\n"

        info += "\n[ТЕХНИЧЕСКИЕ ДАННЫЕ]\n"
        for k, v in meta['details'].items():
            info += f"{k}: {v}\n"

        self.txt_details.insert("1.0", info)
        self.txt_details.config(state="disabled")

        # 2. Картинка (Smart load)
        image_loaded = False

        # Способ 1: Прямое чтение
        try:
            with open(meta['path'], 'rb') as f:
                img = Image.open(f)
                img.load()
                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGB')
                img.thumbnail((300, 300))
                photo = ImageTk.PhotoImage(img)
                self.current_image_ref = photo
                self.lbl_preview.config(image=photo, text="")
                image_loaded = True
        except Exception:
            pass  # Молчим, попробуем миниатюру

        # Способ 2: Миниатюра из EXIF
        if not image_loaded:
            try:
                with open(meta['path'], 'rb') as f:
                    tags = exifread.process_file(f, details=True)
                    if 'JPEGThumbnail' in tags:
                        img = Image.open(io.BytesIO(tags['JPEGThumbnail']))
                        img.thumbnail((300, 300))
                        photo = ImageTk.PhotoImage(img)
                        self.current_image_ref = photo
                        self.lbl_preview.config(image=photo, text="")
                        image_loaded = True
            except Exception:
                pass

        if not image_loaded:
            self.lbl_preview.config(image="", text="❌ Формат не поддерживается")

    def export_csv(self):
        if not self.found_data: return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")])
        if path:
            try:
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Имя файла", "Путь", "Дата", "Широта", "Долгота", "Камера"])
                    for i in self.found_data:
                        writer.writerow([i['filename'], i['path'], i['date'], i['lat'], i['lon'], i['camera']])
                self.log(f"CSV сохранен: {path}")
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))

    def export_html(self):
        if not self.found_data:
            return

        path = filedialog.asksaveasfilename(defaultextension=".html",
                                            filetypes=[("HTML Files", "*.html")])
        if not path:
            return

        try:
            # 1. Подготовка CSS и Шапки
            # Мы используем f-строки для вставки CSS прямо в файл
            html_content = f"""
            <!DOCTYPE html>
            <html lang="ru">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Отчет GeoAnalyzer</title>
                <style>
                    body {{
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        background-color: #f4f7f6;
                        color: #333;
                        margin: 0;
                        padding: 40px;
                    }}
                    .container {{
                        max-width: 1200px;
                        margin: 0 auto;
                        background: #fff;
                        padding: 30px;
                        border-radius: 12px;
                        box-shadow: 0 4px 15px rgba(0,0,0,0.05);
                    }}
                    h1 {{
                        color: #2c3e50;
                        border-bottom: 2px solid #5c6bc0;
                        padding-bottom: 15px;
                        margin-top: 0;
                        font-size: 24px;
                    }}
                    .summary {{
                        background-color: #e8eaf6;
                        padding: 15px;
                        border-radius: 8px;
                        margin-bottom: 25px;
                        font-size: 14px;
                        color: #555;
                        display: flex;
                        justify-content: space-between;
                    }}
                    table {{
                        width: 100%;
                        border-collapse: collapse;
                        margin-top: 10px;
                    }}
                    th, td {{
                        padding: 12px 15px;
                        text-align: left;
                        border-bottom: 1px solid #eee;
                    }}
                    th {{
                        background-color: #5c6bc0;
                        color: white;
                        font-weight: 600;
                        text-transform: uppercase;
                        font-size: 12px;
                        letter-spacing: 0.5px;
                    }}
                    tr:hover {{
                        background-color: #f8f9fa;
                    }}
                    .gps-btn {{
                        display: inline-block;
                        padding: 4px 10px;
                        background-color: #fff;
                        border: 1px solid #5c6bc0;
                        color: #5c6bc0;
                        border-radius: 4px;
                        text-decoration: none;
                        font-size: 12px;
                        font-weight: bold;
                        transition: all 0.2s;
                    }}
                    .gps-btn:hover {{
                        background-color: #5c6bc0;
                        color: #fff;
                    }}
                    .no-data {{
                        color: #ccc;
                        font-style: italic;
                    }}
                    .footer {{
                        margin-top: 40px;
                        text-align: center;
                        font-size: 12px;
                        color: #aaa;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>📸 Отчет анализа метаданных</h1>

                    <div class="summary">
                        <span><strong>Дата генерации:</strong> {datetime.datetime.now().strftime("%d.%m.%Y %H:%M")}</span>
                        <span><strong>Всего файлов:</strong> {len(self.found_data)}</span>
                    </div>

                    <table>
                        <thead>
                            <tr>
                                <th style="width: 25%">Имя файла</th>
                                <th style="width: 15%">Дата съемки</th>
                                <th style="width: 25%">Камера</th>
                                <th style="width: 20%">GPS Координаты</th>
                                <th style="width: 15%">Карта</th>
                            </tr>
                        </thead>
                        <tbody>
            """

            # 2. Генерация строк таблицы
            for item in self.found_data:
                # Формирование ссылки на карты
                if item['lat']:
                    gps_text = f"{item['lat']:.5f}, {item['lon']:.5f}"
                    # Ссылка на Google Maps
                    gmaps_url = f"https://www.google.com/maps?q={item['lat']},{item['lon']}"
                    gps_html = gps_text
                    link_html = f'<a href="{gmaps_url}" target="_blank" class="gps-btn">Открыть на карте</a>'
                else:
                    gps_html = '<span class="no-data">Нет данных</span>'
                    link_html = '<span class="no-data">-</span>'

                # Вставка строки
                row = f"""
                    <tr>
                        <td style="font-weight: 500; color: #333;">{item['filename']}</td>
                        <td>{item['date']}</td>
                        <td>{item['camera']}</td>
                        <td style="font-family: monospace; color: #555;">{gps_html}</td>
                        <td>{link_html}</td>
                    </tr>
                """
                html_content += row

            # 3. Закрытие HTML
            html_content += """
                        </tbody>
                    </table>

                    <div class="footer">
                        Сгенерировано с помощью EXIF GeoAnalyzer Pro
                    </div>
                </div>
            </body>
            </html>
            """

            # 4. Запись в файл
            with open(path, 'w', encoding='utf-8') as f:
                f.write(html_content)

            self.log(f"HTML отчет сохранен: {path}")

        except Exception as e:
            messagebox.showerror("Ошибка экспорта", str(e))


if __name__ == "__main__":
    app = PhotoAnalyzerApp()
    app.mainloop()