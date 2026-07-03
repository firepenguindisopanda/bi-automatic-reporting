export interface BISubmitResponse {
  job_id: string
  status: string
  message: string
}

export interface BIJobStatus {
  job_id: string
  status: string
  progress: { event: string; agent: string }[]
  error: string | null
}


