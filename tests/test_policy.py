import datetime

import pytest

from catalog.models import IdempotencyPolicy, Policy
from catalog.services import ServiceError, get_active_policy, set_policy
from tests.conftest import land, parse_job


@pytest.mark.django_db
def test_only_one_of_two_modes_allowed():
    set_policy("admin", Policy.SKIP)
    with pytest.raises(ServiceError):
        set_policy("admin", "WHATEVER")


@pytest.mark.django_db
def test_policy_locked_same_day(settings):
    set_policy("admin", Policy.SKIP)
    # 写进幂等策略后当天不许换弦
    with pytest.raises(ServiceError):
        set_policy("admin", Policy.FAIL_BATCH)
    assert get_active_policy().mode == Policy.SKIP


@pytest.mark.django_db
def test_policy_can_change_next_day(settings):
    set_policy("admin", Policy.SKIP)
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    policy = IdempotencyPolicy.objects.create(
        mode=Policy.FAIL_BATCH, set_by="admin", set_date=tomorrow)
    assert get_active_policy().id == policy.id
    assert get_active_policy().mode == Policy.FAIL_BATCH


@pytest.mark.django_db
def test_policy_page_locks_form_today(client, login):
    login(client)
    r = client.post("/policy/", {"mode": Policy.SKIP})
    assert r.status_code == 302
    body = client.get("/policy/").content.decode("utf-8")
    assert "当天不许换弦" in body
    # 当天再提交另一种：被拦，渲染 200 且带错误消息，策略未变
    r = client.post("/policy/", {"mode": Policy.FAIL_BATCH})
    assert r.status_code == 200
    assert "当天不许换弦" in r.content.decode("utf-8")
    assert get_active_policy().mode == Policy.SKIP


@pytest.mark.django_db
def test_landing_blocked_before_policy_set(client, login, seeded_job):
    login(client)
    parse_job(client, seeded_job)
    r = land(client, seeded_job, "KEY-1", Policy.SKIP)
    assert r.status_code == 302
    from catalog.models import LandingRecord
    assert not LandingRecord.objects.exists()


@pytest.mark.django_db
def test_landing_with_mismatched_policy_is_blocked(client, login, seeded_job):
    login(client)
    set_policy("admin", Policy.SKIP)
    parse_job(client, seeded_job)
    # 点落档时处理若跟幂等策略不一致 → 拦住
    r = land(client, seeded_job, "KEY-1", Policy.FAIL_BATCH)
    assert r.status_code == 302
    seeded_job.refresh_from_db()
    from catalog.models import LandingRecord
    assert not LandingRecord.objects.exists()
    assert seeded_job.status == "已解析待入库"
    body = client.get(f"/jobs/{seeded_job.id}/").content.decode("utf-8")
    assert "不一致" in body
