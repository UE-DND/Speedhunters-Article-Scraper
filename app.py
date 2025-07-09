#!/usr/bin/env python3

import os
import sys
import logging
from pathlib import Path
from typing import Optional

from PyQt5 import QtCore, QtWidgets

from scraper import SpeedhuntersScraper


class ScraperWorker(QtCore.QThread):
    """在后台线程运行爬虫，防止阻塞界面。"""

    status_signal = QtCore.pyqtSignal(str)
    finished_signal = QtCore.pyqtSignal()
    log_signal = QtCore.pyqtSignal(str)
    error_signal = QtCore.pyqtSignal(str)  # 新增错误信号

    def __init__(
        self,
        output_dir: str,
        max_pages: Optional[int],
        concurrency: int,
        delay: float,
        resume: bool,
        dev_mode: bool,
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.output_dir = output_dir
        self.max_pages = max_pages
        self.concurrency = concurrency
        self.delay = delay
        self.resume = resume
        self.dev_mode = dev_mode
        self.scraper: Optional[SpeedhuntersScraper] = None

    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D401; Qt 线程入口
        try:
            # 配置日志处理器
            logger = logging.getLogger()
            logger.setLevel(logging.DEBUG if self.dev_mode else logging.INFO)
            
            # 确保移除所有已存在的处理器，防止重复
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)

            # 过滤掉 selenium 的冗长日志
            logging.getLogger("selenium").setLevel(logging.WARNING)
            logging.getLogger("urllib3").setLevel(logging.WARNING)

            class QtLogHandler(logging.Handler):
                def __init__(self, qt_signal):
                    super().__init__()
                    self.qt_signal = qt_signal

                def emit(self, record):  # type: ignore[override]
                    msg = self.format(record)
                    self.qt_signal.emit(msg)

            log_handler = QtLogHandler(self.log_signal)
            log_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            logger.addHandler(log_handler)

            self.scraper = SpeedhuntersScraper(
                output_dir=self.output_dir,
                max_pages=self.max_pages,
                concurrency=self.concurrency,
                delay=self.delay,
                resume=self.resume,
                headless=not self.dev_mode,  # 开发者模式下使用有头浏览器
                status_signal=self.status_signal,  # type: ignore[arg-type]
            )
            self.scraper.crawl()
            self.status_signal.emit("Completed! ✅")
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Error: {exc}"
            logging.error(error_msg)
            self.error_signal.emit(error_msg)
        finally:
            if logger.handlers:
                logger.removeHandler(log_handler)
            self.finished_signal.emit()


