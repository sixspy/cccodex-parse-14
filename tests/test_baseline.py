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
