import React, { useEffect } from 'react'
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  PlayCircleOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery } from 'react-query'
import {
  parserApi,
  type RuleAssetAutomationMode,
  type RuleAssetAutomationSettings,
} from '../../../services/api'

interface Props {
  onChanged?: () => void
}

const modeLabels: Record<RuleAssetAutomationMode, string> = {
  disabled: '人工触发',
  generate_only: '自动准备候选',
}

const RuleAssetAutomationControl: React.FC<Props> = ({ onChanged }) => {
  const [open, setOpen] = React.useState(false)
  const [form] = Form.useForm<RuleAssetAutomationSettings & { reason: string }>()
  const query = useQuery(
    ['rule-asset-automation-control'],
    () => parserApi.ruleAssetAutomationControl(),
    { refetchInterval: 15000 },
  )
  const settings = query.data?.settings
  const save = useMutation(
    (values: RuleAssetAutomationSettings & { reason: string }) => (
      parserApi.updateRuleAssetAutomationControl(values)
    ),
    {
      onSuccess: () => {
        message.success('规则资产自动化策略已更新')
        setOpen(false)
        void query.refetch()
        onChanged?.()
      },
      onError: (error: any) => {
        message.error(error?.response?.data?.detail || error?.message || '自动化策略更新失败')
      },
    },
  )
  const runNow = useMutation(
    () => parserApi.runRuleAssetAutomation(),
    {
      onSuccess: (result) => {
        message.success(
          `调度完成：启动 ${result.dispatched_count}，待人工复核 ${result.review_required_count}`,
        )
        void query.refetch()
        onChanged?.()
      },
      onError: (error: any) => {
        message.error(error?.response?.data?.detail || error?.message || '自动调度执行失败')
      },
    },
  )

  useEffect(() => {
    if (!open || !settings) return
    form.setFieldsValue({
      ...settings,
      reason: '',
    })
  }, [form, open, settings])

  const scheduler = query.data?.scheduler
  const mode = settings?.mode || 'disabled'
  const schedulerHealthy = scheduler?.status === 'ok'
  const lastRun = settings?.last_run

  return (
    <>
      <div className="rule-asset-automation">
        <div className="rule-asset-automation__summary">
          <Space size={8} wrap>
            <Typography.Text strong>候选生产自动化</Typography.Text>
            <Tag className={`rule-asset-automation__mode is-${mode}`}>
              {modeLabels[mode]}
            </Tag>
            <Tag
              className={`rule-asset-scheduler-status ${schedulerHealthy ? 'is-online' : 'is-offline'}`}
            >
              调度器{schedulerHealthy ? '在线' : '异常'}
            </Tag>
            <Typography.Text type="secondary">
              活跃生成 {query.data?.active_generation_runs || 0}
            </Typography.Text>
          </Space>
          <Typography.Text type="secondary">
            {lastRun?.ran_at
              ? `最近运行：启动 ${lastRun.dispatched_count || 0}，待人工复核 ${lastRun.review_required_count || 0}`
              : '尚无自动调度记录'}
          </Typography.Text>
        </div>
        <Space>
          <Button
            icon={<PlayCircleOutlined />}
            disabled={mode === 'disabled'}
            loading={runNow.isLoading}
            onClick={() => runNow.mutate()}
          >
            立即运行
          </Button>
          <Button icon={<SettingOutlined />} onClick={() => setOpen(true)}>
            自动化设置
          </Button>
        </Space>
      </div>

      <Modal
        title="规则资产候选生产自动化"
        open={open}
        width={640}
        okText="保存策略"
        cancelText="取消"
        confirmLoading={save.isLoading}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => save.mutate(values)}
        >
          <Form.Item name="mode" label="运行模式" rules={[{ required: true }]}>
            <Segmented
              block
              options={[
                { value: 'disabled', label: '人工触发' },
                { value: 'generate_only', label: '自动准备候选' },
              ]}
            />
          </Form.Item>
          <Typography.Text type="secondary">
            历史回放门槛仅适用于需要生成变化或事件资产的检查项；仅需 Contract 的证据型检查项不受这些数量限制。
          </Typography.Text>
          <div className="rule-asset-automation__grid">
            <Form.Item name="max_concurrency" label="最大并行任务">
              <InputNumber min={1} max={10} />
            </Form.Item>
            <Form.Item name="max_generation_attempts" label="最大生成尝试">
              <InputNumber min={1} max={5} />
            </Form.Item>
            <Form.Item name="stale_run_seconds" label="运行超时（秒）">
              <InputNumber min={900} max={86400} />
            </Form.Item>
            <Form.Item name="min_period_count" label="历史回放最少周期">
              <InputNumber min={2} max={10} />
            </Form.Item>
            <Form.Item name="min_device_count" label="历史回放最少设备">
              <InputNumber min={1} max={10000} />
            </Form.Item>
            <Form.Item name="min_record_count" label="历史回放最少记录">
              <InputNumber min={1} max={500} />
            </Form.Item>
            <Form.Item name="max_estimated_daily_events" label="历史回放每日事件上限">
              <InputNumber min={1} max={1000000} />
            </Form.Item>
          </div>
          <Form.Item
            name="reason"
            label="变更原因"
            rules={[
              { required: true, message: '请输入可审计的变更原因' },
              { min: 5, message: '变更原因至少 5 个字符' },
            ]}
          >
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
          <Form.Item name="retry_delay_seconds" hidden>
            <InputNumber />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

export default RuleAssetAutomationControl
