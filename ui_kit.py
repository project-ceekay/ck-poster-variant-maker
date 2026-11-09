"""CK Poster Variant Maker — Shared UI Kit and System Utilities."""
import ctypes
from ctypes import wintypes
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from PIL import Image, ImageDraw, ImageTk

_R = getattr(Image, "Resampling", Image)
LANCZOS = _R.LANCZOS
RESAMPLE = _R.LANCZOS
BICUBIC = _R.BICUBIC

REPO = "project-ceekay/ck-poster-variant-maker"

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


def make_toolbar_icon(kind, size=18, dark=False):
    """Draw a crisp procedural icon for the toolbar (supersampled 2x then scaled down)."""
    s = size * 2 / 24
    im = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ink = "#e8eaed" if dark else "#1a1a1a"
    acc = "#4cc2ff" if dark else ACCENT
    card = "#2d2d30" if dark else "#ffffff"

    if kind == "browse":
        # Folder open icon
        d.polygon([(3 * s, 6 * s), (9 * s, 6 * s), (11 * s, 9 * s), (21 * s, 9 * s),
                   (21 * s, 19 * s), (3 * s, 19 * s)], fill="#f0b429")
        d.polygon([(1 * s, 10 * s), (19 * s, 10 * s), (17 * s, 20 * s), (1 * s, 20 * s)],
                  fill="#f7c948")
    elif kind == "open_folder":
        # Folder with arrow exiting
        d.polygon([(3 * s, 6 * s), (9 * s, 6 * s), (11 * s, 9 * s), (21 * s, 9 * s),
                   (21 * s, 19 * s), (3 * s, 19 * s)], fill="#f0b429")
        d.polygon([(1 * s, 10 * s), (19 * s, 10 * s), (17 * s, 20 * s), (1 * s, 20 * s)],
                  fill="#f7c948")
        w = max(2, round(2.0 * s))
        d.line([(15 * s, 14 * s), (22 * s, 7 * s)], fill=acc, width=w)
        d.polygon([(22 * s, 7 * s), (18 * s, 7 * s), (22 * s, 11 * s)], fill=acc)
    elif kind == "calendar":
        # Calendar page with grid
        d.rounded_rectangle([3 * s, 5 * s, 21 * s, 21 * s], radius=2 * s, fill=card,
                            outline=ink, width=max(1, round(1.2 * s)))
        d.rectangle([3 * s, 5 * s, 21 * s, 9 * s], fill=ACCENT)
        d.line([(7 * s, 3 * s), (7 * s, 6 * s)], fill=ink, width=max(1, round(1.5 * s)))
        d.line([(17 * s, 3 * s), (17 * s, 6 * s)], fill=ink, width=max(1, round(1.5 * s)))
        # dots/grid
        for r in (12, 16):
            for c in (7, 12, 17):
                d.rectangle([(c - 1) * s, (r - 1) * s, (c + 1) * s, (r + 1) * s], fill=ink)
    elif kind == "sizes":
        # Sliders / settings gears
        w = max(2, round(1.8 * s))
        d.line([(4 * s, 8 * s), (20 * s, 8 * s)], fill=ink, width=w)
        d.line([(4 * s, 16 * s), (20 * s, 16 * s)], fill=ink, width=w)
        d.rectangle([8 * s, 5.5 * s, 12 * s, 10.5 * s], fill=acc)
        d.rectangle([14 * s, 13.5 * s, 18 * s, 18.5 * s], fill=acc)
    elif kind == "generate":
        # Play / run arrow in green
        c = "#4cc355" if dark else SUCCESS
        d.polygon([(7 * s, 4 * s), (19 * s, 12 * s), (7 * s, 20 * s)], fill=c)
    elif kind == "clear":
        # Trash / clear
        w = max(2, round(1.8 * s))
        d.line([(5 * s, 7 * s), (19 * s, 7 * s)], fill=ink, width=w)
        d.line([(9 * s, 5 * s), (15 * s, 5 * s)], fill=ink, width=w)
        d.polygon([(6 * s, 7 * s), (18 * s, 7 * s), (16 * s, 20 * s), (8 * s, 20 * s)],
                  outline=ink, fill=card, width=max(1, round(1.2 * s)))
        d.line([(10 * s, 10 * s), (10 * s, 17 * s)], fill=ink, width=w)
        d.line([(14 * s, 10 * s), (14 * s, 17 * s)], fill=ink, width=w)
    elif kind == "theme":
        # Moon crescent
        d.ellipse([4.5 * s, 4.5 * s, 19.5 * s, 19.5 * s], fill="#fbbc04" if dark else "#5f6368")
        d.ellipse([9.0 * s, 2.5 * s, 22.0 * s, 15.5 * s], fill=(0, 0, 0, 0))

    return ImageTk.PhotoImage(im.resize((size, size), LANCZOS))


