/**
 * 보류 중인 change set (명세 §8.2).
 *
 * 편집은 즉시 적용되지 않는다. 변경은 여기에 쌓이고 변경 검토 화면에서 한 번에
 * 적용한다. 적용 자체는 M2 에서 붙인다.
 */

import { create } from 'zustand'
import type { ApplyMethod, ParamValue } from './types'

export interface PendingChange {
  node: string
  param: string
  /** 현재 런타임/저장값. diff 표시에 쓴다. */
  before: ParamValue
  value: ParamValue
  apply: ApplyMethod
  /** 이 노드가 속한 unit. restart 영향 범위 계산에 쓴다 (§6.1). */
  unit: string | null
  /** 전역 설정 키. 값이 있으면 변경이 센서 스택 전체에 번진다 (§6.1 전역 변경 처리). */
  globalRef: string | null
}

interface ChangeSetState {
  changes: PendingChange[]
  comment: string
  /** WebSocket 으로 들어온 진행 단계. 적용 중 표시에 쓴다 (§7.3). */
  liveStage: string | null
  setChange: (change: PendingChange) => void
  removeChange: (node: string, param: string) => void
  clear: () => void
  setComment: (comment: string) => void
  setLiveStage: (stage: string | null) => void
  find: (node: string, param: string) => PendingChange | undefined
}

const sameTarget = (a: PendingChange, node: string, param: string) =>
  a.node === node && a.param === param

export const useChangeSet = create<ChangeSetState>((set, get) => ({
  changes: [],
  comment: '',
  liveStage: null,

  setChange: (change) =>
    set((state) => {
      const rest = state.changes.filter(
        (c) => !sameTarget(c, change.node, change.param),
      )
      // 원래 값으로 되돌렸으면 change set 에서 빼는 편이 자연스럽다
      if (valuesLookEqual(change.before, change.value)) {
        return { changes: rest }
      }
      return { changes: [...rest, change] }
    }),

  removeChange: (node, param) =>
    set((state) => ({
      changes: state.changes.filter((c) => !sameTarget(c, node, param)),
    })),

  clear: () => set({ changes: [], comment: '', liveStage: null }),
  setComment: (comment) => set({ comment }),
  setLiveStage: (liveStage) => set({ liveStage }),
  find: (node, param) => get().changes.find((c) => sameTarget(c, node, param)),
}))

/**
 * 프론트엔드용 느슨한 비교. 백엔드의 values_equal 과 같은 의도다.
 * 최종 판정은 백엔드가 하므로 여기서는 UI 표시만 책임진다.
 */
function valuesLookEqual(a: ParamValue, b: ParamValue): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => String(v) === String(b[i]))
  }
  if (typeof a === 'boolean' || typeof b === 'boolean') return a === b
  if (a === null || b === null) return a === b
  return String(a) === String(b)
}

/** 전역 설정을 건드리는 변경. 영향 범위가 센서 스택 전체로 번진다 (§6.1). */
export function globalChanges(changes: PendingChange[]): PendingChange[] {
  return changes.filter((change) => change.globalRef !== null)
}

/** change set 이 건드리는 unit 목록. 재시작 영향 범위의 기준이다 (§6.1). */
export function affectedUnits(changes: PendingChange[]): string[] {
  const units = new Set<string>()
  for (const change of changes) {
    if (change.apply === 'restart' && change.unit) units.add(change.unit)
  }
  return [...units].sort()
}
