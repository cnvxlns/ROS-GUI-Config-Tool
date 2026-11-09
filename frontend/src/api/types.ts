/**
 * 백엔드 DTO 미러 (명세 §7.3, av_config/models.py).
 *
 * ROS 2 파라미터 타입은 아래 9종으로 닫혀 있다. 그래서 JSON Schema 폼 생성기 대신
 * 타입별 렌더러를 직접 둔다 (§8.3).
 */

export const ParamType = {
  NOT_SET: 0,
  BOOL: 1,
  INTEGER: 2,
  DOUBLE: 3,
  STRING: 4,
  BYTE_ARRAY: 5,
  BOOL_ARRAY: 6,
  INTEGER_ARRAY: 7,
  DOUBLE_ARRAY: 8,
  STRING_ARRAY: 9,
} as const

export type ParamTypeValue = (typeof ParamType)[keyof typeof ParamType]

export type Layer = 'device' | 'driver' | 'global' | 'host'
export type ApplyMethod = 'runtime' | 'reconfigure' | 'restart'
export type Tier = 'operator' | 'engineer' | 'hidden' | 'read_only'

export interface ValueRange {
  from_value: number
  to_value: number
  step: number
}

/** 파라미터 값으로 올 수 있는 모든 형태. */
export type ParamValue =
  | boolean
  | number
  | string
  | boolean[]
  | number[]
  | string[]
  | null

export interface ParamDTO {
  name: string
  type: ParamTypeValue

  description: string
  additional_constraints: string
  read_only: boolean
  dynamic_typing: boolean
  integer_range: ValueRange | null
  floating_point_range: ValueRange | null

  runtime_value: ParamValue
  saved_value: ParamValue
  saved_source: string | null
  drift: boolean

  has_schema: boolean
  label: string | null
  layer: Layer | null
  apply: ApplyMethod
  tier: Tier
  enum_values: string[] | null
  global_ref: string | null
  lock_while_driving: boolean
}

export interface NodeDTO {
  name: string
  alive: boolean
  /** services.yaml 에 등록됐는지. 미등록이면 변경을 저장할 곳이 없어 읽기 전용이다. */
  registered: boolean
  subsystem: string | null
  unit: string | null
  param_files: string[]

  display_mode: 'mapped' | 'raw'
  schema_driver: string | null

  param_count: number
  drift_count: number
  downstream: string[]

  unreachable_reason: string | null
  launch_rule_violation: boolean
}

export interface NodeParamsDTO {
  node: NodeDTO
  params: ParamDTO[]
}

export interface SchemaLoadError {
  file: string
  error: string
}

export interface DiscoverySnapshotDTO {
  collected_at: number
  nodes: NodeDTO[]
  schema_errors: SchemaLoadError[]
}

// --------------------------------------------------------------------------
// 온보딩 (§12.2)
// --------------------------------------------------------------------------

export interface OnboardingStepDTO {
  number: number
  title: string
  detail: string
  /** null 이면 Config App 이 판단할 수 없는 단계다 */
  done: boolean | null
}

export interface OnboardingDTO {
  node: string
  registered: boolean
  has_schema: boolean
  alive: boolean

  subsystem: string
  unit: string
  param_file: string
  schema_file: string

  services_snippet: string
  param_file_draft: string
  schema_draft: string

  steps: OnboardingStepDTO[]
  notes: string[]
}

export interface SafetyStateDTO {
  mode: 'open' | 'enforce'
  apply_allowed: boolean
  reason: string
  vehicle_state_topic: string | null
}

// --------------------------------------------------------------------------
// change set (§6.1, §7.3)
// --------------------------------------------------------------------------

export type ChangeSetStatus =
  | 'rejected'
  | 'dry_run'
  | 'running'
  | 'succeeded'
  | 'rolled_back'
  | 'rollback_failed'

export type IssueCode =
  | 'unknown_node'
  | 'unknown_param'
  | 'read_only'
  | 'type_mismatch'
  | 'out_of_range'
  | 'not_in_enum'
  | 'forbidden_tier'
  | 'locked_while_driving'
  | 'safety_unknown'
  | 'check_failed'
  | 'file_changed'
  | 'no_param_file'

