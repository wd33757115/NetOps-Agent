"""User-owned desktop preferences. Business permissions are independent."""
import base64
import binascii
import json
import re

from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from ..decorators import require_jwt
from ..models import DesktopPreference
from ..response import bff_error, bff_success


def validate_wallpaper(value):
    if not isinstance(value, dict):
        raise ValueError("壁纸格式无效")
    if value.get("type") == "preset" and value.get("id") in ("mountains", "midnight", "forest", "dusk"):
        return {"type": "preset", "id": value["id"]}
    data = value.get("dataUrl")
    if value.get("type") != "image" or not isinstance(data, str) or len(data) > 524288 or not data.startswith("data:image/jpeg;base64,"):
        raise ValueError("壁纸必须为经过压缩的 JPEG 图片")
    try:
        image = base64.b64decode(data.split(",", 1)[1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("壁纸图片编码无效") from exc
    if not image.startswith(b"\xff\xd8\xff") or not image.endswith(b"\xff\xd9"):
        raise ValueError("壁纸图片格式无效")
    return {"type": "image", "dataUrl": data}


def default_layout():
    return {
        "schemaVersion": 1,
        "nodes": [
            {"id": name, "kind": "app", "appId": name, "containerId": "desktop:0", "col": col, "row": 0}
            for col, name in enumerate(("patrol", "patrol-rules"))
        ],
        "dockAppIds": ["patrol", "patrol-rules"],
    }


def validate_layout(body):
    def integer(value, maximum):
        return type(value) is int and 0 <= value <= maximum

    def identifier(value):
        return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value)

    if not isinstance(body, dict) or (type(body.get("schemaVersion")) is not int or body.get("schemaVersion") != 1):
        raise ValueError("不支持的布局版本")
    if not integer(body.get("revision"), 2147483646):
        raise ValueError("布局版本号无效")
    nodes, dock = body.get("nodes"), body.get("dockAppIds")
    if not isinstance(nodes, list) or len(nodes) > 200 or not isinstance(dock, list) or len(dock) > 30:
        raise ValueError("布局大小超出限制")
    ids, apps, folders, occupied, orders = set(), set(), set(), set(), set()
    for node in nodes:
        if not isinstance(node, dict) or not identifier(node.get("id")) or node["id"] in ids:
            raise ValueError("节点标识无效或重复")
        ids.add(node["id"])
        kind, container = node.get("kind"), node.get("containerId")
        if not isinstance(container, str):
            raise ValueError("节点容器无效")
        if kind == "folder":
            name = node.get("name")
            if container != "desktop:0" or not isinstance(name, str) or not name.strip() or len(name) > 40:
                raise ValueError("文件夹名称或位置无效")
            folders.add("folder:" + node["id"])
        elif kind == "app":
            if not identifier(node.get("appId")) or node["appId"] in apps:
                raise ValueError("应用标识无效或重复")
            apps.add(node["appId"])
        else:
            raise ValueError("节点类型无效")
        if container == "desktop:0":
            if not integer(node.get("col"), 99) or not integer(node.get("row"), 199):
                raise ValueError("桌面坐标无效")
            position = (node["col"], node["row"])
            if position in occupied:
                raise ValueError("桌面位置重复")
            occupied.add(position)
        else:
            if not integer(node.get("order"), 199):
                raise ValueError("文件夹排序无效")
            position = (container, node["order"])
            if position in orders:
                raise ValueError("文件夹排序重复")
            orders.add(position)
    if any(n["containerId"] != "desktop:0" and n["containerId"] not in folders for n in nodes):
        raise ValueError("文件夹引用不存在")
    if any(not identifier(app) or app not in apps for app in dock) or len(set(dock)) != len(dock):
        raise ValueError("Dock 应用引用无效或重复")
    # Persist only the documented fields, never client-provided ownership or routes.
    clean_nodes = []
    for node in nodes:
        keys = ["id", "kind", "containerId", "name" if node["kind"] == "folder" else "appId"]
        keys += ["col", "row"] if node["containerId"] == "desktop:0" else ["order"]
        clean_nodes.append({key: node[key] for key in keys})
    layout = {"schemaVersion": 1, "nodes": clean_nodes, "dockAppIds": dock}
    if "wallpaper" in body:
        layout["wallpaper"] = validate_wallpaper(body["wallpaper"])
    return layout


@csrf_exempt
@require_jwt(strict=True)
def desktop_layout(request):
    if request.method not in ("GET", "PUT"):
        return bff_error("Method not allowed", 405)
    if request.method == "GET":
        record = DesktopPreference.objects.filter(user=request.user).first()
        return bff_success({**(record.layout if record else default_layout()), "revision": record.revision if record else 0})
    if len(request.body) > 614400:
        return bff_error("布局大小超出限制", 413)
    try:
        body = json.loads(request.body)
        layout = validate_layout(body)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        return bff_error(str(exc), 400)
    record, _ = DesktopPreference.objects.get_or_create(user=request.user, defaults={"layout": default_layout()})
    # Older clients editing icon positions must not erase a user's wallpaper.
    if "wallpaper" not in layout and "wallpaper" in record.layout:
        layout["wallpaper"] = record.layout["wallpaper"]
    updated = DesktopPreference.objects.filter(pk=record.pk, revision=body["revision"]).update(
        layout=layout, revision=body["revision"] + 1, updated_at=timezone.now(),
    )
    if not updated:
        return bff_error("布局已在其他页面修改，请加载最新布局后重试", 409, code="desktop_revision_conflict")
    return bff_success({**layout, "revision": body["revision"] + 1})
