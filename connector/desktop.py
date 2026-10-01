"""Windows connector app. The built EXE installs for the current user on first run."""

import asyncio
import filecmp
import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from queue import Empty, Queue
from tkinter import messagebox, ttk

from connector.agent import run, validate_server


APP_NAME = "Tally Connect Connector"
DEFAULT_SERVER = "https://talley.onrender.com"
CONFIG_PATH = Path.home() / ".tally-connect" / "connector.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def installed_executable():
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TallyConnect" / "TallyConnector.exe"


def set_autostart(enabled, executable=None):
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            command = '"' + str(executable or installed_executable()) + '" --background'
            winreg.SetValueEx(key, "TallyConnectConnector", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "TallyConnectConnector")
            except FileNotFoundError:
                pass


def install_current_user():
    """Copy the signed/packaged executable to a stable per-user location."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    target = installed_executable()
    if Path(sys.executable).resolve() == target.resolve():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or not filecmp.cmp(sys.executable, target, shallow=False):
        try:
            shutil.copy2(sys.executable, target)
        except PermissionError as exc:
            raise RuntimeError("Purana connector tray me chal raha hai. Tray icon se Exit karke installer dobara chalayein.") from exc
    set_autostart(True, target)
    subprocess.Popen([str(target)], close_fds=True)
    return True


def saved_settings():
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def icon_image():
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGBA", (64, 64), "#176746")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((1, 1, 62, 62), radius=16, fill="#b8edcb")
    try:
        font = ImageFont.truetype("arialbd.ttf", 42)
    except OSError:
        font = ImageFont.load_default()
    draw.text((17, 7), "T", fill="#12362b", font=font)
    return image


class ConnectorWindow:
    def __init__(self, background=False, autoconnect=True):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("520x405")
        self.root.minsize(480, 370)
        self.root.configure(bg="#f5f8f5")
        self.events = Queue()
        self.stop_event = None
        self.worker = None
        saved = saved_settings()
        self.server = tk.StringVar(value=saved.get("server", DEFAULT_SERVER))
        self.port = tk.StringVar(value=str(saved.get("tally_port", 9001)))
        self.code = tk.StringVar()
        self.status = tk.StringVar(value="Paired PC ready" if saved.get("token") else "Pair this PC with the website")
        self.autostart = tk.BooleanVar(value=True)
        self._build_ui()
        self._build_tray()
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.after(200, self._process_events)
        if saved.get("token") and autoconnect:
            self.start_session()
            if background:
                self.root.withdraw()

    def _build_ui(self):
        frame = tk.Frame(self.root, bg="#f5f8f5", padx=25, pady=22)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="Tally Connect", font=("Segoe UI", 20, "bold"), fg="#12362b", bg="#f5f8f5").pack(anchor="w")
        tk.Label(frame, text="Connect TallyPrime to your website securely.", font=("Segoe UI", 10),
                 fg="#577367", bg="#f5f8f5").pack(anchor="w", pady=(2, 15))
        for title, variable in (("Website URL", self.server), ("Tally port", self.port),
                                ("Pairing code from website (first time only)", self.code)):
            tk.Label(frame, text=title, font=("Segoe UI", 9, "bold"), fg="#274639", bg="#f5f8f5").pack(anchor="w", pady=(5, 3))
            ttk.Entry(frame, textvariable=variable).pack(fill="x")
        tk.Checkbutton(frame, text="Start automatically when I sign in to Windows", variable=self.autostart,
                       command=self._change_autostart, bg="#f5f8f5", fg="#274639").pack(anchor="w", pady=(12, 7))
        buttons = tk.Frame(frame, bg="#f5f8f5")
        buttons.pack(fill="x", pady=(4, 10))
        self.connect_button = ttk.Button(buttons, text="Connect", command=self.start_session)
        self.connect_button.pack(side="left")
        ttk.Button(buttons, text="Stop", command=self.stop_session).pack(side="left", padx=8)
        ttk.Button(buttons, text="Open website", command=self.open_website).pack(side="left")
        self.status_label = tk.Label(frame, textvariable=self.status, wraplength=450, justify="left",
                                     font=("Segoe UI", 10), fg="#20744a", bg="#f5f8f5")
        self.status_label.pack(anchor="w", pady=(7, 5))
        tk.Label(frame, text="Close window to keep connector in the system tray.",
                 font=("Segoe UI", 9), fg="#698177", bg="#f5f8f5").pack(anchor="w")

    def _build_tray(self):
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("Open connector", lambda _icon, _item: self.events.put(("show", None))),
            pystray.MenuItem("Exit connector", lambda _icon, _item: self.events.put(("exit", None))),
        )
        self.tray = pystray.Icon("TallyConnectConnector", icon_image(), APP_NAME, menu)
        self.tray.run_detached()

    def _change_autostart(self):
        try:
            set_autostart(self.autostart.get())
        except OSError as exc:
            messagebox.showerror(APP_NAME, "Windows startup setting save nahi hui: " + str(exc))

    def open_website(self):
        try:
            webbrowser.open(validate_server(self.server.get().strip()))
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def start_session(self):
        if self.worker and self.worker.is_alive():
            self.status.set("Connector already running. Port badalne ke liye pehle Stop dabayein.")
            return
        try:
            server = validate_server(self.server.get().strip())
            port = int(self.port.get())
            if not 1 <= port <= 65535:
                raise ValueError("Tally port 1 se 65535 ke beech hona chahiye.")
            code = self.code.get().strip()
            if not code and not saved_settings().get("token"):
                raise ValueError("Website se Pair new PC code lekar yahan paste karein.")
        except ValueError as exc:
            self.status.set(str(exc))
            self.status_label.configure(fg="#b24545")
            self.show()
            return
        self.status.set("Cloud se connect ho raha hai...")
        self.status_label.configure(fg="#20744a")
        self.connect_button.configure(state="disabled")
        self.stop_event = threading.Event()

        def work():
            try:
                asyncio.run(run(server, code, "localhost", port, str(CONFIG_PATH),
                                on_status=lambda state, detail: self.events.put((state, detail)),
                                stop_event=self.stop_event))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("finished", None))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop_session(self):
        if self.stop_event:
            self.stop_event.set()
            self.status.set("Connector stop ho raha hai...")

    def _process_events(self):
        try:
            while True:
                state, detail = self.events.get_nowait()
                if state == "show":
                    self.show()
                elif state == "exit":
                    self.exit()
                    return
                elif state == "finished":
                    self.connect_button.configure(state="normal")
                elif state in ("error", "warning"):
                    self.status.set(detail)
                    self.status_label.configure(fg="#b24545")
                    if state == "error":
                        self.show()
                elif state == "paired":
                    self.code.set("")
                    self.port.set(str(saved_settings().get("tally_port", self.port.get())))
                    self.status.set("Pairing complete. Cloud connection verify ho raha hai...")
                elif state in ("online", "job", "stopped"):
                    self.status.set(detail)
                    self.status_label.configure(fg="#20744a")
        except Empty:
            pass
        self.root.after(200, self._process_events)

    def hide(self):
        self.root.withdraw()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def exit(self):
        self.stop_session()
        self.tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    if os.name != "nt":
        raise RuntimeError("Desktop connector Windows ke liye hai.")
    if "--self-test" in sys.argv:
        import pystray  # Verify the bundled tray backend can be imported.

        validate_server(DEFAULT_SERVER)
        icon_image()
        return
    if "--ui-smoke-test" in sys.argv:
        window = ConnectorWindow(background=True, autoconnect=False)
        window.root.after(1200, window.exit)
        window.run()
        return
    import ctypes

    try:
        if install_current_user():
            return
    except (OSError, RuntimeError) as exc:
        messagebox.showerror(APP_NAME, str(exc))
        return
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\TallyConnectDesktop")
    if ctypes.windll.kernel32.GetLastError() == 183:
        messagebox.showinfo(APP_NAME, "Connector already running hai. System tray me Tally icon se kholein.")
        return
    try:
        ConnectorWindow(background="--background" in sys.argv).run()
    finally:
        ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
