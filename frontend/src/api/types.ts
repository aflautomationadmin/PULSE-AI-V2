export interface Citation {
  claim: string;
  source_column: string;
  source_value: string;
  metric_column: string;
  metric_value: string;
  row_index: number;
}

export interface VerificationIssue {
  number_in_answer: string;
  issue: string;
}

export interface VerificationResult {
  verified: boolean;
  issues: VerificationIssue[];
}

export interface ChartData {
  chart_type: string;
  title: string;
  labels?: string[];
  datasets?: Array<{
    label: string;
    data: number[];
    backgroundColor?: string | string[];
    borderColor?: string;
  }>;
  columns?: string[];
  rows?: unknown[][];
}

export interface ChatResponse {
  route: 'business_question' | 'normal_chat';
  answer_text: string;
  sql_used: string | null;
  sql_explanation: string | null;
  row_preview: Record<string, unknown>[] | null;
  chart_data: ChartData | null;
  chart_type: string | null;
  citations: Citation[];
  verification: VerificationResult | null;
  last_sql: string | null;
  last_entity_match: string | null;
  last_resolver_explanation: string | null;
  cache_status: string | null;
}

export interface Thread {
  thread_id: string;
  turn_count: number;
  is_active: boolean;
}

export interface ThreadsResponse {
  threads: Thread[];
  active: string;
}

export interface SqlCacheEntry {
  id: number;
  original_question: string;
  normalized_question: string;
  hit_count: number;
  created_at: string;
  last_success_at: string;
}