export interface ValidationIssue {
  node: string
  param: string | null
  code: IssueCode
  message: string
}

export type StepKind = 'set_parameters' | 'reconfigure' | 'restart_units'

export interface PlanStep {
  kind: StepKind
  node: string | null
  units: string[]
  params: string[]
  note: string | null
}

export interface ExecutionPlan {
  steps: PlanStep[]
  restart_units: string[]
  impacted_nodes: string[]
  global_params: string[]
  touches_global_file: boolean
  files_to_write: string[]
}

export interface ParamResult {
  node: string
  param: string
  requested: ParamValue
  actual: ParamValue
  success: boolean
  reason: string | null
  readback_source: string | null
}

export interface ChangeSetResult {
  id: string
  status: ChangeSetStatus
  stage: string
  issues: ValidationIssue[]
  plan: ExecutionPlan | null
  results: ParamResult[]
  commit: string | null
  rollback_commit: string | null
  message: string | null
}

export interface HistoryCommit {
  sha: string
  author: string
  when: string
  subject: string
}

export interface HistoryDTO {
  clean: boolean
  dirty_files: string[]
  commits: HistoryCommit[]
}

export const STATUS_LABEL: Record<ChangeSetStatus, string> = {
  rejected: '거부됨 — 아무것도 바뀌지 않았다',
  dry_run: '검증 통과 (적용 안 함)',
  running: '진행 중',
  succeeded: '적용 완료',
  rolled_back: '되돌렸다',
  rollback_failed: '롤백 실패 — 수동 조치 필요',
}

export const STAGE_ORDER = ['validate', 'plan', 'save', 'apply', 'verify', 'done'] as const

export const STAGE_LABEL: Record<string, string> = {
  validate: '검증',
  plan: '계획',
  save: '저장',
  apply: '적용',
  verify: '반영 확인',
  verify_dataflow: '데이터 흐름 확인',
  rollback: '롤백',
  rolled_back: '롤백 완료',
  rejected: '거부',
  dry_run: '검증만',
  done: '완료',
}

/** WebSocket 메시지. type 디스크리미네이터로 분기한다 (§7.3). */
export type StreamMessage =
  | {
      type: 'node_state'
      node: string
      alive: boolean
      unit: string | null
      drift_count: number
      unreachable_reason: string | null
    }
  | {
      type: 'changeset_progress'
      id: string
      stage: string
      detail: unknown
    }
  | {
      type: 'health_result'
      rule: string
      status: string
      detail: unknown
    }

// --------------------------------------------------------------------------
// 표시용 헬퍼
// --------------------------------------------------------------------------

export const TYPE_LABEL: Record<number, string> = {
  [ParamType.NOT_SET]: '미설정',
  [ParamType.BOOL]: 'bool',
  [ParamType.INTEGER]: 'int',
  [ParamType.DOUBLE]: 'double',
  [ParamType.STRING]: 'string',
  [ParamType.BYTE_ARRAY]: 'byte[]',
  [ParamType.BOOL_ARRAY]: 'bool[]',
  [ParamType.INTEGER_ARRAY]: 'int[]',
  [ParamType.DOUBLE_ARRAY]: 'double[]',
  [ParamType.STRING_ARRAY]: 'string[]',
}

export const APPLY_LABEL: Record<ApplyMethod, string> = {
  runtime: '즉시 반영',
  reconfigure: '장치 재설정',
  restart: 'unit 재시작',
}

/** 적용 방식별 경고 강도. restart 는 서브시스템이 중단된다 (§8.2). */
export const APPLY_SEVERITY: Record<ApplyMethod, 'info' | 'warn' | 'danger'> = {
  runtime: 'info',
  reconfigure: 'warn',
  restart: 'danger',
}

export function formatValue(value: ParamValue): string {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.length === 0 ? '[]' : `[${value.join(', ')}]`
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

/** 편집 가능 여부. read_only 와 hidden 등급은 손댈 수 없다 (§4.3). */
export function isEditable(param: ParamDTO): boolean {
  return !param.read_only && param.tier !== 'read_only' && param.tier !== 'hidden'
}