def attach_tooltip(widget, text, status_fn=None):
    """Native-style hover tooltip below a toolbar button."""
    tip = {"win": None, "job": None}

    def hide():
        if tip["job"]:
            widget.after_cancel(tip["job"])
            tip["job"] = None
        if tip["win"] is not None:
            try:
                tip["win"].destroy()
            except tk.TclError:
                pass
            tip["win"] = None

    def show():
        tip["job"] = None
        if not widget.winfo_exists():
            return
        w = tk.Toplevel(widget)
        w.withdraw()
        w.wm_overrideredirect(True)
        bg = "#2d2d30" if DARK_MODE else "#ffffe1"
        fg = "#e8eaed" if DARK_MODE else "#1a1a1a"
        lbl = tk.Label(w, text=text, bg=bg, fg=fg, font=("Segoe UI", 9),
                       relief="solid", bd=1, padx=4, pady=2)
        lbl.pack()
        w.update_idletasks()
        x = widget.winfo_rootx()
        y = widget.winfo_rooty() + widget.winfo_height() + 3
        w.geometry(f"+{x}+{y}")
        w.deiconify()
        w.attributes("-topmost", True)
        w.lift()
        tip["win"] = w

    def enter(e=None):
        hide()
        if status_fn:
            status_fn(text)
        tip["job"] = widget.after(150, show)

    def leave(e=None):
        hide()
        if status_fn:
            status_fn("")

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")
    widget.bind("<ButtonPress>", lambda e: hide(), add="+")


