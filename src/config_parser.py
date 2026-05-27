import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

class Config:
    CHROME_PORTABLE_PATH = os.getenv("CHROME_PORTABLE_PATH", "")
    LEGACY_URL = os.getenv("LEGACY_URL", "https://legacy-targetsquare.com")
    NEW_URL = os.getenv("NEW_URL", "https://new-targetsquare.com")
    USERNAME = os.getenv("TEST_USERNAME", "testuser")
    PASSWORD = os.getenv("TEST_PASSWORD", "password")
    USER_DATA_DIR = os.getenv("USER_DATA_DIR", "./output/user_data")
    DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", os.path.expanduser("~/Downloads"))

    @staticmethod
    def _split_env_list(value: str):
        if not value:
            return []
        separator = ";" if ";" in value else ","
        return [item.strip() for item in value.split(separator) if item.strip()]

    @classmethod
    def login_entries(cls):
        legacy_urls = cls._split_env_list(os.getenv("LEGACY_URLS", "")) or [cls.LEGACY_URL]
        new_urls = cls._split_env_list(os.getenv("NEW_URLS", "")) or [cls.NEW_URL]
        names = cls._split_env_list(os.getenv("LOGIN_ENTRY_NAMES", ""))
        count = max(len(legacy_urls), len(new_urls))
        entries = []
        for index in range(count):
            legacy_url = legacy_urls[index] if index < len(legacy_urls) else legacy_urls[-1]
            new_url = new_urls[index] if index < len(new_urls) else new_urls[-1]
            name = names[index] if index < len(names) else f"entry-{index + 1}"
            entries.append({"name": name, "legacy_url": legacy_url, "new_url": new_url})
        return entries

    @classmethod
    def select_login_entry(cls, selector: str = None, *, interactive: bool = True):
        entries = cls.login_entries()
        selector = (selector or os.getenv("LOGIN_ENTRY") or "").strip()
        if selector:
            selected = cls._find_login_entry(entries, selector)
            if selected is None:
                available = ", ".join(f"{idx + 1}:{item['name']}" for idx, item in enumerate(entries))
                raise ValueError(f"Unknown login entry '{selector}'. Available: {available}")
            return cls.apply_login_entry(selected)

        if len(entries) == 1 or not interactive:
            return cls.apply_login_entry(entries[0])

        print("\n[LOGIN ENTRY] 请选择登录入口:")
        for index, entry in enumerate(entries, start=1):
            print(f"  [{index}] {entry['name']}")
            print(f"      Legacy: {entry['legacy_url']}")
            print(f"      New:    {entry['new_url']}")

        while True:
            print(f"选择入口 [1-{len(entries)}]，直接回车默认 1:")
            raw = input("> ").strip()
            selected = cls._find_login_entry(entries, raw or "1")
            if selected is not None:
                return cls.apply_login_entry(selected)
            print("输入无效，请输入序号或入口名称。")

    @classmethod
    def _find_login_entry(cls, entries, selector: str):
        if not selector:
            return None
        if selector.isdigit():
            number = int(selector)
            if 1 <= number <= len(entries):
                return entries[number - 1]
            if 0 <= number < len(entries):
                return entries[number]
        selector_lower = selector.lower()
        for entry in entries:
            if entry["name"].lower() == selector_lower:
                return entry
        return None

    @classmethod
    def apply_login_entry(cls, entry):
        cls.LEGACY_URL = entry["legacy_url"]
        cls.NEW_URL = entry["new_url"]
        return entry
