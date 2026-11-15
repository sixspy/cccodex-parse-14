import pytest
from django.conf import settings
from django.test import Client


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _sync_background(settings):
    """去重/回滚默认同步执行，保证用例确定；
    需要测真线程的用例用 override_settings(LANDING_SYNCHRONOUS=False) 自行覆盖。
    注意：触发后台线程的用例必须用 TransactionTestCase，否则线程读不到未提交事务。
    """
    if getattr(settings, "LANDING_SYNCHRONOUS", False):
        return
    settings.LANDING_SYNCHRONOUS = True
    settings.LANDING_WORK_SECONDS = 0


@pytest.fixture
def login():
    def _login(client, user="admin", pwd="123456"):
        r = client.post("/login", {"username": user, "password": pwd})
        assert r.status_code == 302
    return _login


@pytest.fixture
def seeded_job():
    from catalog.seed import ensure_seed
    ensure_seed()
    from catalog.models import ParseJob
    return ParseJob.objects.order_by("id").first()


def set_policy_mode(mode, operator="admin"):
    from catalog.services import set_policy
    return set_policy(operator, mode)


def parse_job(client, job):
    return client.post(f"/jobs/{job.id}/parse/")


def land(client, job, key, policy_mode):
    return client.post(f"/jobs/{job.id}/land/",
                       {"idem_key": key, "policy_mode": policy_mode})
