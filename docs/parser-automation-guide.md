# Parser 自动发现与契约审批

## 配置边界

Parser 只保留一份人工策略文件：

```text
src/skills/shared/parser-config/command_policy.yaml
```

该文件只决定命令是否进入结构化流程，以及非结构化命令采用
`raw_only` 还是 `raw_hash_diff`。它不再保存厂商、型号、字段、主键或
TextFSM 规则。

## 自动化流程

```text
parser-validation.db
  -> 命令归一化
  -> 设备身份发现
  -> 数据契约推断
  -> TextFSM 生成
  -> 全样本字段/类型/主键验证
  -> pending_approval
  -> 人工审批
  -> active 模板和契约
```

Generator 只允许从测试巡检库的任务发布模板。正式巡检不会调用 LLM。

## PostgreSQL 资产

- `parser_command_aliases`：已确认的命令别名和规范命令。
- `parser_device_profiles`：厂商、精确型号和设备族。
- `parser_identity_signatures`：正式巡检可使用的身份识别规则。
- `parser_templates.validation_json`：模板版本对应的数据契约。

数据契约包含：

```json
{
  "entity_type": "interface_l3",
  "primary_keys": ["interface"],
  "required_fields": ["interface", "status", "protocol"],
  "optional_fields": ["ip_address", "description"],
  "validators": {
    "ip_address": {"type": "ip_or_unassigned"}
  },
  "allow_empty": false,
  "empty_patterns": [],
  "ignore_fields": [],
  "contract_approval_status": "approved"
}
```

## 命令别名

`dis`、`int`、`proc`、`env`、`mem` 等常见缩写由确定性规则展开。
现场别名及输出相似性确认结果存入 `parser_command_aliases`。Runtime
只采用状态为 `confirmed` 的别名。

## 设备身份

身份识别顺序：

1. 调用方显式提供的 vendor/model。
2. PostgreSQL 中已确认的身份签名。
3. version、inventory、manuinfo 输出中的通用厂商指纹和型号候选。

首次发现结果仅用于测试模式。模板审批后，设备档案和可复用身份规则才会
成为正式资产。

## 数据契约

Generator 根据规范命令和样本提出实体、字段、主键及验证器。当前常见网络
语义使用确定性规则；未知语义才请求 LLM 返回 JSON 候选。

候选必须通过：

- 每条记录的 required fields 完整；
- 主键字段非空；
- 同一输出内主键唯一；
- 单实体命令每个样本最多一条记录；
- 全部样本达到发布通过率；
- 字段值满足百分比、IP 或枚举验证器。

审批模板即审批该版本的数据契约。激活后契约不可由运行时修改，修复模板会
产生新版本并重新审批。

## 管理界面

`Skills -> Parser 控制面` 提供：

- 模板和数据契约预览；
- 生成任务；
- 命令别名；
- 设备档案；
- 身份规则。

模板激活时系统会把 `contract_approval_status` 更新为 `approved`，并登记
对应设备档案和身份签名。
