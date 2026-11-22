import { defineCollection } from "astro:content"
import { glob } from "astro/loaders"
import { z } from "astro/zod"

const entry = {
  title: z.string(),
  description: z.string(),
  date: z.coerce.date(),
  topic: z.string(),
  sourcePath: z.string().optional(),
}

const notes = defineCollection({
  loader: glob({ pattern: "**/*.md", base: "./src/content/notes" }),
  schema: z.object(entry),
})

const cases = defineCollection({
  loader: glob({ pattern: "**/*.md", base: "./src/content/cases" }),
  schema: z.object({
    ...entry,
    sourcePath: z.string(),
    setupPath: z.string().optional(),
    command: z.string().optional(),
    replay: z.string().optional(),
    relatedNote: z.string().optional(),
    relatedNotes: z.array(z.string()).optional(),
  }).transform(({ relatedNote, relatedNotes = [], ...data }) => ({
    ...data,
    relatedNotes: [...new Set(relatedNote ? [relatedNote, ...relatedNotes] : relatedNotes)],
  })),
})

export const collections = { notes, cases }
