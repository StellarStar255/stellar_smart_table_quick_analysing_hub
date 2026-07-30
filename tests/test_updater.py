"""升级器下载完整性测试

Downloader 直接调 run()（不开线程），用假响应模拟网络。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QCoreApplication

_app = QCoreApplication.instance() or QCoreApplication([])

from qtui import updater


class _FakeResponse:
    """按 chunks 顺序返回数据，之后 EOF；模拟指定 Content-Length。"""

    def __init__(self, chunks, content_length):
        self._chunks = list(chunks)
        self.headers = {"Content-Length": str(content_length)}

    def read(self, n=None):
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _run_downloader(monkeypatch, tmp_path, chunks, content_length):
    monkeypatch.setattr(
        updater, "_urlopen",
        lambda req, timeout: _FakeResponse(chunks, content_length))
    d = updater.Downloader("http://example/pkg.dmg", str(tmp_path / "pkg.dmg"))
    done, failed = [], []
    d.done.connect(done.append)
    d.failed.connect(failed.append)
    d.run()
    return done, failed


class TestDownloaderIntegrity:
    def test_complete_download_succeeds(self, monkeypatch, tmp_path):
        done, failed = _run_downloader(
            monkeypatch, tmp_path, [b'x' * 50, b'y' * 50], 100)
        assert done and not failed

    def test_truncated_download_fails(self, monkeypatch, tmp_path):
        # 连接提前断开：只收到 50/100 字节，必须报失败（触发上层自动重试）
        done, failed = _run_downloader(
            monkeypatch, tmp_path, [b'x' * 50], 100)
        assert failed and not done
        assert '50' in failed[0] and '100' in failed[0]

    def test_no_content_length_still_succeeds(self, monkeypatch, tmp_path):
        done, failed = _run_downloader(monkeypatch, tmp_path, [b'x' * 10], 0)
        assert done and not failed


class TestRetryPolicy:
    def test_max_attempts_constant(self):
        # 首次 + 2 次自动重试
        assert updater.UpdateManager._MAX_DOWNLOAD_ATTEMPTS == 3
