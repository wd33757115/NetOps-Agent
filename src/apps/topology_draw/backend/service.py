"""拓扑绘图的服务层：AI 编辑（走平台助手 DSH 的 drawio 模式）。

编辑协议（照 next-ai-draw-io 的双工具模式）：
- 模型只输出内部 mxCell 元素（不写包装，图标用 huawei.<kind> 短 ID）——
  图标数据永远不经过模型；
- 小改输出全部元素（元素少，全量最稳）；宿主解析后灌回画布。

运行时姿势（对照评审结论）：
- 每次编辑一个独立 DSH 运行（临时目录，跑完清理）——不积累会话历史，
  也不落 2.6MB+/份的 durable 会话目录；失败重试在同一运行内带反馈再来一轮；
- 单轮 120s、总预算 220s，与前端 240s、网关 300s 的超时阶梯对齐；
- 客户端断开即取消运行，不白占全局并发槽位。
"""

import asyncio
import contextlib
import re
import time
import xml.etree.ElementTree as ET
from uuid import uuid4

from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import DshRuntime

from .catalog import DrawCatalog

KINDS = (
    "router",
    "switch",
    "firewall",
    "pc",
    "laptop",
    "server",
    "cloud",
    "generic",
    "ce12800",
    "ac",
    "ap",
    "lb",
    "stacked",
    "stacked_ce",
    "access",
    "aggregate",
)
MAX_XML_BYTES = 200_000
MAX_CELLS = 800
ATTEMPT_SECONDS = 120
TOTAL_BUDGET_SECONDS = 220


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


def wrap_cells(cells: str) -> str:
    return (
        '<mxfile host="netops-topology-draw"><diagram id="topology" name="拓扑">'
        '<mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" page="1" '
        'pageScale="1" pageWidth="1169" pageHeight="826">'
        f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{cells}</root>'
        "</mxGraphModel></diagram></mxfile>"
    )


def parse_cells(cells: str) -> tuple[str, str]:
    """真解析并规范化模型输出：返回 (完整 mxfile, 内部 mxCell 串)。

    一次解决三类问题：未转义实体/尾部残渣/注入标签（fromstring 直接拒收），
    属性实体的规范重转义（tostring，value 里的换行保持 &#10; 形式），以及
    结构校验：id 唯一、数量上限、节点形状白名单、边的端点与 parent 存在性。
    """
    try:
        root = ET.fromstring(wrap_cells(cells)).find(".//mxGraphModel/root")
    except ET.ParseError as exc:
        raise ValueError(f"输出不是合法 XML：{exc}") from exc
    if root is None:
        raise ValueError("输出里没有图形模型")
    ids: set[str] = set()
    count = 0
    for cell in root:
        if cell.tag != "mxCell":
            raise ValueError(f"root 里混入了非 mxCell 元素: {cell.tag}")
        cell_id = cell.get("id")
        if not cell_id:
            raise ValueError("存在缺 id 的 mxCell")
        if cell_id in ids:
            raise ValueError(f"mxCell id 重复: {cell_id[:40]}")
        ids.add(cell_id)
        count += 1
        style = cell.get("style") or ""
        if cell.get("vertex") == "1":
            shape = re.search(r"shape=huawei\.(\w+)", style)
            if not shape:
                raise ValueError("存在没有 huawei.* 形状的节点")
            if shape.group(1) not in KINDS:
                raise ValueError(f"未知图标种类: {shape.group(1)}")
        elif cell.get("edge") == "1":
            for end in ("source", "target"):
                ref = cell.get(end)
                if ref and ref not in ids and ref not in ("0", "1"):
                    raise ValueError(f"边的端点不存在: {ref[:40]}")
    if count > MAX_CELLS:
        raise ValueError(f"元素数量超过上限 {MAX_CELLS}")
    for cell in root:
        parent = cell.get("parent")
        if parent and parent not in ids and parent not in ("0", "1"):
            raise ValueError(f"parent 指向不存在的元素: {parent[:40]}")
    # 根单元格（id 0/1）由 wrap_cells 统一补回，规范化输出里跳过它们，
    # 否则灌进 draw.io 就是 "Duplicate ID: 0"。
    normalized = "".join(
        ET.tostring(cell, encoding="unicode") for cell in root if cell.get("id") not in ("0", "1")
    )
    return wrap_cells(normalized), normalized


