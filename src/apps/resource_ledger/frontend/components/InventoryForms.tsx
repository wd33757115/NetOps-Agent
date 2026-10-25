import React, { useMemo } from 'react'
import { Button, Form, Input, Radio, Select, message } from 'antd'
import { DeleteOutlined, PlusOutlined, ThunderboltOutlined } from '@ant-design/icons'
import {
  ENVIRONMENTS, HOST_KINDS, HOST_ROLES, HOST_STATES, IMPORTANCE, PROTOCOLS, errorText, ledger,
  type HostRow, type Overview, type SystemRow,
} from '../api'
import { FormModal, useSubmit } from './Forms'

type Done = () => void
const Row = ({ columns = '1fr 1fr', children }: { columns?: string; children: React.ReactNode }) =>
  <div className="rl-form-row" style={{ gridTemplateColumns: columns }}>{children}</div>

type SystemValues = Omit<SystemRow, 'id' | 'hosts' | 'blocks' | 'subsystems'>

export function SystemModal({ open, system, onClose, onSaved }: { open: boolean; system?: SystemRow | null; onClose: Done; onSaved: Done }) {
  const { busy, submit } = useSubmit<SystemValues>(values => ledger.save('systems', values, system?.id), system ? '业务系统已保存' : '业务系统已新增', onSaved)
  const initial = useMemo(() => system || { importance: '一般' }, [system])
  return <FormModal<SystemValues> open={open} title={system ? `编辑业务系统 ${system.name}` : '新增业务系统'} okText="保存" busy={busy}
    onClose={onClose} onSubmit={submit} initial={initial as Partial<SystemValues>} width={620}>
    <Row columns="1.6fr 1fr">
      <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={64} placeholder="风险评估系统" /></Form.Item>
      <Form.Item name="short_name" label="简称"><Input maxLength={32} placeholder="风评" /></Form.Item>
    </Row>
    <Form.Item name="importance" label="重要性"><Radio.Group optionType="button" options={IMPORTANCE.map(value => ({ value, label: value }))} /></Form.Item>
    <Row>
      <Form.Item name="department" label="主管部门"><Input maxLength={64} /></Form.Item>
      <Form.Item name="contact" label="联系方式"><Input maxLength={64} /></Form.Item>
    </Row>
    <Row>
      <Form.Item name="business_owner" label="业务负责人"><Input maxLength={64} /></Form.Item>
      <Form.Item name="ops_owner" label="运维负责人"><Input maxLength={64} /></Form.Item>
    </Row>
    <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
  </FormModal>
}

type SubsystemValues = { system_id: string; name: string; owner?: string; note?: string }

export function SubsystemModal({ open, systemId, subsystem, overview, onClose, onSaved }: {
  open: boolean; systemId?: string; subsystem?: { id: string; name: string; owner: string | null; note: string | null } | null
  overview?: Overview; onClose: Done; onSaved: Done
}) {
  const { busy, submit } = useSubmit<SubsystemValues>(values => ledger.save('subsystems', values, subsystem?.id),
    subsystem ? '子系统已保存' : '子系统已新增', onSaved)
  const initial = useMemo(() => ({ system_id: systemId, name: subsystem?.name, owner: subsystem?.owner || undefined, note: subsystem?.note || undefined }), [systemId, subsystem])
  return <FormModal<SubsystemValues> open={open} title={subsystem ? `编辑子系统 ${subsystem.name}` : '新增子系统'} okText="保存" busy={busy}
    onClose={onClose} onSubmit={submit} initial={initial}>
    <Form.Item name="system_id" label="业务系统" rules={[{ required: true }]}>
      <Select showSearch optionFilterProp="label" options={(overview?.systems || []).map(s => ({ value: s.id, label: s.name }))} />
    </Form.Item>
    <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}><Input maxLength={64} placeholder="风险评估子系统1" /></Form.Item>
    <Form.Item name="owner" label="负责人"><Input maxLength={64} /></Form.Item>
    <Form.Item name="note" label="备注"><Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} /></Form.Item>
  </FormModal>
}

type ServiceValues = { protocol: string; ports?: string; name?: string; note?: string }
type HostValues = {
  hostname: string; system_id: string; subsystem_id?: string; environment: string; status: string; kind: string; role: string
  os?: string; owner?: string; online_date?: string; offline_date?: string; tags?: string; note?: string
  ips: string[]; services: ServiceValues[]
}

