/** 试跑面板共享状态：选中的试跑批次在两个模块页之间保持，切页面不丢。 */
import { ref } from 'vue'

export const selectedRunId = ref<string>('')
export const trialNotice = ref<string>('')
export const trialError = ref<string>('')
