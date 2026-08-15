# Contributing to Artifact Fence

感谢改进 Artifact Fence。项目的首要原则是：**只读、可复现、不回显秘密值，并且不执行被扫描仓库的代码。**

## 开始前

请先阅读 README 的限制和 [SECURITY.md](SECURITY.md)。如果改动涉及路径解析、符号链接、YAML 解析、秘密识别或输出脱敏，请先开 Issue 描述边界与复现；不要在公开 Issue、测试、fixture 或日志中提交真实凭据。

## 本地验证

项目支持 Python 3.11–3.13。安装后运行：

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src
bash demo/run_demo.sh
```

Pull Request 应包含针对行为改变的测试。涉及规则的改动应证明两件事：危险输入被检测，接近但无害的输入不会被误判。涉及路径匹配的改动还应覆盖隐藏文件、排除模式和 root escape。

## 代码与设计原则

保持 CLI 退出码、JSON `schema_version` 和 finding 的 `rule_id` 向后兼容；若必须改变，请在 README 和变更说明中明确迁移路径。避免增加会执行 workflow、shell、网络请求或仓库代码的功能。若新 action 或语法不能被静态可靠理解，应把它标为 dynamic，而不是猜测运行时结果。

提交内容须为原创或具有兼容许可证；不要复制其他项目的实现、README 或测试夹具。请写清测试命令和结果，并保持 demo 可在 60 秒内运行。
