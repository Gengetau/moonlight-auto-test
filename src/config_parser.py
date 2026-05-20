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
