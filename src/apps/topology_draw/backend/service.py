"""拓扑绘图的服务层：AI 编辑（走平台助手 DSH 的 drawio 模式）。

编辑协议（照 next-ai-draw-io 的双工具模式）：
- 模型只输出内部 mxCell 元素（不写包装，图标用 huawei.<kind> 短 ID）——
  图标数据永远不经过模型；
- 小改输出全部元素（元素少，全量最稳）；宿主解析后灌回画布。

运行时姿势（对照评审结论）：
- 每次编辑一个独立 DSH 运行（临时目录，跑完自动清理）——不积累会话历史；
- SSE 流式：思考增量实时转发给前端，客户端断开即取消运行；
- 单轮 120s、总预算 220s，与前端 240s、网关 300s 的超时阶梯对齐；
- 成功后自动保存为新版本（diagram 资产，见 repository）。
"""

import asyncio
import contextlib
import re
import time
import xml.etree.ElementTree as ET
from uuid import uuid4

from fastapi import HTTPException

from src.platform.agent_runtime.base import RunContext
from src.platform.agent_runtime.dsh import DshRuntime

from . import repository
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
# 布局数学（与前端 buildDrawioXml 的基线公式保持同一套，见提示词 0 条约定）：
# 模型只输出 layout=r{层}c{序号}，宿主换算坐标 —— LLM 不做坐标数学。
LAYOUT_COL_STEP = 240
LAYOUT_ROW_STEP = 180
LAYOUT_ORIGIN_X = 80
LAYOUT_ORIGIN_Y = 60


def _layout_geometry(style: str) -> tuple[int, int] | None:
    """从 style 的 layout=rXcY 键取 (row, col)；无该键返回 None（保留原坐标）。"""
    match = re.search(r"(?:^|;)layout=r(\d+)c(\d+)(?:;|$)", style)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def inner_cells(xml: str) -> str:
    """从完整 mxfile 里取 <root> 的内部元素（mxCell 序列）。"""
    match = re.search(r"<root>(.*)</root>", xml, re.S)
    body = match.group(1) if match else xml
    # 去掉两个根单元格（宿主灌图时会补回）
    body = re.sub(r'<mxCell id="[01]"[^>]*/>', "", body)
    return body.strip()


def extract_cells(answer: str) -> tuple[str, str]:
    """从模型回答里提取 mxCell 序列与中文说明，返回 (cells, explanation)。

    说明 = 第一个 <mxCell 之前与最后一个 </mxCell> 之后的文字（剥代码围栏标记）。
    """
    text = answer.strip()
    start = text.find("<mxCell")
    if start < 0:
        raise ValueError("模型回答里没有 mxCell 元素")
    end = text.rfind("</mxCell>")
    end = end + len("</mxCell>") if end > start else len(text)
    before = text[:start].replace("```xml", "").replace("```", "").strip()
    after = text[end:].replace("```", "").strip()
    explanation = (before + "\n" + after).strip()
    return text[start:end], explanation


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
    layout_requests: list[tuple[ET.Element, int, int]] = []
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
            layout = _layout_geometry(style)
            if layout is not None:
                layout_requests.append((cell, layout[0], layout[1]))
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
    # 布局落位：模型给的 col 同层冲突时顺序顺延（模型不该管坐标数学，这里兜底）。
    by_row: dict[int, set[int]] = {}
    for cell, row, col in layout_requests:
        used = by_row.setdefault(row, set())
        while col in used:
            col += 1
        used.add(col)
        geo = cell.find("mxGeometry")
        if geo is None:
            geo = ET.SubElement(cell, "mxGeometry")
            geo.set("as", "geometry")
        geo.set("x", str(LAYOUT_ORIGIN_X + col * LAYOUT_COL_STEP))
        geo.set("y", str(LAYOUT_ORIGIN_Y + row * LAYOUT_ROW_STEP))
        # 冲突顺延后的最终位置写回 style 的 layout 键，前端/后续解析所见一致。
        style = re.sub(r";?layout=r\d+c\d+", "", cell.get("style") or "")
        style = re.sub(r";{2,}", ";", style.strip().rstrip(";"))
        cell.set("style", f"{style};layout=r{row}c{col}")
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


