r"""CK Poster Variant Maker - part of the CK Suite.

Turns one high-resolution poster into a set of ready-to-post files in one click:

    Full-quality PNG    the poster at print/web size (keeps transparency)
    Web JPG             a lighter copy for websites
    Landscape / Square / Portrait / Story posts
                        the whole poster, sharp and centred, on a soft washed-out blur of itself

Every variant's size, file name and quality can be changed (Output Sizes...), or switched off.
Everything goes into <Save in>\<Folder name>\ (the folder name defaults to the event date),
together with a copy of the original poster (or the original itself, if "move" is ticked).

https://github.com/project-ceekay/ck-poster-variant-maker   -   MIT License
Requires:  pip install pillow
"""
import calendar
import copy
import datetime as dt
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.error
import urllib.request
import webbrowser
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageCms, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageTk

APP_NAME = "CK Poster Variant Maker"
VERSION = "1.0.1"
REPO = "project-ceekay/ck-poster-variant-maker"

# ------------------------------------------------------------- output variants
# kind "poster": the poster itself, resized. mode "long" keeps its shape (longest side = long),
#                mode "exact" makes exactly w x h, cropping the overflow (never stretching).
# kind "social": the whole poster centred on a blurred, washed-out copy of itself.
#                fill = how much of the canvas the poster may take (%); the background is the
#                poster blurred, then pixel * gain + lift (lighter, lower contrast) and re-saturated.
DEFAULT_VARIANTS = [
    dict(key="full", label="Full-quality PNG", kind="poster", on=True, fmt="PNG", name="{name}_full.png",
         mode="long", long=2000, w=1800, h=2400),
    dict(key="web", label="Web JPG", kind="poster", on=True, fmt="JPEG", name="{name}_web.jpg",
         mode="long", long=2000, w=1800, h=2400, quality=90),
    dict(key="landscape", label="Landscape post (16:9)", kind="social", on=True, name="{name}_landscape.jpg",
         w=1920, h=1080, fill=74, gain=0.85, lift=29, saturation=0.8, quality=82),
    dict(key="square", label="Square post (1:1)", kind="social", on=True, name="{name}_square.jpg",
         w=1080, h=1080, fill=95, gain=0.93, lift=17, saturation=1.0, quality=85),
    dict(key="portrait", label="Portrait post (4:5)", kind="social", on=True, name="{name}_portrait.jpg",
         w=1080, h=1350, fill=94, gain=0.93, lift=17, saturation=1.0, quality=85),
    dict(key="story", label="Story (9:16)", kind="social", on=False, name="{name}_story.jpg",
         w=1080, h=1920, fill=86, gain=0.90, lift=22, saturation=0.9, quality=85),
]
BLUR = 0.021                   # background blur radius as a fraction of the canvas' longest side
WORK_MAX = 3000                # social posts are built from a copy no bigger than this
LANCZOS = getattr(Image, "Resampling", Image).LANCZOS

# Big posters (e.g. 24x36" at 300 dpi) are well past Pillow's default safety limit,
# which exists to stop "decompression bomb" files. Raise it to a generous fixed cap.
Image.MAX_IMAGE_PIXELS = 400_000_000

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp")
IMAGE_TYPES = [("Images", " ".join("*" + e for e in IMAGE_EXTS)), ("All files", "*.*")]


# ------------------------------------------------------------- helpers
def app_path():
    """Folder of the exe when compiled, otherwise of this script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """Per-user settings folder: %APPDATA%\\CK Suite\\Poster Variant Maker."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "CK Suite", "Poster Variant Maker")
    os.makedirs(path, exist_ok=True)
    return path


def open_path(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.call(["open", path])
        else:
            subprocess.call(["xdg-open", path])
    except Exception:
        pass


def folder_name_problem(name):
    """Why name can't be a Windows folder name, or None if it's fine."""
    if not name:
        return "Folder name is empty"
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        return 'Folder name can\'t contain  < > : " / \\ | ? *'
    if name.endswith((".", " ")):
        return "Folder name can't end with a dot or space"
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com\d|lpt\d)(\..*)?", name):
        return "That folder name is reserved by Windows"
    return None


def parse_date(text):
    """'2026-12-05' -> date, or None if it isn't a valid YYYY-MM-DD date."""
    try:
        return dt.date.fromisoformat(text.strip())
    except ValueError:
        return None


def size_text(path):
    n = os.path.getsize(path)
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{n / 1000:.0f} KB"


def merge_variants(saved):
    """The default variants with any saved changes applied (unknown keys are ignored)."""
    out = copy.deepcopy(DEFAULT_VARIANTS)
    saved = {v.get("key"): v for v in saved or [] if isinstance(v, dict)}
    for v in out:
        for k, val in saved.get(v["key"], {}).items():
            if k in v and k not in ("key", "kind", "label", "fmt") and isinstance(val, type(v[k])):
                v[k] = val
    return out


def file_name(variant, src_path, date):
    stem = os.path.splitext(os.path.basename(src_path))[0]
    try:
        name = variant["name"].format(name=stem, date=date.isoformat())
    except (KeyError, IndexError, ValueError):
        name = f"{stem}_{variant['key']}"
    ext = ".png" if variant.get("fmt") == "PNG" else ".jpg"
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name if name.lower().endswith(ext) else os.path.splitext(name)[0] + ext


def describe_size(v):
    if v["kind"] == "poster" and v["mode"] == "long":
        return f"longest side {v['long']}"
    return f"{v['w']} × {v['h']}"


