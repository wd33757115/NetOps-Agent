export type AgentModuleStatus = 'active' | 'building' | 'planned'

export interface AgentModuleDefinition {
  id: string
  phase: string
  moduleId: string
  title: string
  owner: string
  status: AgentModuleStatus
  scope: string
  inputs: string[]
  outputs: string[]
  dependencies: string[]
  controlPlane: string
  healthSignal: string
  healthChecks: AgentModuleHealthCheck[]
}

export interface AgentModuleHealthCheck {
  name: string
  source: string
  target: string
}

export const agentModuleDefinitions: AgentModuleDefinition[] = [
  {
    id: 'parser-control',
    phase: 'Foundation',
    moduleId: 'parser-control',
    title: 'Parser 控制面',
    owner: 'Parser Runtime / textfsm-generator',
    status: 'active',
    scope: '模板资产、任务队列、验证数据集、命令策略、设备身份规则',
    inputs: ['测试巡检数据集', '命令白名单', '设备身份证据'],
    outputs: ['激活模板资产', '模板生成任务', 'Parser 覆盖率'],
    dependencies: ['PostgreSQL', '共享 Parser 配置', 'textfsm-generator'],
    controlPlane: '设备管理 / Parser 控制面',
    healthSignal: '模板覆盖率、缺模板任务、解析失败任务',
    healthChecks: [
      { name: '模板覆盖率', source: 'Parser 覆盖率 API', target: '生产 Profile 无阻塞命令' },
      { name: '任务队列', source: '模板任务队列', target: '失败任务可定位且可重试' },
      { name: '模板资产', source: '模板资产表', target: '已激活模板哈希校验通过' },
    ],
  },
  {
    id: 'state-normalizer',
    phase: 'Phase 1',
    moduleId: 'state-normalizer',
    title: '状态事实归一化',
    owner: 'src/core/state + state-normalizer skill',
    status: 'active',
    scope: '把结构化解析结果转换为字段级 StateObservation / CurrentState',
    inputs: ['结构化快照', '模板数据契约'],
    outputs: ['StateObservation', 'CurrentState'],
    dependencies: ['Parser 控制面', 'PostgreSQL 快照库'],
    controlPlane: '后续进入状态覆盖率页',
    healthSignal: 'Observation 写入数、主键冲突数、CurrentState 更新数',
    healthChecks: [
      { name: 'Observation 幂等', source: 'state-normalizer 运行记录', target: '重复运行不新增重复状态点' },
      { name: '主键约束', source: '模板数据契约', target: '主键缺失或重复明确失败' },
      { name: '生产隔离', source: '快照模式', target: '验证数据不更新 CurrentState' },
    ],
  },
  {
    id: 'change-detector',
    phase: 'Phase 2',
    moduleId: 'change-detector',
    title: '状态变化检测',
    owner: 'change-detector skill',
    status: 'active',
    scope: '基于 StateObservation 生成可追溯 NetworkChange',
    inputs: ['previous/current StateObservation', 'CurrentState'],
    outputs: ['NetworkChange'],
    dependencies: ['state-normalizer'],
    controlPlane: '后续进入变化流页',
    healthSignal: '变化数、无变化比例、重复重放次数',
    healthChecks: [
      { name: '状态对比', source: 'StateObservation', target: '接口/CPU/BGP 变化可追溯' },
      { name: '重放幂等', source: 'change-detector 运行记录', target: '同一运行重放不重复生成 Change' },
      { name: '证据引用', source: 'NetworkChange', target: '可追溯到快照、模板和观测项' },
    ],
  },
  {
    id: 'event-builder',
    phase: 'Phase 3',
    moduleId: 'event-builder',
    title: '事件语义构建',
    owner: 'event-builder skill + event rules',
    status: 'active',
    scope: '把 NetworkChange 转换为可审计、可版本化的 NetworkEvent',
    inputs: ['NetworkChange', '已激活事件规则'],
    outputs: ['NetworkEvent', 'change.event_status'],
    dependencies: ['change-detector', '事件规则注册表'],
    controlPlane: 'Parser 控制面 / 事件规则',
    healthSignal: '事件生成数、无事件数、规则命中率',
    healthChecks: [
      { name: '规则命中', source: '已激活事件规则', target: 'CPUHigh/InterfaceDown/BGP Lost 可生成事件' },
      { name: '小幅变化抑制', source: 'NetworkChange', target: 'CPU 小幅波动不生成 Event' },
      { name: '事件幂等', source: 'event-builder 运行记录', target: '同一 Change 重放不重复生成 Event' },
    ],
  },
  {
    id: 'device-management',
    phase: 'Phase 4',
    moduleId: 'device-management',
    title: '设备资产控制面',
    owner: '设备管理',
    status: 'building',
    scope: '设备资产、凭据引用、动态分组、连接健康、执行计划入口',
    inputs: ['设备资产导入', '凭据引用', '动态选择器'],
    outputs: ['设备资产', '执行计划预览', '巡检批次上下文'],
    dependencies: ['凭据解析器', '采集策略解析器', 'Workflow'],
    controlPlane: '设备管理 / 总览与设备资产',
    healthSignal: '连接健康、巡检就绪、阻塞原因、批次运行状态',
    healthChecks: [
      { name: '资产可见', source: '设备 API', target: '设备列表、筛选、详情稳定可用' },
      { name: '凭据安全', source: '凭据解析器', target: '前端和资产查询不返回密码' },
      { name: '巡检预检', source: '执行计划预览', target: '连接/模板/Profile 阻塞可定位' },
    ],
  },
  {
    id: 'collection-profile',
    phase: 'Phase 5',
    moduleId: 'collection-profile',
    title: '采集策略版本化',
    owner: 'Collection Policy / Command Profile',
    status: 'building',
    scope: '命令 Profile 版本、绑定优先级、Parser Coverage Preflight',
    inputs: ['命令 Profile 草稿', 'Profile 绑定选择器'],
    outputs: ['执行计划', 'profile_version_id', 'collection_item_id'],
    dependencies: ['设备管理', 'Parser 控制面'],
    controlPlane: 'Parser 控制面 / 采集 Profile',
    healthSignal: '影响设备数、覆盖率、阻塞命令数',
    healthChecks: [
      { name: '发布预检', source: 'Profile 影响预览', target: '发布前展示影响设备和覆盖率' },
      { name: '策略追溯', source: '执行计划', target: '快照携带 profile_version_id/collection_item_id' },
      { name: '非白名单隔离', source: 'ParserPolicy', target: '非白名单命令不进入模板队列' },
    ],
  },
  {
    id: 'report-diff',
    phase: 'Phase 6',
    moduleId: 'patrol-diff / report-generator',
    title: '报告与人类差异拆分',
    owner: 'patrol-diff + report-generator skills',
    status: 'building',
    scope: '把人类报告、基线差异和系统变化事实从 device-patrol 解耦',
    inputs: ['快照运行', 'NetworkChange', 'NetworkEvent'],
    outputs: ['Markdown/HTML/XLSX 报告', '人工差异'],
    dependencies: ['device-patrol', 'change-detector', 'event-builder'],
    controlPlane: '后续进入报告中心',
    healthSignal: '报告生成成功率、历史运行重放成功率、归档链接',
    healthChecks: [
      { name: '执行解耦', source: 'device-patrol', target: '关闭报告不影响巡检入库' },
      { name: '历史重放', source: 'report-generator', target: '可从历史运行重新生成报告' },
      { name: '事件独立', source: 'change/event pipeline', target: 'Change/Event 不依赖 Excel 报告' },
    ],
  },
]

export const agentModuleStatusMeta: Record<AgentModuleStatus, { color: string; text: string }> = {
  active: { color: 'green', text: '已接入' },
  building: { color: 'blue', text: '建设中' },
  planned: { color: 'default', text: '规划中' },
}
