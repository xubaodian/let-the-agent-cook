import { useEffect, useState } from "react"
import type { ReplayStep } from "../lib/replays"

const interval = 2600

export default function ReplayPlayer({ steps }: { steps: ReplayStep[] }) {
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const last = steps.length - 1
  const step = steps[index]

  useEffect(() => {
    if (!playing || index === last) return
    const timer = window.setTimeout(() => {
      if (index + 1 === last) setPlaying(false)
      setIndex(index + 1)
    }, interval)
    return () => window.clearTimeout(timer)
  }, [playing, index, last])

  function select(next: number) {
    setPlaying(false)
    setIndex(next)
  }

  function toggle() {
    if (last === 0) return
    if (!playing && index === last) setIndex(0)
    setPlaying(!playing)
  }

  return (
    <section id="replay" className="case-section" aria-labelledby="replay-title">
      <div className="section-heading">
        <h2 id="replay-title">执行回放</h2>
        <span className="step-count" aria-label="回放进度">{index + 1} / {steps.length}</span>
      </div>
      <div className="replay">
        <ol className="step-list" aria-label="回放步骤">
          {steps.map((item, i) => (
            <li key={item.id}>
              <button
                type="button"
                aria-current={i === index ? "step" : undefined}
                aria-controls="replay-step"
                onClick={() => select(i)}
              >
                <span className="step-number" aria-hidden="true">{i + 1}</span>
                <span>{item.title}</span>
              </button>
            </li>
          ))}
        </ol>
        <div className="replay-main">
          <div id="replay-step" data-event-seq={step.seq} className="step-detail" aria-live={playing ? "off" : "polite"}>
            <h3>{step.title}</h3>
            <p>{step.detail}</p>
            <ul className="step-events" aria-label="关联事件">
              {step.events.map((event) => <li key={event}><code>{event}</code></li>)}
            </ul>
          </div>
          <progress className="replay-progress" value={index + 1} max={steps.length} aria-label="回放进度" />
          <div className="playback" role="group" aria-label="播放控制">
            <button type="button" onClick={() => select(index - 1)} disabled={index === 0}>上一步</button>
            <button type="button" className="play" onClick={toggle} disabled={last === 0}>{playing ? "暂停" : "播放"}</button>
            <button type="button" onClick={() => select(index + 1)} disabled={index === last}>下一步</button>
            <button type="button" onClick={() => select(0)} disabled={index === 0 && !playing}>回到开头</button>
          </div>
        </div>
      </div>
    </section>
  )
}