/** A host with its addresses and service ports; new addresses are allocated from its system's blocks when saved. */
export function HostModal({ open, host, overview, defaults, onClose, onSaved }: {
  open: boolean; host?: HostRow | null; overview?: Overview; defaults?: Partial<HostValues>; onClose: Done; onSaved: Done
}) {
  // A port row left blank is an unused row, not a service.
  const { busy, submit } = useSubmit<HostValues>(values => ledger.saveHost({
    ...values, ips: values.ips || [],
    services: (values.services || []).filter(s => s.protocol === 'ICMP' || s.ports).map(s => (s.protocol === 'ICMP' ? { ...s, ports: undefined } : s)),
  }, host?.id), host ? '主机已保存' : '主机已新增', onSaved)
  const initial = useMemo<Partial<HostValues>>(() => host ? {
    hostname: host.hostname, system_id: host.system.id, subsystem_id: host.subsystem?.id, environment: host.environment, status: host.status,
    kind: host.kind, role: host.role, os: host.os || undefined, owner: host.owner || undefined, online_date: host.online_date || undefined,
    offline_date: host.offline_date || undefined, tags: host.tags || undefined, note: host.note || undefined,
    ips: host.ips.map(ip => ip.ip), services: host.services.map(s => ({ protocol: s.protocol, ports: s.ports || undefined, name: s.name || undefined, note: s.note || undefined })),
  } : { environment: '生产', status: '已投产', kind: '虚拟机', role: '应用', ips: [], services: [{ protocol: 'TCP' }], ...defaults }, [host, defaults])
  return <FormModal<HostValues> open={open} title={host ? `编辑主机 ${host.hostname}` : '新增主机'} okText="保存" busy={busy}
    onClose={onClose} onSubmit={submit} initial={initial} width={760}
    subtitle="主机的 IP 只能来自所属业务系统（子系统）的网段：已分配的地址会关联过来，新地址保存时一并分配。">
    {form => <>
      <Row columns="1.3fr 1fr 1fr">
        <Form.Item name="hostname" label="主机名" rules={[{ required: true, message: '请填写主机名' }]}><Input maxLength={128} placeholder="risk-app-01" /></Form.Item>
        <Form.Item name="system_id" label="业务系统" rules={[{ required: true, message: '请选择业务系统' }]}>
          <Select showSearch optionFilterProp="label" options={(overview?.systems || []).map(s => ({ value: s.id, label: s.name }))}
            onChange={() => form.setFieldValue('subsystem_id', undefined)} />
        </Form.Item>
        <Form.Item noStyle shouldUpdate={(a, b) => a.system_id !== b.system_id}>
          {() => <Form.Item name="subsystem_id" label="子系统">
            <Select allowClear placeholder="不属于子系统" options={(overview?.subsystems || [])
              .filter(s => s.system_id === form.getFieldValue('system_id')).map(s => ({ value: s.id, label: s.name }))} />
          </Form.Item>}
        </Form.Item>
      </Row>
      <Row columns="1fr 1fr 1fr 1fr">
        <Form.Item name="status" label="状态"><Select options={HOST_STATES.map(value => ({ value }))} /></Form.Item>
        <Form.Item name="environment" label="环境"><Select options={ENVIRONMENTS.map(value => ({ value }))} /></Form.Item>
        <Form.Item name="role" label="角色"><Select options={HOST_ROLES.map(value => ({ value }))} /></Form.Item>
        <Form.Item name="kind" label="类型"><Select options={HOST_KINDS.map(value => ({ value }))} /></Form.Item>
      </Row>
      <Form.Item label="IP 地址" required={false}>
        <div className="rl-ip-field">
          <Form.Item name="ips" noStyle><Select mode="tags" open={false} tokenSeparators={[',', ' ', '，', '、']} suffixIcon={null}
            placeholder="输入地址后回车，可填多个" /></Form.Item>
          <Button icon={<ThunderboltOutlined aria-hidden="true" />} onClick={async () => {
            const systemId = form.getFieldValue('system_id')
            if (!systemId) { message.info('先选择业务系统'); return }
            try {
              const taken: string[] = form.getFieldValue('ips') || []
              const next = await ledger.suggestIp({ system_id: systemId, subsystem_id: form.getFieldValue('subsystem_id'), taken })
              form.setFieldValue('ips', [...taken, next.ip])
              message.success(`从 ${next.prefix.network} 取了 ${next.ip}，保存后分配`)
            } catch (error) { message.error(errorText(error)) }
          }}>取下一个空闲地址</Button>
        </div>
      </Form.Item>
      <div className="rl-form-label">服务端口</div>
      <Form.List name="services">{(fields, { add, remove }) => <div className="rl-services">
        {fields.map(field => <div className="rl-service-row" key={field.key}>
          <Form.Item name={[field.name, 'protocol']} noStyle><Select options={PROTOCOLS.map(value => ({ value }))} /></Form.Item>
          <Form.Item noStyle shouldUpdate>
            {() => <Form.Item name={[field.name, 'ports']} noStyle rules={[{ pattern: /^\d{1,5}(-\d{1,5})?$/, message: '写成 80 或 8000-8010' }]}>
              <Input placeholder={form.getFieldValue(['services', field.name, 'protocol']) === 'ICMP' ? '不需要端口' : '80 或 8000-8010'}
                disabled={form.getFieldValue(['services', field.name, 'protocol']) === 'ICMP'} aria-label="端口" />
            </Form.Item>}
          </Form.Item>
          <Form.Item name={[field.name, 'name']} noStyle><Input maxLength={64} placeholder="服务，如 nginx" aria-label="服务名" /></Form.Item>
          <Button type="text" danger aria-label="删除端口" icon={<DeleteOutlined aria-hidden="true" />} onClick={() => remove(field.name)} />
        </div>)}
        <Button type="dashed" icon={<PlusOutlined aria-hidden="true" />} onClick={() => add({ protocol: 'TCP' })}>添加端口</Button>
      </div>}</Form.List>
      <Row columns="1fr 1fr 1fr 1fr">
        <Form.Item name="os" label="操作系统"><Input maxLength={64} placeholder="CentOS 7.9" /></Form.Item>
        <Form.Item name="owner" label="负责人"><Input maxLength={64} /></Form.Item>
        <Form.Item name="online_date" label="上线日期"><Input type="date" /></Form.Item>
        <Form.Item name="offline_date" label="下线日期"><Input type="date" /></Form.Item>
      </Row>
      <Row columns="1fr 2fr">
        <Form.Item name="tags" label="标签"><Input maxLength={200} placeholder="多个用逗号分隔" /></Form.Item>
        <Form.Item name="note" label="备注"><Input maxLength={500} /></Form.Item>
      </Row>
    </>}
  </FormModal>
}
