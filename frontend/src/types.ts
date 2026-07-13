// Mirrors the `meta` SSE payload from app/service.py (_meta_payload).
export interface Citation {
  n: number
  chunk_id: number
  doc_code: string
  title: string
  section_type: string
  source_url: string
}

export interface Meta {
  refused: boolean
  top_rrf_score: number | null
  citations: Citation[]
}