# ------------------------------------------------------------- image work
def flatten(img, background=(255, 255, 255)):
    """RGB copy of img; any transparency is placed on a white background (JPG has no alpha)."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        out = Image.new("RGB", rgba.size, background)
        out.paste(rgba, mask=rgba.getchannel("A"))
        return out
    return img.convert("RGB")


SRGB_ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def to_srgb(rgb, icc):
    """(image, profile bytes, log note) with rgb converted from its embedded profile to sRGB.
    Images that are already sRGB, or have no profile (assumed sRGB), pass through untouched."""
    if not icc:
        return rgb, None, None
    try:
        prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        name = (ImageCms.getProfileDescription(prof) or "").strip()
        if "srgb" in name.lower() or "iec61966" in name.lower():
            return rgb, icc, None
        if prof.profile.xcolor_space.strip() != "RGB":
            return rgb, icc, f"Colour profile '{name}' isn't RGB; left unconverted"
        out = ImageCms.profileToProfile(rgb, prof, ImageCms.ImageCmsProfile(io.BytesIO(SRGB_ICC)),
                                        renderingIntent=ImageCms.Intent.PERCEPTUAL, outputMode="RGB")
        return out, SRGB_ICC, f"Converted colours from '{name}' to sRGB for the web files"
    except Exception as e:
        return rgb, icc, f"Couldn't read the colour profile ({e}); left unconverted"


def resize_poster(img, v):
    """(image, cropped?) for a "poster" variant."""
    if img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGBA")  # palette/16-bit images can't be resized with LANCZOS
    if v["mode"] == "exact":
        cropped = abs(img.width / img.height - v["w"] / v["h"]) > 0.002
        return ImageOps.fit(img, (v["w"], v["h"]), LANCZOS, centering=(0.5, 0.5)), cropped
    f = v["long"] / max(img.size)
    return img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))), LANCZOS), False


def make_social(base, v, scale=1.0):
    """The whole poster, sharp and centred, on a blurred copy of itself. scale shrinks it for previews."""
    W, H = max(2, round(v["w"] * scale)), max(2, round(v["h"] * scale))
    # blur a quarter-size copy then scale up: much faster, and just as smooth at these radii
    small = (max(1, W // 4), max(1, H // 4))
    bg = ImageOps.fit(base, small, LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(BLUR * max(W, H) / 4)).resize((W, H), LANCZOS)
    gain, lift = v["gain"], v["lift"]
    bg = bg.point(lambda x: max(0, min(255, round(x * gain + lift))))
    if v["saturation"] != 1:
        bg = ImageEnhance.Color(bg).enhance(v["saturation"])
    box = (max(1, round(W * v["fill"] / 100)), max(1, round(H * v["fill"] / 100)))
    poster = ImageOps.contain(base, box, LANCZOS)
    bg.paste(poster, ((W - poster.width) // 2, (H - poster.height) // 2))
    return bg


def generate(src_path, date, out_dir, variants, log, saved, move_source=False):
    """Write every enabled variant plus the original poster (copied, or moved if move_source)
    into out_dir. log(msg) reports progress, saved(key, path) fires as each file is written.
    Returns the original's path afterwards."""
    os.makedirs(out_dir, exist_ok=True)
    with Image.open(src_path) as im:
        src = ImageOps.exif_transpose(im)  # read fully into memory, upright
        src.load()
    icc = src.info.get("icc_profile")
    log(f"Loaded {os.path.basename(src_path)} ({src.width} x {src.height}, {src.mode})")

    # one sRGB copy for every web / social file: sites strip colour profiles, and anything
    # that isn't sRGB then looks washed out or shifted once posted
    rgb, web_icc, note = to_srgb(flatten(src), icc)
    if note:
        log(note)
    base = rgb.copy()
    base.thumbnail((WORK_MAX, WORK_MAX), LANCZOS)

    for v in variants:
        if not v["on"]:
            continue
        path = os.path.join(out_dir, file_name(v, src_path, date))
        if v["kind"] == "poster" and v["fmt"] == "PNG":
            img, cropped = resize_poster(src, v)   # keeps transparency and the original profile
            img.save(path, "PNG", compress_level=6, icc_profile=icc)
        elif v["kind"] == "poster":
            img, cropped = resize_poster(rgb, v)
            img.save(path, "JPEG", quality=v["quality"], optimize=True, icc_profile=web_icc)
        else:
            img, cropped = make_social(base, v), False
            img.save(path, "JPEG", quality=v["quality"], optimize=True, icc_profile=web_icc)
        extra = "  - the poster's shape differed, so the edges were trimmed" if cropped else ""
        log(f"Saved {os.path.basename(path)}  ({img.width} x {img.height}, {size_text(path)}){extra}")
        saved(v["key"], path)

    # the original goes last, so a failure above never leaves it half-moved
    dest = os.path.join(out_dir, os.path.basename(src_path))
    if os.path.normcase(os.path.abspath(src_path)) == os.path.normcase(os.path.abspath(dest)):
        log(f"{os.path.basename(dest)} is already in the folder")
    elif move_source:
        shutil.move(src_path, dest)
        log(f"Moved {os.path.basename(dest)} into the folder")
    else:
        shutil.copy2(src_path, dest)
        log(f"Copied {os.path.basename(dest)} into the folder  ({size_text(dest)})")
    saved("source", dest)
    return dest if move_source else src_path


# ------------------------------------------------------------- updates
def version_tuple(text):
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3]) or (0,)


def latest_release():
    """{'version', 'notes', 'url', 'page'} of the newest GitHub release, or None."""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"User-Agent": "CK-Suite-Updater", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}  # the repo has no published release yet
        raise
    asset = next((a for a in data.get("assets", []) if a["name"].lower().endswith("setup.exe")), None)
    return {"version": data.get("tag_name", "").lstrip("v"), "notes": data.get("body") or "",
            "url": asset["browser_download_url"] if asset else None, "page": data.get("html_url")}


# ------------------------------------------------------------- look & feel
ACCENT = "#0067c0"      # Windows 11 accent blue
ERROR = "#c42b1c"
SUCCESS = "#0f7b0f"
MUTED = "#5f6368"
HEADER_BG = "#ffffff"
PREVIEW_BG = "#ececec"


