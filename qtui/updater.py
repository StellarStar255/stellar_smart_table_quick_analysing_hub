#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用内一键升级。

流程：查询 GitHub Releases 最新版本 → 下载当前平台安装包 →
用发布附带的 SHA256SUMS.txt 校验完整性 → 静默安装并重启应用。

打包后的应用（PyInstaller frozen）走全自动升级；
源码运行时只提示新版本并引导到 Release 页面。
"""

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QMessageBox, QProgressDialog

from version import __version__, APP_NAME, GITHUB_REPO

API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_HEADERS = {"User-Agent": f"{APP_NAME.replace(' ', '')}/{__version__}",
            "Accept": "application/vnd.github+json"}


def _parse_version(text):
    """'v1.2.3' -> (1, 2, 3)；无法解析返回 None。"""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", str(text).strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _platform_asset_pattern():
    """返回当前平台安装包文件名的匹配正则。"""
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        arch = "arm64" if machine == "arm64" else "x86_64"
        return re.compile(rf"macos-{arch}.*\.dmg$")
    if sys.platform.startswith("win"):
        return re.compile(r"windows.*setup\.exe$")
    return re.compile(r"linux.*\.deb$")


class UpdateChecker(QThread):
    """后台查询最新 Release，避免阻塞 UI。"""

    finished_ok = pyqtSignal(dict)   # release JSON
    finished_none = pyqtSignal()     # 已是最新
    failed = pyqtSignal(str)

    def run(self):
        try:
            req = urllib.request.Request(API_LATEST, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                release = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # 网络失败不打扰用户
            self.failed.emit(str(exc))
            return
        latest = _parse_version(release.get("tag_name", ""))
        current = _parse_version(__version__)
        if latest and current and latest > current:
            self.finished_ok.emit(release)
        else:
            self.finished_none.emit()


class Downloader(QThread):
    progress = pyqtSignal(int, int)  # received, total
    done = pyqtSignal(str)           # 本地文件路径
    failed = pyqtSignal(str)

    def __init__(self, url, dest, parent=None):
        super().__init__(parent)
        self.url, self.dest = url, dest
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            req = urllib.request.Request(self.url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp, \
                    open(self.dest, "wb") as fh:
                total = int(resp.headers.get("Content-Length") or 0)
                received = 0
                while True:
                    if self._cancelled:
                        return
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
                    received += len(chunk)
                    self.progress.emit(received, total)
            self.done.emit(self.dest)
        except Exception as exc:
            self.failed.emit(str(exc))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_checksum(release, asset_name):
    """从 Release 的 SHA256SUMS.txt 中取出指定文件的哈希；取不到返回 None。"""
    for asset in release.get("assets", []):
        if asset["name"] == "SHA256SUMS.txt":
            try:
                req = urllib.request.Request(
                    asset["browser_download_url"], headers=_HEADERS)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    for line in resp.read().decode("utf-8").splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[-1].lstrip("*") == asset_name:
                            return parts[0].lower()
            except Exception:
                return None
    return None


# ================= 安装（分平台） =================

def _install_macos(installer_path):
    """挂载 dmg，用辅助脚本在应用退出后替换 .app 并重启。"""
    app_bundle = None
    if getattr(sys, "frozen", False):
        # .../SmartTableHub.app/Contents/MacOS/SmartTableHub
        candidate = os.path.abspath(
            os.path.join(os.path.dirname(sys.executable), "..", ".."))
        if candidate.endswith(".app"):
            app_bundle = candidate
    if not app_bundle:
        subprocess.Popen(["open", installer_path])
        return True

    script = os.path.join(tempfile.gettempdir(), "smart_table_hub_update.sh")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(f"""#!/bin/bash
