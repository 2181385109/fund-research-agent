# 进度记录（PROGRESS）

> 由执行者在每个阶段结束时按 CLAUDE.md §11 的模板追加一节，旧内容不删除。统筹审查后会在对应小节末尾追加「统筹审查结论」。

## 当前状态
- 当前阶段：S0（未开始）
- 最近一次 CI：—
- 待用户处理：
  1. 在 `.env` 中填写 `LLM_API_KEY`（DeepSeek）
  2. 在 `%USERPROFILE%\.wslconfig` 的 `[wsl2]` 下加 `memory=10GB`、`swap=4GB`，执行 `wsl --shutdown`，然后运行 `wscript.exe D:\tools\wsl-keepalive.vbs`
  3. （可选）用自己的浏览器打开 http://eid.csrc.gov.cn/fund/disclose/index.html，告诉统筹能否访问
  4. S1 确认基金池；S3 抽检评测集；S8 盲标回答

---
