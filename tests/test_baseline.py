import pytest
from catalog.models import ParseJob
from catalog.seed import ensure_seed


@pytest.mark.django_db
def test_login_page_mentions_password(client):
    r = client.get("/login")
    assert r.status_code == 200
    body = r.content.decode("utf-8")
    assert "登录" in body and "密码" in body


@pytest.mark.django_db
def test_login_and_list(client):
    ensure_seed()
    r = client.post("/login", {"username": "admin", "password": "123456"})
    assert r.status_code == 302
    r = client.get("/jobs/")
    assert r.status_code == 200
    assert ParseJob.objects.count() >= 1
    assert "解析任务" in r.content.decode("utf-8")


@pytest.mark.django_db
def test_second_user_can_login(client):
    # 回滚不能由本人发起，台面至少两名操作员
    r = client.post("/login", {"username": "auditor", "password": "123456"})
    assert r.status_code == 302


@pytest.mark.django_db
def test_list_shows_skip_rollback_policy_columns(client, login, seeded_job):
    login(client)
    r = client.get("/jobs/")
    body = r.content.decode("utf-8")
    # 落档列表横排写出跳过条数、已回滚次数、处置策略名
    assert "跳过条数" in body and "已回滚次数" in body and "处置策略名" in body
