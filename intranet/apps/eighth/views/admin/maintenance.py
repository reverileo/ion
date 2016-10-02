# -*- coding: utf-8 -*-

import logging
import ldap3
import os
import threading
import shutil

from tempfile import gettempdir
from io import StringIO

from django.conf import settings
from django.urls import reverse
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.core.management import call_command
from django.contrib import messages

from intranet.db.ldap_db import LDAPConnection

from cacheops import invalidate_obj

from ....auth.decorators import eighth_admin_required
from ....users.models import User

logger = logging.getLogger(__name__)

LDAP_BASIC_FIELDS = [
    ("iodineUid", "Username"),
    ("iodineUidNumber", "User ID"),
    ("cn", "Full Name"),
    ("givenName", "First Name"),
    ("sn", "Last Name"),
    ("mail", "Email")
]
LDAP_TEACHER_ADVANCED_FIELDS = {
    "header": True,
    "style": "default",
    "mailentries": -1,
    "chrome": True,
    "startpage": "news"
}


@eighth_admin_required
def index_view(request):
    context = {
        "admin_page_title": "Maintenance Tools"
    }
    return render(request, "eighth/admin/maintenance.html", context)


@eighth_admin_required
def ldap_management(request):
    context = {
        "admin_page_title": "LDAP Management",
        "fields": LDAP_BASIC_FIELDS,
        "advanced_fields": LDAP_TEACHER_ADVANCED_FIELDS
    }
    return render(request, "eighth/admin/ldap_management.html", context)


def clear_user_cache(dn):
    user = User.get_user(dn=dn)
    invalidate_obj(user)
    user.clear_cache()


@eighth_admin_required
def ldap_modify(request):
    dn = request.POST.get("dn", None)
    if request.method == "POST":
        c = LDAPConnection()
        if dn:  # modify account
            attrs = {}
            for field, name in LDAP_BASIC_FIELDS:
                value = request.POST.get(field, None)
                if value:
                    attrs[field] = [(ldap3.MODIFY_REPLACE, [value])]
            success = c.conn.modify(dn, attrs)
            clear_user_cache(dn)
            return JsonResponse({
                "success": success,
                "id": request.POST.get("iodineUid", None) if success else None,
                "error": "LDAP query failed!" if not success else None,
                "details": c.conn.last_error
            })
        else:  # create new account
            objectClass = request.POST.get("objectClass", None)
            if not objectClass == "tjhsstStudent" and not objectClass == "tjhsstTeacher":
                return JsonResponse({
                    "success": False,
                    "error": "Invalid objectClass!",
                    "details": "Valid objectClasses are tjhsstStudent and tjhsstTeacher."
                })
            attrs = dict(LDAP_TEACHER_ADVANCED_FIELDS)
            for field, name in LDAP_BASIC_FIELDS:
                value = request.POST.get(field, None)
                if not value:
                    return JsonResponse({"success": False, "error": "{} is a required field!".format(field)})
                attrs[field] = value
            try:
                iodineUidNum = int(attrs["iodineUidNumber"])
            except ValueError:
                return JsonResponse({"success": False, "error": "iodineUidNumber must be an integer!"})
            if objectClass == "tjhsstTeacher":
                if not (0 <= iodineUidNum <= 10000):
                    return JsonResponse({"success": False, "error": "iodineUidNumber must be between 0 and 10,000!"})
            else:
                if not iodineUidNum > 30000:
                    return JsonResponse({"success": False, "error": "iodineUidNumber must be above 30,000!"})
            success = c.conn.add("iodineUid={},{}".format(attrs["iodineUid"], settings.USER_DN), object_class=objectClass, attributes=attrs)
            return JsonResponse({
                "success": success,
                "error": "LDAP query failed!" if not success else None,
                "details": c.conn.last_error
            })
    return JsonResponse({"success": False})


@eighth_admin_required
def ldap_delete(request):
    dn = request.POST.get("dn", None)
    if request.method == "POST" and dn:
        if not dn.endswith(settings.USER_DN):
            return JsonResponse({
                "success": False,
                "error": "Invalid DN!"
            })
        c = LDAPConnection()
        success = c.conn.delete(dn)
        clear_user_cache(dn)
        return JsonResponse({
            "success": success,
            "error": "LDAP query failed!" if not success else None,
            "details": c.conn.last_error
        })
    return JsonResponse({"success": False})


