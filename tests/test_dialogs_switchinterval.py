"""sys.setswitchinterval 是进程全局的：多个后台任务重叠时必须能还原到最初的值。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PyQt6.QtCore import QCoreApplication

_app = QCoreApplication.instance() or QCoreApplication([])

from qtui import dialogs


@pytest.fixture(autouse=True)
def restore_interval():
    orig = sys.getswitchinterval()
    yield
    sys.setswitchinterval(orig)


def test_nested_fast_switch_restores_original_once_all_exit():
    orig = sys.getswitchinterval()
    assert orig != dialogs._FAST_SWITCH_INTERVAL
    dialogs._enter_fast_switch()
    assert sys.getswitchinterval() == pytest.approx(dialogs._FAST_SWITCH_INTERVAL)
    dialogs._enter_fast_switch()                  # 第二个任务重叠进来
    dialogs._exit_fast_switch()                   # 第一个先结束：另一个还在跑，保持快切换
    assert sys.getswitchinterval() == pytest.approx(dialogs._FAST_SWITCH_INTERVAL)
    dialogs._exit_fast_switch()
    assert sys.getswitchinterval() == pytest.approx(orig)   # 而不是 0.001


def test_exit_without_enter_is_noop():
    orig = sys.getswitchinterval()
    dialogs._exit_fast_switch()
    assert sys.getswitchinterval() == pytest.approx(orig)


def test_worker_run_restores_interval_even_on_error():
    orig = sys.getswitchinterval()
    failed = []
    w = dialogs._Worker(lambda: 1 / 0)
    w.failed.connect(failed.append)
    w.run()
    assert failed and sys.getswitchinterval() == pytest.approx(orig)
