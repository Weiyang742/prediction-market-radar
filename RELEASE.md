# 发布前体检

提交 / 发布前运行（只读，不修改任何文件）：

```bash
bash check.sh
```

`check.sh` 检查项与退出码：

- 敏感文件：`.env*`、`*.db`、`*.db-wal`、`*.db-shm`、`*.sqlite*`、私钥、`*.log`
  （白名单 `predictions.log`）→ 命中即非零退出
- 明文密钥 / 令牌 / 密码（启发式，空占位符不计）→ 命中即非零退出
- 缺失 `LICENSE` / `README.md` → 非零退出
- 大文件（>1MB）→ 仅提示人工确认，不改变退出码

## 发布清单（体检通过后才可提交 / 发布）

- [ ] 无任何真实 API key / token / 密码（只允许空占位符或示例）
- [ ] 无 `.env`、`*.db`、`*.log`（明确公开的 `predictions.log` 除外）
- [ ] `LICENSE` 与 `README.md` 齐备且内容一致
- [ ] 不包含个人或第三方敏感数据
- [ ] 第三方数据 / 代码已标注来源与许可（见 `README.md`「数据来源标注」）
- [ ] `git status` 干净，且已 `git add`
