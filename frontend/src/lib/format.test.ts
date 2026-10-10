import { describe, expect, it } from 'vitest'
import { duration, elapsed, eta, hostname, modelName, tokens } from './format'

describe('duration', () => {
  it('shows a dash when there is nothing measured yet', () => {
    expect(duration(null)).toBe('-')
    expect(duration(undefined)).toBe('-')
  })

  it('keeps sub-second values in milliseconds', () => {
    expect(duration(0)).toBe('0ms')
    expect(duration(999)).toBe('999ms')
  })

  it('uses tenths of a second under a minute', () => {
    expect(duration(1000)).toBe('1.0s')
    expect(duration(12_345)).toBe('12.3s')
    expect(duration(59_940)).toBe('59.9s')
  })

  it('switches to minutes and seconds from one minute', () => {
    expect(duration(60_000)).toBe('1m 0s')
    expect(duration(125_400)).toBe('2m 5s')
  })

  it('never shows 60 in the seconds slot when rounding carries over', () => {
    expect(duration(59_960)).toBe('1m 0s')
    expect(duration(119_600)).toBe('2m 0s')
  })
})

describe('elapsed', () => {
  it('rounds to whole seconds and clamps negatives to zero', () => {
    expect(elapsed(0)).toBe('0s')
    expect(elapsed(-5000)).toBe('0s')
    expect(elapsed(59_499)).toBe('59s')
  })

  it('adds minutes once a minute has passed', () => {
    expect(elapsed(59_500)).toBe('1m 0s')
    expect(elapsed(61_000)).toBe('1m 1s')
    expect(elapsed(605_000)).toBe('10m 5s')
  })
})

describe('eta', () => {
  it('says nothing when there is no estimate', () => {
    expect(eta(null)).toBeNull()
    expect(eta(0)).toBeNull()
    expect(eta(-1)).toBeNull()
  })

  it('phrases short waits loosely', () => {
    expect(eta(30_000)).toBe('less than a minute left')
    expect(eta(45_000)).toBe('less than a minute left')
    expect(eta(46_000)).toBe('about a minute left')
    expect(eta(89_999)).toBe('about a minute left')
  })

  it('counts minutes for longer waits', () => {
    expect(eta(90_000)).toBe('about 2 minutes left')
    expect(eta(300_000)).toBe('about 5 minutes left')
  })
})

describe('modelName', () => {
  it('strips the vendor prefix', () => {
    expect(modelName('meta/llama-3.1-8b-instruct')).toBe('llama-3.1-8b-instruct')
    expect(modelName('org/team/model-x')).toBe('model-x')
  })

  it('passes bare names through and blanks a missing model', () => {
    expect(modelName('mistral-7b')).toBe('mistral-7b')
    expect(modelName(null)).toBe('')
  })
})

describe('tokens', () => {
  it('shows a dash for missing or zero counts', () => {
    expect(tokens(null)).toBe('-')
    expect(tokens(undefined)).toBe('-')
    expect(tokens(0)).toBe('-')
  })

  it('abbreviates thousands to one decimal', () => {
    expect(tokens(999)).toBe('999')
    expect(tokens(1000)).toBe('1.0k')
    expect(tokens(12_345)).toBe('12.3k')
  })
})

describe('hostname', () => {
  it('reduces a URL to its host without www', () => {
    expect(hostname('https://www.acme.com/about?x=1')).toBe('acme.com')
    expect(hostname('http://shop.acme.co.uk')).toBe('shop.acme.co.uk')
  })

  it('returns the input unchanged when it is not a URL', () => {
    expect(hostname('acme')).toBe('acme')
  })
})
