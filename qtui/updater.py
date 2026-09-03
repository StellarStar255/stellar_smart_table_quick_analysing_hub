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
import ssl
import subprocess
import sys
import tempfile
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QMessageBox, QProgressDialog

from version import __version__, APP_NAME, GITHUB_REPO
from qtui.i18n import tr

API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_HEADERS = {"User-Agent": f"{APP_NAME.replace(' ', '')}/{__version__}",
            "Accept": "application/vnd.github+json"}

# PyInstaller 打包后的 Python 找不到系统 CA 证书（SSL: CERTIFICATE_VERIFY_FAILED），
# 显式使用 certifi 内置的 CA 证书包（PyInstaller 会将其打进应用）。
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


def _urlopen(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout, context=_SSL_CTX)


def _parse_version(text):
    """'v1.2.3' -> (1, 2, 3)；接受 2～4 段（'v1.5' -> (1, 5, 0)）；无法解析返回 None。"""
    m = re.match(r"v?(\d+(?:\.\d+){1,3})(?!\d)", str(text).strip())
    if not m:
        return None
    parts = [int(x) for x in m.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


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
            with _urlopen(req, timeout=15) as resp:
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
    """下载安装包；给了 release 时顺带在本线程完成 SHA256 校验（不卡 UI）。

    校验结果存于 checksum_status：
        "ok"          哈希一致
        "mismatch"    哈希不一致（传输损坏，上层重下）
        "absent"      Release 没有 SHA256SUMS.txt 或其中没有本文件的条目
        "unavailable" 取校验文件时网络失败（与 absent 区分：不能借此跳过校验）
        None          未要求校验
    """
    progress = pyqtSignal(int, int)  # received, total
    done = pyqtSignal(str)           # 本地文件路径
    failed = pyqtSignal(str)

    def __init__(self, url, dest, parent=None, release=None, asset_name=None):
        super().__init__(parent)
        self.url, self.dest = url, dest
        self._release = release
        self._asset_name = asset_name
        self._cancelled = False
        self.checksum_status = None
        self.checksum_error = ""

    def cancel(self):
        self._cancelled = True

    def _remove_partial(self):
        try:
            if os.path.exists(self.dest):
                os.unlink(self.dest)
        except OSError:
            pass

    def run(self):
        try:
            req = urllib.request.Request(self.url, headers=_HEADERS)
            with _urlopen(req, timeout=30) as resp, \
                    open(self.dest, "wb") as fh:
                total = int(resp.headers.get("Content-Length") or 0)
                received = 0
                while True:
                    if self._cancelled:
                        break
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
                    received += len(chunk)
                    self.progress.emit(received, total)
            if self._cancelled:
                self._remove_partial()   # 半截文件不留在临时目录
                return
            # 弱网下连接提前断开时 read() 只返回 EOF 不报错，
            # 按 Content-Length 预检出截断，让上层走自动重试
            if total and received < total:
                raise IOError(
                    tr("下载不完整（{}/{} 字节）").format(received, total))
            if self._release is not None:
                self._verify()
            self.done.emit(self.dest)
        except Exception as exc:
            self._remove_partial()
            self.failed.emit(str(exc))

    def _verify(self):
        status, expected = _fetch_checksum(self._release, self._asset_name)
        if status != "ok":
            self.checksum_status = status
            self.checksum_error = expected or ""
            return
        actual = _sha256(self.dest)
        self.checksum_status = "ok" if actual == expected else "mismatch"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_checksum(release, asset_name):
    """从 Release 的 SHA256SUMS.txt 中取出指定文件的哈希。

    返回 (状态, 值)：("ok", 哈希) / ("absent", None) 没有校验文件或没有该
    文件的条目 / ("unavailable", 错误信息) 网络失败——后者不能当作"没有
    校验文件"而放行安装。
    """
    for asset in release.get("assets", []):
        if asset["name"] == "SHA256SUMS.txt":
            try:
                req = urllib.request.Request(
                    asset["browser_download_url"], headers=_HEADERS)
                with _urlopen(req, timeout=15) as resp:
                    text = resp.read().decode("utf-8")
            except Exception as exc:
                return "unavailable", str(exc)
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[-1].lstrip("*") == asset_name:
                    return "ok", parts[0].lower()
            return "absent", None
    return "absent", None


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

    log_dir = os.path.expanduser("~/.smart_table_hub")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "update_error.log")
    script = os.path.join(tempfile.gettempdir(), "smart_table_hub_update.sh")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(f"""#!/bin/bash
# Smart Table Hub 自动升级脚本：先把新版复制到 TARGET.new，成功后再交换，
# 任何一步失败都保留旧版本并弹窗告知（日志见 {log_path}）。
DMG="{installer_path}"
TARGET="{app_bundle}"
LOG="{log_path}"
PID={os.getpid()}
exec >>"$LOG" 2>&1
echo "==== $(date) update start: $DMG -> $TARGET"
while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done

fail() {{
    echo "FAILED: $1"
    [ -n "$MOUNT" ] && hdiutil detach "$MOUNT" -quiet
    rm -rf "$TARGET.new"
    osascript -e "display alert \\"{APP_NAME}\\" message \\"升级失败：$1\\n详情见 $LOG\\n原版本未受影响。\\" as critical" || true
    [ -d "$TARGET" ] && open "$TARGET"
    rm -f "$0"
    exit 1
}}

MOUNT=$(hdiutil attach -nobrowse -readonly "$DMG" | tail -1 | awk -F'\\t' '{{print $NF}}')
[ -z "$MOUNT" ] && fail "无法挂载升级包"
NEW_APP=$(ls -d "$MOUNT"/*.app 2>/dev/null | head -1)
[ -z "$NEW_APP" ] && fail "升级包内没有应用"
rm -rf "$TARGET.new"
ditto "$NEW_APP" "$TARGET.new" || fail "复制新版本失败（磁盘空间或权限不足）"
xattr -dr com.apple.quarantine "$TARGET.new" 2>/dev/null
rm -rf "$TARGET.old"
if [ -d "$TARGET" ]; then
    mv "$TARGET" "$TARGET.old" || fail "无法移走旧版本（权限不足）"
fi
if ! mv "$TARGET.new" "$TARGET"; then
    [ -d "$TARGET.old" ] && mv "$TARGET.old" "$TARGET"
    fail "无法放置新版本"
fi
rm -rf "$TARGET.old"
hdiutil detach "$MOUNT" -quiet
rm -f "$DMG"
echo "OK"
open "$TARGET"
rm -f "$0"
""")
    os.chmod(script, 0o755)
    subprocess.Popen(["/bin/bash", script], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def _install_windows(installer_path):
    """静默运行 Inno Setup 安装器；安装器会关闭旧程序并在结束后重启。

    安装器退出后由一个后台批处理把安装包从临时目录删掉（安装器运行期间
    文件被占用删不掉，批处理每 10 秒重试，最多约 30 分钟后放弃）。
    """
    subprocess.Popen(
        [installer_path, "/SILENT", "/CLOSEAPPLICATIONS",
         "/RESTARTAPPLICATIONS", "/NORESTART"],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    try:
        cleaner = os.path.join(tempfile.gettempdir(), "smart_table_hub_cleanup.bat")
        with open(cleaner, "w", encoding="utf-8") as fh:
            fh.write(f"""@echo off
set /a tries=0
:retry
timeout /t 10 /nobreak >nul
del /q "{installer_path}" 2>nul
if not exist "{installer_path}" goto done
set /a tries+=1
if %tries% lss 180 goto retry
:done
del /q "%~f0"
""")
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        subprocess.Popen(["cmd", "/c", cleaner], creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
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
                    self.window, tr("检查更新"),
                    tr("当前已是最新版本（v{}）。").format(__version__)))
            self._checker.failed.connect(
                lambda msg: QMessageBox.warning(
                    self.window, tr("检查更新"),
                    tr("检查更新失败：\n{}").format(msg)))
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
            box.setWindowTitle(tr("发现新版本"))
            box.setText(tr("发现新版本 {}（当前 v{}）。").format(tag, __version__))
            box.setInformativeText(notes or "")
            go = box.addButton(tr("前往下载页面"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(tr("以后再说"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is go:
                QDesktopServices.openUrl(QUrl(release.get(
                    "html_url", f"https://github.com/{GITHUB_REPO}/releases")))
            return

        box = QMessageBox(self.window)
        box.setWindowTitle(tr("发现新版本"))
        box.setText(tr("发现新版本 {}（当前 v{}），是否立即升级？\n"
                       "升级包下载完成后将自动安装并重启应用。").format(tag, __version__))
        box.setInformativeText(notes or "")
        yes = box.addButton(tr("一键升级"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("以后再说"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            self._download_and_install(release, asset)

    # ---- 下载 + 安装 ----

    # 首次下载 + 自动重试 2 次（弱网下截断/损坏常见，重试通常即可恢复）
    _MAX_DOWNLOAD_ATTEMPTS = 3

    def _download_and_install(self, release, asset, attempt=1):
        dest = os.path.join(tempfile.gettempdir(), asset["name"])
        label = tr("正在下载 {} …").format(asset['name'])
        if attempt > 1:
            label = tr("正在重试下载（第 {}/{} 次）{} …").format(
                attempt, self._MAX_DOWNLOAD_ATTEMPTS, asset['name'])
        dialog = QProgressDialog(label, tr("取消"), 0, 100, self.window)
        dialog.setWindowTitle(tr("下载升级包"))
        dialog.setAutoClose(False)
        dialog.setMinimumDuration(0)

        self._downloader = Downloader(asset["browser_download_url"], dest,
                                      release=release, asset_name=asset["name"])
        self._downloader.progress.connect(
            lambda got, total: dialog.setValue(
                int(got * 100 / total) if total else 0))
        dialog.canceled.connect(self._downloader.cancel)
        self._downloader.done.connect(
            lambda path: (dialog.close(),
                          self._verify_and_install(release, asset, path, attempt)))
        self._downloader.failed.connect(
            lambda msg: (dialog.close(),
                         self._on_download_failed(release, asset, attempt, msg)))
        self._downloader.start()
        dialog.exec()

    def _on_download_failed(self, release, asset, attempt, msg):
        if attempt < self._MAX_DOWNLOAD_ATTEMPTS:
            self._download_and_install(release, asset, attempt + 1)
            return
        QMessageBox.warning(
            self.window, tr("下载失败"),
            tr("升级包下载失败（已自动重试 {} 次）：\n{}").format(
                self._MAX_DOWNLOAD_ATTEMPTS - 1, msg))

    def _verify_and_install(self, release, asset, path, attempt=1):
        # 校验已在下载线程完成（取校验文件 + 对几百 MB 做 SHA256 都不占 UI 线程）
        status = self._downloader.checksum_status if self._downloader else None
        if status == "mismatch":
            os.unlink(path)
            if attempt < self._MAX_DOWNLOAD_ATTEMPTS:
                # 校验失败大概率是传输损坏，自动重新下载
                self._download_and_install(release, asset, attempt + 1)
                return
            QMessageBox.critical(
                self.window, tr("校验失败"),
                tr("升级包 SHA256 校验失败（已自动重试 {} 次），已取消安装。\n"
                   "请前往官方 Release 页面手动下载。").format(
                    self._MAX_DOWNLOAD_ATTEMPTS - 1))
            return
        if status == "unavailable":
            # 网络取不到校验文件 ≠ 没有校验文件：不能放行未校验的安装包
            os.unlink(path)
            if attempt < self._MAX_DOWNLOAD_ATTEMPTS:
                self._download_and_install(release, asset, attempt + 1)
                return
            QMessageBox.critical(
                self.window, tr("无法校验"),
                tr("无法获取升级包的校验文件（网络错误），已取消安装：\n{}").format(
                    self._downloader.checksum_error if self._downloader else ""))
            return
        if status != "ok":
            box = QMessageBox(self.window)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(tr("无法校验"))
            box.setText(tr("该版本缺少 SHA256SUMS.txt，无法校验升级包完整性。\n"
                           "是否仍然继续安装？"))
            cont = box.addButton(tr("继续安装"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(tr("取消"), QMessageBox.ButtonRole.RejectRole)
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
