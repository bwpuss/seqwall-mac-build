#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, platform, hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

try:
    from PIL import Image, ImageTk, ImageChops, Image
except Exception as e:
    from PIL import Image, ImageTk, ImageChops

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

BG_WINDOW = "#111111"
BG_PANEL  = "#1a1a1a"
BG_TILE   = "#222222"
FG_TEXT   = "#dddddd"
BORDER    = "#444444"

SEQ_PATTERNS = [
    re.compile(r'^(?P<prefix>.*?)[._-](?P<index>\d{3,6})\.(?P<ext>png|jpg|jpeg|bmp|tif|tiff)$', re.IGNORECASE),
    re.compile(r'^(?P<prefix>.*?)(?P<index>\d{3,6})\.(?P<ext>png|jpg|jpeg|bmp|tif|tiff)$', re.IGNORECASE),
]
SUPPORTED_EXTS = ('.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff')

@dataclass
class SequenceItem:
    key: str
    folder: str
    prefix: str
    ext: str
    frames: List[str]
    digits: int

def find_sequences(root: str) -> List[SequenceItem]:
    sequences: Dict[str, SequenceItem] = {}
    for dirpath, _, filenames in os.walk(root):
        candidates = [f for f in filenames if f.lower().endswith(SUPPORTED_EXTS)]
        temp: Dict[Tuple[str, str], List[Tuple[int, str, int]]] = {}
        for f in candidates:
            for pat in SEQ_PATTERNS:
                m = pat.match(f)
                if m:
                    prefix = m.group('prefix')
                    ext = m.group('ext').lower()
                    idx_str = m.group('index')
                    try:
                        idx = int(idx_str)
                    except ValueError:
                        continue
                    temp.setdefault((prefix, ext), []).append((idx, f, len(idx_str)))
                    break
        for (prefix, ext), items in temp.items():
            items.sort(key=lambda x: x[0])
            if len(items) >= 3:
                key = os.path.join(dirpath, f"{prefix}[##].{ext}")
                frames = [os.path.join(dirpath, fn) for _, fn, _ in items]
                digits = items[0][2]
                sequences[key] = SequenceItem(
                    key=key, folder=dirpath, prefix=prefix, ext=ext, frames=frames, digits=digits
                )
    return sorted(sequences.values(), key=lambda s: s.key.lower())

def resize_rgba_contain_premultiplied(img: Image.Image, box: Tuple[int,int]) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    dst_w, dst_h = box
    scale = min(dst_w / max(1,w), dst_h / max(1,h))
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    r, g, b, a = img.split()
    r = ImageChops.multiply(r, a).resize((new_w, new_h), Image.LANCZOS)
    g = ImageChops.multiply(g, a).resize((new_w, new_h), Image.LANCZOS)
    b = ImageChops.multiply(b, a).resize((new_w, new_h), Image.LANCZOS)
    a = a.resize((new_w, new_h), Image.LANCZOS)
    pm = Image.merge("RGBA", (r, g, b, a))
    return pm

def compose_on_bg(img_rgba: Image.Image, size: int, bg_color: str) -> Image.Image:
    if img_rgba.mode != "RGBA":
        img_rgba = img_rgba.convert("RGBA")
    bg = Image.new("RGBA", (size, size), bg_color)
    x = (size - img_rgba.width) // 2
    y = (size - img_rgba.height) // 2
    bg.alpha_composite(img_rgba, (x, y))
    return bg.convert("RGB")