class MainWindow(QtWidgets.QWidget):
    """主窗口。"""

    def __init__(self) -> None:  # noqa: D401
        super().__init__()
        self.setWindowTitle("Speedhunters Article Scraper")
        self.resize(600, 520)
        self.setMinimumSize(500, 400)
        self._build_ui()

        self.worker: Optional[ScraperWorker] = None

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:  # noqa: D401
        # 使用 QVBoxLayout 作为主布局
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # 创建表单布局
        form_layout = QtWidgets.QFormLayout()
        form_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        main_layout.addLayout(form_layout)

        # 输出目录
        self.output_dir_edit = QtWidgets.QLineEdit()
        # 设置默认输出目录为项目根目录下的 "out" 文件夹
        default_output_dir = Path(__file__).resolve().parent / "out"
        self.output_dir_edit.setText(str(default_output_dir))
        browse_btn = QtWidgets.QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse)  # type: ignore[arg-type]

        dir_layout = QtWidgets.QHBoxLayout()
        dir_layout.addWidget(self.output_dir_edit)
        dir_layout.addWidget(browse_btn)
        form_layout.addRow("PDF Output Directory:", dir_layout)
        
        # 设置输出目录输入框可扩展
        self.output_dir_edit.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, 
            QtWidgets.QSizePolicy.Policy.Fixed
        )

        # 抓取页数（0 表示全部）
        self.pages_spin = QtWidgets.QSpinBox()
        self.pages_spin.setRange(0, 10000)
        self.pages_spin.setSpecialValueText("All")
        form_layout.addRow("Pages to Crawl:", self.pages_spin)

        # 并发线程数
        self.concurrent_spin = QtWidgets.QSpinBox()
        self.concurrent_spin.setRange(1, 32)
        self.concurrent_spin.setValue(4)
        form_layout.addRow("Concurrency:", self.concurrent_spin)

        # 请求延迟
        self.delay_spin = QtWidgets.QDoubleSpinBox()
        self.delay_spin.setRange(0, 10)
        self.delay_spin.setSingleStep(0.5)
        self.delay_spin.setValue(0.5)
        form_layout.addRow("Request Delay (s):", self.delay_spin)

        # 断点续传
        self.resume_check = QtWidgets.QCheckBox("Resume from last session")
        self.resume_check.setChecked(True)
        main_layout.addWidget(self.resume_check)

        # 日志输出区域
        self.log_edit = QtWidgets.QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setStyleSheet(
            """
            QPlainTextEdit {
                background-color: #2b2b2b;
                color: #f8f8f2;
                font-family: Consolas, Monaco, 'Andale Mono', 'Ubuntu Mono', monospace;
            }
            """
        )

        # 创建日志区域的容器和布局
        log_container = QtWidgets.QWidget()
        log_layout = QtWidgets.QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        
        # 创建日志头部布局（标题 + 复制按钮）
        log_header_layout = QtWidgets.QHBoxLayout()
        
        # 添加日志标题
        log_header = QtWidgets.QLabel("Logs")
        log_header.setStyleSheet("font-weight: bold;")
        log_header_layout.addWidget(log_header)
        
        log_header_layout.addStretch()
        
        # 添加复制按钮
        self.copy_log_btn = QtWidgets.QPushButton("Copy")
        self.copy_log_btn.clicked.connect(self._copy_logs)  # type: ignore[arg-type]
        log_header_layout.addWidget(self.copy_log_btn)
        
        log_layout.addLayout(log_header_layout)
        
        # 添加日志文本框
        log_layout.addWidget(self.log_edit)
        
        # 将日志容器添加到主布局
        main_layout.addWidget(log_container)
        
        self.log_container = log_container

        # 创建按钮
        self.start_btn = QtWidgets.QPushButton("Start")
        self.start_btn.setFixedHeight(36)
        self.start_btn.clicked.connect(self._on_start)  # type: ignore[arg-type]

        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setFixedHeight(36)
        self.stop_btn.clicked.connect(self._on_stop)  # type: ignore[arg-type]
        self.stop_btn.setEnabled(False)  # 默认禁用

        # 创建按钮布局
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.start_btn)
        button_layout.addWidget(self.stop_btn)
        button_layout.addStretch()
        main_layout.addLayout(button_layout)

        # 状态标签 - 改为文本框以支持换行和复制
        self.status_label = QtWidgets.QTextEdit()
        self.status_label.setReadOnly(True)
        self.status_label.setMaximumHeight(60)
        self._apply_status_style("Ready")
        main_layout.addWidget(self.status_label)
        
        # 添加弹性空间
        main_layout.addStretch()

        # 全局样式表（简单美化）
        self.setStyleSheet(
            """
            QWidget { font-size: 13px; }
            QPushButton {
                padding: 6px 14px;
                border-radius: 4px;
                background-color: #0078d7;
                color: #ffffff;
            }
            QPushButton:disabled {
                background-color: #b0b0b0;
                color: #f0f0f0;
            }
            QPushButton:hover:!disabled {
                background-color: #0063b1;
            }
            QLineEdit, QPlainTextEdit {
                padding: 4px;
                border: 1px solid #cccccc;
                border-radius: 3px;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #dcdcdc;
                border-radius: 6px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
            """
        )

    # ------------------------------------------------------------------
    def _on_browse(self) -> None:  # noqa: D401
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if directory:
            self.output_dir_edit.setText(directory)

    def _on_start(self) -> None:  # noqa: D401
        output_dir = self.output_dir_edit.text().strip()
        if not output_dir:
            QtWidgets.QMessageBox.warning(self, "Warning", "Please select PDF output directory first!")
            return

        # 解析页数（0 表示全部）
        pages_value = self.pages_spin.value()
        max_pages: Optional[int] = None if pages_value == 0 else pages_value

        concurrency = self.concurrent_spin.value()
        delay = self.delay_spin.value()
        resume = self.resume_check.isChecked()

        # 禁用按钮，更新状态
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._update_status("Crawling, please wait…")
        
        # 清空日志
        self.log_edit.clear()

        # 启动后台线程
        self.worker = ScraperWorker(
            output_dir=output_dir,
            max_pages=max_pages,
            concurrency=concurrency,
            delay=delay,
            resume=resume,
            dev_mode=True,  # 默认启用开发者模式
        )
        self.worker.status_signal.connect(self._update_status)  # type: ignore[arg-type]
        self.worker.finished_signal.connect(self._on_finished)  # type: ignore[arg-type]
        self.worker.log_signal.connect(self._append_log)  # type: ignore[arg-type]
        self.worker.error_signal.connect(self._handle_error)  # type: ignore[arg-type]
        self.worker.start()

    def _apply_status_style(self, msg: str) -> None:
        """根据消息内容应用不同颜色样式。"""
        # Bootstrap 风格配色
        if any(keyword in msg for keyword in ("完成", "Completed", "Finished", "✅")):
            bg, border, color = "#d4edda", "#c3e6cb", "#155724"
        elif "错误" in msg or "Error" in msg:
            bg, border, color = "#f8d7da", "#f5c6cb", "#721c24"
        elif any(k in msg for k in ("终止", "停止", "Stopped", "Stopping")):
            bg, border, color = "#fff3cd", "#ffeeba", "#856404"
        else:  # 进行中 / 默认
            bg, border, color = "#d1ecf1", "#bee5eb", "#0c5460"

        self.status_label.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 4px;
                color: {color};
                font-weight: bold;
                padding: 4px;
            }}
            """
        )
        self.status_label.setText(msg)

    def _update_status(self, msg: str) -> None:
        """更新状态文本框并应用样式。"""
        self._apply_status_style(msg)

    def _handle_error(self, error_msg: str) -> None:
        """处理错误信息并应用错误样式。"""
        self._apply_status_style(error_msg)

    def _on_stop(self) -> None:
        """终止抓取任务并直接退出进程。"""
        if self.worker and self.worker.scraper:
            self.worker.scraper.stop()
        os._exit(0)

    def _copy_logs(self) -> None:  # noqa: D401
        """复制日志内容到剪贴板，并提供临时状态提示。"""
        original_status = self.status_label.toPlainText()
        self.log_edit.selectAll()
        self.log_edit.copy()
        # 使用正确的方式取消选择
        cursor = self.log_edit.textCursor()
        cursor.clearSelection()
        self.log_edit.setTextCursor(cursor)
        self.status_label.setText("Logs copied to clipboard.")
        # 3秒后恢复原始状态
        QtCore.QTimer.singleShot(3000, lambda: self.status_label.setText(original_status))

    def _on_finished(self) -> None:  # noqa: D401
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        # 任务结束后，检查状态文本，如果不是错误或终止信息，则设为"就绪"
        current_status = self.status_label.toPlainText()
        if ("错误" not in current_status and "Error" not in current_status and
                "终止" not in current_status and "Stopped" not in current_status):
            self._apply_status_style("Ready")

    def _append_log(self, msg: str) -> None:  # noqa: D401
        self.log_edit.appendPlainText(msg)
        # 自动滚动到底部
        scrollbar = self.log_edit.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())


# ---------------------------------------------------------------------

def main() -> None:  # noqa: D401
    app = QtWidgets.QApplication(sys.argv)
    # 使用 Fusion 风格以获得一致的跨平台外观
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main() 