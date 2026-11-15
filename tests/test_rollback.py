import pytest

from catalog.models import (
    JobStatus,
    LandingRecord,
    Policy,
    RecordStatus,
    RollbackTicket,
)
from catalog.services import set_policy
from tests.conftest import land, parse_job


def _land_one(client, job, key="KEY-R"):
    set_policy("admin", Policy.SKIP)
    parse_job(client, job)
    land(client, job, key, Policy.SKIP)
    return LandingRecord.objects.get(idem_key=key)


@pytest.mark.django_db
def test_rollback_requires_reason(client, login, seeded_job):
    login(client, "auditor")
    record = _land_one(client, seeded_job)
    r = client.post(f"/records/{record.id}/rollback/", {"reason": "  "})
    assert r.status_code == 302
    assert not RollbackTicket.objects.exists()
    body = client.get(f"/jobs/{seeded_job.id}/").content.decode("utf-8")
    assert "缘故" in body


@pytest.mark.django_db
def test_land_operator_cannot_rollback_self(client, login, seeded_job):
    login(client, "admin")  # admin 点的落档
    record = _land_one(client, seeded_job)
    # admin 自己点回滚 → 拦住
    r = client.post(f"/records/{record.id}/rollback/",
                    {"reason": "数据有问题"})
    assert r.status_code == 302
    assert not RollbackTicket.objects.exists()
    record.refresh_from_db()
    assert record.status == RecordStatus.LANDED
    body = client.get(f"/jobs/{seeded_job.id}/").content.decode("utf-8")
    assert "自己点回滚" in body or "本人" in body


@pytest.mark.django_db
def test_other_user_can_rollback_and_job_restores(client, login, seeded_job):
    login(client, "admin")
    record = _land_one(client, seeded_job)
    # 换另一人发起回滚
    login(client, "auditor")
    r = client.post(f"/records/{record.id}/rollback/",
                    {"reason": "下游口径变更，撤回本批"})
    assert r.status_code == 302
    seeded_job.refresh_from_db()
    assert seeded_job.status == JobStatus.PARSED  # 撤回到落档前
    assert seeded_job.landed == 0
    assert seeded_job.rollback_count == 1
    record.refresh_from_db()
    assert record.status == RecordStatus.ROLLED_BACK

    ticket = RollbackTicket.objects.get()
    assert ticket.operator == "auditor"
    assert ticket.reason == "下游口径变更，撤回本批"
    # 回滚详情可读回滚单正文
    body = client.get(f"/rollbacks/{ticket.id}/").content.decode("utf-8")
    assert "回滚单正文" in body
    assert "下游口径变更" in body
    assert "撤回条数" in body


@pytest.mark.django_db
def test_no_landing_while_rollback_in_progress(client, login, seeded_job):
    # 回滚单处理中时落档 → 拦住（真后台回滚窗口见 tests/test_background.py）
    login(client, "admin")
    record = _land_one(client, seeded_job)
    login(client, "auditor")
    from catalog.models import RollbackTicket, TicketStatus
    RollbackTicket.objects.create(
        record=record, job=seeded_job, kind="手动回滚",
        reason="撤回重跑", operator="auditor", status=TicketStatus.IN_PROGRESS)
    seeded_job.status = JobStatus.ROLLING_BACK
    seeded_job.save(update_fields=["status"])

    r = client.post(f"/jobs/{seeded_job.id}/land/",
                    {"idem_key": "KEY-NEW", "policy_mode": Policy.SKIP})
    assert r.status_code == 302
    assert not LandingRecord.objects.filter(idem_key="KEY-NEW").exists()
    body = client.get(f"/jobs/{seeded_job.id}/").content.decode("utf-8")
    assert "回滚尚未结束" in body


@pytest.mark.django_db
def test_rollback_detail_shows_contention_wording(client, login, seeded_job):
    login(client, "admin")
    set_policy("admin", Policy.SKIP)
    parse_job(client, seeded_job)
    land(client, seeded_job, "KEY-1", Policy.SKIP)
    land(client, seeded_job, "KEY-2", Policy.SKIP)  # 后到争用失败
    record = LandingRecord.objects.get(idem_key="KEY-1")

    login(client, "auditor")
    client.post(f"/records/{record.id}/rollback/", {"reason": "撤回核对"})
    ticket = RollbackTicket.objects.get()
    # 回滚详情可读落档争用失败字样
    body = client.get(f"/rollbacks/{ticket.id}/").content.decode("utf-8")
    assert "落档争用失败" in body
