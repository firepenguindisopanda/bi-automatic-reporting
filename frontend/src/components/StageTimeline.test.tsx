import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeStage } from '../test/fixtures'
import { StageTimeline } from './StageTimeline'

const stages = [
  makeStage({
    stage: 'scrape',
    label: 'Reading the website',
    seq: 1,
    status: 'succeeded',
    duration_ms: 4200,
    completeness: 92,
  }),
  makeStage({
    stage: 'profile',
    label: 'Profiling the business',
    seq: 2,
    status: 'failed',
    duration_ms: 61_000,
    completeness: 40,
    model: 'meta/llama-3.1-8b-instruct',
    attempts: 2,
    error: 'The model returned no usable JSON.',
    error_kind: 'parse',
  }),
  makeStage({ stage: 'market', label: 'Sizing the market', seq: 3, status: 'running' }),
  makeStage({ stage: 'competitors', label: 'Mapping competitors', seq: 4, status: 'skipped' }),
  makeStage({ stage: 'write', label: 'Writing the report', seq: 5, status: 'pending' }),
]

/** The list item for a stage, found by its label. */
function rowFor(label: string): HTMLElement {
  const item = screen.getByText(label).closest('li')
  if (!item) throw new Error(`No timeline row for ${label}`)
  return item
}

describe('StageTimeline', () => {
  it('renders every stage, in order', () => {
    render(<StageTimeline stages={stages} />)

    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(stages.length)
    stages.forEach((stage, i) => expect(rows[i]?.textContent).toContain(stage.label))
  })

  it('marks each stage with its status', () => {
    render(<StageTimeline stages={stages} />)

    expect(within(rowFor('Reading the website')).getByText('✓')).toBeTruthy()
    expect(within(rowFor('Profiling the business')).getByText('✕')).toBeTruthy()
    expect(
      within(rowFor('Sizing the market')).getByRole('status', { name: 'In progress' }),
    ).toBeTruthy()
    expect(within(rowFor('Mapping competitors')).getByText('(skipped)')).toBeTruthy()
    expect(within(rowFor('Writing the report')).getByText('·')).toBeTruthy()
  })

  it('shows durations, with a placeholder while running and a dash before starting', () => {
    render(<StageTimeline stages={stages} />)

    expect(within(rowFor('Reading the website')).getByText('4.2s')).toBeTruthy()
    expect(within(rowFor('Profiling the business')).getByText('1m 1s')).toBeTruthy()
    expect(within(rowFor('Sizing the market')).getByText('···')).toBeTruthy()
    expect(within(rowFor('Writing the report')).getByText('-')).toBeTruthy()
  })

  it('shows the completeness score only for stages that succeeded', () => {
    render(<StageTimeline stages={stages} />)

    expect(
      within(rowFor('Reading the website')).getByLabelText('Completeness 92 of 100'),
    ).toBeTruthy()
    expect(
      within(rowFor('Profiling the business')).queryByLabelText(/Completeness/),
    ).toBeNull()
  })

  it('reveals the error, model and attempts of a failed stage on demand', async () => {
    const user = userEvent.setup()
    render(<StageTimeline stages={stages} />)
    const row = rowFor('Profiling the business')
    const toggle = within(row).getByRole('button')

    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(within(row).queryByText('The model returned no usable JSON.')).toBeNull()
    expect(within(row).getByText('×2')).toBeTruthy()

    await user.click(toggle)

    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(within(row).getByText('Error')).toBeTruthy()
    expect(within(row).getByText('The model returned no usable JSON.')).toBeTruthy()
    expect(within(row).getByText('parse')).toBeTruthy()
    expect(within(row).getByText('llama-3.1-8b-instruct')).toBeTruthy()
    expect(within(row).getByText('Completeness')).toBeTruthy()
    expect(within(row).getByText('40/100')).toBeTruthy()

    await user.click(toggle)
    expect(within(row).queryByText('The model returned no usable JSON.')).toBeNull()
  })

  it('shows reviewer scores, notes and token counts in the detail', async () => {
    const user = userEvent.setup()
    render(
      <StageTimeline
        stages={[
          makeStage({
            label: 'Reading the website',
            status: 'succeeded',
            completeness: 88,
            confidence: 71.6,
            critic_note: 'Pricing is inferred, not quoted.',
            prompt_tokens: 2400,
            completion_tokens: 512,
            warnings: ['Only 3 pages could be read.'],
          }),
        ]}
      />,
    )

    await user.click(screen.getByRole('button'))

    expect(screen.getByText('72/100')).toBeTruthy()
    expect(screen.getByText('Pricing is inferred, not quoted.')).toBeTruthy()
    expect(screen.getByText('2.4k in · 512 out')).toBeTruthy()
    expect(screen.getByText('Only 3 pages could be read.')).toBeTruthy()
  })

  it('offers nothing to expand for a stage that has not run', () => {
    render(<StageTimeline stages={[makeStage({ status: 'pending', model: 'meta/x' })]} />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