@eighth_admin_required
def ldap_next_id(request):
    is_student = request.GET.get("type", "teacher") == "student"
    usrid = 0
    c = LDAPConnection()
    res = c.search(settings.USER_DN, "objectClass=tjhsstStudent" if is_student else "objectClass=tjhsstTeacher", ["iodineUidNumber"])
    if len(res) > 0:
        res = [int(x["attributes"]["iodineUidNumber"][0]) for x in res]
        if is_student:
            usrid = max(res) + 1
        else:
            res = set([x for x in res if x < 1200])
            usrid = max(res) + 1
            if usrid == 1200:
                for x in range(1200):
                    if x not in res:
                        usrid = x
                        break

    return JsonResponse({"id": usrid})


@eighth_admin_required
def ldap_list(request):
    c = LDAPConnection()
    usrid = request.GET.get("id", None)
    if usrid:
        data = c.search(settings.USER_DN, "iodineUid={}".format(usrid), ["*"])
        if len(data) == 0:
            return JsonResponse({"account": None})
        account = {x: y[0] for x, y in data[0]["attributes"].items()}
        account["dn"] = data[0]["dn"]
        return JsonResponse({"account": account})
    else:
        is_student = request.GET.get("type", "teacher") == "student"
        data = c.search(settings.USER_DN, "objectClass=tjhsstStudent" if is_student else "objectClass=tjhsstTeacher", ["iodineUid", "cn"])
        accounts = [{"id": x["attributes"]["iodineUid"][0], "name": x["attributes"]["cn"][0]} for x in data]
        accounts = sorted(accounts, key=lambda acc: acc["name"])
        return JsonResponse({"accounts": accounts})


def get_import_directory():
    return os.path.join(gettempdir(), "ion_sis_import")


class ImportThread(threading.Thread):
    def __init__(self, email, folder):
        threading.Thread.__init__(self)
        self.email = email
        self.folder = folder

    def run(self):
        # TODO: run import process
        # TODO: import LDIF files
        # TODO: send email to user
        shutil.rmtree(self.folder)


@eighth_admin_required
def sis_import(request):
    import_dir = get_import_directory()
    context = {
        "admin_page_title": "SIS Import",
        "email": request.user.tj_email,
        "help_email": settings.FEEDBACK_EMAIL,
        "already_importing": os.path.isdir(import_dir),
        "completed": False
    }
    if request.method == "POST":
        if context["already_importing"]:
            messages.error(request, "An upload is currently in progress!")
            return redirect(reverse("eighth_admin_amintenance_sis_import"))
        if len(request.FILES) == 0:
            messages.error(request, "You need to upload a file!")
            return redirect(reverse("eighth_admin_maintenance_sis_import"))
        data = request.FILES["data"]
        os.makedirs(import_dir)
        with open(os.path.join(import_dir, "data.csv"), "wb+") as f:
            for chunk in data.chunks():
                f.write(chunk)
        thread = ImportThread(request.user.tj_email, import_dir)
        thread.start()
        context["completed"] = True
    return render(request, "eighth/admin/sis_import.html", context)


@eighth_admin_required
def start_of_year_view(request):
    context = {
        "admin_page_title": "Start of Year Operations",
        "completed": False
    }
    if request.method == "POST" and request.POST.get("confirm"):
        content = StringIO()
        call_command("year_cleanup", run=True, confirm=True, stdout=content)
        content.seek(0)
        context["output"] = content.read()
        context["completed"] = True
    return render(request, "eighth/admin/start_of_year.html", context)


@eighth_admin_required
def clear_comments_view(request):
    context = {
        "admin_page_title": "Clear Admin Comments",
        "completed": False
    }
    if request.method == "POST" and request.POST.get("confirm"):
        deleted_comments = ""
        count = 0

        c = LDAPConnection()
        comments = c.search(settings.USER_DN, "objectClass=tjhsstStudent", ["eighthoffice-comments", "dn"])
        for row in comments:
            if "eighthoffice-comments" in row:
                c.del_attribute(row["dn"], "eighthoffice-comments")
                u = User.objects.get(dn=row["dn"])
                invalidate_obj(u)
                u.clear_cache()
                deleted_comments += "=== {} ({})\n{}\n".format(u.full_name, u.username, row["eighthoffice-comments"])
                count += 1

        context["deleted_comments"] = deleted_comments or "No comments were deleted."
        context["deleted_comments_count"] = count
        context["completed"] = True
    return render(request, "eighth/admin/clear_comments.html", context)