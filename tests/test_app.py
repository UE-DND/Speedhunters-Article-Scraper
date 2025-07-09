import pytest
from PyQt5 import QtCore
from app import MainWindow

# 标记所有测试都需要 qtbot fixture
pytestmark = pytest.mark.qt

def test_initial_ui_state(qtbot):
    """测试主窗口的初始 UI 状态。"""
    window = MainWindow()
    qtbot.addWidget(window)  # 将 widget 注册到 qtbot

    assert window.start_btn.isEnabled()
    assert not window.stop_btn.isEnabled()
    assert "out" in window.output_dir_edit.text()
    assert window.status_label.toPlainText() == "Ready"
    assert window.windowTitle() == "Speedhunters Article Scraper & PDF Converter"

def test_start_button_behavior(qtbot, mocker):
    """测试点击“开始”按钮后的 UI 行为和 worker 的启动。"""
    window = MainWindow()
    qtbot.addWidget(window)

    # 模拟 ScraperWorker 类，以防它真的运行
    mock_worker_class = mocker.patch("app.ScraperWorker")

    # 模拟用户点击
    qtbot.mouseClick(window.start_btn, QtCore.Qt.LeftButton)

    # 检查 UI 状态变化
    assert not window.start_btn.isEnabled()
    assert window.stop_btn.isEnabled()
    assert window.status_label.toPlainText() == "Crawling, please wait…"

    # 验证 worker 是否被正确地实例化和启动
    mock_worker_class.assert_called_once()
    instance = mock_worker_class.return_value
    instance.start.assert_called_once() 