class ThumbCache:
    def __init__(self, root_dir: str, size: int):
        self.mem: Dict[Tuple[str,int,int], ImageTk.PhotoImage] = {}
        self.root_dir = root_dir
        self.size = size
        self.cache_dir = os.path.join(root_dir, ".seqwall_cache")
        os.makedirs(self.cache_dir, exist_ok=True)

    def key(self, path: str, frame_idx: int) -> Tuple[str,int,int]:
        return (path, self.size, frame_idx)

    def disk_path(self, path: str, frame_idx: int) -> str:
        h = hashlib.sha1(f"{path}|{self.size}|{frame_idx}".encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, f"{h}.png")

    def get(self, path: str, frame_idx: int) -> Optional[ImageTk.PhotoImage]:
        k = self.key(path, frame_idx)
        if k in self.mem:
            return self.mem[k]
        dp = self.disk_path(path, frame_idx)
        if os.path.exists(dp):
            try:
                im = Image.open(dp)
                ph = ImageTk.PhotoImage(im)
                self.mem[k] = ph
                return ph
            except Exception:
                pass
        return None

    def put(self, path: str, frame_idx: int, image_rgb: Image.Image) -> ImageTk.PhotoImage:
        k = self.key(path, frame_idx)
        ph = ImageTk.PhotoImage(image_rgb)
        self.mem[k] = ph
        dp = self.disk_path(path, frame_idx)
        try:
            image_rgb.save(dp, format="PNG")
        except Exception:
            pass
        return ph

class SequenceWallApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("序列墙 · 极速版 + 导航 — 吊儿郎当的呜喵王制作")
        self.geometry("1500x900")
        self.minsize(1100, 650)
        self.configure(bg=BG_WINDOW)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG_PANEL)
        style.configure("TLabel", background=BG_PANEL, foreground=FG_TEXT)
        style.configure("TButton", background=BG_PANEL, foreground=FG_TEXT)
        style.configure("TCheckbutton", background=BG_PANEL, foreground=FG_TEXT)
        style.configure("TEntry", fieldbackground=BG_TILE, foreground=FG_TEXT)
        style.configure("TSpinbox", fieldbackground=BG_TILE, foreground=FG_TEXT)
        style.configure("Treeview", background=BG_PANEL, fieldbackground=BG_PANEL, foreground=FG_TEXT)
        style.map("TButton", background=[("active", "#2a2a2a")])

        self.current_root: Optional[str] = None
        self.thumb_cache: Optional[ThumbCache] = None

        self.path_var = tk.StringVar(value="")
        self.fps_var = tk.DoubleVar(value=8.0)
        self.cols_var = tk.IntVar(value=6)
        self.tile_var = tk.IntVar(value=200)
        self.animate_var = tk.BooleanVar(value=False)
        self.frame_step = tk.IntVar(value=2)
        self.status_var = tk.StringVar(value="准备就绪")

        self.sequences: List[SequenceItem] = []
        self.tiles: List['SeqTile'] = []
        self.pool = ThreadPoolExecutor(max_workers=max(2, os.cpu_count()//2))

        self._suppress_tree_select = False

        self._build_header()
        self._build_body()
        self._schedule_tick()

    def _build_header(self):
        header = ttk.Frame(self)
        header.pack(side=tk.TOP, fill=tk.X, padx=8, pady=6)

        ttk.Button(header, text="添加根目录", command=self.add_root).pack(side=tk.LEFT, padx=(0,6))
        ttk.Label(header, text="根目录路径").pack(side=tk.LEFT, padx=(12,4))
        ttk.Entry(header, textvariable=self.path_var, width=45).pack(side=tk.LEFT, padx=4)
        ttk.Button(header, text="选择…", command=self.choose_dir).pack(side=tk.LEFT, padx=4)
        ttk.Button(header, text="加载", command=self.load_from_path).pack(side=tk.LEFT, padx=4)

        ttk.Label(header, text="FPS").pack(side=tk.LEFT, padx=(20,4))
        ttk.Spinbox(header, from_=1, to=30, textvariable=self.fps_var, width=4).pack(side=tk.LEFT)
        ttk.Label(header, text="列数").pack(side=tk.LEFT, padx=(20,4))
        ttk.Spinbox(header, from_=1, to=24, textvariable=self.cols_var, width=4, command=self.relayout).pack(side=tk.LEFT)
        ttk.Label(header, text="瓦片大小").pack(side=tk.LEFT, padx=(20,4))
        ttk.Spinbox(header, from_=80, to=480, increment=10, textvariable=self.tile_var, width=6, command=self.on_tile_size_change).pack(side=tk.LEFT)
        ttk.Label(header, text="取样步长").pack(side=tk.LEFT, padx=(20,4))
        ttk.Spinbox(header, from_=1, to=8, textvariable=self.frame_step, width=4).pack(side=tk.LEFT)
        ttk.Checkbutton(header, text="播放全部", variable=self.animate_var).pack(side=tk.LEFT, padx=20)
        ttk.Button(header, text="暂停全部", command=lambda: self.set_animate(False)).pack(side=tk.LEFT, padx=4)
        ttk.Button(header, text="播放全部", command=lambda: self.set_animate(True)).pack(side=tk.LEFT, padx=4)

        status_frame = ttk.Frame(self)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(status_frame, textvariable=self.status_var, anchor="w").pack(side=tk.LEFT, padx=8, pady=4)

    def _build_body(self):
        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0,8))

        left = ttk.Frame(body, width=320)
        self.tree = ttk.Treeview(left, show="tree")
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self._init_tree_roots()
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        body.add(left, weight=1)

        right = ttk.Frame(body)
        container = ttk.Frame(right)
        container.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(container, highlightthickness=0, bg=BG_WINDOW, bd=0)
        self.scroll_y = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.scroll_x = ttk.Scrollbar(container, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.scroll_y.set, xscrollcommand=self.scroll_x.set)

        self.grid_frame = ttk.Frame(self.canvas, style="TFrame")
        self.canvas_win = self.canvas.create_window((0,0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        self.canvas.bind("<Enter>", lambda e: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda e: self._bind_wheel(False))

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll_y.grid(row=0, column=1, sticky="ns")
        self.scroll_x.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        body.add(right, weight=4)

    def _bind_wheel(self, enable: bool):
        if enable:
            self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        else:
            self.canvas.unbind_all("<MouseWheel>")

    def _init_tree_roots(self):
        if "windows" in platform.system().lower():
            import string
            from ctypes import windll
            bitmask = windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if bitmask & (1 << i):
                    drive = f"{letter}:\\"
                    node = self.tree.insert("", "end", text=drive, values=(drive,), open=False)
                    self.tree.insert(node, "end", text="...", values=("dummy",))
        else:
            node = self.tree.insert("", "end", text="/", values=("/",), open=False)
            self.tree.insert(node, "end", text="...", values=("dummy",))

    def add_root(self):
        d = filedialog.askdirectory()
        if d:
            node = self.tree.insert("", "end", text=d, values=(d,), open=False)
            self.tree.insert(node, "end", text="...", values=("dummy",))

    def _on_tree_open(self, event):
        node = self.tree.focus()
        path = self._node_path(node)
        children = self.tree.get_children(node)
        if children and self.tree.item(children[0], "text") == "...":
            self.tree.delete(children[0])
            try:
                for name in sorted(os.listdir(path)):
                    full = os.path.join(path, name)
                    if os.path.isdir(full):
                        child = self.tree.insert(node, "end", text=name, values=(full,), open=False)
                        if self._has_subdir(full):
                            self.tree.insert(child, "end", text="...", values=("dummy",))
            except PermissionError:
                pass

    def _on_tree_select(self, event):
        if self._suppress_tree_select:
            return
        node = self.tree.focus()
        path = self._node_path(node)
        if path and os.path.isdir(path):
            self.path_var.set(path)
            # don't auto scan

    def _node_path(self, node) -> Optional[str]:
        vals = self.tree.item(node, "values")
        if not vals: return None
        v = vals[0]
        return None if v == "dummy" else v

    def _has_subdir(self, path: str) -> bool:
        try:
            for name in os.listdir(path):
                if os.path.isdir(os.path.join(path, name)):
                    return True
        except Exception:
            return False
        return False

    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.path_var.get() or os.path.expanduser("~"))
        if d:
            self.path_var.set(d)

    def load_from_path(self):
        p = self.path_var.get().strip()
        if p and os.path.isdir(p):
            self.current_root = p
            self._expand_tree_to_path(p)
            self._scan_and_show(p)
        else:
            messagebox.showerror("错误", "请选择有效的根目录路径")

    def _expand_tree_to_path(self, path: str):
        target = os.path.abspath(path)

        # Build parts list
        parts = []
        if "windows" in platform.system().lower():
            drive, tail = os.path.splitdrive(target)
            if drive:
                parts.append(drive + "\\")
            if tail:
                sub = tail.strip("\\/")
                if sub:
                    parts.extend(sub.split("\\"))
        else:
            parts = ["/"] + [p for p in target.strip("/").split("/") if p]

        # find matching root
        root_node = None
        for node in self.tree.get_children(""):
            vals = self.tree.item(node, "values")
            if not vals: continue
            if os.path.abspath(vals[0]).lower() == os.path.abspath(parts[0]).lower():
                root_node = node
                break
        if not root_node:
            return

        self._suppress_tree_select = True
        try:
            node = root_node
            self.tree.item(node, open=True)
            self._on_tree_open(None)
            current = os.path.abspath(parts[0])
            for name in parts[1:]:
                current = os.path.join(current, name)
                # ensure children loaded
                self.tree.item(node, open=True)
                self._on_tree_open(None)
                match = None
                for child in self.tree.get_children(node):
                    vals = self.tree.item(child, "values")
                    if vals and os.path.abspath(vals[0]).lower() == os.path.abspath(current).lower():
                        match = child
                        break
                if match is None:
                    # create a node if not visible (optional)
                    match = self.tree.insert(node, "end", text=name, values=(current,), open=False)
                node = match
            # select final node
            self.tree.selection_set(node)
            self.tree.focus(node)
            self.tree.see(node)
        finally:
            self._suppress_tree_select = False

    def _scan_and_show(self, root: str):
        self.status_var.set(f"扫描中… {root}")
        self.update_idletasks()
        self.sequences = find_sequences(root)
        self.status_var.set(f"找到 {len(self.sequences)} 组序列 — {root}")
        self.thumb_cache = ThumbCache(root, size=int(self.tile_var.get()))
        self._populate_tiles()

    def _populate_tiles(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.tiles.clear()
        if not self.sequences:
            return
        cols = max(1, int(self.cols_var.get()))
        size = max(40, int(self.tile_var.get()))
        for i, seq in enumerate(self.sequences):
            r, c = divmod(i, cols)
            tile = SeqTile(self.grid_frame, seq, size=size, cache=self.thumb_cache, pool=self.pool)
            tile.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")
            self.tiles.append(tile)
        for ci in range(cols):
            self.grid_frame.grid_columnconfigure(ci, weight=1)

    def on_tile_size_change(self):
        size = max(40, int(self.tile_var.get()))
        if self.current_root:
            self.thumb_cache = ThumbCache(self.current_root, size=size)
        for t in self.tiles:
            t.set_size(size, new_cache=self.thumb_cache)

    def relayout(self):
        if not self.tiles:
            return
        cols = max(1, int(self.cols_var.get()))
        for i, t in enumerate(self.tiles):
            r, c = divmod(i, cols)
            t.grid_configure(row=r, column=c)
        for ci in range(cols):
            self.grid_frame.grid_columnconfigure(ci, weight=1)

    def set_animate(self, flag: bool):
        self.animate_var.set(flag)

    def _visible_y_range(self) -> Tuple[int,int]:
        bbox = self.canvas.bbox("all")
        if not bbox:
            return (0,0)
        y0 = int(self.canvas.canvasy(0))
        y1 = y0 + int(self.canvas.winfo_height())
        return (y0, y1)

    def _schedule_tick(self):
        fps = max(1.0, float(self.fps_var.get()))
        interval = int(1000 / fps)
        self._tick()
        self.after(interval, self._schedule_tick)

    def _tick(self):
        if not self.tiles:
            return
        y0, y1 = self._visible_y_range()
        step = max(1, int(self.frame_step.get()))
        play = self.animate_var.get()
        for t in self.tiles:
            ty = t.winfo_y()
            th = t.winfo_height() or (t.size + 32)
            if (ty < y1 and ty + th > y0):
                t.ensure_first_frame_loaded()
                if play:
                    t.step(step=step)

    def _on_mousewheel(self, event):
        delta = -1*(event.delta//120)*40
        self.canvas.yview_scroll(int(delta/40), "units")

class SeqTile(ttk.Frame):
    def __init__(self, parent, seq: SequenceItem, size=200, cache: Optional[ThumbCache]=None, pool=None):
        super().__init__(parent, style="TFrame")
        self.grid_propagate(False)
        self.pack_propagate(False)
        self.seq = seq
        self.size = size
        self.idx = 0
        self.running = True
        self.cache = cache
        self.pool = pool
        self.loading_first = False
        self.first_loaded = False

        outer = tk.Frame(self, bg=BORDER, width=size+2, height=size+32, highlightthickness=0, bd=0)
        outer.pack(fill=tk.BOTH, expand=False)
        outer.pack_propagate(False)

        inner = tk.Frame(outer, bg=BG_TILE, highlightthickness=0, bd=0)
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        inner.pack_propagate(False)

        self.canvas = tk.Canvas(inner, width=size, height=size, bg=BG_TILE, highlightthickness=0, bd=0)
        self.canvas.pack(side=tk.TOP, anchor="center")
        self.canvas.pack_propagate(False)

        self.caption = tk.Label(inner, text=self._caption_text(), bg=BG_TILE, fg=FG_TEXT, anchor="center")
        self.caption.pack(fill=tk.X)
        self.caption.configure(wraplength=size, justify="center")

        self.canvas.bind("<Button-1>", self._toggle)
        self.caption.bind("<Button-1>", self._toggle)

        self.set_size(size)

    def _caption_text(self):
        rel = os.path.basename(self.seq.folder)
        return f"{rel}/{self.seq.prefix}[{self.seq.digits*'#'}].{self.seq.ext}  ({len(self.seq.frames)}f)"

    def set_size(self, size: int, new_cache: Optional[ThumbCache]=None):
        self.size = size
        if new_cache is not None:
            self.cache = new_cache
        total_h = size + 30
        self.configure(width=size+2, height=total_h+2)
        for child in self.winfo_children():
            try:
                child.configure(width=size+2, height=total_h+2)
            except tk.TclError:
                pass
        self.canvas.config(width=size, height=size)
        self.caption.configure(wraplength=size)

    def _toggle(self, *_):
        self.running = not self.running

    def ensure_first_frame_loaded(self):
        if self.first_loaded or self.loading_first or not self.seq.frames:
            return
        self.loading_first = True
        path = self.seq.frames[0]
        if self.cache:
            ph = self.cache.get(path, 0)
            if ph:
                self._draw_photo(ph)
                self.first_loaded = True
                self.loading_first = False
                return
        if self.pool:
            self.pool.submit(self._load_and_cache, path, 0)
        else:
            self._load_and_cache(path, 0)

    def _load_and_cache(self, path: str, frame_idx: int):
        try:
            from PIL import Image
            im = Image.open(path).convert("RGBA")
            pm = resize_rgba_contain_premultiplied(im, (self.size, self.size))
            composed = compose_on_bg(pm, self.size, BG_TILE)
            if self.cache:
                ph = self.cache.put(path, frame_idx, composed)
            else:
                ph = ImageTk.PhotoImage(composed)
            self.canvas.after(0, self._draw_photo, ph)
            self.first_loaded = True
        except Exception as e:
            self.canvas.after(0, self._draw_text, f"Error:\n{e}")
        finally:
            self.loading_first = False

    def _draw_text(self, s: str):
        self.canvas.delete("all")
        self.canvas.create_text(self.size//2, self.size//2, text=s, fill=FG_TEXT)

    def _draw_photo(self, ph: ImageTk.PhotoImage):
        self.canvas.delete("all")
        self.canvas.create_image(self.size//2, self.size//2, image=ph)
        self.canvas.image = ph

    def step(self, step: int = 1):
        if not self.running or not self.seq.frames:
            return
        if not self.first_loaded:
            self.ensure_first_frame_loaded()
            return
        self.idx = (self.idx + step) % len(self.seq.frames)
        path = self.seq.frames[self.idx]
        if self.cache:
            ph = self.cache.get(path, self.idx)
            if ph:
                self._draw_photo(ph)
                return
        if self.pool:
            self.pool.submit(self._load_and_cache, path, self.idx)
        else:
            self._load_and_cache(path, self.idx)

if __name__ == "__main__":
    app = SequenceWallApp()
    app.mainloop()
