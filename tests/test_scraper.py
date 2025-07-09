import json
import pytest
from scraper import SpeedhuntersScraper

# 使用 @pytest.mark.parametrize 装饰器，可以方便地测试多种输入情况
@pytest.mark.parametrize(
    "input_name, expected_name",
    [
        ("A simple title", "A simple title"),
        ("Title with /\\:*?\"<>| characters", "Title with _________ characters"),
        ("  Leading and trailing spaces  ", "  Leading and trailing spaces  "),
        ("", ""),
        ("A very long title" * 10, ("A very long title" * 10)[:200]),
    ],
)
def test_sanitize_filename(input_name, expected_name):
    """
    Tests that illegal characters are removed from filenames and length is constrained.
    """
    sanitized = SpeedhuntersScraper._sanitize_filename(input_name)
    assert sanitized == expected_name


def test_progress_saving_and_loading(tmp_path, mocker):
    """
    Tests that progress is correctly saved to and loaded from a JSON file.
    """
    # 阻止测试时真的创建 webdriver
    mocker.patch.object(SpeedhuntersScraper, "_create_webdriver", return_value=None)

    output_dir = tmp_path
    progress_file = output_dir / "progress.json"

    # 1. 创建 scraper，保存进度
    scraper1 = SpeedhuntersScraper(output_dir=str(output_dir), resume=True)
    scraper1.progress = {
        "completed_page_for_collection": 5,
        "visited_urls": {"url1", "url2"},
    }
    scraper1._save_progress()

    # 验证文件已创建且内容正确
    assert progress_file.exists()
    with open(progress_file, "r") as f:
        data = json.load(f)
        assert data["completed_page_for_collection"] == 5
        assert set(data["visited_urls"]) == {"url1", "url2"}

    # 2. 创建第二个 scraper 实例，它应该会自动加载进度
    scraper2 = SpeedhuntersScraper(output_dir=str(output_dir), resume=True)
    assert scraper2.progress["completed_page_for_collection"] == 5
    assert scraper2.progress["visited_urls"] == {"url1", "url2"}

    # 3. 测试 resume=False 的情况，不应该加载进度
    scraper3 = SpeedhuntersScraper(output_dir=str(output_dir), resume=False)
    assert scraper3.progress["completed_page_for_collection"] == 0
    assert scraper3.progress["visited_urls"] == set() 