def _instruction(cells: str, instruction: str) -> str:
    return (
        "当前画布的 mxCell 元素：\n"
        f"{cells}\n\n"
        f"编辑指令：{instruction}\n\n"
        "按系统规则输出编辑后的全部 mxCell 元素。"
    )


async def _run_turn(runtime: DshRuntime, catalog: DrawCatalog, actor_id: str, prompt: str,
                    current_run: dict, *, seconds: float) -> tuple[str, str]:
    """跑一轮 DSH（drawio 模式），返回 (答案文本, 思考过程)。"""
    context = RunContext(
        run_id=uuid4().hex,
        actor_id=actor_id,
        session_id=catalog.session_id,
        timeout_seconds=seconds,
        max_tool_calls=None,
        max_model_rounds=4,
        model=None,  # 部署配置的模型
        reasoning_effort="low",  # 开思考：前端展示过程，输出也更稳
    )
    current_run["id"] = context.run_id
    messages = [{"role": "user", "content": prompt}]
    answer = None
    reasoning: list[str] = []
    async for event in runtime.start(context, messages, catalog):
        if event.kind == "failed":
            raise RuntimeError(str(event.data))
        if event.kind == "reasoning_delta":
            reasoning.append(event.data.get("text") or "")
        if event.kind == "text_delta":
            # 模型直接回文本（没调 submit_answer）时，DSH 以 text_delta 发布答案。
            answer = event.data.get("text") or answer
        if event.kind == "completed":
            answer = catalog.answer or answer or ""
            break
    if not answer:
        raise RuntimeError("模型没有返回编辑结果")
    return answer, "".join(reasoning)


async def ai_edit(actor_id: str, instruction: str, current_xml: str, network_id: str,
                  request=None) -> dict:
    """一轮 AI 编辑：指令 + 当前画布 → DSH（drawio 模式）→ 新画布 XML。

    输出未过校验时带反馈重试一次（同一请求内）；客户端断开即取消运行。
    """
    if len(current_xml.encode()) > MAX_XML_BYTES:
        raise ValueError("当前画布 XML 超过 200 KB，先精简再编辑")
    cells = inner_cells(current_xml)
    # 每次请求独立运行目录：不积累历史上下文，也避开 durable 会话的磁盘成本
    # 与跨请求锁。重试在同一 catalog/运行内，模型仍能看到自己上一轮的输出。
    catalog = DrawCatalog(actor_id, session_id=f"draw-{uuid4().hex[:8]}")
    runtime = DshRuntime(durable=False)
    current_run: dict = {"id": None}

    async def watch_disconnect() -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                if await request.is_disconnected():
                    run_id = current_run["id"]
                    if run_id:
                        with contextlib.suppress(Exception):
                            await runtime.cancel(run_id, actor_id=actor_id)
                    return
            except Exception:
                return

    watcher = asyncio.create_task(watch_disconnect()) if request is not None else None
    prompt = _instruction(cells, instruction)
    feedback = ""
    document: str | None = None
    process: list[str] = []
    started = time.monotonic()
    try:
        for attempt in range(2):
            budget = TOTAL_BUDGET_SECONDS - (time.monotonic() - started)
            if budget < 15:
                raise RuntimeError("编辑预算耗尽（超过 220s），请重试")
            answer, reasoning = await _run_turn(
                runtime, catalog, actor_id, prompt + feedback, current_run,
                seconds=min(ATTEMPT_SECONDS, budget),
            )
            if reasoning.strip():
                process.append(reasoning.strip())
            try:
                document, _ = parse_cells(extract_cells(answer))
                break
            except ValueError as exc:
                if attempt:
                    raise
                feedback = (
                    f"\n\n注意：你上一次的输出未通过校验（{exc}）。"
                    "请重新输出编辑后的全部 mxCell 元素：节点 style 必须含 "
                    "shape=huawei.<kind>，边的 source/target 必须指向输出里存在的 id，"
                    "value 里的特殊字符用 XML 实体转义。"
                )
    finally:
        if watcher is not None:
            watcher.cancel()
    assert document is not None
    return {"xml": document, "cells": inner_cells(document), "process": process}
