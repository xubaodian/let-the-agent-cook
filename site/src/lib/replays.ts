export type ReplayStep = {
  id: string
  seq: number
  title: string
  detail: string
  events: string[]
}

export type Preview =
  | { id: string; kind: "slides"; label: string; slides: { path: string; title: string }[] }
  | { id: string; kind: "html"; label: string; path: string; title: string }

export type Replay = {
  version: 1
  steps: ReplayStep[]
  previews: Preview[]
  downloads: { path: string; label: string }[]
}

const replays = import.meta.glob<Replay>("../data/replays/*.json", {
  eager: true,
  import: "default",
})

export function getReplay(id: string) {
  const replay = replays["../data/replays/" + id + ".json"]
  if (!replay || replay.version !== 1 || replay.steps.length === 0) {
    throw new Error("Missing or invalid replay for " + id)
  }
  return replay
}