def enable_dpi_awareness():
    """Tell Windows we draw at real resolution, so text is sharp instead of stretched."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def make_icon(size=64):
    """A small poster-on-blue window icon, drawn so the program needs no .ico file."""
    s = size / 64
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([4 * s, 4 * s, 60 * s, 60 * s], radius=12 * s, fill=ACCENT)
    d.rectangle([19 * s, 11 * s, 45 * s, 53 * s], fill="white")
    d.rectangle([23 * s, 15 * s, 41 * s, 35 * s], fill="#8cc4f0")
    d.rectangle([23 * s, 39 * s, 41 * s, 42 * s], fill=ACCENT)
    d.rectangle([23 * s, 45 * s, 35 * s, 48 * s], fill=ACCENT)
    return im


# ------------------------------------------------------------- date picker
class DatePicker(tk.Toplevel):
    """Small month calendar under `anchor`. Calls on_pick(date) when a day is clicked."""

    def __init__(self, master, initial, on_pick, anchor):
        super().__init__(master)
        self.withdraw()
        self.title("Pick a date")
        self.resizable(False, False)
        self.transient(master)
        self.configure(bg="white")
        self.on_pick = on_pick
        self.year, self.month = initial.year, initial.month
        self.selected = initial

        head = tk.Frame(self, bg="white")
        head.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Button(head, text="‹", width=3, command=lambda: self.shift(-1)).pack(side="left")
        self.title_lbl = tk.Label(head, bg="white", font=("Segoe UI Semibold", 11))
        self.title_lbl.pack(side="left", fill="x", expand=True)
        ttk.Button(head, text="›", width=3, command=lambda: self.shift(1)).pack(side="right")

        self.grid_frame = tk.Frame(self, bg="white")
        self.grid_frame.pack(padx=10)
        foot = tk.Frame(self, bg="white")
        foot.pack(fill="x", padx=10, pady=10)
        ttk.Button(foot, text="Today", command=lambda: self.pick(dt.date.today())).pack(side="right")

        self.bind("<Escape>", lambda e: self.destroy())
        self.draw()
        self.update_idletasks()
        self.geometry(f"+{anchor.winfo_rootx()}+{anchor.winfo_rooty() + anchor.winfo_height() + 4}")
        self.deiconify()
        self.grab_set()
        self.focus_set()

    def shift(self, months):
        m = self.month - 1 + months
        self.year, self.month = self.year + m // 12, m % 12 + 1
        self.draw()

    def draw(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.title_lbl.configure(text=f"{calendar.month_name[self.month]} {self.year}")
        for c, name in enumerate(["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]):
            tk.Label(self.grid_frame, text=name, bg="white", fg=MUTED,
                     font=("Segoe UI", 9)).grid(row=0, column=c, pady=(0, 4))
        today = dt.date.today()
        cal = calendar.Calendar(firstweekday=6)  # weeks start on Sunday
        for r, week in enumerate(cal.monthdatescalendar(self.year, self.month), start=1):
            for c, day in enumerate(week):
                bg, fg, font = "white", "#1a1a1a", ("Segoe UI", 10)
                if day.month != self.month:
                    fg = "#a0a0a0"
                if day == today:
                    fg, font = ACCENT, ("Segoe UI Semibold", 10)
                if day == self.selected:
                    bg, fg = ACCENT, "white"
                cell = tk.Label(self.grid_frame, text=str(day.day), width=4, pady=4,
                                bg=bg, fg=fg, font=font, cursor="hand2")
                cell.grid(row=r, column=c, padx=1, pady=1)
                cell.bind("<Button-1>", lambda e, d=day: self.pick(d))
                if day != self.selected:  # light hover highlight
                    cell.bind("<Enter>", lambda e: e.widget.configure(bg="#e5f1fb"))
                    cell.bind("<Leave>", lambda e: e.widget.configure(bg="white"))

    def pick(self, day):
        self.on_pick(day)
        self.destroy()


# ------------------------------------------------------------- output sizes dialog
class SizesDialog(tk.Toplevel):
    """Edit which variants are made, their sizes, file names and quality."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.withdraw()
        self.title("Output Sizes")
        self.transient(app)
        self.resizable(False, False)
        P = app.px
        body = ttk.Frame(self, padding=P(16))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Choose which files to make and how big they are. In file names, {name} is "
                             "the poster's file name and {date} is the event date.",
                  style="Muted.TLabel", wraplength=P(760), justify="left").grid(row=0, column=0, columnspan=8,
                                                                                sticky="w", pady=(0, P(10)))
        heads = ["Make", "Variant", "File name", "Size", "", "", "Quality", "Poster %"]
        for c, h in enumerate(heads):
            ttk.Label(body, text=h, font=("Segoe UI Semibold", 9)).grid(row=1, column=c, sticky="w", padx=P(4))
        self.rows = []
        for r, v in enumerate(app.variants, start=2):
            vars_ = {"on": tk.BooleanVar(value=v["on"]), "name": tk.StringVar(value=v["name"]),
                     "w": tk.StringVar(value=v["w"]), "h": tk.StringVar(value=v["h"])}
            ttk.Checkbutton(body, variable=vars_["on"]).grid(row=r, column=0, padx=P(4), pady=P(3))
            ttk.Label(body, text=v["label"]).grid(row=r, column=1, sticky="w", padx=P(4))
            ttk.Entry(body, textvariable=vars_["name"], width=24).grid(row=r, column=2, sticky="w", padx=P(4))
            size = ttk.Frame(body)
            size.grid(row=r, column=3, columnspan=3, sticky="w", padx=P(4))
            if v["kind"] == "poster":
                vars_["mode"] = tk.StringVar(value=v["mode"])
                vars_["long"] = tk.StringVar(value=v["long"])
                ttk.Radiobutton(size, text="Keep shape, longest side", value="long", variable=vars_["mode"]).pack(side="left")
                ttk.Entry(size, textvariable=vars_["long"], width=6).pack(side="left", padx=(P(4), P(10)))
                ttk.Radiobutton(size, text="Exact", value="exact", variable=vars_["mode"]).pack(side="left")
            ttk.Entry(size, textvariable=vars_["w"], width=6).pack(side="left", padx=(P(4), P(2)))
            ttk.Label(size, text="×").pack(side="left")
            ttk.Entry(size, textvariable=vars_["h"], width=6).pack(side="left", padx=(P(2), 0))
            if "quality" in v:
                vars_["quality"] = tk.StringVar(value=v["quality"])
                ttk.Spinbox(body, from_=40, to=100, textvariable=vars_["quality"], width=5).grid(row=r, column=6, padx=P(4))
            else:
                ttk.Label(body, text="lossless", style="Muted.TLabel").grid(row=r, column=6, padx=P(4))
            if v["kind"] == "social":
                vars_["fill"] = tk.StringVar(value=v["fill"])
                ttk.Spinbox(body, from_=30, to=100, textvariable=vars_["fill"], width=5).grid(row=r, column=7, padx=P(4))
            self.rows.append((v, vars_))
        bar = ttk.Frame(body)
        bar.grid(row=len(app.variants) + 2, column=0, columnspan=8, sticky="ew", pady=(P(14), 0))
        ttk.Button(bar, text="Reset to Defaults", command=self.reset).pack(side="left")
        ttk.Button(bar, text="Save", style="Go.TButton", default="active", command=self.save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=(0, P(8)))
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + P(60)}+{app.winfo_rooty() + P(80)}")
        self.deiconify()
        self.grab_set()

    def reset(self):
        defaults = {v["key"]: v for v in DEFAULT_VARIANTS}
        for v, vars_ in self.rows:
            d = defaults[v["key"]]
            for k, var in vars_.items():
                var.set(d[k])

    def save(self):
        new = copy.deepcopy(self.app.variants)
        for (v, vars_), out in zip(self.rows, new):
            try:
                for k in ("w", "h", "long", "quality", "fill"):
                    if k in vars_:
                        n = int(vars_[k].get())
                        lo, hi = {"quality": (40, 100), "fill": (30, 100)}.get(k, (16, 20000))
                        if not lo <= n <= hi:
                            raise ValueError(f"{v['label']}: {k} must be between {lo} and {hi}")
                        out[k] = n
            except ValueError as e:
                messagebox.showerror("Output Sizes", str(e) if "between" in str(e)
                                     else f"{v['label']}: sizes and quality must be whole numbers.", parent=self)
                return
            out["on"] = vars_["on"].get()
            out["name"] = vars_["name"].get().strip() or v["name"]
            if "mode" in vars_:
                out["mode"] = vars_["mode"].get()
        if not any(v["on"] for v in new):
            messagebox.showerror("Output Sizes", "Turn on at least one variant.", parent=self)
            return
        self.app.set_variants(new)
        self.destroy()


