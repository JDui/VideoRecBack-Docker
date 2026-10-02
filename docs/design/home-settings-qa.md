# 首页与设置页设计验证

final result: passed

## 对比依据与状态

- 首页参考：`docs/design/home-reference.png`，原始像素 1486 × 1059。对应用户选定的第 1 版及文案修订稿。
- 设置参考：`docs/design/settings-reference.png`，原始像素 1487 × 1058。使用同一浅色与绿色视觉方向。
- 桌面浏览器：Codex 内置浏览器，CSS viewport 1440 × 1024，浅色、页面顶部、无展开菜单。
- 实际浏览器截图为 1425 × 1013 像素；并排比较时将参考与实现统一缩放为 1440 × 1024，不裁切内容。
- 手机浏览器：CSS viewport 390 × 844。页面内容宽度 375px（扣除浏览器滚动条），未出现横向溢出。
- 页面来自真实 FastAPI/Jinja 应用，预览连接 `/private/tmp/videorecback-home-preview` 下的独立示例库。视频数量、路径、封面、时长和收藏为示例数据，正式页面直接读取实际数据库与缓存封面。

## 最终证据

- 首页：`docs/design/home-desktop-final.jpg`。
- 设置：`docs/design/settings-desktop-final.jpg`。
- 并排完整比较：`docs/design/home-comparison.jpg`、`docs/design/settings-comparison.jpg`。
- 局部比较：`docs/design/home-focus-comparison.jpg`（首屏标题、三张封面与换组按钮）、`docs/design/settings-focus-comparison.jpg`（表单字段与单位后缀）。
- 手机页面：`docs/design/home-mobile.jpg`、`docs/design/settings-mobile.jpg`、`docs/design/settings-mobile-playback.jpg`。
- 空库：`docs/design/home-empty-mobile.jpg`。
- 维护区域：`docs/design/settings-maintenance.jpg`。

## 比较历史与修复

1. 首页初次比较发现 P2：底部概览超出首屏，初版 footer 起点约 998px，更新按钮需要滚动才能完整看到。证据：`/private/tmp/videorecback-home-preview/home-comparison-first.jpg`。缩小导航高度、首屏标题区域与模块间距，并调整日期回顾封面比例。最终在 scrollY=0 时 document height 为 1024px，footer bottom 为约 999px，五个模块与更新按钮均完整可见。
2. 设置初次比较发现 P2：“小时”后缀被挤成两行，字段间距偏大。证据：`/private/tmp/videorecback-home-preview/settings-desktop.jpg`。输入框改为 flex:1/width:0，后缀禁止收缩与换行，压缩字段间距并恢复深色标签。最终单位输入区域高 44px，未换行；分组、双列字段和固定保存栏清楚分离。
3. 复查发现浏览器继续使用同 URL 的早期样式缓存。为新首页和设置布局使用 v2 资源 URL，等待网络空闲后重新捕获。最终 computed styles 确认：首页顶部 padding 20px，模块间距 32px，footer margin 24px；设置字段 gap 为 18px/36px，后缀 flex-shrink 为 0。
4. 修订后的完整与局部并排图均已打开检查，无剩余需要阻止交付的 P0/P1/P2 问题。

## 五项视觉检查

- 字体：沿用项目系统字体栈。标题层级明确，表单与正文 14–16px，辅助信息 12–13px。长视频名使用省略号并保留完整 title；手机字段保持完整标签。
- 布局：首页为三张大封面、六张最近记录、下方收藏与日期回顾两列、底部概览；设置为分组导航、双列表单与固定保存栏。手机端首页单列焦点与双列列表，设置单列表单与横向分组导航。
- 颜色：使用项目绿色强调色、浅灰绿色页面、深色标题及细分隔线。没有引入深色界面或多层嵌套面板；键盘焦点使用实色绿色轮廓。
- 图片：使用真实缓存图接口，保留全景与 10bit 标记；示例图片为独立生成的照片，未裁切整张效果图充当网站。实际视频内容决定最终封面，示例图片不加入生产静态资源。
- 文案：首页使用“回忆拾回、最近记录、珍藏片段、那年今日、影像库概览”；扫描空闲不会误报为“已同步”。空库、未收藏、无往年同日记录和封面异常均有对应文案。

## 交互与回归

- “再拾三段”可换组，视频 ID 不重复，库足够大时避开上一组。空库按钮禁用。
- 最近记录播放入口可进入播放器，返回后回到首页。
- “那年今日”查看全部只显示往年同月同日视频；原有时间线、收藏链接与新的 `/library` 均可进入视频库。
- 设置分组跳转与手机分组高亮正常；修改音量后显示待保存反馈，保存后留在设置页并回显新值。示例音量已恢复为 20%。
- 服务器连通测试完成 2MB 样本下载并显示结果。
- 维护操作沿用既有二次确认与后台接口。预览环境未安装 FFmpeg，因此没有把本地封面重建或转码成功列为本次 UI 验证结果；维护验证后示例封面已恢复。
- 首页与设置的浏览器控制台检查未发现 error/warn。
- 自动测试：111 passed，覆盖随机换组、小库/空库、缺失视频过滤、往年日期与闰日匹配、设置保存以及原有视频库/播放功能。

## 可接受差异与后续细节

- 预览中的实际示例标题、路径、数量和封面与效果图示例不同，生产数据接入属于预期差异。
- 设置使用原生复选框，目录字段保留文本输入；未添加效果图中的装饰性目录选择按钮，以免暗示浏览器可以选择服务器目录。
- P3：部分装饰图标精简为文字，字体抗锯齿与 JPEG 截图清晰度受浏览器和系统影响，不影响布局或操作。
- 完成项：首屏完整、表单分组、固定保存、响应式、空状态、主要交互与兼容入口均已验证。
