import { useState } from "react"
import type { Replay } from "../lib/replays"

export default function CaseOutputs({ previews, downloads }: Pick<Replay, "previews" | "downloads">) {
  const [selected, setSelected] = useState(0)
  const [slideIndex, setSlideIndex] = useState(0)
  const preview = previews[selected]

  return (
    <section id="outputs" className="case-section" aria-labelledby="outputs-title">
      <div className="section-heading"><h2 id="outputs-title">交付文件</h2></div>
      <div className="output-panel">
        <div className="output-bar">
          {previews.length > 1 && (
            <div className="output-picker" role="group" aria-label="选择预览">
              {previews.map((item, i) => (
                <button key={item.id} type="button" aria-pressed={i === selected} onClick={() => {
                  setSelected(i)
                  setSlideIndex(0)
                }}>{item.label}</button>
              ))}
            </div>
          )}
          <div className="downloads">
            {downloads.map(({ path, label }) => (
              <a key={path} className="text-link" href={path} download>{label} <span aria-hidden="true">↓</span></a>
            ))}
          </div>
        </div>
        {preview?.kind === "slides" && (
          <div className="slide-viewer">
            <img src={preview.slides[slideIndex].path} width="1040" height="585" alt={preview.slides[slideIndex].title} loading="lazy" />
            {preview.slides.length > 1 && (
              <div className="slide-controls" role="group" aria-label="幻灯片翻页">
                <button type="button" onClick={() => setSlideIndex(slideIndex - 1)} disabled={slideIndex === 0}>上一页</button>
                <span aria-live="polite">{slideIndex + 1} / {preview.slides.length}</span>
                <button type="button" onClick={() => setSlideIndex(slideIndex + 1)} disabled={slideIndex === preview.slides.length - 1}>下一页</button>
              </div>
            )}
          </div>
        )}
        {preview?.kind === "html" && (
          <div className="dashboard-preview">
            <iframe src={preview.path} title={preview.title} sandbox="allow-scripts" />
            <a className="text-link" href={preview.path} target="_blank" rel="noopener noreferrer">单独打开网页 <span aria-hidden="true">↗</span></a>
          </div>
        )}
      </div>
    </section>
  )
}
