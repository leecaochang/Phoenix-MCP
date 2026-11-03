---
{ "id": "window.requestDetails", "title": "{{label:workspace.requestDetails}}", "parent": "workspace.approvals", "order": 3, "keywords": ["参数", "原始", "结果", "错误"] }
---
审核中所选请求的技术记录。它随工作区在审核下方打开；如果关闭了它，可从 **{{label:workspace.options}}** 重新打开。

每个部分都可以折叠，并会为你选择的下一个请求保持折叠或展开状态。

## {{label:approvals.colRequest}} {#request}

访问密钥、工具、能力、请求发出以及过期或决定的时间，以及其状态。已决定的请求还会显示结束原因。

## {{label:approvals.detailTabArgs}} {#raw-args}

智能体发送的参数。看起来像机密的值会被隐藏，Phoenix 自己的内部参数也会隐藏。默认折叠。

## {{label:approvals.detailTabResult}} {#result}

已决定请求的操作返回内容，包括任何错误。

## 一小时之后 {#pruned}

请求决定一小时后，Phoenix 会清除其参数、更改和结果。请求会连同结果保留在历史中，但审核和这些部分没有内容可显示。
