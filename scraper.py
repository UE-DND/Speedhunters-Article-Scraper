import os
import json
import re
import time
import base64
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from typing import Optional, cast

import logging
from PyQt5 import QtCore # noqa
from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class SpeedhuntersScraper:
    """Speedhunters 文章抓取与 PDF 转换核心类。"""

    BASE_URL = "https://www.speedhunters.com/category/content/"
    # 页面上包含 12 篇文章的列表区域 XPath（作为定位文章链接的锚点）
    ARTICLE_CONTAINER_XPATH = "/html/body/div[4]/section/div/section/div[1]/ul"
    # 文章正文根节点 XPath，用于等待页面加载完成
    ARTICLE_ROOT_XPATH = "/html/body/div[4]"

    def __init__(
        self,
        output_dir: str,
        max_pages: Optional[int] = None,
        resume: bool = True,
        concurrency: int = 4,
        delay: float = 1.0,
        *,
        headless: bool = True,
        status_signal: Optional[QtCore.pyqtSignal] = None,
    ) -> None:
        """初始化爬虫。

        参数说明：
            output_dir: PDF 输出目录
            max_pages: 最大抓取页数；None 表示抓取所有页
            resume: 是否从上次进度继续
            concurrency: 并发线程数（PDF 转换阶段）
            delay: 每次翻页后的固定延时，给页面留出加载时间
            headless: 是否使用无头模式
        """
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.max_pages = max_pages
        self.resume = resume
        self.concurrency = concurrency
        self.delay = delay
        self.headless = headless
        self.status_signal = status_signal
        self._stop_event = threading.Event()
        self._active_drivers: list[webdriver.Edge] = []
        self._driver_lock = threading.Lock()

        self.progress_file = os.path.join(self.output_dir, "progress.json")
        self.progress = {"completed_page_for_collection": 0, "visited_urls": set()}
        self._load_progress()

        self._driver = self._create_webdriver()

    # -------------------------------------------------------------------------
    # 进度管理
    # -------------------------------------------------------------------------
    def _load_progress(self) -> None:
        if self.resume and os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # 兼容新旧两种格式
                    if isinstance(data, dict):
                        self.progress["visited_urls"] = set(data.get("visited_urls", []))
                        self.progress["completed_page_for_collection"] = data.get(
                            "completed_page_for_collection", 0
                        )
                    elif isinstance(data, list):
                        self.progress["visited_urls"] = set(data)
                    logging.info(
                        "成功加载进度：已完成 %s 页的链接收集，已下载 %s 篇文章。",
                        self.progress["completed_page_for_collection"],
                        len(self.progress["visited_urls"]),
                    )
            except Exception as e:
                logging.warning(
                    "进度文件 %s 加载失败或格式错误，将从头开始: %s", self.progress_file, e
                )
        # 如果加载失败或不续传，则使用默认值
        if "completed_page_for_collection" not in self.progress:
            self.progress["completed_page_for_collection"] = 0
        if "visited_urls" not in self.progress:
            self.progress["visited_urls"] = set()

    def _save_progress(self) -> None:
        # 为了在 set 和 list 之间转换，创建一个可序列化的副本
        serializable_progress = {
            "completed_page_for_collection": self.progress[
                "completed_page_for_collection"
            ],
            "visited_urls": list(self.progress["visited_urls"]),
        }
        with open(self.progress_file, "w", encoding="utf-8") as f:
            json.dump(serializable_progress, f, ensure_ascii=False, indent=2)

    def _emit_status(self, msg: str) -> None:
        """安全地发送状态信号。"""
        if self.status_signal:
            self.status_signal.emit(msg)  # type: ignore[attr-defined]

    # -------------------------------------------------------------------------
    # WebDriver 初始化
    # -------------------------------------------------------------------------
    def _create_webdriver(self) -> webdriver.Edge:
        options = EdgeOptions()
        if self.headless:
            # 仅在需要无头模式时添加参数
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1080")
        return webdriver.Edge(options=options)

    def stop(self) -> None:
        """设置停止事件，并强制关闭所有正在运行的 WebDriver 实例。"""
        self._stop_event.set()

        # 强制关闭所有由工作线程创建的浏览器
        with self._driver_lock:
            if self._active_drivers:
                logging.info("收到终止信号，正在强制关闭 %s 个工作浏览器...", len(self._active_drivers))
                # 创建副本以安全迭代
                for driver in list(self._active_drivers):
                    try:
                        driver.quit()
                    except Exception:
                        pass  # 忽略关闭期间的错误
                self._active_drivers.clear()

        # 关闭主 WebDriver 实例
        try:
            self._driver.quit()
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # 公共 API
    # -------------------------------------------------------------------------
    def crawl(self) -> None:
        """启动整体抓取流程，改为逐页处理模式。"""
        try:
            start_page = self.progress["completed_page_for_collection"] + 1
            if self.resume and start_page > 1:
                self._emit_status(f"Resuming from page {start_page} based on progress...")
                logging.info(
                    "根据进度，跳过 %s 页，从第 %s 页开始。", start_page - 1, start_page
                )

            page_idx = start_page - 1
            while True:
                if self._stop_event.is_set():
                    logging.info("任务被用户终止。")
                    break

                page_idx += 1

                if self.max_pages and page_idx > self.max_pages:
                    logging.info("已达到或超过最大页数 %s，停止收集。", self.max_pages)
                    break

                # 1. 收集单页链接
                links_for_page = self._collect_links_for_page(page_idx)
                if links_for_page is None:  # 到达末页的信号
                    break

                # 2. 下载该页文章
                if links_for_page:
                    all_succeeded = self._download_all(links_for_page)
                    if not all_succeeded:
                        logging.warning(
                            "第 %s 页的文章未全部下载成功，将不会标记为完成。下次将从此页重试。", page_idx
                        )
                        break  # 中断以避免后续页码错误

                # 3. 标记该页完成
                self.progress["completed_page_for_collection"] = page_idx
                self._save_progress()
                logging.info("已完成第 %s 页的收集与下载。", page_idx)

        finally:
            self._driver.quit()

    # -------------------------------------------------------------------------
    # 步骤 1：为单个页面收集链接
    # -------------------------------------------------------------------------
    def _collect_links_for_page(self, page_idx: int) -> Optional[list[str]]:
        """收集指定页码的文章链接，返回链接列表；如果页面不存在则返回 None。"""
        current_url = (
            self.BASE_URL if page_idx == 1 else f"{self.BASE_URL}page/{page_idx}/"
        )
        self._emit_status(f"Parsing list page {page_idx}...")
        logging.info("正在解析第 %s 页: %s", page_idx, current_url)
        self._driver.get(current_url)

        try:
            WebDriverWait(self._driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, self.ARTICLE_CONTAINER_XPATH)
                )
            )
        except Exception:
            logging.info("在第 %s 页未找到文章列表，已到达最后一页。", page_idx)
            return None  # 到达末页

        anchor_xpath = f"{self.ARTICLE_CONTAINER_XPATH}/li/article/div/h2/a"
        anchors = self._driver.find_elements(By.XPATH, anchor_xpath)
        page_links = [
            cast(str, a.get_attribute("href"))
            for a in anchors
            if a.get_attribute("href")
        ]

        if not page_links and page_idx > 1:
            logging.info("第 %s 页没有找到任何文章链接，可能为末页。", page_idx)
            return None

        # 筛选出尚未访问过的新链接
        new_links = [
            link
            for link in page_links
            if link not in self.progress["visited_urls"]
        ]
        logging.info("第 %s 页找到 %s 篇待下载文章。", page_idx, len(new_links))
        return new_links

    # -------------------------------------------------------------------------
    # 步骤 2：并发下载并生成 PDF
    # -------------------------------------------------------------------------
    def _download_all(self, article_urls: list[str]) -> bool:
        """并发下载所有文章，生成 PDF。返回 True 表示全部成功，False 表示有失败。"""
        if not article_urls:
            return True  # 没有需要下载的文章，视为成功

        total_count = len(article_urls)
        self._emit_status(f"Preparing to download {total_count} articles...")
        logging.info(
            "共找到 %s 篇新文章，开始使用 %s 个线程下载...", total_count, self.concurrency
        )

        completed_count = 0
        failure_count = 0
        progress_lock = threading.Lock()

        # 手动创建和管理线程池，而不是使用 with 语句
        executor = ThreadPoolExecutor(max_workers=self.concurrency)
        try:
            future_to_url = {
                executor.submit(self._download_single, url): url
                for url in article_urls
            }

            for future in as_completed(future_to_url):
                if self._stop_event.is_set():
                    logging.info("任务被用户终止，正在取消所有未完成的任务...")
                    # 取消所有未完成的任务
                    for f in future_to_url:
                        if not f.done():
                            f.cancel()
                    return False  # 有任务未完成

                url = future_to_url[future]
                completed_count += 1

                try:
                    future.result()  # 检查线程中是否有异常抛出
                    with progress_lock:
                        self.progress["visited_urls"].add(url)
                        self._save_progress()
                        self._emit_status(f"Download progress: [{completed_count}/{total_count}]")
                        logging.info(
                            "[%s/%s] Completed: %s", completed_count, total_count, url
                        )
                except Exception as exc:
                    failure_count += 1
                    with progress_lock:
                        self._emit_status(f"Download progress: [{completed_count}/{total_count}]")
                    logging.error(
                        "[%s/%s] Failed: %s -> %s",
                        completed_count,
                        total_count,
                        url,
                        exc,
                    )
        finally:
            # 如果是终止状态，则不等待任务完成就关闭线程池
            executor.shutdown(wait=not self._stop_event.is_set())

        return failure_count == 0

    # -------------------------------------------------------------------------
    # 每篇文章处理流程
    # -------------------------------------------------------------------------
    def _download_single(self, url: str) -> None:
        """下载单篇文章并保存为 PDF（在独立线程中使用独立的 WebDriver）。"""
        if self._stop_event.is_set():
            # 如果任务开始前就已经收到停止信号，则直接返回，不执行任何操作。
            return

        driver = self._create_webdriver()
        with self._driver_lock:
            self._active_drivers.append(driver)

        logging.info("开始处理文章: %s", url)
        try:
            driver.get(url)
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, self.ARTICLE_ROOT_XPATH))
            )
            time.sleep(self.delay)

            # 提取标题（用于文件名）
            title = self._extract_title(driver)
            filename = self._sanitize_filename(title) + ".pdf"
            filepath = os.path.join(self.output_dir, filename)
            logging.debug("文章标题: %s", title)

            # 使用浏览器打印功能生成 PDF
            logging.debug("准备使用浏览器打印功能生成 PDF: %s", filepath)
            print_options = {"printBackground": True}
            pdf_data = driver.execute_cdp_cmd("Page.printToPDF", print_options)
            with open(filepath, "wb") as f:
                f.write(base64.b64decode(pdf_data["data"]))
            logging.info("成功生成 PDF: %s", filepath)
        except Exception:
            logging.error("处理文章 %s 时发生错误", url, exc_info=True)
            # 重新抛出异常，以便在 _download_all 中捕获和记录
            raise
        finally:
            driver.quit()
            with self._driver_lock:
                try:
                    self._active_drivers.remove(driver)
                except ValueError:
                    # Driver 可能已被 stop() 方法强制关闭并从列表中移除，
                    # 因此这里的 remove 可能会失败。
                    pass

    # -------------------------------------------------------------------------
    # 工具函数
    # -------------------------------------------------------------------------
    def _extract_title(self, driver: webdriver.Edge) -> str:
        """尝试提取文章标题。"""
        try:
            h1 = driver.find_element(By.TAG_NAME, "h1")
            title = h1.text.strip()
            if title:
                return title
        except Exception:
            pass
        # 兜底：使用 URL 片段
        return urlparse(driver.current_url).path.strip("/").replace("/", "_")

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """清理非法文件名字符。"""
        return re.sub(r"[\\/:*?\"<>|]", "_", name)[:200]  # 限制最大长度 