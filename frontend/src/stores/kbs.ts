import { reactive } from 'vue'
import { http } from '../api/http'
import type { Kb } from '../api/types'

export const kbStore = reactive<{ list: Kb[]; loaded: boolean }>({ list: [], loaded: false })

export async function loadKbs(): Promise<void> {
  kbStore.list = await http.get<Kb[]>('/api/kbs')
  kbStore.loaded = true
}

export const isPublic = (kb: Kb): boolean => kb.readOnly || kb.type === 'PUBLIC'

/** 检索范围的文字描述：「公共库 + 我的库 A、我的库 B」。库已被删除时显示 id。 */
export function describeScope(ids: number[] | null | undefined, kbs: Kb[]): string {
  if (!ids || ids.length === 0) return '未记录'
  const byId = new Map(kbs.map((k) => [k.id, k]))
  const pub: string[] = []
  const priv: string[] = []
  for (const id of ids) {
    const kb = byId.get(id)
    if (!kb) priv.push(`已删除的库 #${id}`)
    else if (isPublic(kb)) pub.push('公共库')
    else priv.push(`私有库「${kb.name}」`)
  }
  return [...pub, ...priv].join(' + ')
}
