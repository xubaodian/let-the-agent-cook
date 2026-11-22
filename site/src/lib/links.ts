export const repository = "https://github.com/xubaodian/let-the-agent-cook"

export function url(path = "") {
  const base = import.meta.env.BASE_URL.replace(/\/$/, "")
  return base + "/" + path.replace(/^\//, "")
}

export function sourceUrl(path: string) {
  const ref = import.meta.env.PUBLIC_REPO_REF || "main"
  const directory = path.endsWith("/")
  const encoded = path.split("/").filter(Boolean).map(encodeURIComponent).join("/")
  return repository + (directory ? "/tree/" : "/blob/") + ref + "/" + encoded
}

export function readableDate(date: Date) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "long", timeZone: "UTC" }).format(date)
}

export function newestFirst<T extends { data: { date: Date } }>(a: T, b: T) {
  return b.data.date.getTime() - a.data.date.getTime()
}
