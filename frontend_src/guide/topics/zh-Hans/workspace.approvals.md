---
{ "id": "workspace.approvals", "title": "审批工作区", "order": 20, "keywords": ["确认", "待处理", "队列", "审核", "批准", "拒绝"] }
---
当某项能力设为{{label:caps.modeConfirm}}时，智能体请求的更改会在此工作区等待你的决定。MESA 留待确认的更改和配置恢复审查也在此工作区等待。

- [{{label:shell.tabApprovals}}](guide:window.approvals)：请求，待处理或已有结果。工具栏的 **{{label:shell.tabApprovals}}** 按钮会显示有多少请求在等待。
- [{{label:workspace.review}}](guide:window.review)：请求会更改什么，以及 **{{label:approvals.approve}}** 和 **{{label:approvals.reject}}**。
- [{{label:workspace.requestDetails}}](guide:window.requestDetails)：请求的密钥、能力、参数和结果。

无人处理的请求会在一小时后过期。

相关：[审批如何运作](guide:topic.approvalLifecycle)、[变更](guide:workspace.changes)
