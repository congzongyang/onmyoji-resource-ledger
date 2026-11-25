"""Read the courtyard's resource details using the installed OAS OCR runtime."""

import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import cv2
import numpy as np
from ppocronnx.predict_system import TextSystem


ROOT = Path(__file__).resolve().parent
CALIBRATION = ROOT / "data" / "calibration"
ADB = sys.argv[1]
SERIAL = sys.argv[2]
RESOURCES = {
    "gold": ("金币", 574, (534, 16, 616, 48), 330),
    "jade": ("勾玉", 751, (727, 16, 790, 48), 506),
    "stamina": ("体力", 940, (908, 16, 971, 48), 697),
}
ANCHORS = [(626, 23, 650, 43), (801, 23, 824, 43),
           (981, 23, 1005, 43), (541, 649, 602, 684),
           (1005, 649, 1066, 684)]


class LastInput(ctypes.Structure):
    _fields_ = [("size", wintypes.UINT), ("tick", wintypes.DWORD)]


def last_input_tick():
    state = LastInput(ctypes.sizeof(LastInput), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(state)):
        return None
    return state.tick


def adb(*args):
    return subprocess.check_output(
        [ADB, "-s", SERIAL, *args], timeout=5,
        stdin=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def screenshot():
    data = adb("exec-out", "screencap", "-p")
    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if frame is None or frame.shape[:2] != (720, 1280):
        raise RuntimeError("需要 1280×720 的 MuMu 游戏画面")
    return frame


def crop(frame, box):
    x1, y1, x2, y2 = box
    return frame[y1:y2, x1:x2]


def courtyard(frame, references):
    for box, reference in zip(ANCHORS, references):
        actual = crop(frame, box)
        score = cv2.matchTemplate(actual, reference, cv2.TM_CCOEFF_NORMED)[0, 0]
        if score < 0.80 or cv2.absdiff(actual, reference).mean() > 25:
            return False
    return True


def read_line(ocr, frame, box):
    region = crop(frame, box)
    region = cv2.resize(region, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    word, confidence = ocr.ocr_single_line(region)
    return re.sub(r"\s+", "", word).replace("：", ":"), confidence


def details(ocr, frame, title, left):
    heading, confidence = read_line(ocr, frame, (left - 3, 130, left + 67, 163))
    if heading != title or confidence < 0.82:
        return False, None
    line, confidence = read_line(ocr, frame, (left - 3, 166, left + 163, 197))
    result = re.fullmatch(r"已拥有:(\d{1,12})#?", line)
    if not result or confidence < 0.90:
        return True, None
    return True, int(result[1])


def abbreviated_matches(display, value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([万亿]?)", display)
    if not match:
        return False
    number, unit = match.groups()
    if not unit:
        return number.isdigit() and int(number) == value
    scale = {"万": 10_000, "亿": 100_000_000}[unit]
    digits = len(number.partition(".")[2])
    step = scale // 10**digits
    floor = int(number.replace(".", "")) * step
    return floor - step // 2 <= value < floor + step


def scan(ocr, references):
    input_tick = last_input_tick()
    if input_tick is None:
        return {"status": "无法确认鼠标键盘状态，请重试"}
    frame = screenshot()
    if not courtyard(frame, references):
        return {"status": "请回到庭院，再点「记录」"}
    displays = {}
    exact = {}
    # If a detail was already open, use it and leave it open.
    for key, (title, _, top, left) in RESOURCES.items():
        shown, confidence = read_line(ocr, frame, top)
        if confidence >= 0.85 and re.fullmatch(r"\d+(?:\.\d+)?[万亿]?", shown):
            displays[key] = shown
        opened, value = details(ocr, frame, title, left)
        if opened:
            if value is not None and abbreviated_matches(displays.get(key, ""), value):
                exact[key] = value
            return {"status": "请关闭已打开的详情，再点「记录」", "exact": exact, "display": displays}

    status = "已读取庭院余额"
    for key, (title, x, _, left) in RESOURCES.items():
        shown = displays.get(key, "")
        if re.fullmatch(r"\d+", shown):
            exact[key] = int(shown)
            continue
        if not shown or key == "gold":
            continue
        if last_input_tick() != input_tick:
            status = "读取时检测到操作，请重试"
            break
        if not courtyard(frame, references):
            status = "庭院画面已变化，请重试"
            break
        adb("shell", "input", "tap", str(x), "32")
        time.sleep(0.28)
        opened_frame = screenshot()
        opened, value = details(ocr, opened_frame, title, left)
        if not opened or not courtyard(opened_frame, references):
            status = "资源详情未确认，请重试"
            break
        if value is not None and abbreviated_matches(shown, value):
            _, second = details(ocr, screenshot(), title, left)
            if value == second:
                exact[key] = value
        if last_input_tick() != input_tick:
            status = "读取时检测到操作，请关闭详情后重试"
            break
        adb("shell", "input", "tap", str(x), "32")
        time.sleep(0.22)
        frame = screenshot()
        if details(ocr, frame, title, left)[0]:
            status = "请关闭资源详情后重试"
            break
    if not exact and status == "已读取庭院余额":
        status = "未识别到完整余额，请重试"
    return {"status": status, "exact": exact, "display": displays}


def main():
    if len(sys.argv) > 3 and sys.argv[3] == "--calibrate":
        frame = screenshot()
        CALIBRATION.mkdir(parents=True, exist_ok=True)
        for index, box in enumerate(ANCHORS):
            cv2.imencode(".png", crop(frame, box))[1].tofile(str(CALIBRATION / f"anchor-{index}.png"))
        return
    files = [CALIBRATION / f"anchor-{index}.png" for index in range(len(ANCHORS))]
    if not all(path.is_file() for path in files):
        for command in sys.stdin:
            if command.strip() == "scan":
                print(json.dumps({"status": "请先运行 calibrate.cmd 完成庭院校准"}), flush=True)
        return
    references = [
        cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        for path in files
    ]
    ocr = TextSystem(use_angle_cls=False, ort_providers=["CPUExecutionProvider"])
    for command in sys.stdin:
        if command.strip() != "scan":
            continue
        try:
            result = scan(ocr, references)
        except (RuntimeError, subprocess.SubprocessError, OSError, cv2.error) as error:
            result = {"status": f"读取失败：{error}"}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
