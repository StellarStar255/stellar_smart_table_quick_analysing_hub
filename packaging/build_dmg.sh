#!/bin/bash
# macOS dmg 打包脚本。在仓库根目录执行: bash packaging/build_dmg.sh
#
# 代码签名（可选，用于 Gatekeeper 放行与一键升级信任链）:
#   export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
# 未设置时使用 ad-hoc 签名（本机可运行，分发时用户需手动放行一次）。
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 -c "from version import __version__; print(__version__)")
ARCH=$(uname -m)
APP="dist/SmartTableHub.app"
DMG="dist/SmartTableHub-${VERSION}-macos-${ARCH}.dmg"

echo "==> PyInstaller 构建 v${VERSION} (${ARCH})"
python3 -m PyInstaller packaging/smart_table_hub.spec --noconfirm

echo "==> 代码签名"
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    # 真证书：启用 hardened runtime（公证必需）+ Python 应用所需 entitlements
    codesign --force --deep --options runtime \
        --entitlements packaging/entitlements.plist \
        --sign "$CODESIGN_IDENTITY" "$APP"
else
    # 未提供证书：保留 PyInstaller 已做好的 ad-hoc 签名。
    # 注意不要在 ad-hoc 上启用 hardened runtime——库校验会拒绝加载
    # 第三方签名的动态库（如 Anaconda 的 libpython），应用会闪退。
    echo "（未设置 CODESIGN_IDENTITY，使用 PyInstaller 的 ad-hoc 签名）"
fi
codesign --verify --verbose=1 "$APP"

echo "==> 生成 dmg"
STAGING=$(mktemp -d)
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
rm -f "$DMG"
# GitHub macOS runner 上 hdiutil 偶发 "Resource busy"（XProtect 扫描占用），重试即可
for attempt in 1 2 3 4 5; do
    if hdiutil create -volname "Smart Table Hub" -srcfolder "$STAGING" \
        -ov -format UDZO "$DMG"; then
        break
    fi
    [ "$attempt" -eq 5 ] && { echo "hdiutil 连续失败"; exit 1; }
    echo "hdiutil 失败（第 $attempt 次），5 秒后重试..."
    sleep 5
done
rm -rf "$STAGING"

# 公证（需要 Developer ID 证书 + App 专用密码）:
#   export NOTARY_PROFILE=<notarytool keychain profile>
if [ -n "${NOTARY_PROFILE:-}" ]; then
    echo "==> 公证 dmg"
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
fi

echo "==> 完成: $DMG"
