import React from 'react'
import { Alert, Button, Collapse, Pagination, Space, Spin, Typography } from 'antd'
import { useQuery } from 'react-query'
import { patrolReports, type EvidenceLine, type ReportEvidence } from '@netops/services/patrolReports'
import './PatrolCliEvidence.css'

const sides = ['baseline', 'current'] as const
const sideNames = { baseline: '基线 CLI 原文', current: '当前 CLI 原文' }

function EvidenceText({ value }: { value?: EvidenceLine | null }) {
  if (!value) return <span className="patrol-cli-missing">— 无对应原文行</span>
  // Python evidence offsets count Unicode code points, not JavaScript UTF-16 units.
  const chars = Array.from(value.text.trimEnd())
  const spans = [...(value.anomaly_spans || []), ...(value.anomaly_span ? [value.anomaly_span] : [])]
    .filter(([start, end]) => Number.isInteger(start) && Number.isInteger(end) && start >= 0 && end <= chars.length && end > start)
  const bounds = [...new Set([0, chars.length, ...spans.flat()])].sort((a, b) => a - b)
  return <><span className="patrol-cli-line-number">{value.line}</span>{bounds.slice(0, -1).map((start, index) => {
    const end = bounds[index + 1]
    const text = chars.slice(start, end).join('')
    return spans.some(([a, b]) => start >= a && end <= b)
      ? <mark className="patrol-cli-anomaly" title="当前异常值" key={start}>{text}</mark>
      : <React.Fragment key={start}>{text}</React.Fragment>
  })}</>
}

export function PatrolCliComparison({ entry, jobId, digest }: {
  entry: ReportEvidence; jobId: string; digest: string
}) {
  const [showFull, setShowFull] = React.useState(false)
  const full = useQuery(['patrol-cli-full', jobId, digest, entry.lookup_reference, entry.id],
    ({ signal }) => patrolReports.fullEvidence(jobId, entry.id, digest, entry.lookup_reference, signal),
    { enabled: showFull, retry: false, staleTime: 60_000, refetchOnWindowFocus: false })
  const previous = { baseline: 0, current: 0 }
  return <Space direction="vertical" size={10} style={{ width: '100%' }}>
    <Typography.Text strong>{entry.command}</Typography.Text>
    <Typography.Paragraph style={{ marginBottom: 0 }}>{entry.note}</Typography.Paragraph>
    <div className="patrol-cli-scroll" tabIndex={0} role="region" aria-label="基线与当前 CLI 原文对比">
      <table className="patrol-cli-table">
        <thead><tr>{sides.map(side => <th key={side} scope="col">
          <strong>{sideNames[side]}</strong>
          <div>快照：{entry[side]?.snapshot_id || '未提供'}</div>
          <div>时间：{entry[side]?.observed_at ? new Date(entry[side]!.observed_at!).toLocaleString() : '未提供'}</div>
        </th>)}</tr></thead>
        <tbody>{entry.rows.map((row, index) => {
          const gaps = sides.map(side => Boolean(row[side] && previous[side] && row[side]!.line > previous[side] + 1))
          sides.forEach(side => { if (row[side]) previous[side] = row[side]!.line })
          return <React.Fragment key={index}>
            {gaps.some(Boolean) && <tr>{sides.map((side, i) => <td className="patrol-cli-gap" key={side}>{gaps[i] ? '……省略其他原文行……' : ''}</td>)}</tr>}
            <tr>{sides.map(side => <td key={side} className={`patrol-cli-${row.change} patrol-cli-${side}`}>
              <pre><EvidenceText value={row[side]} /></pre>
            </td>)}</tr>
          </React.Fragment>
        })}</tbody>
      </table>
      {!entry.rows.length && <Typography.Paragraph>无法可靠定位对象，请核对完整命令输出。</Typography.Paragraph>}
    </div>
    <Typography.Text type="secondary">红色粗体：当前异常值；橙色：修改；蓝色：新增；删除线：删除。未变化原文保持原色。</Typography.Text>
    <Button size="small" onClick={() => setShowFull(value => !value)}>{showFull ? '收起完整命令输出' : '查看完整命令输出'}</Button>
    {showFull && <>
      {full.isLoading && <Space><Spin /><span>正在读取完整命令输出</span></Space>}
      {full.isError && <Alert type="error" showIcon message="完整原文读取失败；若分析已更新，请重新加载上方对比。"
        action={<Button onClick={() => full.refetch()}>重试完整原文</Button>} />}
      {full.data && <div className="patrol-cli-full">{sides.map(side => <section key={side}>
        <Typography.Text strong>{sideNames[side]} · 完整命令</Typography.Text>
        <pre>{full.data[side] ?? '该周期未保留原文，不能据此判断无变化。'}</pre>
      </section>)}</div>}
    </>}
  </Space>
}

export default function PatrolCliEvidence({ jobId, reference, revision }: {
  jobId: string; reference: string; revision?: string
}) {
  const [page, setPage] = React.useState(1)
  const query = useQuery(['patrol-cli-excerpts', jobId, reference, revision, page],
    ({ signal }) => patrolReports.evidence(jobId, (page - 1) * 10, reference, signal),
    { retry: false, staleTime: 60_000, refetchOnWindowFocus: false })
  return <Space direction="vertical" size={12} style={{ width: '100%' }}>
    <Space wrap><Typography.Text strong>基线与当前 CLI 原文对比</Typography.Text>
      <Button size="small" loading={query.isFetching} onClick={() => query.refetch()}>重新加载对比</Button></Space>
    {query.isLoading && <Space><Spin /><span>正在读取该对象的 CLI 摘录</span></Space>}
    {query.isError && <Alert type="error" showIcon message="CLI 对比加载失败，请重试；技术追溯信息仍保留。" />}
    {!query.isFetching && query.data?.total === 0 && <Alert type="info" showIcon message="尚无该条结果的可靠原文摘录，不代表没有变化。" />}
    {query.data?.items.map(entry => <PatrolCliComparison key={`${query.data.source_digest}-${entry.id}`}
      entry={entry} jobId={jobId} digest={query.data.source_digest} />)}
    {(query.data?.total || 0) > 10 && <Pagination current={page} pageSize={10} total={query.data?.total}
      showSizeChanger={false} onChange={setPage} />}
  </Space>
}

export function LazyPatrolCliEvidence(props: React.ComponentProps<typeof PatrolCliEvidence>) {
  return <Collapse items={[{ key: 'cli', label: '查看基线与当前 CLI 原文对比', children: <PatrolCliEvidence {...props} /> }]} />
}
