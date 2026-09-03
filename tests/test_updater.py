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


class TestVersionParsing:
    def test_two_and_four_components(self):
        assert updater._parse_version("v1.5") == (1, 5, 0)
        assert updater._parse_version("1.4.12") == (1, 4, 12)
        assert updater._parse_version("v2.0.1.3") == (2, 0, 1, 3)
        assert updater._parse_version("v1.4.9") < updater._parse_version("v1.4.10")
        assert updater._parse_version("v1") is None
        assert updater._parse_version("latest") is None


class _FakeTextResponse:
    def __init__(self, text):
        self._data = text.encode("utf-8")

    def read(self, n=None):
        d, self._data = self._data, b""
        return d

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestChecksumStatus:
    _release = {"assets": [{"name": "SHA256SUMS.txt",
                            "browser_download_url": "http://example/SHA256SUMS.txt"}]}

    def test_absent_when_no_checksum_asset(self):
        assert updater._fetch_checksum({"assets": []}, "pkg.dmg") == ("absent", None)

    def test_absent_when_entry_missing(self, monkeypatch):
        monkeypatch.setattr(updater, "_urlopen",
                            lambda req, timeout: _FakeTextResponse("abc  other.dmg\n"))
        assert updater._fetch_checksum(self._release, "pkg.dmg") == ("absent", None)

    def test_ok_entry(self, monkeypatch):
        monkeypatch.setattr(updater, "_urlopen",
                            lambda req, timeout: _FakeTextResponse("ABCD *pkg.dmg\n"))
        assert updater._fetch_checksum(self._release, "pkg.dmg") == ("ok", "abcd")

    def test_network_failure_is_unavailable_not_absent(self, monkeypatch):
        def boom(req, timeout):
            raise OSError("no network")
        monkeypatch.setattr(updater, "_urlopen", boom)
        status, detail = updater._fetch_checksum(self._release, "pkg.dmg")
        assert status == "unavailable" and "no network" in detail


class TestDownloaderCleanup:
    def test_truncated_download_removes_partial_file(self, monkeypatch, tmp_path):
        done, failed = _run_downloader(monkeypatch, tmp_path, [b'x' * 50], 100)
        assert failed and not os.path.exists(str(tmp_path / "pkg.dmg"))

    def test_cancel_removes_partial_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            updater, "_urlopen",
            lambda req, timeout: _FakeResponse([b'x' * 50, b'y' * 50], 100))
        d = updater.Downloader("http://example/pkg.dmg", str(tmp_path / "pkg.dmg"))
        d.progress.connect(lambda got, total: d.cancel())   # 收到第一块后取消
        done, failed = [], []
        d.done.connect(done.append)
        d.failed.connect(failed.append)
        d.run()
        assert not done and not failed
        assert not os.path.exists(str(tmp_path / "pkg.dmg"))

    def test_verify_in_thread_reports_mismatch(self, monkeypatch, tmp_path):
        release = {"assets": [{"name": "SHA256SUMS.txt",
                               "browser_download_url": "http://example/sums"}]}

        def fake_open(req, timeout):
            if req.full_url.endswith("sums"):
                return _FakeTextResponse("0000 pkg.dmg\n")
            return _FakeResponse([b'x' * 10], 10)
        monkeypatch.setattr(updater, "_urlopen", fake_open)
        d = updater.Downloader("http://example/pkg.dmg", str(tmp_path / "pkg.dmg"),
                               release=release, asset_name="pkg.dmg")
        done = []
        d.done.connect(done.append)
        d.run()
        assert done and d.checksum_status == "mismatch"
