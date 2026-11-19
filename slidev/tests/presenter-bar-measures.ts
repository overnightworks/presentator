// What the presenter bar paints and where: boxes, colours and contrast as the
// browser measures them, for the overlay's layout and readability checks.
import type { ElementHandle, Locator, Page } from 'playwright-chromium'
import { expect } from 'vitest'

export function boxesIntersect(first: { x: number, y: number, width: number, height: number }, second: { x: number, y: number, width: number, height: number }) {
  return first.x < second.x + second.width
    && second.x < first.x + first.width
    && first.y < second.y + second.height
    && second.y < first.y + first.height
}

export async function readSameFrameBoxes(page: Page, homeHandle: ElementHandle<Element>, aiHandle: ElementHandle<Element>) {
  return page.evaluate(([home, ai]) => {
    const rectOf = (element: Element) => {
      const { x, y, width, height } = element.getBoundingClientRect()
      return { x, y, width, height }
    }
    return {
      homeBox: rectOf(home),
      aiBox: rectOf(ai),
      barBox: rectOf(home.parentElement!),
      sectionsBox: rectOf(document.querySelector('.slidev-presenter .grid-container')!),
    }
  }, [homeHandle, aiHandle] as const)
}
// The switch's track and the fault word as the browser paints them, so a test
// judges colours by their channels and never by a copied literal.
export async function readPaintedColours(page: Page) {
  const colours = await page.evaluate(() => ({
    track: getComputedStyle(document.querySelector('.copresenter .track')!).backgroundColor,
    barText: getComputedStyle(document.querySelector('.copresenter .label')!).color,
    fault: document.querySelector('.copresenter .fault')
      ? getComputedStyle(document.querySelector('.copresenter .fault')!).color
      : null,
  }))
  const channels = (color: string) => color.match(/[\d.]+/g)!.slice(0, 3).map(Number) as [number, number, number]
  return { track: channels(colours.track), barText: channels(colours.barText), fault: colours.fault === null ? null : channels(colours.fault) }
}

type Channels = [number, number, number]

// WCAG 2.x contrast ratio, with a foreground painted at `opacity` over the
// background.
export function contrastRatio(foreground: Channels, background: Channels, opacity = 1) {
  const luminance = (channels: Channels) => {
    const [red, green, blue] = channels.map((channel) => {
      const unit = channel / 255
      return unit <= 0.04045 ? unit / 12.92 : ((unit + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * red! + 0.7152 * green! + 0.0722 * blue!
  }
  const seen = foreground.map((channel, index) => channel * opacity + background[index]! * (1 - opacity)) as Channels
  const [lighter, darker] = [luminance(seen), luminance(background)].sort((first, second) => second - first)
  return (lighter! + 0.05) / (darker! + 0.05)
}

// What the presenter bar is painted on: the first opaque background up its
// ancestors.
export async function readBarBackground(page: Page): Promise<Channels> {
  const painted = await page.evaluate(() => {
    for (let element: Element | null = document.querySelector('.presenter-bar'); element; element = element.parentElement) {
      const colour = getComputedStyle(element).backgroundColor
      if (!/^rgba?\(.*, 0\)$/.test(colour) && colour !== 'transparent') return colour
    }
    return 'rgb(255, 255, 255)'
  })
  return painted.match(/[\d.]+/g)!.slice(0, 3).map(Number) as Channels
}

// The open heard panel against every interactive element Slidev draws outside
// the presenter bar: the panel covers none of them, and the list it was
// compared with holds Slidev's own controls (never an empty comparison).
export async function expectPanelCoversNoSlidevControl(page: Page) {
  const { panel, controls } = await page.evaluate(() => {
    const bar = document.querySelector('.presenter-bar')!
    const rectOf = (element: Element) => {
      const { x, y, width, height } = element.getBoundingClientRect()
      return { x, y, width, height }
    }
    const panelElement = bar.querySelector('.panel')!
    const outsideBar = [...document.querySelectorAll('button, a, [role]')]
      .filter((element) => !bar.contains(element) && !element.contains(bar))
      .filter((element) => element.checkVisibility({ visibilityProperty: true }))
      .map((element) => ({ name: `${element.tagName.toLowerCase()} "${(element.textContent ?? '').trim().slice(0, 30)}"`, box: rectOf(element) }))
      .filter(({ box }) => box.width > 0 && box.height > 0)
    return { panel: rectOf(panelElement), controls: outsideBar }
  })
  expect(controls.map(({ name }) => name).join(' | '), 'Slidev\'s own controls are among those compared').toContain('Screen Mirror')
  expect(controls.filter(({ box }) => boxesIntersect(panel, box)).map(({ name }) => name)).toEqual([])
}

// The fault word stands whole in the window, clear of the switch and, once a `/who` has answered and Home shows, of Home
// (whatever language it is named in).
export async function expectFaultWordClearOfControls(page: Page, width: number, aiSwitch: Locator, faultWord: Locator) {
  const home = page.locator('.presenter-bar a[href="/"]')
  const [homeBox, aiBox, faultBox] = await Promise.all([
    (await home.count()) > 0 ? home.boundingBox() : null,
    aiSwitch.boundingBox(),
    faultWord.boundingBox(),
  ])
  expect(faultBox!.x).toBeGreaterThanOrEqual(0)
  expect(faultBox!.x + faultBox!.width).toBeLessThanOrEqual(width)
  if (homeBox) expect(boxesIntersect(homeBox, faultBox!)).toBe(false)
  expect(boxesIntersect(aiBox!, faultBox!)).toBe(false)
  return { homeBox, aiBox: aiBox!, faultBox: faultBox! }
}