# ------------------------------------------------------------- drag and drop
def enable_file_drop(widget, on_drop):
    """Windows only: on_drop(paths) is called when files are dragged onto widget's window.
    Uses the classic WM_DROPFILES message directly, so no extra package is needed."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.CallWindowProcW.restype = LRESULT
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                           wintypes.WPARAM, wintypes.LPARAM]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        shell32.DragFinish.argtypes = [ctypes.c_void_p]
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        WM_DROPFILES, GWLP_WNDPROC = 0x0233, -4
        hwnd = widget.winfo_id()
        drops = queue.Queue()

        def proc(h, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                paths = []
                for i in range(shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)):
                    size = shell32.DragQueryFileW(wparam, i, None, 0) + 1
                    buf = ctypes.create_unicode_buffer(size)
                    shell32.DragQueryFileW(wparam, i, buf, size)
                    paths.append(buf.value)
                shell32.DragFinish(wparam)
                drops.put(paths)  # never call Tk from in here (it crashes Python)
                return 0
            return user32.CallWindowProcW(old_proc, h, msg, wparam, lparam)

        widget._drop_proc = WNDPROC(proc)  # must stay referenced while the window exists
        shell32.DragAcceptFiles(hwnd, True)
        old_proc = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(widget._drop_proc, ctypes.c_void_p))

        def check():
            while not drops.empty():
                on_drop(drops.get())
            widget.after(150, check)

        check()
    except Exception:
        pass  # drag and drop is a convenience; Browse still works


# ------------------------------------------------------------------ app
def smooth_resize(root, settle_ms=120):
    """Keep dragging the window edge smooth. On Windows every Tk widget is its own native window,
    and all of them are moved and repainted on every resize step, so live resizing a full window
    lags. While the size is changing, the window shows a still picture of itself instead (the
    bottom-packed footer pinned to the bottom edge) and the real widgets are hidden; settle_ms
    after the last change they come back, laid out once for the new size."""
    if not sys.platform.startswith("win"):
        return
    import ctypes
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    H = ctypes.c_void_p
    user32.GetDC.restype = H
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, H]
    user32.PrintWindow.argtypes = [wintypes.HWND, H, wintypes.UINT]
    gdi32.CreateCompatibleDC.restype = H
    gdi32.CreateCompatibleDC.argtypes = [H]
    gdi32.CreateCompatibleBitmap.restype = H
    gdi32.CreateCompatibleBitmap.argtypes = [H, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = H
    gdi32.SelectObject.argtypes = [H, H]
    gdi32.GetDIBits.argtypes = [H, H, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [H]
    gdi32.DeleteDC.argtypes = [H]

    def capture(w, h):
        """The window's client area as Windows composed it, even where other windows cover it."""
        hwnd = int(root.wm_frame(), 16)
        hdc = user32.GetDC(hwnd)
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mem, bmp)
        try:
            if not user32.PrintWindow(hwnd, mem, 3):  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
                return None
            header = (ctypes.c_uint32 * 10)(40, w, ctypes.c_uint32(-h).value, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)  # 32-bit top-down BGRX rows
            if not gdi32.GetDIBits(mem, bmp, 0, h, buf, header, 0):
                return None
            return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(hwnd, hdc)

    bg = ttk.Style(root).lookup("TFrame", "background") or "#f0f0f0"
    state = {"size": None, "frozen": None, "shots": [], "job": None, "zoomed": False}
    big = round(200 * root.winfo_fpixels("1i") / 96)  # a jump this large (200 px at 100%) is no drag

    def thaw():
        state["job"] = None
        frozen, state["frozen"] = state["frozen"], None
        for w, info in frozen or ():
            w.pack(**info)  # in the original order, so the layout is exactly as before
        for shot in state["shots"]:
            shot.destroy()
        state["shots"] = []

    def freeze(old_w, old_h):
        slaves = [(w, w.pack_info()) for w in root.pack_slaves() if w.winfo_ismapped()]
        if not slaves:
            return
        w, h = root.winfo_width(), root.winfo_height()
        try:
            shown = capture(w, h)
        except (OSError, ValueError, tk.TclError):
            shown = None
        if shown is None:
            return  # just resize normally
        # the sections are still laid out for the old size; a strip lost to shrinking shows as background
        image = Image.new("RGB", (old_w, old_h), tuple(c >> 8 for c in root.winfo_rgb(bg)))
        image.paste(shown.crop((0, 0, min(w, old_w), min(h, old_h))))
        # everything from the first bottom-packed section down stays pinned to the bottom edge
        split = min([w.winfo_y() for w, info in slaves if info.get("side") == "bottom"] or [old_h])
        for box, place in (((0, 0, old_w, split), dict(x=0, y=0, anchor="nw")),
                           ((0, split, old_w, old_h), dict(x=0, rely=1, anchor="sw"))):
            if box[3] > box[1]:
                photo = ImageTk.PhotoImage(image.crop(box))
                shot = tk.Label(root, image=photo, bd=0, highlightthickness=0, bg=bg)
                shot.image = photo
                shot.place(**place)
                state["shots"].append(shot)
        for w, info in slaves:
            info.pop("in", None)
            w.pack_forget()
        state["frozen"] = slaves

    def on_configure(e):
        if e.widget is not root:
            return
        size = (e.width, e.height)
        old = state["size"]
        state["size"] = size
        if old in (None, size):
            return
        zoomed, was_zoomed = root.state() == "zoomed", state["zoomed"]
        state["zoomed"] = zoomed
        if state["frozen"] is None:
            # one-off jumps (maximize, restore, snapping) just lay out once, as normal
            if zoomed or was_zoomed or abs(size[0] - old[0]) > big or abs(size[1] - old[1]) > big:
                return
            freeze(*old)  # the window still shows the layout for the previous size
        if state["job"]:
            root.after_cancel(state["job"])
        state["job"] = root.after(settle_ms, thaw)

    root.configure(bg=bg)  # what shows in newly uncovered space while frozen
    root.bind("<Configure>", on_configure, add="+")


