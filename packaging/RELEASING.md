# 发版指南

## 发一个新版本

1. 修改 `version.py` 中的 `__version__`（唯一的版本来源）。
2. 提交并打 tag 推送：

   ```bash
   git commit -am "release: v1.0.1"
   git tag v1.0.1
   git push origin main --tags
   ```

3. GitHub Actions 自动构建并发布 Release，产物：
   - `SmartTableHub-<ver>-macos-arm64.dmg`（Apple Silicon）
   - `SmartTableHub-<ver>-macos-x86_64.dmg`（Intel Mac）
   - `SmartTableHub-<ver>-linux-amd64.deb`
   - `SmartTableHub-<ver>-windows-x64-setup.exe`
   - `SHA256SUMS.txt`（一键升级的完整性校验依据）

## 一键升级原理

已安装的应用启动 3 秒后静默检查 GitHub Releases（也可在菜单
「帮助 → 检查更新...」手动检查）。发现新版本后：

1. 下载当前平台安装包到临时目录；
2. 用 Release 中的 `SHA256SUMS.txt` 校验完整性（防篡改）；
3. 静默安装并自动重启：
   - macOS：辅助脚本等应用退出后挂载 dmg、替换 .app、重新打开；
   - Windows：以 `/SILENT` 运行 Inno 安装器，装完自动启动新版本；
   - Linux：`pkexec dpkg -i` 提权安装后重启应用。

**注意**：升级依赖 Release 资产文件名规则（见上），请勿改动
`packaging/` 下脚本的输出文件名，否则旧版本客户端将找不到升级包。

## 代码签名证书

不配置证书也能出包和升级（macOS 用 ad-hoc 签名，SHA256 校验保证完整
性），但用户首次安装会遇系统拦截提示。配好证书后签名/公证自动启用。

在仓库 **Settings → Secrets and variables → Actions** 添加：

### macOS（需 Apple Developer 账号，$99/年）

| Secret | 内容 |
|---|---|
| `MACOS_CERT_P12` | **Developer ID Application** 证书导出的 .p12，base64 编码：`base64 -i cert.p12 \| pbcopy` |
| `MACOS_CERT_PASSWORD` | .p12 导出密码 |
| `APPLE_ID` | Apple ID 邮箱（公证用） |
| `APPLE_TEAM_ID` | 团队 ID，如 `U95563B5JA` |
| `APPLE_APP_PASSWORD` | App 专用密码（appleid.apple.com 生成） |

> 注意必须是 **Developer ID Application** 类型证书；
> 本机现有的 "Apple Development" 证书只能本机调试，不能对外分发。

### Windows（需向 CA 购买代码签名证书）

| Secret | 内容 |
|---|---|
| `WIN_CERT_PFX` | 代码签名证书 .pfx 的 base64 |
| `WIN_CERT_PASSWORD` | .pfx 密码 |

## 本地构建（调试用）

```bash
# macOS（本机）
bash packaging/build_dmg.sh
# 用本机证书签名：
CODESIGN_IDENTITY="Apple Development: Qiliang Huang (U95563B5JA)" \
    bash packaging/build_dmg.sh

# Linux
bash packaging/build_deb.sh

# Windows
powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
```
