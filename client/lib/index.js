/**
 * Host 半边：本包只贡献浏览器端按钮，没有宿主子服务可托管。
 * name / inject / apply 三个导出缺一个，宿主加载本包时都会报错。
 */
export const name = "voice-stop-button"
export const inject = []

export function apply() {}
