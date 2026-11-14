"""拓扑绘图的服务层：AI 编辑（走平台助手 DSH 的 drawio 模式）。

编辑协议（照 next-ai-draw-io 的双工具模式）：
- 模型只输出内部 mxCell 元素（不写包装，图标用 huawei.<kind> 短 ID）——
  图标数据永远不经过模型；
- 小改输出全部元素（元素少，全量最稳）；宿主解析后灌回画布。
"""

import re
from uuid import uuid4

from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import DshRuntime

from .catalog import DrawCatalog

KINDS = ("router", "switch", "firewall", "pc", "server", "cloud", "generic")
MAX_XML_BYTES = 200_000


def inner_cells(xml: str) -> str:
    """从完整 mxfile 里取 <root> 的内部元素（mxCell 序列）。"""
    match = re.search(r"<root>(.*)</root>", xml, re.S)
    body = match.group(1) if match else xml
    # 去掉两个根单元格（宿主灌图时会补回）
    body = re.sub(r'<mxCell id="[01]"[^>]*/>', "", body)
    return body.strip()


def extract_cells(answer: str) -> str:
    """从模型回答里提取 mxCell 序列（剥掉可能的代码围栏与解释）。"""
    text = answer.strip()
    fence = re.search(r"```(?:xml)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("<mxCell")
    if start < 0:
        raise ValueError("模型回答里没有 mxCell 元素")
    end = text.rfind("</mxCell>")
    end = end + len("</mxCell>") if end > start else len(text)
    return text[start:end]


def validate_cells(cells: str) -> None:
    """白名单校验：节点必须是 huawei.* 形状，边的端点必须存在。"""
    import xml.etree.ElementTree as ET

    ids, targets, shapes = set(), [], []
    for match in re.finditer(r"<mxCell\b([^>]*)>", cells):
        attrs = match.group(1)
        cell_id = re.search(r'id="([^"]+)"', attrs)
        edge = 'edge="1"' in attrs
        vertex = 'vertex="1"' in attrs
        style = re.search(r'style="([^"]+)"', attrs)
        if cell_id:
            ids.add(cell_id.group(1))
        if vertex and style:
            shape = re.search(r"shape=huawei\.(\w+)", style.group(1))
            shapes.append(shape.group(1) if shape else None)
        if edge:
            source = re.search(r'source="([^"]+)"', attrs)
            target = re.search(r'target="([^"]+)"', attrs)
            targets.append((source.group(1) if source else None, target.group(1) if target else None))
    if None in shapes:
        raise ValueError("存在没有 huawei.* 形状的节点")
    unknown = [s for s in shapes if s and s not in KINDS]
    if unknown:
        raise ValueError(f"未知图标种类: {unknown[:3]}")
    for source, target in targets:
        missing = [end for end in (source, target) if end and end not in ids]
        if missing:
            raise ValueError(f"边的端点不存在: {missing[:2]}")


def wrap_cells(cells: str) -> str:
    return (
        '<mxfile host="netops-topology-draw"><diagram id="topology" name="拓扑">'
        '<mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" page="1" '
        'pageScale="1" pageWidth="1169" pageHeight="826">'
        f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{cells}</root>'
        "</mxGraphModel></diagram></mxfile>"
    )


def _instruction(cells: str, instruction: str) -> str:
    return (
        "当前画布的 mxCell 元素：\n"
        f"{cells}\n\n"
        f"编辑指令：{instruction}\n\n"
        "按系统规则输出编辑后的全部 mxCell 元素。"
    )


async def _run_turn(catalog: DrawCatalog, actor_id: str, prompt: str) -> str:
    """跑一轮 DSH（drawio 模式），返回最终答案文本。"""
    context = RunContext(
        run_id=uuid4().hex,
        actor_id=actor_id,
        session_id=catalog.session_id,
        timeout_seconds=240,
        max_tool_calls=None,
        max_model_rounds=4,
        model=None,  # 部署配置的模型
        reasoning_effort="off",
    )
    messages = [{"role": "user", "content": prompt}]
    answer = None
    async for event in DshRuntime().start(context, messages, catalog):
        if event.kind == "failed":
            raise RuntimeError(str(event.data))
        if event.kind == "text_delta":
            # 模型直接回文本（没调 submit_answer）时，DSH 以 text_delta 发布答案。
            answer = event.data.get("text") or answer
        if event.kind == "completed":
            answer = catalog.answer or answer or ""
            break
    if not answer:
        raise RuntimeError("模型没有返回编辑结果")
    return answer


async def ai_edit(actor_id: str, instruction: str, current_xml: str, network_id: str) -> dict:
    """一轮 AI 编辑：指令 + 当前画布 → DSH（drawio 模式）→ 新画布 XML。

    输出未过校验时带反馈重试一次（同一会话续跑，模型能看到自己上一轮的输出）。
    """
    if len(current_xml.encode()) > MAX_XML_BYTES:
        raise ValueError("当前画布 XML 超过 200 KB，先精简再编辑")
    cells = inner_cells(current_xml)
    catalog = DrawCatalog(actor_id, session_id=f"draw-{network_id}")
    prompt = _instruction(cells, instruction)
    feedback = ""
    edited: str | None = None
    for attempt in range(2):
        answer = await _run_turn(catalog, actor_id, prompt + feedback)
        try:
            edited = extract_cells(answer)
            validate_cells(edited)
            break
        except ValueError as exc:
            if attempt:
                raise
            feedback = (
                f"\n\n注意：你上一次的输出未通过校验（{exc}）。"
                "请重新输出编辑后的全部 mxCell 元素：节点 style 必须含 shape=huawei.<kind>，"
                "边的 source/target 必须指向输出里存在的 id。"
            )
    assert edited is not None
    return {"xml": wrap_cells(edited), "cells": edited}
