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


class _FakeThread:
    """假的下载线程：只记录被要求 cancel/wait，不真的开线程。"""

    def __init__(self):
        self.running = True
        self.cancelled = False
        self.waited = False
        self.deleted = False
        self.checksum_status = None
        self.checksum_error = ""

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True

    def wait(self, ms=None):
        self.waited = True
        self.running = False
        return True

    def deleteLater(self):
        self.deleted = True


class TestDownloaderLifetime:
    """线程生命周期与取消语义。"""

    def _manager(self):
        mgr = updater.UpdateManager.__new__(updater.UpdateManager)
        mgr.window = None
        mgr._checker = None
        mgr._downloader = None
        mgr._pending = []
        mgr._user_cancelled = False
        return mgr

    def test_running_downloader_is_cancelled_and_waited(self):
        mgr = self._manager()
        old = _FakeThread()
        mgr._downloader = old
        mgr._stop_downloader()
        assert old.cancelled and old.waited
        assert mgr._downloader is None

    def test_retire_releases_only_after_finish(self):
        mgr = self._manager()
        dl = _FakeThread()
        mgr._downloader = dl
        mgr._pending.append(dl)
        mgr._retire_downloader(dl)
        assert mgr._pending == [] and dl.deleted and mgr._downloader is None

    def test_shutdown_waits_for_both_threads(self):
        mgr = self._manager()
        dl, chk = _FakeThread(), _FakeThread()
        mgr._downloader, mgr._checker = dl, chk
        mgr.shutdown()
        assert dl.cancelled and dl.waited and chk.waited
        assert mgr._downloader is None and mgr._checker is None


class TestCancelStopsRetries:
    """点了取消就必须彻底停下——这是最招人烦的老毛病。"""

    def _manager(self, rounds):
        """rounds: 每轮 _download_once 的返回值列表。"""
        mgr = updater.UpdateManager.__new__(updater.UpdateManager)
        mgr.window = None
        mgr._checker = None
        mgr._downloader = None
        mgr._pending = []
        mgr._user_cancelled = False
        mgr.attempts = []

        def fake_once(release, asset, attempt):
            mgr.attempts.append(attempt)
            return rounds[attempt - 1]
        mgr._download_once = fake_once
        mgr._install = lambda path: mgr.attempts.append("install")
        return mgr

    def test_cancel_during_download_stops_immediately(self):
        dl = _FakeThread()

        def cancelled_round(*_):
            return (None, None, dl)
        mgr = self._manager([(None, None, dl)] * 3)
        original = mgr._download_once

        def once(release, asset, attempt):
            mgr._user_cancelled = True      # 用户在这一轮点了取消
            return original(release, asset, attempt)
        mgr._download_once = once
        mgr._download_and_install({}, {"name": "pkg"})
        assert mgr.attempts == [1]          # 只跑了一轮，没有重试

    def test_failure_retries_up_to_the_limit(self, monkeypatch):
        warned = []
        monkeypatch.setattr(updater.QMessageBox, "warning",
                            staticmethod(lambda *a, **k: warned.append(a[2])))
        dl = _FakeThread()
        mgr = self._manager([(None, "网络错误", dl)] * 3)
        mgr._download_and_install({}, {"name": "pkg"})
        assert mgr.attempts == [1, 2, 3]    # 重试到上限后才提示
        assert warned and "网络" in warned[0]

    def test_cancel_on_the_retry_round_stops_there(self):
        dl = _FakeThread()
        mgr = self._manager([(None, "网络错误", dl), (None, None, dl),
                             (None, "网络错误", dl)])
        original = mgr._download_once

        def once(release, asset, attempt):
            if attempt == 2:
                mgr._user_cancelled = True   # 第二轮用户点了取消
            return original(release, asset, attempt)
        mgr._download_once = once
        mgr._download_and_install({}, {"name": "pkg"})
        assert mgr.attempts == [1, 2]        # 第三轮不该再来

    def test_cancel_flag_set_by_dialog_cancel(self):
        mgr = self._manager([])
        dl = _FakeThread()
        mgr._cancel_download(dl)
        assert mgr._user_cancelled and dl.cancelled

    def test_unexpected_error_does_not_escape(self, monkeypatch):
        """升级流程里的异常必须被兜住：PyQt 里槽抛异常会 qFatal 掉整个进程。"""
        reported = []
        mgr = self._manager([])
        mgr._download_once = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        mgr._report_unexpected = reported.append
        mgr._download_and_install({}, {"name": "pkg"})
        assert reported and isinstance(reported[0], RuntimeError)

    def test_quiet_unlink_tolerates_missing_file(self, tmp_path):
        updater.UpdateManager._quiet_unlink(str(tmp_path / "nope"))   # 不抛异常


