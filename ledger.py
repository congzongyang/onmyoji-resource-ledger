"""A small Windows window for locally recording observed balance changes."""

import csv
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CSV = DATA / "ledger.csv"
INITIAL_BALANCES = DATA / "initial-balances.json"
MU_MU_ADB = Path(os.environ.get("SystemDrive", "C:") + "/MuMuPlayer/nx_main/adb.exe")
ADB = os.environ.get("ONMYOJI_ADB") or shutil.which("adb") or str(MU_MU_ADB)
SERIAL = os.environ.get("ONMYOJI_SERIAL", "emulator-5554")
LABELS = {"gold": "金币", "jade": "勾玉", "stamina": "体力"}
FIELDS = ["时间", "资源", "变动", "之前余额", "之后余额", "备注"]


class Ledger:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.reading = False
        self.baseline = {}

        root.title("阴阳师资源流水")
        root.geometry("740x430")
        root.minsize(650, 360)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        body = ttk.Frame(root, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="阴阳师资源流水", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")

        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=12)
        self.record_button = ttk.Button(controls, text="记录", command=self.record)
        self.record_button.pack(side="left")
        ttk.Button(controls, text="打开数据文件夹", command=self.open_data).pack(side="left", padx=8)
        self.status = tk.StringVar(value="回到庭院后点击「记录」")
        ttk.Label(controls, textvariable=self.status).pack(side="right")

        balances = ttk.Frame(body)
        balances.pack(fill="x", pady=(0, 12))
        self.values = {}
        for key, label in LABELS.items():
            cell = ttk.LabelFrame(balances, text=label, padding=(12, 8))
            cell.pack(side="left", fill="x", expand=True, padx=(0, 8))
            self.values[key] = tk.StringVar(value="待读取")
            ttk.Label(cell, textvariable=self.values[key], font=("Microsoft YaHei UI", 14)).pack(anchor="w")

        self.table = ttk.Treeview(body, columns=FIELDS, show="headings", height=7)
        for field, width in zip(FIELDS, (153, 65, 80, 100, 100, 105)):
            self.table.heading(field, text=field)
            self.table.column(field, width=width)
        self.table.pack(fill="both", expand=True)
        ttk.Label(body, text="每次点击读取一次。按资源与上次保存的余额比较，有变化才新增流水。").pack(anchor="w", pady=(10, 0))
        self.initial_balances = {}
        if INITIAL_BALANCES.exists():
            try:
                saved = json.loads(INITIAL_BALANCES.read_text(encoding="utf-8"))
                self.initial_balances = {
                    key: value for key, value in saved.items()
                    if key in LABELS and type(value) is int and value >= 0
                }
            except (OSError, ValueError, AttributeError):
                self.status.set("无法读取首次余额基准")
        self.baseline.update(self.initial_balances)
        if CSV.exists():
            try:
                with CSV.open(encoding="utf-8-sig", newline="") as file:
                    rows = list(csv.DictReader(file))
                    keys_by_label = {label: key for key, label in LABELS.items()}
                    for row in rows:
                        key = keys_by_label.get(row.get("资源"))
                        balance = row.get("之后余额") or ""
                        if key is not None and balance.isdecimal():
                            self.baseline[key] = int(balance)
                    for row in rows[-100:]:
                        self.show_row([row[field] for field in FIELDS])
            except (OSError, KeyError, UnicodeError) as error:
                self.status.set(f"无法读取历史流水：{error}")
                self.record_button.state(["disabled"])
        for key, value in self.baseline.items():
            self.values[key].set(f"{value:,}")
        root.protocol("WM_DELETE_WINDOW", self.close)

    def open_data(self):
        DATA.mkdir(exist_ok=True)
        os.startfile(DATA)

    def record(self):
        if self.reading:
            return
        self.reading = True
        self.record_button.state(["disabled"])
        self.status.set("正在读取…")
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            if self.process is None or self.process.poll() is not None:
                self.process = subprocess.Popen(
                    [str(Path(sys.executable).with_name("python.exe")),
                     "-u", str(ROOT / "reader.py"), ADB, SERIAL],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
                )
            self.process.stdin.write("scan\n")
            self.process.stdin.flush()
            reply = self.process.stdout.readline()
            if not reply:
                raise OSError("读取器未启动，请检查依赖和庭院校准")
            result = json.loads(reply)
        except (OSError, ValueError) as error:
            result = {"status": str(error)}
        try:
            self.root.after(0, self.handle_scan, result)
        except (RuntimeError, tk.TclError):
            return

    def handle_scan(self, result):
        self.reading = False
        self.record_button.state(["!disabled"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = result["status"]
        exact = result.get("exact", {})
        added = 0
        established = False
        for key, shown in result.get("display", {}).items():
            if key not in exact and key not in self.baseline:
                self.values[key].set(f"{shown}（缩写）")
        for key, value in exact.items():
            before = self.baseline.get(key)
            if before is None:
                try:
                    self.save_initial_balance(key, value)
                except OSError:
                    status = "无法保存首次余额，请检查数据文件夹"
                    continue
                established = True
            elif before != value:
                row = [now, LABELS[key], f"{value - before:+d}", before, value, "期间净变化"]
                try:
                    self.append(row)
                except OSError:
                    status = "无法写入流水，请关闭占用 CSV 的程序"
                    continue
                self.show_row(row)
                added += 1
            self.baseline[key] = value
            self.values[key].set(f"{value:,}")
        if status == "已读取庭院余额":
            if added:
                status = f"已记录 {added} 条变化"
            elif established:
                status = "已建立余额基准"
            else:
                status = "已读取，余额无变化"
        self.status.set(status)

    def show_row(self, row):
        self.table.insert("", 0, values=row)
        items = self.table.get_children()
        if len(items) > 100:
            self.table.delete(items[-1])

    def save_initial_balance(self, key, value):
        DATA.mkdir(exist_ok=True)
        updated = {**self.initial_balances, key: value}
        temporary = INITIAL_BALANCES.with_suffix(".tmp")
        temporary.write_text(json.dumps(updated), encoding="utf-8")
        temporary.replace(INITIAL_BALANCES)
        self.initial_balances = updated

    def append(self, row):
        DATA.mkdir(exist_ok=True)
        add_header = not CSV.exists() or CSV.stat().st_size == 0
        with CSV.open("a", encoding="utf-8-sig" if add_header else "utf-8", newline="") as file:
            writer = csv.writer(file)
            if add_header:
                writer.writerow(FIELDS)
            writer.writerow(row)

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        self.root.destroy()


if __name__ == "__main__":
    if sys.argv[1:] == ["--calibrate"]:
        subprocess.run(
            [sys.executable, str(ROOT / "reader.py"), ADB, SERIAL, "--calibrate"],
            check=True,
        )
        print("庭院校准完成。")
        sys.exit()
    root = tk.Tk()
    Ledger(root)
    root.mainloop()