def set_window_dark_titlebar(win, dark):
    """Enable or disable Windows 10/11 native dark title bar on a Tk or Toplevel window."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        try:
            uxtheme = ctypes.windll.uxtheme
            uxtheme[135](2 if dark else 3)
            uxtheme[136]()
        except Exception:
            pass
        val = ctypes.c_int(1 if dark else 0)
        hwnds = set()
        try:
            frame_id = win.wm_frame()
            if frame_id and frame_id != "0x0":
                hwnds.add(int(frame_id, 16))
        except Exception:
            pass
        hwnd = win.winfo_id()
        hwnds.add(hwnd)
        parent = ctypes.windll.user32.GetParent(hwnd)
        if parent:
            hwnds.add(parent)
        for h in hwnds:
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(h, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def apply_theme(root, dark=False):
    """Switch the entire window between Light Mode (Windows vista) and Dark Mode (clam dark)."""
    global DARK_MODE
    DARK_MODE = bool(dark)
    style = ttk.Style(root)
    P = root.px if hasattr(root, "px") else lambda n: n
    if dark:
        BG = "#1e1e1e"
        SURFACE = "#252526"
        FIELD = "#141414"
        HEAD = "#2d2d30"
        FG = "#e8eaed"
        MUTED_FG = "#9aa0a6"
        BD = "#3c4043"
        ACC = "#4cc2ff"
        SEL = "#094771"
        if "clam" in style.theme_names():
            style.theme_use("clam")
        root.configure(bg=BG)
        style.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                        troughcolor=SURFACE, bordercolor=BD, darkcolor=BG, lightcolor=SURFACE,
                        selectbackground=SEL, selectforeground="#ffffff", insertcolor=FG)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED_FG)
        style.configure("Error.TLabel", background=BG, foreground="#ff6b6b")
        style.configure("Success.TLabel", background=BG, foreground="#4cc355")
        style.configure("Link.TLabel", background=BG, foreground=ACC)
        style.configure("TSeparator", background=BD)
        style.configure("TLabelframe", background=BG, bordercolor=BD)
        style.configure("TLabelframe.Label", background=BG, foreground=FG)
        style.configure("Section.TLabelframe", background=BG, bordercolor=BD)
        style.configure("Section.TLabelframe.Label", background=BG, foreground=ACC, font=("Segoe UI Semibold", 10))
        style.configure("TButton", background=HEAD, foreground=FG, bordercolor=BD, focuscolor=ACC, padding=(P(8), P(3)))
        style.map("TButton",
                  background=[("disabled", "#222222"), ("pressed", "#005a9e"), ("active", "#38383c")],
                  foreground=[("disabled", "#666666"), ("pressed", "#ffffff")])
        style.configure("Go.TButton", background=ACCENT, foreground="#ffffff", bordercolor="#005a9e",
                        font=("Segoe UI Semibold", 10), padding=(P(18), P(5)))
        style.map("Go.TButton",
                  background=[("disabled", "#2a2a2a"), ("pressed", "#004c87"), ("active", "#1077d0")],
                  foreground=[("disabled", "#666666"), ("!disabled", "#ffffff")])
        style.configure("Footer.TButton", background=HEAD, foreground=FG, bordercolor=BD, padding=(P(12), P(5)))
        style.map("Footer.TButton",
                  background=[("disabled", "#222222"), ("pressed", "#005a9e"), ("active", "#38383c")],
                  foreground=[("disabled", "#666666")])
        style.configure("TCheckbutton", background=BG, foreground=FG, indicatorbackground=FIELD,
                        indicatorforeground=ACC, upperbordercolor=BD, lowerbordercolor=BD)
        style.map("TCheckbutton",
                  background=[("active", BG)],
                  foreground=[("disabled", "#666666")],
                  indicatorbackground=[("selected", ACCENT), ("disabled", "#222222")],
                  indicatorforeground=[("selected", "#ffffff")])
        style.configure("TRadiobutton", background=BG, foreground=FG, indicatorbackground=FIELD,
                        indicatorforeground=ACC, upperbordercolor=BD, lowerbordercolor=BD)
        style.map("TRadiobutton",
                  background=[("active", BG)],
                  foreground=[("disabled", "#666666")],
                  indicatorbackground=[("selected", ACCENT), ("disabled", "#222222")])
        style.configure("TEntry", fieldbackground=FIELD, foreground=FG, bordercolor=BD,
                        lightcolor=BD, darkcolor=BD, insertcolor=FG)
        style.configure("TSpinbox", fieldbackground=FIELD, background=HEAD, foreground=FG,
                        arrowcolor=FG, bordercolor=BD, lightcolor=BD, darkcolor=BD, insertcolor=FG)
        style.configure("TNotebook", background=BG, bordercolor=BD, tabmargins=(0, P(2), 0, 0))
        style.configure("TNotebook.Tab", background=SURFACE, foreground=MUTED_FG,
                        bordercolor=BD, lightcolor=SURFACE, padding=(P(10), P(4)))
        style.map("TNotebook.Tab",
                  background=[("selected", HEAD), ("active", "#323236")],
                  foreground=[("selected", FG), ("active", FG)])
        style.configure("TScrollbar", background=HEAD, troughcolor=FIELD, bordercolor=FIELD,
                        arrowcolor=MUTED_FG, darkcolor=HEAD, lightcolor=HEAD)
        style.map("TScrollbar", background=[("active", "#454549"), ("pressed", "#55555a")])
        style.configure("TProgressbar", background=ACCENT, troughcolor=SURFACE, bordercolor=BD)
        style.configure("Treeview", background=FIELD, foreground=FG, fieldbackground=FIELD, bordercolor=BD)
        style.map("Treeview", background=[("selected", SEL)], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background=HEAD, foreground=FG, bordercolor=BD,
                        font=("Segoe UI Semibold", 10))
        style.map("Treeview.Heading", background=[("active", "#38383c")])
    else:
        if "vista" in style.theme_names():
            style.theme_use("vista")
        bg = style.lookup("TFrame", "background") or "#f0f0f0"
        root.configure(bg=bg)
        style.configure("Go.TButton", font=("Segoe UI Semibold", 10), padding=(P(18), P(5)))
        style.configure("Footer.TButton", padding=(P(12), P(5)))
        style.configure("Section.TLabelframe.Label", font=("Segoe UI Semibold", 10), foreground=ACCENT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Error.TLabel", foreground=ERROR)
        style.configure("Success.TLabel", foreground=SUCCESS)
        style.configure("Link.TLabel", foreground=ACCENT)
        style.configure("TNotebook.Tab", padding=(P(10), P(4)))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))

    set_window_dark_titlebar(root, dark)


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


