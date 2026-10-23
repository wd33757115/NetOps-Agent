"""Read-only circuit answers for chat: find circuits and how to report a fault, show a
site's paths to the core sites and its single points, and work out what one failure would cut off."""

from .circuits import circuits_page
from .common import bad
from .resilience import KINDS, impact_page, resilience_page

KIND_BY_NAME = {name: kind for kind, name in KINDS.items()}


def end_text(end):
    if not end:
        return None
    place = end["place"]["name"] if end["place"] else "未填"
    parts = [place, end.get("device"), end.get("interface"), end["pop"]["name"] if end.get("pop") else None, end.get("ip")]
    return " · ".join(part for part in parts if part)


def find_circuits(actor, query):
    page = circuits_page(actor, query=query, limit=30)
    return {"total": page["total"], "circuits": [{
        "cid": c["cid"], "provider": c["provider"]["name"] if c["provider"] else None,
        "type": c["type"]["name"] if c["type"] else None, "status": c["status"], "bandwidth_mbps": c["bandwidth"],
        "a": end_text(c["a"]), "z": end_text(c["z"]),
        "interconnect": c["interconnect"]["network"] if c["interconnect"] else None,
        "routing": " ".join(part for part in (c["routing"], c["local_as"], c["remote_as"]) if part) or None,
        "risks": [r["name"] for r in c["risks"]], "single_point_for": c["single_point_for"],
    } for c in page["circuits"]]}


def path_text(path):
    return " → ".join(hop.get("cid") or hop.get("name") for hop in path)


def site_resilience(actor, site=None):
    report = resilience_page(actor, query=site, limit=50)
    return {"core_sites": [c["name"] for c in report["core"]], "sites": [{
        "site": r["site"]["name"], "state": r["state_name"], "note": r["note"],
        "paths": [path_text(p) for p in r["paths"]],
        "single_points": [f"{i['kind_name']} {i['name']}" for i in r["single_points"]],
    } for r in report["sites"]], "basis": "只按台账里在用的专线计算；设备上实际配置的路由看不到，是否可接受由网络负责人判断。"}


def failure_impact(actor, kind, name):
    key = KIND_BY_NAME.get(kind, kind)
    if key not in KINDS:
        raise bad(f"故障对象类型只能是：{'、'.join(KINDS.values())}")
    options = impact_page(actor)["options"][key]
    matched = [o for o in options if o["name"] == name] or [o for o in options if name in o["name"]]
    if not matched:
        raise bad(f"台账里没有{KINDS[key]}“{name}”，或它没有在用的专线")
    if len(matched) > 1:
        raise bad(f"“{name}”对应多个{KINDS[key]}：{'、'.join(o['name'] for o in matched[:10])}")
    impact = impact_page(actor, key, matched[0]["id"])["impact"]
    states = {"down": "断开", "single": "只剩一条路"}
    return {
        "element": f"{impact['element']['kind_name']} {impact['element']['name']}",
        "circuits": [f"{c['cid']}（{c['provider']}，报障 {c['support_phone'] or '未登记'}）" for c in impact["circuits"]],
        "sites": [{"site": r["site"]["name"], "impact": states[r["state"]]} for r in impact["sites"]],
        "unaffected_sites": impact["counts"].get("fine", 0),
        "systems": [f"{s['system']}（{'、'.join(x['name'] for x in s['sites'])}）" for s in impact["systems"]],
    }
