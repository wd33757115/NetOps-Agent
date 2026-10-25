"""Files attached to a question: kept in the asker's netdisk, used by the turn.

Uploading stores the file in the user's netdisk folder 问答附件 (netdisk quota and
permissions). A question carries netdisk file ids and the user message keeps them. Each
turn checks every file is still the user's to read, then:

- text files go into the turn's document workspace, so the model reads them with DSH's
  read/grep and cites the lines like any document;
- images go to DSH as image attachments when the conversation's model takes images;
- every file can fill a Workflow's file field (``netdisk:<file_id>``), which a Workflow
  step reads again as the user.
"""

from pathlib import PurePosixPath

from fastapi import HTTPException

from src.platform import files

MAX_BYTES = 10 * 1024 * 1024
MAX_PER_MESSAGE = 5
# Text files of a conversation put into each turn's workspace, newest first.
MAX_READABLE = 10
TEXT = frozenset({".txt", ".log", ".cfg", ".conf", ".cli", ".csv", ".md", ".json", ".yaml",
                  ".yml", ".xml", ".ini"})
IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
          ".webp": "image/webp", ".gif": "image/gif"}
# Kept for Workflows (an application form, a document); the model cannot read them.
OTHER = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".pdf": "application/pdf", ".zip": "application/zip"}
# DSH's DeepSeek catalog marks these as taking image input; the others read text only.
VISION_MODELS = frozenset({"deepseek-flash", "deepseek-v4-flash-vision-exp"})


def suffix(name):
    return PurePosixPath(str(name)).suffix.lower()


def kind_of(name):
    ending = suffix(name)
    return "text" if ending in TEXT else "image" if ending in IMAGES else "file"


def content_type(name):
    ending = suffix(name)
    if ending in TEXT:
        return "text/plain; charset=utf-8"
    return IMAGES.get(ending) or OTHER.get(ending) or "application/octet-stream"


def upload(user, filename, data):
    """Store one attached file in the user's netdisk; what a question then refers to."""
    name = PurePosixPath(str(filename or "").replace("\\", "/")).name.strip()
    if not name or len(name) > 200 or any(ord(c) < 32 for c in name):
        raise HTTPException(422, "文件名无效")
    if suffix(name) not in TEXT | set(IMAGES) | set(OTHER):
        raise HTTPException(422, "不支持这种文件；可以上传文本、配置、表格（xlsx）、图片、PDF 或 zip")
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(413, "文件应大于 0 字节且不超过 10 MB")
    if not user.can_execute_skills():
        raise HTTPException(403, "当前账号不能上传网盘文件")
    stored = files.save(user, name, data, content_type(name))
    return {**stored, "kind": kind_of(stored["name"])}


def resolve(user, file_ids):
    """The files a question names, each one the user may read; in the order given."""
    ids = list(dict.fromkeys(file_ids or []))
    if len(ids) > MAX_PER_MESSAGE:
        raise HTTPException(422, f"一条消息最多附带 {MAX_PER_MESSAGE} 个文件")
    found = []
    for file_id in ids:
        meta = files.describe(user, file_id)
        found.append({"kind": "attachment", "file_id": meta["file_id"], "name": meta["name"],
                      "size": meta["size"], "content_type": meta["content_type"],
                      "type": kind_of(meta["name"])})
    return found


def in_conversation(rows):
    """Attachments of the conversation's user messages; the last message's ones are current."""
    users = [row for row in rows if row["role"] == "user"]
    found = []
    for index, row in enumerate(users):
        for item in row.get("references") or []:
            if isinstance(item, dict) and item.get("kind") == "attachment":
                found.append({**item, "current": index == len(users) - 1})
    return found


def load(user, items, *, vision):
    """Read each file again as the user; text decoded, images kept for a model that sees them.

    Only the latest MAX_READABLE text files go into the turn's workspace, so a long
    conversation does not re-read every file it ever had.
    """
    texts = [item["file_id"] for item in items if item["type"] == "text"]
    readable = set(texts[-MAX_READABLE:])
    loaded = []
    for item in items:
        entry = dict(item)
        if item["type"] == "text" and item["file_id"] not in readable:
            entry["dropped"] = True
            loaded.append(entry)
            continue
        try:
            if item["type"] == "text":
                _, _, data = files.read(user, item["file_id"])
                entry["text"] = _decode(data)
            elif item["type"] == "image" and vision and item.get("current"):
                _, _, data = files.read(user, item["file_id"])
                entry["image"], entry["media_type"] = data, IMAGES[suffix(item["name"])]
        except HTTPException:
            # Deleted from the netdisk or no longer shared: the model is told it is unavailable.
            entry["unavailable"] = True
        loaded.append(entry)
    return loaded


def _decode(data):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def note(items, paths, *, vision):
    """What the model is told about the attachments, appended to the user's message."""
    if not items:
        return ""

    def line(item):
        if item.get("unavailable"):
            return f"- {item['name']}：已从网盘删除或不再可读，不能使用"
        if item.get("dropped"):
            return f"- {item['name']}：较早的文本附件，本轮没有放进资料目录；需要时请用户重新上传"
        if item["type"] == "text" and item["file_id"] in paths:
            return (f"- {item['name']}：文本，已放进资料目录 {paths[item['file_id']]}，"
                    "可用 read、grep 阅读，引用格式与资料相同")
        if item["type"] == "image":
            return (f"- {item['name']}：图片，已随消息提供" if vision and item.get("current")
                    else f"- {item['name']}：图片，当前模型不能查看")
        return f"- {item['name']}：{suffix(item['name']).lstrip('.') or '文件'}，你不能直接阅读"

    current = [item for item in items if item.get("current")]
    earlier = [item for item in items if not item.get("current")]
    parts = []
    if current:
        parts.append("[本条消息附带的文件]\n" + "\n".join(line(item) for item in current))
    if earlier:
        parts.append("[本对话之前上传的文件]\n" + "\n".join(line(item) for item in earlier))
    parts.append("启动需要文件的 Workflow 时，平台会按类型自动使用这些文件，不用填写对应字段。")
    return "\n\n" + "\n\n".join(parts)
