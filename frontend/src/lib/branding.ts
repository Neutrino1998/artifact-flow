/**
 * Build-time 品牌常量 —— 应用名 / 副标题。
 *
 * 为什么不走 public/site/branding.json：
 *  - layout.tsx 的 metadata.title 是 Next server-side metadata，
 *    生成时机早于任何 client fetch，runtime JSON 触达不到。
 *  - Sidebar / login 的标题是首屏立刻可见的内容，走 fetch 会闪一帧空白。
 *
 * 这两个常量改动 = 改代码 + 重新部署，符合「应用身份不常变」的语义。
 * 而 developer / contact_email（运营信息，可能换客户/换联系人）走
 * public/site/branding.json，运维改文件即生效，不走这里。
 */

// intranet 品牌覆盖（main 上是 ArtifactFlow / 多智能体任务工作台）
export const APP_NAME = '银清Claw测试版';
export const APP_TAGLINE = 'powered by deepseek-v4-flash';