class TestCancelledDownloadDoesNotReportFailure:
    def test_cancelled_read_error_is_not_a_failure(self, monkeypatch, tmp_path):
        """取消后连接报错不能算下载失败，否则上层还会重试。"""
        class Boom:
            headers = {"Content-Length": "100"}

            def read(self, n=None):
                raise IOError("connection reset")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(updater, "_urlopen", lambda req, timeout: Boom())
        d = updater.Downloader("http://x/pkg", str(tmp_path / "pkg"))
        d.cancel()
        failed = []
        d.failed.connect(failed.append)
        d.run()
        assert failed == []


class _FakeProgressDialog:
    """假进度框：exec() 立刻返回，不进事件循环。"""

    def __init__(self, *a, **k):
        self.closed = False
        self._cancel_cb = []

        class _Sig:
            def __init__(self, cbs):
                self._cbs = cbs

            def connect(self, cb):
                self._cbs.append(cb)
        self.canceled = _Sig(self._cancel_cb)

    def setWindowTitle(self, *a): pass
    def setAutoClose(self, *a): pass
    def setMinimumDuration(self, *a): pass
    def setValue(self, *a): pass
    def close(self): self.closed = True
    def wasCanceled(self): return True      # close() 之后 Qt 也会置真
    def exec(self): return 0


class TestCancelDetection:
    """用户是否取消，只看"这一轮既没结果也没报错"。

    canceled 信号和 wasCanceled() 都会被我们自己的 dialog.close() 带上，
    拿它们判断会把"下载完成/失败"误判成用户取消。
    """

    def _manager(self, monkeypatch, emit):
        """emit: 拿到 downloader 后要触发的信号（'done'/'failed'/None）。"""
        monkeypatch.setattr(updater, "QProgressDialog", _FakeProgressDialog)

        class FakeDownloader:
            def __init__(self, *a, **k):
                self.checksum_status = "ok"
                self.cancelled = False
                self._slots = {}

            def _sig(self, name):
                class _S:
                    def __init__(s, owner, n): s.owner, s.n = owner, n
                    def connect(s, cb): s.owner._slots.setdefault(s.n, []).append(cb)
                return _S(self, name)

            def __getattr__(self, name):
                if name in ("progress", "done", "failed", "finished"):
                    return self._sig(name)
                raise AttributeError(name)

            def cancel(self): self.cancelled = True
            def isRunning(self): return False
            def wait(self, ms=None): return True
            def deleteLater(self): pass

            def start(self):
                for cb in self._slots.get(emit, []):
                    cb("/tmp/pkg.dmg" if emit == "done" else "网络错误")

        monkeypatch.setattr(updater, "Downloader", FakeDownloader)
        mgr = updater.UpdateManager.__new__(updater.UpdateManager)
        mgr.window = None
        mgr._checker = None
        mgr._downloader = None
        mgr._pending = []
        mgr._user_cancelled = False
        return mgr

    def _asset(self):
        return {"name": "pkg.dmg", "browser_download_url": "http://x/pkg.dmg"}

    def test_completed_download_is_not_treated_as_cancel(self, monkeypatch):
        mgr = self._manager(monkeypatch, "done")
        path, err, _ = mgr._download_once({}, self._asset(), 1)
        assert path == "/tmp/pkg.dmg" and err is None
        assert mgr._user_cancelled is False       # 关键：不能误判成取消

    def test_failed_download_is_not_treated_as_cancel(self, monkeypatch):
        mgr = self._manager(monkeypatch, "failed")
        path, err, _ = mgr._download_once({}, self._asset(), 1)
        assert path is None and err == "网络错误"
        assert mgr._user_cancelled is False       # 否则自动重试就没了

    def test_no_outcome_means_user_cancelled(self, monkeypatch):
        mgr = self._manager(monkeypatch, None)    # 既不 done 也不 failed
        path, err, dl = mgr._download_once({}, self._asset(), 1)
        assert path is None and err is None
        assert mgr._user_cancelled is True and dl.cancelled
