import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "cc-parse-lab-dev-only"
DEBUG = True
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "catalog",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
db_path = os.environ.get("DATABASE_PATH") or str(BASE_DIR / "instance" / "parse_lab.db")
Path(db_path).parent.mkdir(parents=True, exist_ok=True)
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": db_path,
    # IMMEDIATE：事务一开始即取写锁，配合 busy_timeout 排队，
    # 避免落档/回滚多线程下保留锁升级死锁（database table is locked）
    "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
}}
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LAB_NAME = "解析入库台"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "123456"
SESSION_COOKIE_NAME = "parse_session"
# 台面演示账号：回滚不能由落档本人发起，故至少两人
LAB_USERS = {
    "admin": "123456",
    "auditor": "123456",
    "operator": "123456",
}
# 去重 / 回滚后台执行；测试置 True 改同步
LANDING_SYNCHRONOUS = os.environ.get("LANDING_SYNCHRONOUS", "") == "1"
LANDING_WORK_SECONDS = float(os.environ.get("LANDING_WORK_SECONDS", "0.2"))
