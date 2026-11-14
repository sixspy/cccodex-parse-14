from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods, require_POST

from catalog.models import ParseJob


def _authed(request) -> bool:
    return request.session.get("user") == settings.ADMIN_USER


def root(request):
    return HttpResponseRedirect("/jobs/" if _authed(request) else "/login")


@require_http_methods(["GET", "POST"])
def login_page(request):
    err = ""
    if request.method == "POST":
        user = request.POST.get("username", "").strip()
        pwd = request.POST.get("password", "")
        if user == settings.ADMIN_USER and pwd == settings.ADMIN_PASSWORD:
            request.session["user"] = user
            return HttpResponseRedirect("/jobs/")
        err = "账号或密码错误"
    return render(request, "login.html", {"error": err, "lab": settings.LAB_NAME})


def logout_view(request):
    request.session.flush()
    return HttpResponseRedirect("/login")


def job_list(request):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    items = list(ParseJob.objects.all())
    return render(request, "jobs_list.html", {"items": items, "lab": settings.LAB_NAME, "user": request.session["user"]})


def job_detail(request, job_id: int):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    job = get_object_or_404(ParseJob, pk=job_id)
    return render(request, "job_detail.html", {"job": job, "lab": settings.LAB_NAME, "user": request.session["user"]})


@require_POST
def job_parse(request, job_id: int):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    job = get_object_or_404(ParseJob, pk=job_id)
    if job.status in ("待解析", "解析失败"):
        job.parsed = 12 + (job.id * 2) % 9
        job.landed = 0
        job.status = "已解析待入库"
        job.save(update_fields=["parsed", "landed", "status"])
    return HttpResponseRedirect(f"/jobs/{job.id}/")


@require_POST
def job_land(request, job_id: int):
    if not _authed(request):
        return HttpResponseRedirect("/login")
    job = get_object_or_404(ParseJob, pk=job_id)
    if job.status == "已解析待入库":
        job.landed = job.parsed
        job.status = "已入库"
        job.save(update_fields=["landed", "status"])
    return HttpResponseRedirect(f"/jobs/{job.id}/")
