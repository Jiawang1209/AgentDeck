# 2026-07-27 Copilot Line 1 Round 10：F1 测试竞态修复（PASS，影子零 diff #2）

## 目的

修复 round 9 审查者取证定位的 F1（高）：focusedMouseleave 测试用例竞态
挂起（切页后未等交叉淡切即 focus+Tab → closest null → TypeError →
promise 永不 resolve；main 首跑即 TIMEOUT 复现）。同时验证折叠框提取
回退在全轮次的表现，并攒影子零 diff 证据 #2。

## 整环结果

- DeepSeek 精简 2 步（coder→reviewer，方案已精确无需 planner）；
  approve-plan 一次批准；单段 follow 41 wave 到 complete。
- coder：基线复现（自建 10s 超时夹具证明挂起）→ 修复（await delay(750)
  + 页面回调可选链空值防御始终 resolve）→ 连跑 5 次夹具收退出码证据 →
  跨资产回归 → 自主 commit `fe171be test: stabilize carousel focus
  timing`。reviewer 复核 PASS，`92e0b7f` 审查文档。merge-on-complete
  自动合并两分支；release 3/3 零 dirty；prune 全回收。
- **F1 独立复验**：合并后 main 连跑 5 次 carousel 测试全 exit 0 零挂起
  （对照修复前首跑即 TIMEOUT）；back-to-top 回归 exit 0。

## 授权框观察（scoped 委托新数据）

- 本轮出现**第三类命令**：`node -e '<内联脚本>'`（超时夹具/连跑夹具/
  退出码包装，共 3 框）。注册表委托只覆盖 `node tests/` 与 git 前缀，
  哨兵三次精确报警；user 拍板"本轮 node -e 类全委托，操作者逐框目视
  内联脚本内容后放行"——人在环放行 3 次，全程零误放。
- **发现**：挂起类缺陷修复任务天然催生进程管理类新命令（spawn/kill/
  timeout 夹具），委托 scope 设计需为"验证工具链随任务类型漂移"留空间；
  `node -e` 类因内容任意性不宜进前缀注册表，"哨兵报警+目视放行"是
  正确形态。
- 折叠框提取回退（2abac5f6）本轮 live 复验：第 3 框成功提取出 node -e
  命令文本（回退工作正常），delegated=false 判定准确——报警语义精确。

## 影子零 diff 证据 #2

收尾 `agentdeck storage shadow-diff`：in_sync=true，零 mismatch。
连续两轮 live（合计数百次写入）零漂移。

## 结论

F1 闭环（缺陷池清空至只剩 F2-F4 低级项）；测试资产四轮跨轮复利后回到
全绿稳态；走开链路三连 PASS；影子镜像 2/2 零 diff。