class App(tk.Tk):
    def __init__(self):
        enable_dpi_awareness()
        super().__init__()
        self.scale = self.winfo_fpixels("1i") / 96  # 1.0 at 100% Windows scaling
        self.title(APP_NAME)
        h = min(self.px(740), self.winfo_screenheight() - self.px(80))
        self.geometry(f"{self.px(940)}x{h}")
        self.minsize(self.px(800), min(self.px(600), h))
        self.icon = ImageTk.PhotoImage(make_icon())
        self.iconphoto(True, self.icon)
        self.setup_style()

        self.config_file = os.path.join(data_dir(), "config.json")
        self.cfg = self.load_config()
        self.variants = merge_variants(self.cfg.get("variants"))
        self.source = tk.StringVar()
        # start at the last date used while it's still upcoming (same event), else today
        last = parse_date(str(self.cfg.get("last_date", "")))
        start_date = last if last and last >= dt.date.today() else dt.date.today()
        self.date = tk.StringVar(value=start_date.isoformat())
        saved_out = self.cfg.get("output_dir", "")
        self.out_dir = tk.StringVar(value=os.path.normpath(saved_out) if saved_out else "")
        # the folder name follows the date until the user types their own title
        self.folder = tk.StringVar(value=self.date.get())
        self.auto_folder = self.date.get()
        self.move_src = tk.BooleanVar(value=bool(self.cfg.get("move_source", False)))
        self.auto_update = tk.BooleanVar(value=self.cfg.get("auto_update", True))
        self.last_out = ""      # folder made by the last successful run
        self.busy = False
        self.row_paths = {}     # table row -> file it produced (double-click opens it)
        self.preview_path = None
        self.preview_photo = None
        self.preview_base = None   # small poster image the previews are drawn from
        self.preview_token = 0
        self.update_info_ = None   # newest release when it's newer than this copy

        self.build_ui()
        smooth_resize(self)
        enable_file_drop(self, self.on_drop)
        # the date is the thing most likely to need changing, so start there
        self.after(150, lambda: (self.date_entry.focus_set(), self.date_entry.select_range(0, "end")))
        self.bind("<Control-o>", lambda e: self.browse_source())
        self.bind("<Control-Return>", lambda e: self.start())
        self.bind("<F5>", lambda e: self.start())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log(f"{APP_NAME} {VERSION}. Choose a poster, a date and where to save.")
        if self.auto_update.get() and time.time() - self.cfg.get("last_update_check", 0) > 20 * 3600:
            self.after(2500, lambda: self.check_updates(quiet=True))

    def px(self, n):
        """n pixels at 100% scaling, converted to this screen's scaling."""
        return round(n * self.scale)

    def setup_style(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        if sys.platform.startswith("win"):
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
                try:
                    tkfont.nametofont(name).configure(family="Segoe UI", size=10)
                except tk.TclError:
                    pass
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        style.configure("Go.TButton", font=("Segoe UI Semibold", 10), padding=(self.px(20), self.px(6)))
        style.configure("Footer.TButton", padding=(self.px(14), self.px(6)))
        style.configure("Section.TLabelframe.Label", font=("Segoe UI Semibold", 10), foreground=ACCENT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Error.TLabel", foreground=ERROR)
        style.configure("Success.TLabel", foreground=SUCCESS)
        style.configure("Link.TLabel", foreground=ACCENT)
        style.configure("Treeview", rowheight=line + self.px(10))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))

    # ---------------------------------------------------------- UI
    def build_ui(self):
        P, gap = self.px, self.px(12)

        # header band
        header = tk.Frame(self, bg=HEADER_BG)
        header.pack(fill="x")
        tk.Label(header, text="Poster Variant Maker", bg=HEADER_BG, fg="#1a1a1a",
                 font=("Segoe UI Semibold", 16)).pack(anchor="w", padx=P(20), pady=(P(14), 0))
        tk.Label(header, text="Turn one high-resolution poster into web, social and print-ready versions "
                              "in one click.",
                 bg=HEADER_BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=P(20), pady=(0, P(14)))
        ttk.Separator(self).pack(fill="x")

        # footer (packed before the body so it always stays visible)
        footer = ttk.Frame(self, padding=(P(20), P(12)))
        footer.pack(side="bottom", fill="x")
        ttk.Separator(self).pack(side="bottom", fill="x")
        self.go_btn = ttk.Button(footer, text="Generate", style="Go.TButton", command=self.start, default="active")
        self.go_btn.pack(side="right")
        ttk.Button(footer, text="Open Folder", style="Footer.TButton",
                   command=self.open_out).pack(side="right", padx=(0, P(8)))
        about = ttk.Label(footer, text=f"About · v{VERSION}", style="Link.TLabel", cursor="hand2")
        about.pack(side="right", padx=(0, P(16)))
        about.bind("<Button-1>", lambda e: self.show_about())
        self.update_lbl = ttk.Label(footer, text="", style="Link.TLabel", cursor="hand2",
                                    font=("Segoe UI Semibold", 10))
        self.update_lbl.bind("<Button-1>", lambda e: self.offer_update())
        self.progress = ttk.Progressbar(footer, maximum=1, length=P(160))  # shown while working
        self.status = ttk.Label(footer, text="Ready", style="Muted.TLabel")
        self.status.pack(side="left")

        body = ttk.Frame(self, padding=(P(20), P(16), P(20), P(12)))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        # --- poster
        src = ttk.Labelframe(body, text="Poster", style="Section.TLabelframe", padding=P(12))
        src.grid(row=0, column=0, sticky="nsew", pady=(0, gap))
        src.columnconfigure(1, weight=1)
        ttk.Label(src, text="Image file").grid(row=0, column=0, sticky="w")
        ttk.Entry(src, textvariable=self.source).grid(row=0, column=1, sticky="ew", padx=P(8))
        ttk.Button(src, text="Browse…", command=self.browse_source).grid(row=0, column=2)
        self.info = ttk.Label(src, text="", style="Muted.TLabel")
        self.info.grid(row=1, column=1, columnspan=2, sticky="w", padx=P(8), pady=(P(3), P(10)))

        ttk.Label(src, text="Event date").grid(row=2, column=0, sticky="w")
        date_row = ttk.Frame(src)
        date_row.grid(row=2, column=1, columnspan=2, sticky="w", padx=P(8))
        self.date_entry = ttk.Entry(date_row, textvariable=self.date, width=12)
        self.date_entry.pack(side="left")
        self.date_entry.bind("<Up>", lambda e: self.nudge_date(1))
        self.date_entry.bind("<Down>", lambda e: self.nudge_date(-1))
        self.cal_btn = ttk.Button(date_row, text="Calendar…", command=self.pick_date)
        self.cal_btn.pack(side="left", padx=(P(6), 0))
        ttk.Button(date_row, text="Today",
                   command=lambda: self.date.set(dt.date.today().isoformat())).pack(side="left", padx=(P(6), 0))
        self.date_note = ttk.Label(date_row, text="", style="Muted.TLabel")
        self.date_note.pack(side="left", padx=(P(12), 0))

        # --- output
        out = ttk.Labelframe(body, text="Output", style="Section.TLabelframe", padding=P(12))
        out.grid(row=1, column=0, sticky="nsew", pady=(0, gap))
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Save in").grid(row=0, column=0, sticky="w")
        ttk.Entry(out, textvariable=self.out_dir).grid(row=0, column=1, sticky="ew", padx=P(8))
        ttk.Button(out, text="Browse…", command=self.browse_out).grid(row=0, column=2)

        ttk.Label(out, text="Folder name").grid(row=1, column=0, sticky="w", pady=(P(8), 0))
        folder_row = ttk.Frame(out)
        folder_row.grid(row=1, column=1, columnspan=2, sticky="ew", padx=P(8), pady=(P(8), 0))
        ttk.Entry(folder_row, textvariable=self.folder, width=28).pack(side="left")
        ttk.Button(folder_row, text="Use Date", command=self.folder_from_date).pack(side="left", padx=(P(6), 0))
        ttk.Button(folder_row, text="Output Sizes…", command=lambda: SizesDialog(self)).pack(side="right")
        ttk.Checkbutton(out, text="Move the original poster into the folder instead of copying it",
                        variable=self.move_src).grid(row=2, column=1, columnspan=2, sticky="w",
                                                     padx=P(8), pady=(P(8), 0))
        self.path_lbl = ttk.Label(out, text="", style="Muted.TLabel")
        self.path_lbl.grid(row=3, column=1, columnspan=2, sticky="w", padx=P(8), pady=(P(6), 0))

        # --- preview
        pv = self.pv_frame = ttk.Labelframe(body, text="Preview", style="Section.TLabelframe", padding=P(10))
        pv.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(gap, 0), pady=(0, gap))
        self.pv_box = (P(180), P(225))
        holder = tk.Frame(pv, width=self.pv_box[0], height=self.pv_box[1], bg=PREVIEW_BG)
        holder.pack(expand=True)
        holder.pack_propagate(False)
        self.preview = tk.Label(holder, text="No poster", bg=PREVIEW_BG, fg=MUTED, font=("Segoe UI", 9))
        self.preview.pack(fill="both", expand=True)

        # --- files table and activity log, as tabs
        tabs = ttk.Notebook(body)
        tabs.grid(row=2, column=0, columnspan=2, sticky="nsew")
        files = ttk.Frame(tabs, padding=P(10))
        act = ttk.Frame(tabs, padding=P(10))
        tabs.add(files, text="  Files  ")
        tabs.add(act, text="  Activity  ")
        self.tabs, self.act_tab = tabs, act
        self.tree = ttk.Treeview(files, columns=("file", "purpose", "size", "status"), show="headings",
                                 height=7, selectmode="browse")
        for col, text, width, stretch in (("file", "File", 270, True), ("purpose", "Purpose", 190, False),
                                          ("size", "Size", 140, False), ("status", "Status", 120, False)):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=P(width), minwidth=P(60), stretch=stretch, anchor="w")
        self.tree.tag_configure("pending", foreground=MUTED)
        self.tree.tag_configure("working", foreground=ACCENT)
        self.tree.tag_configure("error", foreground=ERROR)
        self.tree.bind("<Double-1>", self.open_row)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_preview())
        self.tree.bind("<Escape>", lambda e: self.tree.selection_set(()))
        sb = ttk.Scrollbar(files, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # --- activity log
        self.log_box = tk.Text(act, height=4, wrap="word", state="disabled", font=("Consolas", 9),
                               relief="flat", borderwidth=0, bg="#fbfbfb", padx=P(6), pady=P(4),
                               highlightthickness=1, highlightbackground="#d9d9d9", highlightcolor="#d9d9d9")
        sb = ttk.Scrollbar(act, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=sb.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log_box.tag_configure("error", foreground=ERROR)
        self.log_box.tag_configure("ok", foreground=SUCCESS)

        self.source.trace_add("write", lambda *_: self.update_info())
        self.date.trace_add("write", lambda *_: self.update_date_note())
        for var in (self.out_dir, self.folder, self.move_src):
            var.trace_add("write", lambda *_: self.update_names())
        self.build_rows()
        self.update_info()
        self.update_date_note()

    def build_rows(self):
        """One table row per enabled variant, plus the original poster."""
        self.tree.delete(*self.tree.get_children())
        self.order = [v["key"] for v in self.variants if v["on"]] + ["source"]
        for v in self.variants:
            if v["on"]:
                self.tree.insert("", "end", iid=v["key"], values=("—", v["label"], describe_size(v), ""))
        self.tree.insert("", "end", iid="source", values=("—", "Original (copy)", "", ""))

    def set_variants(self, variants):
        self.variants = variants
        self.cfg["variants"] = variants
        self.save_config()
        self.build_rows()
        self.update_info()
        self.log("Output sizes updated")

    # ---------------------------------------------------------- small updates
    def log(self, msg, tag=None):
        """Append a timestamped line to the log (main thread only)."""
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{dt.datetime.now():%H:%M:%S}] {msg}\n", tag)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_status(self, text, style="Muted.TLabel"):
        self.status.configure(text=text, style=style)

    def update_info(self):
        path = self.source.get().strip()
        text, style, dims = "No poster selected. Drag one onto this window, or click Browse.", "Muted.TLabel", ""
        if path:
            if os.path.isfile(path):
                try:
                    with Image.open(path) as im:  # reads only the header, so this is instant
                        dims = f"{im.width} × {im.height}"
                        text = f"{dims} px  ·  {im.format}  ·  {size_text(path)}"
                except Exception:
                    text, style = "Not an image this program can read", "Error.TLabel"
            else:
                text, style = "File not found", "Error.TLabel"
        self.info.configure(text=text, style=style)
        self.tree.set("source", "size", dims)
        self.load_preview(path if dims else None)
        self.update_names()

    def load_preview(self, path):
        """Show a thumbnail of path. Big posters take a moment, so it's made in a thread."""
        if path == self.preview_path:
            return
        self.preview_path = path
        self.preview_token += 1
        token = self.preview_token
        self.preview_base = None
        if not path:
            self.show_preview()
            return
        self.preview_photo = None
        self.preview.configure(image="", text="Loading preview…")
        big = (self.pv_box[0] * 3, self.pv_box[1] * 3)  # extra detail for the social mock-ups
        result = {}

        def work():
            try:
                with Image.open(path) as im:
                    im.draft("RGB", big)  # fast path for JPGs
                    im = ImageOps.exif_transpose(im)
                    if im.mode not in ("RGB", "RGBA", "L", "LA"):
                        im = im.convert("RGBA")
                    im.thumbnail(big, LANCZOS)
                    result["img"] = flatten(im)
            except Exception:
                pass

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

        def check():
            if token != self.preview_token:
                return  # a different poster was chosen meanwhile
            if thread.is_alive():
                self.after(100, check)
            elif "img" in result:
                self.preview_base = result["img"]
                self.show_preview()
            else:
                self.preview.configure(image="", text="No preview")

        self.after(100, check)

    def show_preview(self):
        """Draw the selected file in the table (social posts as mock-ups), else the poster."""
        sel = self.tree.selection()
        v = next((v for v in self.variants if sel and v["key"] == sel[0]), None)
        social = v is not None and v["kind"] == "social"
        self.pv_frame.configure(text=f"Preview · {v['label']}" if social else "Preview")
        base = self.preview_base
        if base is None:
            if not self.preview_path:
                self.preview_photo = None
                self.preview.configure(image="", text="No poster")
            return
        if social:
            img = make_social(base, v, scale=self.pv_box[0] * 2 / max(v["w"], v["h"]))
        elif v is not None and v["mode"] == "exact":  # show the crop the exact size will make
            img = ImageOps.fit(base, (base.width, max(1, round(base.width * v["h"] / v["w"]))), LANCZOS)
        else:
            img = base.copy()
        img.thumbnail(self.pv_box, LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(img)
        self.preview.configure(image=self.preview_photo, text="")

    def update_date_note(self):
        d = parse_date(self.date.get())
        self.date_note.configure(text=f"{d:%A, %B} {d.day}, {d.year}" if d else "Not a valid date (YYYY-MM-DD)",
                                 style="Muted.TLabel" if d else "Error.TLabel")
        # keep the folder name in step with the date, unless the user typed their own
        if d and self.folder.get().strip() in (self.auto_folder, ""):
            self.folder_from_date()
        self.update_names()

    def nudge_date(self, days):
        """Up/Down arrows in the date box move it a day at a time."""
        d = parse_date(self.date.get()) or dt.date.today()
        self.date.set((d + dt.timedelta(days=days)).isoformat())
        return "break"

    def folder_from_date(self):
        d = parse_date(self.date.get())
        if d:
            self.auto_folder = d.isoformat()
            self.folder.set(self.auto_folder)

    def target_dir(self):
        return os.path.normpath(os.path.join(self.out_dir.get().strip(), self.folder.get().strip()))

    def output_names(self, src, date):
        names = {v["key"]: file_name(v, src, date) for v in self.variants if v["on"]}
        names["source"] = os.path.basename(src)
        return names

    def update_names(self):
        """Refresh the file names in the table and the 'Saves to' line."""
        if self.busy or not hasattr(self, "order"):
            return
        d = parse_date(self.date.get())
        src = self.source.get().strip()
        names = self.output_names(src or "poster.png", d or dt.date.today())
        if not src:
            names = {k: "—" for k in names}
        self.row_paths = {}
        for key in self.order:
            self.tree.set(key, "file", names[key])
            self.tree.set(key, "status", "")
            self.tree.item(key, tags=())
        self.tree.set("source", "purpose", "Original (moved)" if self.move_src.get() else "Original (copy)")

        problem = folder_name_problem(self.folder.get().strip())
        if problem:
            self.path_lbl.configure(text=problem, style="Error.TLabel")
        elif self.out_dir.get().strip():
            self.path_lbl.configure(text=f"Saves to  {self.target_dir()}\\", style="Muted.TLabel")
        else:
            self.path_lbl.configure(text="Choose a folder to save in.", style="Muted.TLabel")

    # ---------------------------------------------------------- browsing
    def browse_source(self):
        start = self.cfg.get("source_dir") or os.path.expanduser("~")
        path = filedialog.askopenfilename(title="Choose the poster", initialdir=start, filetypes=IMAGE_TYPES)
        if path:
            self.set_source(path)

    def set_source(self, path):
        path = os.path.normpath(path)
        self.source.set(path)
        self.cfg["source_dir"] = os.path.dirname(path)
        if not self.out_dir.get():  # handy default: next to the poster
            self.out_dir.set(os.path.dirname(path))

    def on_drop(self, paths):
        if self.busy:
            return
        images = [p for p in paths if os.path.isfile(p) and p.lower().endswith(IMAGE_EXTS)]
        if images:
            self.set_source(images[0])
            self.log(f"Dropped {os.path.basename(images[0])}")
        else:
            self.set_status("That isn't an image file this program can use.", "Error.TLabel")

    def browse_out(self):
        start = self.out_dir.get() or self.cfg.get("source_dir") or os.path.expanduser("~")
        folder = filedialog.askdirectory(title="Choose the output folder", initialdir=start)
        if folder:
            self.out_dir.set(os.path.normpath(folder))

    def pick_date(self):
        DatePicker(self, parse_date(self.date.get()) or dt.date.today(),
                   lambda d: self.date.set(d.isoformat()), anchor=self.cal_btn)

    def open_out(self):
        # the folder just made, else the one the settings point to, else its parent
        for folder in (self.last_out, self.target_dir(), self.out_dir.get().strip()):
            if folder and os.path.isdir(folder):
                open_path(folder)
                return
        messagebox.showinfo(APP_NAME, "Choose where to save first.")

    def open_row(self, event):
        path = self.row_paths.get(self.tree.identify_row(event.y))
        if path and os.path.exists(path):
            open_path(path)

    # ---------------------------------------------------------- generate
    def validate(self):
        """Returns (source, date, target folder, move) or None after telling the user what's wrong."""
        src = self.source.get().strip()
        if not src:
            messagebox.showwarning(APP_NAME, "Please choose a poster image first.")
            return None
        if not os.path.isfile(src):
            messagebox.showerror(APP_NAME, f"The poster file doesn't exist:\n{src}")
            return None
        date = parse_date(self.date.get())
        if not date:
            messagebox.showwarning(APP_NAME, "The date must look like 2026-12-05 (YYYY-MM-DD).")
            self.date_entry.focus_set()
            return None
        if not self.out_dir.get().strip():
            messagebox.showwarning(APP_NAME, "Please choose where to save (the \"Save in\" folder).")
            return None
        problem = folder_name_problem(self.folder.get().strip())
        if problem:
            messagebox.showwarning(APP_NAME, problem + ".")
            return None
        out = self.target_dir()
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Can't create the output folder:\n{e}")
            return None
        names = list(self.output_names(src, date).values())
        if len(set(n.lower() for n in names)) < len(names):
            messagebox.showerror(APP_NAME, "Two of the files would get the same name. Change the file names "
                                           "in Output Sizes… so they're all different.")
            return None
        same = lambda a, b: os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        existing = [n for n in names if os.path.exists(os.path.join(out, n)) and not same(os.path.join(out, n), src)]
        if existing and not messagebox.askyesno(
                APP_NAME, f"These files already exist in\n{out}\nand will be replaced:\n\n"
                          + "\n".join(existing) + "\n\nContinue?"):
            return None
        return src, date, out, self.move_src.get()

    def mark_working(self, key):
        self.tree.set(key, "status", "Working…")
        self.tree.item(key, tags=("working",))
        self.set_status(f"Making {self.tree.set(key, 'file')}…")

    def start(self):
        if self.busy:
            return
        args = self.validate()
        if not args:
            return
        self.update_names()  # fresh table before the run
        self.busy = True
        self.started = time.perf_counter()
        self.go_btn.configure(state="disabled", text="Generating…")
        self.progress.configure(value=0, maximum=len(self.order))
        self.progress.pack(side="left", padx=(0, self.px(12)), before=self.status)
        for key in self.order:
            self.tree.set(key, "status", "Waiting")
            self.tree.item(key, tags=("pending",))
        self.mark_working(self.order[0])
        self.log(f"Generating variants into {args[2]} ...")
        # Heavy image work runs in a thread so the window stays responsive. The thread never
        # touches Tk itself (that isn't thread-safe); it posts messages that _poll() applies.
        self.events = queue.Queue()
        threading.Thread(target=self._worker, args=args + (copy.deepcopy(self.variants),), daemon=True).start()
        self.after(50, self._poll)

    def _worker(self, src, date, out, move, variants):
        q = self.events
        try:
            new_src = generate(src, date, out, variants, move_source=move,
                               log=lambda m: q.put(("log", m)), saved=lambda k, p: q.put(("saved", k, p)))
            q.put(("done", None, out, new_src))
        except Exception as e:
            q.put(("done", e, out, None))

    def _poll(self):
        """Apply the worker's messages on the Tk thread; keeps polling until it's done."""
        try:
            while True:
                kind, *data = self.events.get_nowait()
                if kind == "log":
                    self.log(data[0])
                elif kind == "saved":
                    key, path = data
                    self.row_paths[key] = path
                    self.tree.set(key, "status", f"✓  {size_text(path)}")
                    self.tree.item(key, tags=())
                    i = self.order.index(key) + 1
                    self.progress.configure(value=i)
                    if i < len(self.order):
                        self.mark_working(self.order[i])
                else:
                    self._finished(*data)
                    return
        except queue.Empty:
            pass
        self.after(50, self._poll)

    def _finished(self, error, out, new_src):
        self.go_btn.configure(state="normal", text="Generate")
        self.progress.pack_forget()
        if error:
            for key in self.order:
                if self.tree.set(key, "status") == "Working…":
                    self.tree.set(key, "status", "Failed")
                    self.tree.item(key, tags=("error",))
                elif self.tree.set(key, "status") == "Waiting":
                    self.tree.set(key, "status", "")
                    self.tree.item(key, tags=())
            self.busy = False
            self.set_status("Failed. See the Activity tab.", "Error.TLabel")
            self.tabs.select(self.act_tab)
            self.log(f"Failed: {error}", "error")
            messagebox.showerror(APP_NAME, f"Something went wrong:\n{error}")
            return
        self.last_out = out
        self.cfg["last_date"] = self.date.get().strip()
        if new_src and new_src != self.source.get().strip():
            # it was moved; keep pointing at it (still busy, so the table isn't reset)
            self.preview_path = new_src
            self.source.set(new_src)
        self.busy = False
        secs = time.perf_counter() - self.started
        self.set_status(f"Done in {secs:.1f} s  ·  double-click a file to open it", "Success.TLabel")
        self.log(f"Done - {len(self.order) - 1} variants and the original poster are in {out}", "ok")

    # ---------------------------------------------------------- about & updates
    def show_about(self):
        win = tk.Toplevel(self)
        win.withdraw()
        win.title(f"About {APP_NAME}")
        win.transient(self)
        win.resizable(False, False)
        P = self.px
        body = ttk.Frame(win, padding=P(20))
        body.pack()
        self._about_icon = ImageTk.PhotoImage(make_icon(P(56)))
        ttk.Label(body, image=self._about_icon).grid(row=0, column=0, rowspan=3, sticky="n", padx=(0, P(14)))
        ttk.Label(body, text=APP_NAME, font=("Segoe UI Semibold", 13)).grid(row=0, column=1, sticky="w")
        ttk.Label(body, text=f"Version {VERSION}  ·  CK Suite  ·  © CK Software  ·  MIT License",
                  style="Muted.TLabel").grid(row=1, column=1, sticky="w")
        link = ttk.Label(body, text=f"github.com/{REPO}", style="Link.TLabel", cursor="hand2")
        link.grid(row=2, column=1, sticky="w", pady=(P(4), 0))
        link.bind("<Button-1>", lambda e: webbrowser.open(f"https://github.com/{REPO}"))
        ttk.Separator(body).grid(row=3, column=0, columnspan=2, sticky="ew", pady=P(14))
        ttk.Checkbutton(body, text="Check for updates automatically (once a day)", variable=self.auto_update).grid(
            row=4, column=0, columnspan=2, sticky="w")
        self._about_status = ttk.Label(body, text="", style="Muted.TLabel")
        self._about_status.grid(row=5, column=0, columnspan=2, sticky="w", pady=(P(8), 0))
        bar = ttk.Frame(body)
        bar.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(P(14), 0))
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(bar, text="Check for Updates Now",
                   command=lambda: self.check_updates(quiet=False)).pack(side="right", padx=(0, P(8)))
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        win.geometry(f"+{self.winfo_rootx() + P(120)}+{self.winfo_rooty() + P(120)}")
        win.deiconify()
        win.grab_set()

    def about_status(self, text):
        lbl = getattr(self, "_about_status", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=text)

    def check_updates(self, quiet):
        """Ask GitHub for the newest release in a thread; show the footer link if it's newer."""
        self.about_status("Checking…")
        result = {}

        def work():
            try:
                result["rel"] = latest_release()
            except Exception as e:
                result["err"] = e

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def done():
            if t.is_alive():
                self.after(200, done)
                return
            self.cfg["last_update_check"] = time.time()
            rel = result.get("rel")
            if rel is None:
                self.about_status("Couldn't reach GitHub. Check your internet connection.")
                return
            if not rel:
                self.about_status(f"No releases published yet. You have version {VERSION}.")
                return
            if version_tuple(rel["version"]) > version_tuple(VERSION):
                self.update_info_ = rel
                self.update_lbl.configure(text=f"⬆ Version {rel['version']} is available")
                self.update_lbl.pack(side="right", padx=(0, self.px(16)))
                self.about_status(f"Version {rel['version']} is available (you have {VERSION}).")
                if not quiet:
                    self.offer_update()
            else:
                self.about_status(f"You have the latest version ({VERSION}).")

        done()

    def offer_update(self):
        rel = self.update_info_
        if not rel:
            return
        win = tk.Toplevel(self)
        win.withdraw()
        win.title("Update available")
        win.transient(self)
        P = self.px
        body = ttk.Frame(win, padding=P(18))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"{APP_NAME} {rel['version']} is available", font=("Segoe UI Semibold", 12)).pack(anchor="w")
        ttk.Label(body, text=f"You have version {VERSION}. What's new:", style="Muted.TLabel").pack(anchor="w", pady=(P(2), P(8)))
        notes = tk.Text(body, height=10, width=64, wrap="word", font=("Segoe UI", 10), relief="flat",
                        highlightthickness=1, highlightbackground="#d9d9d9", padx=P(8), pady=P(6))
        notes.insert("1.0", rel["notes"].strip() or "(no release notes)")
        notes.configure(state="disabled")
        notes.pack(fill="both", expand=True)
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(P(12), 0))
        frozen = getattr(sys, "frozen", False) and rel["url"]
        if frozen:
            ttk.Button(bar, text="Update Now", style="Go.TButton", default="active",
                       command=lambda: (win.destroy(), self.install_update(rel))).pack(side="right")
        else:  # running from source (or no installer attached): send them to the release page
            ttk.Button(bar, text="Open Download Page", style="Go.TButton", default="active",
                       command=lambda: (win.destroy(), webbrowser.open(rel["page"]))).pack(side="right")
        ttk.Button(bar, text="Later", command=win.destroy).pack(side="right", padx=(0, P(8)))
        win.update_idletasks()
        win.geometry(f"+{self.winfo_rootx() + P(100)}+{self.winfo_rooty() + P(100)}")
        win.deiconify()
        win.grab_set()

    def install_update(self, rel):
        """Download the new installer, run it quietly and close; the installer reopens the program."""
        if self.busy:
            messagebox.showinfo(APP_NAME, "Wait for the current job to finish, then update.")
            return
        self.set_status(f"Downloading version {rel['version']}…")
        self.progress.configure(value=0, maximum=1)
        self.progress.pack(side="left", padx=(0, self.px(12)), before=self.status)
        dest = os.path.join(tempfile.gettempdir(), "CK Suite updates", os.path.basename(rel["url"]))
        state = {"frac": 0.0}

        def work():
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                req = urllib.request.Request(rel["url"], headers={"User-Agent": "CK-Suite-Updater"})
                with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    got = 0
                    while chunk := r.read(256 * 1024):
                        f.write(chunk)
                        got += len(chunk)
                        state["frac"] = got / total if total else 0
                state["ok"] = True
            except Exception as e:
                state["err"] = e

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def watch():
            self.progress.configure(value=state["frac"])
            if t.is_alive():
                self.after(150, watch)
                return
            self.progress.pack_forget()
            if not state.get("ok"):
                self.set_status(f"Download failed: {state.get('err')}", "Error.TLabel")
                return
            try:
                subprocess.Popen([dest, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"])
            except OSError as e:
                self.set_status(f"Couldn't start the installer: {e}", "Error.TLabel")
                return
            self.on_close()

        watch()

    # ---------------------------------------------------------- config
    def load_config(self):
        if not os.path.exists(self.config_file):
            # settings used to live next to the program; bring them along once
            old = os.path.join(app_path(), "config.json")
            if os.path.isfile(old):
                try:
                    shutil.copy2(old, self.config_file)
                except OSError:
                    pass
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_config(self):
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2)
        except OSError:
            pass  # settings are a convenience; never block closing

    def on_close(self):
        self.cfg["output_dir"] = self.out_dir.get().strip()
        self.cfg["move_source"] = self.move_src.get()
        self.cfg["auto_update"] = self.auto_update.get()
        self.save_config()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