# Smart Table Hub 自动升级脚本
DMG="{installer_path}"
TARGET="{app_bundle}"
PID={os.getpid()}
while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done
MOUNT=$(hdiutil attach -nobrowse -readonly "$DMG" | tail -1 | awk -F'\\t' '{{print $NF}}')
[ -z "$MOUNT" ] && exit 1
NEW_APP=$(ls -d "$MOUNT"/*.app 2>/dev/null | head -1)
if [ -n "$NEW_APP" ]; then
    rm -rf "$TARGET"
    ditto "$NEW_APP" "$TARGET"
    xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null
fi
hdiutil detach "$MOUNT" -quiet
rm -f "$DMG"
open "$TARGET"
rm -f "$0"
""")
    os.chmod(script, 0o755)
    subprocess.Popen(["/bin/bash", script],
                     start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def _install_windows(installer_path):
    """静默运行 Inno Setup 安装器；安装器会关闭旧程序并在结束后重启。"""
    subprocess.Popen(
        [installer_path, "/SILENT", "/CLOSEAPPLICATIONS",
         "/RESTARTAPPLICATIONS", "/NORESTART"],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return True


def _install_linux(installer_path):
    """通过 pkexec 提权安装 deb，然后重启应用。"""
    exe = sys.executable if getattr(sys, "frozen", False) else ""
    script = os.path.join(tempfile.gettempdir(), "smart_table_hub_update.sh")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(f"""#!/bin/bash
DEB="{installer_path}"
PID={os.getpid()}
while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done
pkexec sh -c "dpkg -i '$DEB' || apt-get -f install -y"
rm -f "$DEB"
EXE="{exe}"
[ -n "$EXE" ] && [ -x "$EXE" ] && "$EXE" &
rm -f "$0"
""")
    os.chmod(script, 0o755)
    subprocess.Popen(["/bin/bash", script], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


# ================= 对外入口 =================

class UpdateManager:
    """挂在 MainWindow 上，管理检查/下载/安装的整个生命周期。"""

    def __init__(self, parent_window):
        self.window = parent_window
        self._checker = None
        self._downloader = None

    # ---- 检查 ----

    def check(self, silent=False):
        """silent=True 时（启动自检）仅在有新版本时弹窗。"""
        if self._checker and self._checker.isRunning():
            return
        self._checker = UpdateChecker()
        self._checker.finished_ok.connect(
            lambda release: self._on_update_found(release))
        if not silent:
            self._checker.finished_none.connect(
                lambda: QMessageBox.information(
                    self.window, "检查更新",
                    f"当前已是最新版本（v{__version__}）。"))
            self._checker.failed.connect(
                lambda msg: QMessageBox.warning(
                    self.window, "检查更新", f"检查更新失败：\n{msg}"))
        self._checker.start()

    def _on_update_found(self, release):
        tag = release.get("tag_name", "")
        notes = (release.get("body") or "").strip()
        if len(notes) > 600:
            notes = notes[:600] + "…"
        pattern = _platform_asset_pattern()
        asset = next((a for a in release.get("assets", [])
                      if pattern.search(a["name"])), None)

        if not getattr(sys, "frozen", False) or asset is None:
            # 源码运行 / 未找到本平台安装包：引导到 Release 页面
            box = QMessageBox(self.window)
            box.setWindowTitle("发现新版本")
            box.setText(f"发现新版本 {tag}（当前 v{__version__}）。")
            box.setInformativeText(notes or "")
            go = box.addButton("前往下载页面", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("以后再说", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is go:
                QDesktopServices.openUrl(QUrl(release.get(
                    "html_url", f"https://github.com/{GITHUB_REPO}/releases")))
            return

        box = QMessageBox(self.window)
        box.setWindowTitle("发现新版本")
        box.setText(f"发现新版本 {tag}（当前 v{__version__}），是否立即升级？\n"
                    "升级包下载完成后将自动安装并重启应用。")
        box.setInformativeText(notes or "")
        yes = box.addButton("一键升级", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("以后再说", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            self._download_and_install(release, asset)

    # ---- 下载 + 安装 ----

    def _download_and_install(self, release, asset):
        dest = os.path.join(tempfile.gettempdir(), asset["name"])
        dialog = QProgressDialog(
            f"正在下载 {asset['name']} …", "取消", 0, 100, self.window)
        dialog.setWindowTitle("下载升级包")
        dialog.setAutoClose(False)
        dialog.setMinimumDuration(0)

        self._downloader = Downloader(asset["browser_download_url"], dest)
        self._downloader.progress.connect(
            lambda got, total: dialog.setValue(
                int(got * 100 / total) if total else 0))
        dialog.canceled.connect(self._downloader.cancel)
        self._downloader.done.connect(
            lambda path: (dialog.close(),
                          self._verify_and_install(release, asset, path)))
        self._downloader.failed.connect(
            lambda msg: (dialog.close(), QMessageBox.warning(
                self.window, "下载失败", f"升级包下载失败：\n{msg}")))
        self._downloader.start()
        dialog.exec()

    def _verify_and_install(self, release, asset, path):
        expected = _fetch_checksum(release, asset["name"])
        if expected:
            actual = _sha256(path)
            if actual != expected:
                QMessageBox.critical(
                    self.window, "校验失败",
                    "升级包 SHA256 校验失败，已取消安装。\n"
                    "请前往官方 Release 页面手动下载。")
                os.unlink(path)
                return
        else:
            box = QMessageBox(self.window)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("无法校验")
            box.setText("该版本缺少 SHA256SUMS.txt，无法校验升级包完整性。\n"
                        "是否仍然继续安装？")
            cont = box.addButton("继续安装", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not cont:
                os.unlink(path)
                return

        if sys.platform == "darwin":
            ok = _install_macos(path)
        elif sys.platform.startswith("win"):
            ok = _install_windows(path)
        else:
            ok = _install_linux(path)
        if ok:
            self.window.close()