async def ai_edit_stream(actor_id: str, instruction: str, current_xml: str, network_id: str):
    """AI 编辑的 SSE 事件流。

    事件：started → reasoning*（思考增量，实时）→ stage*（validate/save）→
    done（xml/cells/process/explanation/versionId）｜error。
    客户端断开时 StreamingResponse 关闭生成器，finally 里取消 DSH 运行，
    不白占全局并发槽位。
    """
    if len(current_xml.encode()) > MAX_XML_BYTES:
        yield {"type": "error", "message": "当前画布 XML 超过 200 KB，先精简再编辑"}
        return
    cells = inner_cells(current_xml)
    # 每次请求独立运行目录：不积累历史上下文，也避开 durable 会话的磁盘成本
    # 与跨请求锁。失败重试在同一请求内带反馈再来一轮（模型仍能看到自己
    # 上一轮的输出——DSH 把轮次历史保存在运行内）。
    catalog = DrawCatalog(actor_id, session_id=f"draw-{uuid4().hex[:8]}")
    runtime = DshRuntime(durable=False)
    run_id_holder: dict = {"id": None}
    process: list[str] = []
    explanation = ""
    document: str | None = None
    started = time.monotonic()

    try:
        yield {"type": "started"}
        prompt = _instruction(cells, instruction)
        feedback = ""
        for attempt in range(2):
            budget = TOTAL_BUDGET_SECONDS - (time.monotonic() - started)
            if budget < 15:
                raise RuntimeError("编辑预算耗尽（超过 220s），请重试")
            context = RunContext(
                run_id=uuid4().hex,
                actor_id=actor_id,
                session_id=catalog.session_id,
                timeout_seconds=min(ATTEMPT_SECONDS, int(budget)),
                max_tool_calls=None,
                max_model_rounds=4,
                model=None,  # 部署配置的模型
                reasoning_effort="low",  # 开思考：过程展示 + 输出更稳
            )
            run_id_holder["id"] = context.run_id
            answer = None
            reasoning_buf: list[str] = []
            async for event in runtime.start(context, [{"role": "user", "content": prompt + feedback}], catalog):
                if event.kind == "failed":
                    raise RuntimeError(str(event.data))
                if event.kind == "reasoning_delta":
                    piece = event.data.get("text") or ""
                    reasoning_buf.append(piece)
                    # 思考增量实时转发；事件密集但体量小（每轮几 KB）。
                    if piece.strip():
                        yield {"type": "reasoning", "text": piece}
                if event.kind == "text_delta":
                    # 模型直接回文本（没调 submit_answer）时，DSH 以 text_delta 发布答案。
                    answer = event.data.get("text") or answer
                if event.kind == "completed":
                    answer = catalog.answer or answer or ""
                    break
            if not answer:
                raise RuntimeError("模型没有返回编辑结果")
            if "".join(reasoning_buf).strip():
                process.append("".join(reasoning_buf).strip())
            yield {"type": "stage", "name": "validate"}
            try:
                cells_out, explanation = extract_cells(answer)
                document, _ = parse_cells(cells_out)
            except ValueError as exc:
                if attempt:
                    raise
                feedback = (
                    f"\n\n注意：你上一次的输出未通过校验（{exc}）。"
                    "请重新输出编辑后的全部 mxCell 元素：节点 style 必须含 "
                    "shape=huawei.<kind>，边的 source/target 必须指向输出里存在的 id，"
                    "value 里的特殊字符用 XML 实体转义。"
                )
                continue
            break
    except ValueError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except RuntimeError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except HTTPException as exc:
        yield {"type": "error", "message": str(exc.detail)}
        return
    finally:
        # 正常完成 / 出错 / 客户端断开（GeneratorExit）都走到这里：
        # 取消可能仍在跑的 DSH 运行，释放全局并发槽位。
        run_id = run_id_holder["id"]
        if run_id:
            with contextlib.suppress(Exception):
                await runtime.cancel(run_id, actor_id=actor_id)

    assert document is not None
    yield {"type": "stage", "name": "save"}
    label = f"AI: {instruction.strip().splitlines()[0][:28]}" if instruction.strip() else "AI 编辑"
    try:
        version_id = await asyncio.to_thread(
            repository.save_version, actor_id, network_id, document, label, "ai"
        )
    except HTTPException as exc:
        yield {"type": "error", "message": f"保存版本失败：{exc.detail}"}
        return
    yield {
        "type": "done",
        "xml": document,
        "cells": inner_cells(document),
        "process": process,
        "explanation": explanation,
        "versionId": version_id,
        "label": label,
    }


async def cancel_edit(runtime_holder: dict, actor_id: str) -> None:
    """兼容入口：按 holder 里的 run_id 取消（预留，当前由生成器 finally 自理）。"""
    run_id = runtime_holder.get("id")
    if run_id:
        await DshRuntime(durable=False).cancel(run_id, actor_id=actor_id)
